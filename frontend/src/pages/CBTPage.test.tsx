import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { ApiClient, MemoryTokenStore } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";
import { CBTPage } from "./CBTPage";

function fake(interrupt = false) {
  const tokens = new MemoryTokenStore();
  tokens.write({
    accessToken: "test",
    refreshToken: "test",
    tokenType: "bearer",
    expiresAt: Date.now() + 3600000,
  });
  const saved: unknown[] = [];
  const records: unknown[] = [];
  const client = new ApiClient({
    tokens,
    fetchImpl: async (url, init) => {
      const path = new URL(url, "http://localhost").pathname;
      const body = init?.body ? (JSON.parse(String(init.body)) as { text?: string }) : null;
      let response: unknown;
      if (path.endsWith("/cbt/records") && init?.method === "POST") {
        saved.push(body);
        const record = { ...body, id: "record-1", created_at: "2026-10-11T12:00:00Z" };
        records.push(record);
        response = { interrupted: false, record };
      } else if (path.endsWith("/cbt/records")) response = records;
      else if (interrupt && body?.text?.includes("kill myself"))
        response = {
          interrupted: true,
          crisis: {
            level: "high",
            message: null,
            emergency: null,
            resources: [
              {
                id: "help",
                name: "Help",
                region: "US",
                hours: "24/7",
                audience: null,
                type: "phone",
                number: "988",
                tel_href: "tel:988",
                sms_href: null,
                text_keyword: null,
                url: null,
                priority: 1,
                needs_verification: false,
              },
            ],
          },
          candidates: [],
          question: "",
          source: "none",
        };
      else if (path.endsWith("/cbt/traps"))
        response = {
          interrupted: false,
          crisis: null,
          candidates: [
            { id: "mind-reading", explanation: "Guessing what others think might be happening." },
          ],
          question: "What might you say to a friend?",
          source: "llm",
        };
      else response = { interrupted: false, crisis: null };
      return new Response(JSON.stringify(response), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    },
  });
  return { client, saved };
}

describe("CBT wizard", () => {
  it("completes all seven steps and displays before/after", async () => {
    const { client, saved } = fake();
    renderWithProviders(<CBTPage client={client} />);
    const user = userEvent.setup();
    screen.getByRole("button", { name: "Start a reflection" }).focus();
    await user.keyboard("{Enter}");
    for (const text of ["My exam is tomorrow", "Everyone will think I'm stupid"]) {
      await user.type(screen.getByRole("textbox"), text);
      await user.click(screen.getByRole("button", { name: "Next" }));
    }
    expect(await screen.findByText(/Guessing what others think/)).toBeVisible();
    await user.clear(screen.getByLabelText("Intensity before (0–100)"));
    await user.type(screen.getByLabelText("Intensity before (0–100)"), "85");
    await user.click(screen.getByRole("button", { name: "Next" }));
    for (const text of [
      "I missed one question",
      "I studied",
      "I prepared even if I don't know everything",
    ]) {
      await user.type(screen.getByRole("textbox"), text);
      await user.click(screen.getByRole("button", { name: "Next" }));
    }
    await user.clear(screen.getByLabelText("Intensity after (0–100)"));
    await user.type(screen.getByLabelText("Intensity after (0–100)"), "45");
    await user.click(screen.getByRole("checkbox", { name: "looking at evidence" }));
    await user.click(screen.getByRole("button", { name: "Save reflection" }));
    await waitFor(() => expect(saved).toHaveLength(1));
    expect(await screen.findByText(/before 85\/100 → after 45\/100/)).toBeVisible();
    expect(screen.getByText(/What helped: looking at evidence/)).toBeVisible();
  });

  it("interrupts on step 2, offers continue later and never proceeds", async () => {
    const { client, saved } = fake(true);
    renderWithProviders(<CBTPage client={client} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Start a reflection" }));
    await user.type(screen.getByRole("textbox"), "My exam");
    await user.click(screen.getByRole("button", { name: "Next" }));
    await user.type(screen.getByRole("textbox"), "I want to kill myself");
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("Let's pause here")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Next" })).not.toBeInTheDocument();
    expect(saved).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Continue later" }));
    expect(screen.getByRole("button", { name: "Resume reflection" })).toBeVisible();
  });
});
