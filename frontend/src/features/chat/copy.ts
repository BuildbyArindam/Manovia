/**
 * Every user-facing string the chat surface can show.
 *
 * Kept in one module for two reasons: the tone rules in AGENTS.md ("warm,
 * plain, short, non-judgemental, no toxic positivity") are easier to review in
 * one place than spread over nine components, and the tests assert on these
 * constants rather than on literals that a copy edit would silently break.
 *
 * Day 23: the words themselves now live in `src/lib/i18n/locales/*.json`
 * (English, Hindi, Bengali). The accessors below read through i18next at call
 * time, so a language switch re-renders this surface in the new language with
 * no code change here. Tests still read the same properties and see the
 * English tree, because the app starts in English.
 *
 * The pre-written crisis copy is **not** here — it arrives already written and
 * already localised from the backend, and the UI never rewords it.
 */

import { t } from "@/lib/i18n/i18n";

export const CHAT_COPY = {
  get heading() {
    return t("chat.heading");
  },
  get lede() {
    return t("chat.lede");
  },

  /** The permanent line under the conversation. Always visible, never hidden. */
  get companionNotice() {
    return t("chat.companionNotice");
  },
  get companionNoticeAction() {
    return t("chat.companionNoticeAction");
  },

  get composerLabel() {
    return t("chat.composerLabel");
  },
  get composerPlaceholder() {
    return t("chat.composerPlaceholder");
  },
  get send() {
    return t("chat.send");
  },
  get sending() {
    return t("chat.sending");
  },

  get typingIndicator() {
    return t("chat.typingIndicator");
  },
  /** Screen-reader text while a reply is streaming. */
  get typingAnnouncement() {
    return t("chat.typingAnnouncement");
  },

  get youSaid() {
    return t("chat.youSaid");
  },
  get manoviaSaid() {
    return t("chat.manoviaSaid");
  },

  /** Shown above a reply that stands in for a model answer that failed. */
  get degradedNotice() {
    return t("chat.degradedNotice");
  },

  get newChat() {
    return t("chat.newChat");
  },
  get clearConversation() {
    return t("chat.clearConversation");
  },
  get clearConfirm() {
    return t("chat.clearConfirm");
  },
  get saveConversation() {
    return t("chat.saveConversation");
  },
  get saveConversationHint() {
    return t("chat.saveConversationHint");
  },
  get languageLabel() {
    return t("chat.languageLabel");
  },

  get jumpToLatest() {
    return t("chat.jumpToLatest");
  },

  /** Shown under a reply whose stream was cut off. */
  get incompleteReply() {
    return t("chat.incompleteReply");
  },

  /** Suggested starters. Generic on purpose: they must not presume a problem. */
  get starters(): readonly string[] {
    return [t("chat.starter1"), t("chat.starter2"), t("chat.starter3")];
  },

  /** Announced once a reply finishes, so a screen reader reads the answer. */
  replyAnnouncement: (text: string): string => t("chat.replyAnnouncement", { text }),
  get crisisAnnouncement() {
    return t("chat.crisisAnnouncement");
  },
};

/**
 * Plain-language failures.
 *
 * The backend already writes curated messages for the cases it can name
 * (`message_empty`, `message_too_long`, `session_ended`, …) and those win — a
 * second, client-side version of the same sentence would drift. These are the
 * cases the client knows better than the server: no network, an aborted
 * request, and a stream that stopped half way.
 */
export interface ChatErrorCopy {
  title: string;
  body: string;
  action: string;
}

function chatError(kind: string): ChatErrorCopy {
  return {
    title: t(`chat.errors.${kind}Title`),
    body: t(`chat.errors.${kind}Body`),
    action: t(`chat.errors.${kind}Action`),
  };
}

export const CHAT_ERRORS = {
  get offline(): ChatErrorCopy {
    return chatError("offline");
  },
  get network(): ChatErrorCopy {
    return chatError("network");
  },
  get streamInterrupted(): ChatErrorCopy {
    return chatError("streamInterrupted");
  },
  get streamError(): ChatErrorCopy {
    return chatError("streamError");
  },
  get consent(): ChatErrorCopy {
    return chatError("consent");
  },
  get rateLimited(): ChatErrorCopy {
    return chatError("rateLimited");
  },
  get session(): ChatErrorCopy {
    return chatError("session");
  },
  get unknown(): ChatErrorCopy {
    return chatError("unknown");
  },
};

/** The message length ceiling. Mirrors the backend's `CHAT_MAX_MESSAGE_CHARS`. */
export const MAX_MESSAGE_CHARS = 4000;
