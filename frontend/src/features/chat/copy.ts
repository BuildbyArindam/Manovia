/**
 * Every user-facing string the chat surface can show.
 *
 * Kept in one module for two reasons: the tone rules in AGENTS.md ("warm,
 * plain, short, non-judgemental, no toxic positivity") are easier to review in
 * one place than spread over nine components, and the tests assert on these
 * constants rather than on literals that a copy edit would silently break.
 *
 * The pre-written crisis copy is **not** here — it arrives already written and
 * already localised from the backend, and the UI never rewords it.
 */

export const CHAT_COPY = {
  heading: "Chat",
  lede: "A quiet conversation about how you are doing. Take your time; nothing is timed.",

  /** The permanent line under the conversation. Always visible, never hidden. */
  companionNotice: "AI companion — not a therapist.",
  companionNoticeAction: "Need help now?",

  composerLabel: "What is on your mind?",
  composerPlaceholder: "Write as much or as little as you like.",
  send: "Send",
  sending: "Sending…",

  typingIndicator: "Manovia is thinking",
  /** Screen-reader text while a reply is streaming. */
  typingAnnouncement: "Manovia is replying…",

  youSaid: "You said",
  manoviaSaid: "Manovia said",

  /** Shown above a reply that stands in for a model answer that failed. */
  degradedNotice:
    "Manovia could not reach its language model just now, so this is a short written reply instead.",

  newChat: "Start a new chat",
  clearConversation: "Clear conversation",
  clearConfirm:
    "Clear this conversation from your screen? Nothing is deleted from Manovia's storage — use Start a new chat for a fresh session.",
  saveConversation: "Save this conversation",
  saveConversationHint:
    "Off: this chat stays in memory only and is forgotten when you leave. On: it is stored, encrypted, on your account.",
  languageLabel: "Language",

  jumpToLatest: "Jump to latest",

  /** Shown under a reply whose stream was cut off. */
  incompleteReply: "This reply was cut off before it finished.",

  /** Suggested starters. Generic on purpose: they must not presume a problem. */
  starters: ["I feel anxious", "I can't sleep", "I had a bad day"] as readonly string[],

  /** Announced once a reply finishes, so a screen reader reads the answer. */
  replyAnnouncement: (text: string): string => `Manovia replied: ${text}`,
  crisisAnnouncement:
    "Manovia has shown help lines because this message may be about safety. " +
    "They are free, confidential, and staffed by people.",
} as const;

/**
 * Plain-language failures.
 *
 * The backend already writes curated messages for the cases it can name
 * (`message_empty`, `message_too_long`, `session_ended`, …) and those win — a
 * second, client-side version of the same sentence would drift. These are the
 * cases the client knows better than the server: no network, an aborted
 * request, and a stream that stopped half way.
 */
export const CHAT_ERRORS = {
  offline: {
    title: "You appear to be offline",
    body: "Manovia could not reach its server. Your message has not been sent — check your connection and try again.",
    action: "Try again",
  },
  network: {
    title: "The connection dropped",
    body: "Manovia could not reach its server. Nothing was sent. Please try again.",
    action: "Try again",
  },
  streamInterrupted: {
    title: "The reply was cut off",
    body: "The connection dropped while Manovia was answering, so this reply may be incomplete. Your message was sent; asking again will get a full answer.",
    action: "Ask again",
  },
  streamError: {
    title: "Manovia could not finish that reply",
    body: "Something went wrong part way through the answer. Please try again.",
    action: "Try again",
  },
  consent: {
    title: "One more agreement needed",
    body: "Manovia needs your agreement that it is an AI before it can reply. You can give it in Settings.",
    action: "Open Settings",
  },
  rateLimited: {
    title: "Let's take a breath",
    body: "That was a lot of messages in a short time. Please wait a moment, then send again.",
    action: "Try again",
  },
  session: {
    title: "This conversation has ended",
    body: "Start a new chat to keep talking.",
    action: "Start a new chat",
  },
  unknown: {
    title: "Something went wrong",
    body: "Manovia could not send that. Please try again.",
    action: "Try again",
  },
} as const;

/** The message length ceiling. Mirrors the backend's `CHAT_MAX_MESSAGE_CHARS`. */
export const MAX_MESSAGE_CHARS = 4000;
