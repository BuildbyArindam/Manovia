/**
 * The per-reply honesty badge.
 *
 * Small component, load-bearing claims: it must say which runtime wrote the reply,
 * whether the reply was stored, and — for the two paths where no model wrote
 * anything — that a model did not write it.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PrivateReplyBadge } from "./PrivateReplyBadge";
import { PRIVATE_COPY } from "./copy";
import type { ChatMetadata } from "@/features/chat/types";

function metadata(overrides: Partial<ChatMetadata> = {}): ChatMetadata {
  return {
    risk_level: "none",
    emotion: "sadness",
    response_type: "normal",
    resources: [],
    emergency: null,
    crisis: null,
    check_in: null,
    degraded: false,
    persisted: false,
    about_someone_else: false,
    locale: "en",
    region: "IN",
    ...overrides,
  };
}

describe("PrivateReplyBadge", () => {
  it("names the runtime that wrote the reply", () => {
    render(
      <PrivateReplyBadge metadata={metadata()} runtimeLabel="template writer (en, check-in)" />,
    );
    expect(screen.getByTestId("private-reply-badge")).toHaveTextContent(
      PRIVATE_COPY.answeredBy("template writer (en, check-in)"),
    );
  });

  it("falls back to the rules when nothing has answered yet", () => {
    render(<PrivateReplyBadge metadata={metadata()} />);
    expect(screen.getByTestId("private-reply-badge")).toHaveTextContent(
      PRIVATE_COPY.answeredBy("the on-device rules"),
    );
  });

  it("says when the turn was not stored", () => {
    render(<PrivateReplyBadge metadata={metadata({ persisted: false })} />);
    const badge = screen.getByTestId("private-reply-badge");
    expect(badge).toHaveAttribute("data-stored", "false");
    expect(badge).toHaveTextContent(PRIVATE_COPY.notStoredBadge);
    expect(badge).not.toHaveTextContent(PRIVATE_COPY.storedBadge);
  });

  it("says when the turn was encrypted and kept on this device", () => {
    render(<PrivateReplyBadge metadata={metadata({ persisted: true })} />);
    const badge = screen.getByTestId("private-reply-badge");
    expect(badge).toHaveAttribute("data-stored", "true");
    expect(badge).toHaveTextContent(PRIVATE_COPY.storedBadge);
  });

  it("says a crisis reply was not written by a model", () => {
    render(<PrivateReplyBadge metadata={metadata({ response_type: "crisis" })} />);
    expect(screen.getByTestId("private-reply-badge")).toHaveTextContent(PRIVATE_COPY.crisisNoModel);
  });

  it("says when the guard replaced a draft", () => {
    render(<PrivateReplyBadge metadata={metadata()} guardReplaced />);
    expect(screen.getByTestId("private-reply-badge")).toHaveTextContent(PRIVATE_COPY.guardReplaced);
  });

  it("carries the timings as data, not as copy", () => {
    const { container } = render(
      <PrivateReplyBadge
        metadata={metadata({ response_type: "crisis" })}
        runtimeLabel="deterministic crisis copy (no model)"
        timings={{ assessedInMs: 1, repliedInMs: 0, totalMs: 2, tokensPerSecond: null, tokens: null }}
      />,
    );
    const badge = screen.getByTestId("private-reply-badge");
    expect(badge).toHaveAttribute("data-total-ms", "2");
    expect(badge).toHaveAttribute("data-assessed-ms", "1");
    expect(badge).toHaveAttribute("data-crisis", "true");
    // The numbers are for whoever goes looking, never for the reader.
    expect(badge.textContent).not.toContain("2");
    expect(container.textContent).not.toContain("tokens");
  });

  it("stays quiet about the guard when the draft passed", () => {
    render(<PrivateReplyBadge metadata={metadata()} guardReplaced={false} />);
    expect(screen.getByTestId("private-reply-badge")).not.toHaveTextContent(
      PRIVATE_COPY.guardReplaced,
    );
  });
});
