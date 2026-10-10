/**
 * The chat surface, tested through what a person does to it.
 *
 * Only `fetch` is faked (see `test/chatTransport`): the controller, the SSE
 * parser, the reducer and the components are all the real ones. The five checks
 * the Day 12 brief names are here explicitly — streaming render, crisis card,
 * toggle behaviour, keyboard-only send, and the aria-live announcement — plus
 * the states that decide whether the thing is usable: offline, cut-off reply,
 * retry, the character limit, the mood hint and axe.
 */

import { axe } from "jest-axe";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiClient, MemoryTokenStore } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";
import {
  createFakeChatServer,
  finalFrame,
  normalMetadata,
  tokenFrame,
  type FakeChatServerOptions,
} from "@/test/chatTransport";
import { crisisMessage, TEST_EMERGENCY, TEST_RESOURCES } from "@/test/crisisFixtures";

import { ChatView } from "./ChatView";

function renderChat(options: FakeChatServerOptions = {}) {
  const server = createFakeChatServer(options);
  const tokens = new MemoryTokenStore();
  const client = new ApiClient({ fetchImpl: server.fetchImpl, tokens, now: () => 0 });
  client.applySession({ access_token: "access-1", refresh_token: "refresh-1", expires_in: 900 });

  const view = renderWithProviders(<ChatView client={client} />);
  return { ...view, server, client };
}

/** Metadata for a HIGH-risk turn: the deterministic reply, no model text. */
function crisisMetadata(): Record<string, unknown> {
  return normalMetadata({
    risk_level: "high",
    emotion: null,
    response_type: "crisis",
    resources: TEST_RESOURCES.map((resource) => ({ ...resource })),
    emergency: { ...TEST_EMERGENCY },
    crisis: crisisMessage(),
  });
}

/**
 * A stream whose later chunks wait for the test.
 *
 * Without a gate, "watch the reply arrive in pieces" is a race against
 * `waitFor`'s polling interval: on a fast machine the whole stream can land
 * before the first assertion runs. Here the test decides when the rest arrives.
 */
function gatedStream(firstChunk: string, restChunks: readonly string[]) {
  let releaseGate: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => {
    releaseGate = resolve;
  });
  const encoder = new TextEncoder();

  return {
    release: (): void => {
      releaseGate();
    },
    options: {
      hangStream: true,
      streamChunks: [
        firstChunk,
        (controller: ReadableStreamDefaultController<Uint8Array>) => {
          void gate.then(() => {
            for (const chunk of restChunks) {
              controller.enqueue(encoder.encode(chunk));
            }
            controller.close();
          });
        },
      ],
    } as const,
  };
}

async function type(user: ReturnType<typeof userEvent.setup>, text: string): Promise<void> {
  const box = screen.getByRole("textbox", { name: /what is on your mind/i });
  await user.type(box, text);
}

describe("ChatView — streaming", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("renders the reply as its tokens arrive", async () => {
    const user = userEvent.setup();
    const { release, options } = gatedStream(tokenFrame("That sounds heavy. "), [
      tokenFrame("Thank you for saying it."),
      finalFrame({
        reply: "That sounds heavy. Thank you for saying it.",
        metadata: normalMetadata(),
      }),
    ]);
    renderChat(options);

    await type(user, "I had a bad day");
    await user.click(screen.getByTestId("send-button"));

    // The first token is on screen before the reply exists.
    await waitFor(() => {
      expect(screen.getByText("That sounds heavy.")).toBeInTheDocument();
    });
    // While it is unfinished, the visible text is hidden from AT and the live
    // region says one calm sentence instead of every token.
    expect(screen.getByText("Manovia is replying…")).toBeInTheDocument();
    expect(screen.queryByTestId("assistant-text")).not.toBeInTheDocument();

    release();

    await waitFor(() => {
      expect(screen.getByTestId("assistant-text")).toHaveTextContent(
        "That sounds heavy. Thank you for saying it.",
      );
    });
  });

  it("shows a typing indicator before the first token", async () => {
    const user = userEvent.setup();
    renderChat({ streamChunks: [], hangStream: true });

    await type(user, "hello");
    await user.click(screen.getByTestId("send-button"));

    expect(await screen.findByTestId("typing-indicator")).toBeInTheDocument();
    expect(screen.getByTestId("send-button")).toHaveTextContent("Sending…");
  });

  it("keeps a partial reply and says it was cut off when the stream dies", async () => {
    const user = userEvent.setup();
    // Tokens, then the connection closes with no `final` event.
    renderChat({ streamChunks: [tokenFrame("That sounds ")] });

    await type(user, "I had a bad day");
    await user.click(screen.getByTestId("send-button"));

    expect(await screen.findByTestId("chat-error")).toHaveTextContent("The reply was cut off");
    expect(screen.getByText("This reply was cut off before it finished.")).toBeInTheDocument();
    expect(screen.getByTestId("chat-error-action")).toHaveTextContent("Ask again");
  });

  it("replaces the streamed text when the backend says it was replaced", async () => {
    const user = userEvent.setup();
    renderChat({
      streamChunks: [
        tokenFrame("a half written sen"),
        finalFrame({
          reply: "The checked, complete answer.",
          replaced: true,
          metadata: normalMetadata(),
        }),
      ],
    });

    await type(user, "hi");
    await user.click(screen.getByTestId("send-button"));

    await waitFor(() => {
      expect(screen.getByText("The checked, complete answer.")).toBeInTheDocument();
    });
    expect(screen.queryByText("a half written sen")).not.toBeInTheDocument();
  });
});

describe("ChatView — crisis", () => {
  it("renders the CrisisCard under the message, with tel: and sms: actions", async () => {
    const user = userEvent.setup();
    renderChat({
      streamChunks: [
        tokenFrame(crisisMessage().body[0] ?? ""),
        finalFrame({ reply: crisisMessage().body[0] ?? "", metadata: crisisMetadata() }),
      ],
    });

    await type(user, "I want to kill myself");
    await user.click(screen.getByTestId("send-button"));

    const card = await screen.findByTestId("chat-crisis-card");
    // The pre-written copy, verbatim — not a model sentence.
    expect(card).toHaveTextContent(crisisMessage().title);
    expect(within(card).getByRole("link", { name: /call 911/i })).toHaveAttribute(
      "href",
      "tel:911",
    );
    expect(within(card).getByRole("link", { name: /text 55501/i })).toHaveAttribute(
      "href",
      "sms:55501",
    );
  });

  it("softens the page: no starter chips, and the softened flag is set", async () => {
    const user = userEvent.setup();
    renderChat({
      streamChunks: [
        finalFrame({ reply: crisisMessage().body[0] ?? "", metadata: crisisMetadata() }),
      ],
    });

    expect(screen.getByTestId("starter-chips")).toBeInTheDocument();

    await type(user, "I want to kill myself");
    await user.click(screen.getByTestId("send-button"));

    await screen.findByTestId("chat-crisis-card");
    expect(screen.getByTestId("chat-view")).toHaveAttribute("data-softened", "true");
    expect(screen.queryByTestId("starter-chips")).not.toBeInTheDocument();
  });

  it("does not soften on an ordinary reply", async () => {
    const user = userEvent.setup();
    renderChat({
      streamChunks: [finalFrame({ reply: "I hear you.", metadata: normalMetadata() })],
    });

    await type(user, "hello");
    await user.click(screen.getByTestId("send-button"));

    await waitFor(() => {
      expect(screen.getByText("I hear you.")).toBeInTheDocument();
    });
    expect(screen.getByTestId("chat-view")).toHaveAttribute("data-softened", "false");
    expect(screen.queryByTestId("chat-crisis-card")).not.toBeInTheDocument();
  });

  it("shows an honest note when the reply is a stand-in for a failed model call", async () => {
    const user = userEvent.setup();
    renderChat({
      streamChunks: [
        finalFrame({
          reply: "I'm having trouble reaching the model.",
          metadata: normalMetadata({ response_type: "fallback", degraded: true }),
        }),
      ],
    });

    await type(user, "hello");
    await user.click(screen.getByTestId("send-button"));

    expect(await screen.findByText(/could not reach its language model/i)).toBeInTheDocument();
  });
});

describe("ChatView — keyboard", () => {
  it("sends on Enter and keeps focus in the box", async () => {
    const user = userEvent.setup();
    const { server } = renderChat({
      streamChunks: [finalFrame({ reply: "Thanks.", metadata: normalMetadata() })],
    });

    await type(user, "I feel anxious");
    await user.keyboard("{Enter}");

    await waitFor(() => {
      expect(screen.getByText("Thanks.")).toBeInTheDocument();
    });
    expect(server.calls.at(-1)?.body).toMatchObject({ message: "I feel anxious" });
    expect(screen.getByRole("textbox", { name: /what is on your mind/i })).toHaveFocus();
  });

  it("inserts a newline on Shift+Enter instead of sending", async () => {
    const user = userEvent.setup();
    const { server } = renderChat({ streamChunks: [] });

    const box = screen.getByRole("textbox", { name: /what is on your mind/i });
    await user.type(box, "line one{Shift>}{Enter}{/Shift}line two");

    expect(box).toHaveValue("line one\nline two");
    expect(server.calls.filter((call) => call.url.includes("/stream"))).toHaveLength(0);
  });

  it("does not send on Enter while an IME is composing", async () => {
    const { server } = renderChat({ streamChunks: [] });
    const box = screen.getByRole("textbox", { name: /what is on your mind/i });

    box.focus();
    // What a Devanagari IME emits when Enter commits a candidate.
    const event = new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true });
    Object.defineProperty(event, "isComposing", { value: true });
    box.dispatchEvent(event);

    expect(screen.getByTestId("send-button")).toBeDisabled();
    expect(server.calls.filter((call) => call.url.includes("/stream"))).toHaveLength(0);
  });

  it("sends a starter chip with the keyboard", async () => {
    const user = userEvent.setup();
    const { server } = renderChat({
      streamChunks: [finalFrame({ reply: "Tell me more.", metadata: normalMetadata() })],
    });

    const chip = screen.getAllByTestId("starter-chip")[0];
    expect(chip).toHaveTextContent("I feel anxious");
    chip?.focus();
    await user.keyboard("{Enter}");

    await waitFor(() => {
      expect(server.calls.at(-1)?.body).toMatchObject({ message: "I feel anxious" });
    });
  });

  it("will not send an empty message", async () => {
    const user = userEvent.setup();
    renderChat({ streamChunks: [] });

    screen.getByRole("textbox", { name: /what is on your mind/i }).focus();
    await user.keyboard("{Enter}");

    expect(screen.getByTestId("send-button")).toBeDisabled();
  });
});

describe("ChatView — screen readers", () => {
  it("labels every bubble with its speaker", async () => {
    const user = userEvent.setup();
    renderChat({
      streamChunks: [finalFrame({ reply: "I hear you.", metadata: normalMetadata() })],
    });

    await type(user, "hello there");
    await user.click(screen.getByTestId("send-button"));
    await waitFor(() => {
      expect(screen.getByText("I hear you.")).toBeInTheDocument();
    });

    expect(screen.getByText("You said:")).toBeInTheDocument();
    expect(screen.getByText("Manovia said:")).toBeInTheDocument();
  });

  it("marks the latest assistant message as a polite live region", async () => {
    const user = userEvent.setup();
    const { release, options } = gatedStream(tokenFrame("I hear "), [
      finalFrame({ reply: "I hear you.", metadata: normalMetadata() }),
    ]);
    renderChat(options);

    await type(user, "hello");
    await user.click(screen.getByTestId("send-button"));

    // During the stream the region announces one sentence, not every token.
    const liveRegion = await waitFor(() => {
      const region = screen.getByText("Manovia is replying…").closest("li") as HTMLElement;
      expect(region).toHaveAttribute("aria-live", "polite");
      expect(region).toHaveAttribute("aria-atomic", "true");
      return region;
    });
    expect(liveRegion).toHaveAttribute("aria-live", "polite");

    // When the reply lands, the region's accessible content becomes the reply.
    release();
    const finished = await waitFor(() => {
      const text = screen.getByTestId("assistant-text");
      expect(text).toHaveTextContent("I hear you.");
      return text;
    });
    expect(finished.closest("li")).toHaveAttribute("aria-live", "polite");
    expect(finished.closest("li")).not.toHaveAttribute("aria-hidden");
    expect(
      within(finished.closest("li") as HTMLElement).queryByText("Manovia is replying…"),
    ).toBeNull();

    // Only one live region: the user's own bubble is not one.
    expect(screen.getByTestId("user-bubble").closest("li")).not.toHaveAttribute("aria-live");
  });

  it("has no axe violations with a message list present", async () => {
    const user = userEvent.setup();
    const { container } = renderChat({
      streamChunks: [finalFrame({ reply: "I hear you.", metadata: normalMetadata() })],
    });

    await type(user, "hello");
    await user.click(screen.getByTestId("send-button"));
    await waitFor(() => {
      expect(screen.getByText("I hear you.")).toBeInTheDocument();
    });

    expect(await axe(container)).toHaveNoViolations();
  });

  it("has no axe violations with a crisis card on screen", async () => {
    const user = userEvent.setup();
    const { container } = renderChat({
      streamChunks: [
        finalFrame({ reply: crisisMessage().body[0] ?? "", metadata: crisisMetadata() }),
      ],
    });

    await type(user, "I want to kill myself");
    await user.click(screen.getByTestId("send-button"));
    await screen.findByTestId("chat-crisis-card");

    expect(await axe(container)).toHaveNoViolations();
  });
});

describe("ChatView — controls", () => {
  it("toggles Save this conversation, off by default", async () => {
    const user = userEvent.setup();
    const { server } = renderChat({ sessionBody: { persistent: true } });
    const toggle = screen.getByTestId("save-toggle");

    expect(toggle).not.toBeChecked();

    await user.click(toggle);
    expect(toggle).toBeChecked();

    await type(user, "hello");
    await user.click(screen.getByTestId("send-button"));
    await waitFor(() => {
      expect(server.calls[0]?.body).toMatchObject({ save_history: true });
    });
  });

  it("asks before clearing, and clears the screen only", async () => {
    const user = userEvent.setup();
    renderChat({
      streamChunks: [finalFrame({ reply: "I hear you.", metadata: normalMetadata() })],
    });

    await type(user, "hello");
    await user.click(screen.getByTestId("send-button"));
    await waitFor(() => {
      expect(screen.getByText("I hear you.")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("clear-button"));
    // Still there: clearing is confirmed, not one click.
    expect(screen.getByText("I hear you.")).toBeInTheDocument();

    await user.click(screen.getByTestId("confirm-clear-button"));
    expect(screen.queryByText("I hear you.")).not.toBeInTheDocument();
    expect(screen.getByTestId("chat-empty")).toBeInTheDocument();
  });

  it("opens a fresh session on Start a new chat", async () => {
    const user = userEvent.setup();
    const { server } = renderChat({
      streamChunks: [finalFrame({ reply: "I hear you.", metadata: normalMetadata() })],
    });

    await type(user, "hello");
    await user.click(screen.getByTestId("send-button"));
    await waitFor(() => {
      expect(screen.getByText("I hear you.")).toBeInTheDocument();
    });
    expect(server.calls.filter((call) => call.url.endsWith("/chat/sessions"))).toHaveLength(1);

    await user.click(screen.getByTestId("new-chat-button"));
    expect(screen.getByTestId("chat-empty")).toBeInTheDocument();

    await type(user, "and now?");
    await user.click(screen.getByTestId("send-button"));
    await waitFor(() => {
      expect(server.calls.filter((call) => call.url.endsWith("/chat/sessions"))).toHaveLength(2);
    });
  });

  it("sends the chosen language with the message", async () => {
    const user = userEvent.setup();
    const { server } = renderChat({
      streamChunks: [finalFrame({ reply: "ठीक है।", metadata: normalMetadata({ locale: "hi" }) })],
    });

    await user.selectOptions(screen.getByTestId("language-select"), "hi");
    expect(screen.getByTestId("language-select")).toHaveValue("hi");

    await type(user, "मुझे नींद नहीं आती");
    await user.click(screen.getByTestId("send-button"));

    await waitFor(() => {
      expect(server.calls.at(-1)?.body).toMatchObject({ locale: "hi" });
    });
  });

  it("counts characters and refuses to send past the limit", async () => {
    const user = userEvent.setup();
    renderChat({ streamChunks: [] });
    const box = screen.getByRole("textbox", { name: /what is on your mind/i });

    await type(user, "twenty characters long");
    expect(screen.getByTestId("char-counter")).toHaveTextContent("22 / 4000");

    // Past the limit the browser would stop accepting keys; the UI must not
    // offer to send what it cannot.
    await user.clear(box);
    Object.defineProperty(box, "value", { value: "x".repeat(4001), configurable: true });
    box.dispatchEvent(new Event("input", { bubbles: true }));

    await waitFor(() => {
      expect(screen.getByTestId("char-counter")).toHaveTextContent("4001 / 4000");
    });
    expect(screen.getByTestId("send-button")).toBeDisabled();
  });

  it("keeps the AI companion line always, and opens the help dialog from it", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              region: null,
              requested_region: null,
              fallback_used: false,
              version: 2,
              last_verified: "2026-10-09",
              source: "synthetic",
              disclaimer: "synthetic",
              known_regions: ["US"],
              emergency: TEST_EMERGENCY,
              resources: [...TEST_RESOURCES],
              needs_verification: [],
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          ),
      ),
    );
    renderChat({ streamChunks: [] });

    expect(screen.getByTestId("companion-notice")).toHaveTextContent(
      "AI companion — not a therapist.",
    );

    await user.click(screen.getByTestId("companion-notice-help"));
    expect(await screen.findByTestId("crisis-modal")).toBeInTheDocument();
  });
});

describe("ChatView — mood hint", () => {
  it("is hidden by default", async () => {
    const user = userEvent.setup();
    renderChat({
      streamChunks: [
        finalFrame({ reply: "I hear you.", metadata: normalMetadata({ emotion: "sadness" }) }),
      ],
    });

    await type(user, "I feel low");
    await user.click(screen.getByTestId("send-button"));
    await waitFor(() => {
      expect(screen.getByText("I hear you.")).toBeInTheDocument();
    });

    expect(screen.queryByTestId("mood-hint")).not.toBeInTheDocument();
  });

  it("appears when the setting is switched on", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem("manovia.chat.showMoodHint.v1", "true");
    renderChat({
      streamChunks: [
        finalFrame({ reply: "I hear you.", metadata: normalMetadata({ emotion: "sadness" }) }),
      ],
    });

    await type(user, "I feel low");
    await user.click(screen.getByTestId("send-button"));

    expect(await screen.findByTestId("mood-hint")).toHaveTextContent("Reads as low");
  });

  it("says nothing about mood on a crisis turn", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem("manovia.chat.showMoodHint.v1", "true");
    renderChat({
      streamChunks: [
        finalFrame({ reply: crisisMessage().body[0] ?? "", metadata: crisisMetadata() }),
      ],
    });

    await type(user, "I want to kill myself");
    await user.click(screen.getByTestId("send-button"));
    await screen.findByTestId("chat-crisis-card");

    expect(screen.queryByTestId("mood-hint")).not.toBeInTheDocument();
  });
});

describe("ChatView — failures", () => {
  it("says you are offline and resends on Try again", async () => {
    const user = userEvent.setup();
    const server = createFakeChatServer({
      streamChunks: [finalFrame({ reply: "I hear you.", metadata: normalMetadata() })],
    });
    const tokens = new MemoryTokenStore();
    const client = new ApiClient({ fetchImpl: server.fetchImpl, tokens, now: () => 0 });
    client.applySession({ access_token: "a", refresh_token: "r", expires_in: 900 });

    const offlineFlag = { value: false };
    const onlineSpy = vi
      .spyOn(navigator, "onLine", "get")
      .mockImplementation(() => offlineFlag.value);

    renderWithProviders(<ChatView client={client} />);

    expect(await screen.findByTestId("offline-notice")).toHaveTextContent(
      "You appear to be offline",
    );

    await type(user, "hello");
    await user.click(screen.getByTestId("send-button"));
    expect(await screen.findByTestId("chat-error")).toHaveTextContent("You appear to be offline");
    expect(server.calls.filter((call) => call.url.includes("/stream"))).toHaveLength(0);

    // The connection comes back: the same message goes through.
    offlineFlag.value = true;
    window.dispatchEvent(new Event("online"));
    await waitFor(() => {
      expect(screen.queryByTestId("offline-notice")).not.toBeInTheDocument();
    });

    await user.click(screen.getByTestId("chat-error-action"));
    await waitFor(() => {
      expect(screen.getByText("I hear you.")).toBeInTheDocument();
    });
    expect(server.calls.at(-1)?.body).toMatchObject({ message: "hello" });

    onlineSpy.mockRestore();
  });

  it("turns a failed send into plain words with a retry", async () => {
    const user = userEvent.setup();
    let streamAttempts = 0;
    const good = createFakeChatServer({
      streamChunks: [finalFrame({ reply: "I hear you.", metadata: normalMetadata() })],
    });
    const client = (() => {
      const tokens = new MemoryTokenStore();
      const apiClient = new ApiClient({
        fetchImpl: async (url, init) => {
          if (String(url).includes("/stream")) {
            streamAttempts += 1;
            if (streamAttempts === 1) {
              // The first send never gets a response at all.
              throw new TypeError("Failed to fetch");
            }
          }
          return good.fetchImpl(url, init);
        },
        tokens,
        now: () => 0,
      });
      apiClient.applySession({ access_token: "a", refresh_token: "r", expires_in: 900 });
      return apiClient;
    })();

    renderWithProviders(<ChatView client={client} />);

    await type(user, "hello");
    await user.click(screen.getByTestId("send-button"));

    expect(await screen.findByTestId("chat-error")).toHaveTextContent("The connection dropped");

    await user.click(screen.getByTestId("chat-error-action"));
    await waitFor(() => {
      expect(screen.getByText("I hear you.")).toBeInTheDocument();
    });
  });

  it("explains a missing consent instead of failing silently", async () => {
    const user = userEvent.setup();
    renderChat({
      sessionStatus: 403,
      sessionError: { code: "consent_required", message: "Consent required: ai_disclosure" },
    });

    await type(user, "hello");
    await user.click(screen.getByTestId("send-button"));

    const banner = await screen.findByTestId("chat-error");
    expect(banner).toHaveAttribute("data-failure-kind", "consent");
    expect(banner).toHaveTextContent("One more agreement needed");
    expect(screen.getByTestId("chat-error-action")).toHaveTextContent("Open Settings");
  });
});

describe("ChatView — auto-scroll", () => {
  it("stops following when the reader scrolls up, and offers a way back", async () => {
    const user = userEvent.setup();
    renderChat({
      streamChunks: [finalFrame({ reply: "I hear you.", metadata: normalMetadata() })],
    });

    const region = screen.getByTestId("chat-scroll-region");
    // Pretend there is more conversation than the box can show, and that the
    // reader has scrolled to the top of it.
    Object.defineProperty(region, "scrollHeight", { value: 1000, configurable: true });
    Object.defineProperty(region, "clientHeight", { value: 300, configurable: true });
    Object.defineProperty(region, "scrollTop", { value: 0, configurable: true, writable: true });
    region.dispatchEvent(new Event("scroll"));

    await type(user, "hello");
    await user.click(screen.getByTestId("send-button"));

    const jump = await screen.findByTestId("jump-to-latest");
    await user.click(jump);

    await waitFor(() => {
      expect(region.scrollTop).toBe(1000);
    });
    expect(screen.queryByTestId("jump-to-latest")).not.toBeInTheDocument();
  });
});
