/**
 * The privacy wire calls, including the parts a browser will not tell you are
 * wrong: the filename parsed out of a response header, and the exact bodies the
 * delete endpoint receives.
 */

import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";

import { makePrivacyFake } from "@/test/privacyFakes";
import { deleteAccount, filenameFrom } from "./endpoints";

describe("filenameFrom", () => {
  it("reads a quoted filename", () => {
    expect(filenameFrom('attachment; filename="manovia-export-2026-10-11.zip"')).toBe(
      "manovia-export-2026-10-11.zip",
    );
  });

  it("reads a bare filename", () => {
    expect(filenameFrom("attachment; filename=export.json")).toBe("export.json");
  });

  it("falls back to a dated name when the header is absent or useless", () => {
    const fallback = filenameFrom(null);
    expect(fallback).toMatch(/^manovia-export-\d{4}-\d{2}-\d{2}\.zip$/);
    expect(filenameFrom("attachment")).toMatch(/^manovia-export-\d{4}-\d{2}-\d{2}\.zip$/);
    expect(filenameFrom('attachment; filename=""')).toMatch(
      /^manovia-export-\d{4}-\d{2}-\d{2}\.zip$/,
    );
    expect(filenameFrom(null, "json")).toMatch(/\.json$/);
  });

  it("strips a path and anything that is not a filename character", () => {
    // A header is untrusted input: no traversal, no separators, no quotes.
    expect(filenameFrom('attachment; filename="../../../etc/passwd"')).toBe("passwd");
    expect(filenameFrom('attachment; filename="..\\..\\windows\\evil.zip"')).toBe("evil.zip");
    expect(filenameFrom('attachment; filename="a b;c<script>.zip"')).toBe("abcscript.zip");
  });
});

describe("deleteAccount", () => {
  it("sends only the phrase for an anonymous account", async () => {
    const fake = makePrivacyFake();
    const receipt = await deleteAccount({ confirm: "DELETE" }, fake.client);

    expect(fake.lastDeleteBody).toEqual({ confirm: "DELETE" });
    expect(receipt.irreversible).toBe(true);
    expect(receipt.rows_deleted["users"]).toBe(1);
  });

  it("adds the password when one is supplied, and omits it otherwise", async () => {
    const fake = makePrivacyFake();
    await deleteAccount({ confirm: "DELETE", password: "correct-horse-battery" }, fake.client);
    expect(fake.lastDeleteBody).toEqual({
      confirm: "DELETE",
      password: "correct-horse-battery",
    });
  });

  it("surfaces the backend's code so the UI can curate the message", async () => {
    const fake = makePrivacyFake();
    fake.failDelete = { status: 403, code: "account_locked", message: "Locked for 900 seconds." };
    await expect(deleteAccount({ confirm: "DELETE" }, fake.client)).rejects.toMatchObject({
      code: "account_locked",
      status: 403,
    });
    await expect(deleteAccount({ confirm: "DELETE" }, fake.client)).rejects.toBeInstanceOf(
      ApiError,
    );
  });
});
