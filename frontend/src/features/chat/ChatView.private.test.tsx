/**
 * Private Mode as the chat page actually delivers it: behind one button, in a
 * chunk of its own.
 *
 * The Day-25 bundle budget gives /chat 25 KB gzip, and the on-device stack is
 * larger than that, so `ChatView` imports `PrivateModeProvider` dynamically and
 * keeps a small entry point on the critical path. These tests cover the three
 * things that arrangement could quietly break:
 *
 *   1. the entry point is there and the panel is not, until it is asked for;
 *   2. asking for it works, and is remembered across renders;
 *   3. a boot that fails says so, leaves Private Mode off, and does not take the
 *      standard chat surface down with it;
 *   4. when the boot succeeds, a message is answered on device — with the badge —
 *      and the fake chat server is never asked for anything.
 */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiClient, MemoryTokenStore } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";
import { createFakeChatServer } from "@/test/chatTransport";
import { closePrivateDatabase } from "@/features/private/store/idb";
import {
  BUNDLE_PATH,
  MODEL_MANIFEST_PATH,
  readText,
} from "@/features/private/testSupport/repoArtifacts";

import { ChatView } from "./ChatView";

const PANEL_FLAG = "manovia.private.panel.v1";
const MESSAGE = "I had a bad day and I cannot switch my brain off";

let fetchSpy: ReturnType<typeof vi.fn>;

/** Serves only Private Mode's two static assets; anything else is a failure. */
const privateFetch = vi.fn(async (input: RequestInfo | URL) => {
  const url = typeof input === "string" ? input : input.toString();
  if (url.endsWith("/private/safety-bundle.json")) {
    return new Response(readText(BUNDLE_PATH), { status: 200 });
  }
  if (url.endsWith("/private/model-manifest.json")) {
    return new Response(readText(MODEL_MANIFEST_PATH), { status: 200 });
  }
  throw new Error(`unexpected request from Private Mode: ${url}`);
});

function renderChat() {
  const server = createFakeChatServer();
  const tokens = new MemoryTokenStore();
  const client = new ApiClient({ fetchImpl: server.fetchImpl, tokens, now: () => 0 });
  client.applySession({ access_token: "access-1", refresh_token: "refresh-1", expires_in: 900 });
  const view = renderWithProviders(<ChatView client={client} />);
  return { ...view, server };
}

async function openPanel(): Promise<void> {
  const user = userEvent.setup();
  await user.click(screen.getByTestId("private-mode-open"));
  await screen.findByTestId("private-mode-panel");
}

/** The panel's switch is a native checkbox with `role="switch"`. */
function isOn(): boolean {
  return (screen.getByTestId("private-mode-toggle") as HTMLInputElement).checked;
}

async function switchOn(): Promise<void> {
  const user = userEvent.setup();
  if (!isOn()) {
    await user.click(screen.getByTestId("private-mode-toggle"));
  }
  await waitFor(() => expect(isOn()).toBe(true));
}

beforeEach(() => {
  window.localStorage.clear();
  fetchSpy = vi.fn(() => Promise.reject(new Error("no network in this test")));
  vi.stubGlobal("fetch", fetchSpy);
});

afterEach(async () => {
  vi.unstubAllGlobals();
  await closePrivateDatabase();
  window.localStorage.clear();
});

describe("the way into Private Mode", () => {
  it("offers it with one button, and does not load the panel first", () => {
    renderChat();
    expect(screen.getByTestId("private-mode-entry")).toBeInTheDocument();
    expect(screen.getByTestId("private-mode-open")).toBeInTheDocument();
    expect(screen.queryByTestId("private-mode-panel")).toBeNull();
    // Nothing was fetched: the on-device stack is not even in this chunk.
    expect(fetchSpy).not.toHaveBeenCalled();
    // The standard surface is untouched.
    expect(screen.getByTestId("chat-controls")).toBeInTheDocument();
  });

  it("loads the panel when asked, and remembers that it was", async () => {
    renderChat();
    await openPanel();

    expect(window.localStorage.getItem(PANEL_FLAG)).toBe("1");
    // One way in: the entry point steps aside once the panel is on screen.
    expect(screen.queryByTestId("private-mode-entry")).toBeNull();
    // Off, and showing nothing that needs a boot: no verdict, no models, no keys.
    expect(screen.getByTestId("private-mode-toggle")).not.toBeChecked();
    expect(screen.queryByTestId("private-tier")).toBeNull();
    expect(screen.queryByTestId("model-reply")).toBeNull();
    expect(screen.queryByTestId("private-storage")).toBeNull();
    // Asking for the panel is not turning Private Mode on: no boot, no fetch.
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("comes back with the panel already open on the next visit", async () => {
    window.localStorage.setItem(PANEL_FLAG, "1");
    vi.stubGlobal("fetch", privateFetch);
    renderChat();
    expect(await screen.findByTestId("private-mode-panel")).toBeInTheDocument();
    expect(screen.queryByTestId("private-mode-open")).toBeNull();
  });
});

describe("when the boot fails", () => {
  it("says so, stays off, and leaves the standard surface working", async () => {
    renderChat();
    await openPanel();

    const user = userEvent.setup();
    // `fetch` rejects in this test, so the bundle cannot be read.
    await user.click(screen.getByTestId("private-mode-toggle"));

    expect(await screen.findByTestId("private-boot-error")).toBeInTheDocument();
    await waitFor(() => expect(isOn()).toBe(false));
    // The honest failure: no verdict, no models, and no private answers.
    expect(screen.queryByTestId("private-tier")).toBeNull();
    expect(screen.queryByTestId("private-voice-notice")).toBeNull();
    // And the page is still a chat page.
    expect(screen.getByTestId("chat-controls")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: /what is on your mind/i })).toBeEnabled();
  });
});

describe("when the boot succeeds", () => {
  it("answers on device, labels the reply, and never asks the server", async () => {
    vi.stubGlobal("fetch", privateFetch);
    const { server } = renderChat();
    await openPanel();
    await switchOn();

    // jsdom is not a secure context, so the verdict is the honest "rules only".
    expect(await screen.findByTestId("private-tier")).toBeInTheDocument();
    expect(screen.getByTestId("private-voice-notice")).toBeInTheDocument();
    expect(screen.getByTestId("model-reply")).toBeInTheDocument();

    const user = userEvent.setup();
    await user.type(screen.getByRole("textbox", { name: /what is on your mind/i }), MESSAGE);
    await user.click(screen.getByTestId("send-button"));

    const badge = await screen.findByTestId("private-reply-badge");
    expect(badge).toHaveTextContent("Answered on this device");
    expect(badge).toHaveAttribute("data-stored", "false");
    expect(screen.getByTestId("assistant-bubble")).toHaveTextContent(/./);

    // Only the two static assets were ever requested, and the chat server — which
    // is right there, and would have answered — was never asked for anything.
    const urls = privateFetch.mock.calls.map((call) => String(call[0]));
    expect(urls).toHaveLength(2);
    expect(urls.every((url) => url.startsWith("/private/"))).toBe(true);
    expect(server.calls.some((call) => String(call.url).includes("/chat"))).toBe(false);
    // Not one recorded request carries the message text, in a URL or in a body.
    expect(
      server.calls.some((call) =>
        [String(call.url), JSON.stringify(call.body ?? {})].some((part) =>
          part.includes("cannot switch my brain off"),
        ),
      ),
    ).toBe(false);
  }, 20_000);
});
