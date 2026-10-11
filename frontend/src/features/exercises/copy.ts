/** Reviewable interface copy. Exercise instructions live in the backend YAML. */
export const EXERCISE_COPY = {
  heading: "Exercises",
  lede: "One short practice, at your pace. You can pause or stop at any time.",
  framing: "Self-help, not treatment. Instructions are currently in English. No sound is needed.",
  offline:
    "Once loaded, the exercise runs in this tab without a connection. Reloading offline is not supported.",
  suggestions: "Suggestions, if you want them",
  suggestionsNote:
    "Choose how you feel to see rule-based suggestions. This is not a diagnosis, and your choice is not saved.",
  why: "Why this?",
  loading: "Loading exercises…",
  unavailable: "Exercises could not be loaded. Please reconnect and try again.",
  historyHeading: "Exercise history",
  historyNote:
    "These are self-reported completions. Before-and-after ratings show an association, not proof of what caused a change.",
  historyEmpty: "No saved completions yet. There is no schedule to keep up with.",
  historyError: "History could not be loaded. You can still run a loaded exercise without it.",
  before: "How do you feel before? (optional)",
  after: "How do you feel afterwards? (optional)",
  ratingHint: "1 is very low; 5 is very good. You may leave this unanswered.",
  clearRating: "Leave this rating unanswered",
  beforeBegin: "Before you begin",
  evidence: "Notes and evidence",
  start: "Start exercise",
  pause: "Pause",
  resume: "Resume",
  stop: "Stop exercise",
  stopNote: "Stopping early does not save a completion. Your comfort matters more than the timer.",
  paused: "Paused. Resume when you want to, or stop here.",
  backgroundPause:
    "Paused while this page was away or its timer was delayed. Resume when you are ready.",
  textOnly: "Use a static countdown (no animation)",
  reducedMotion: "Your device requests reduced motion, so the animated guide is off.",
  haptics: "Use a brief vibration at each step (optional)",
  hapticsUnavailable:
    "Haptic cues are not available in this browser. All instructions are visible text.",
  hapticsNote: "Vibration is off by default and may not work on every device.",
  finished: "Exercise finished",
  finishedNote: "Notice how you feel; no particular result is expected. Ratings are optional.",
  save: "Save completion",
  saving: "Saving…",
  saved: "Completion saved.",
  retrySave: "Retry saving",
  unsaved:
    "Not saved. Your completion and ratings are only in this tab. Reconnect and retry; closing or leaving this tab loses unsaved results.",
  saveNote:
    "Saving sends only the exercise ID and optional ratings. Your thoughts and planner choice are not sent.",
  discard: "Finish without saving",
  discardFailed: "Discard unsaved completion",
  back: "Back to exercises",
  chooseAction: "Choose one small action (optional)",
} as const;

export const CUE_LABELS = {
  instruction: "Follow the text at your own pace",
  inhale: "Breathe in gently",
  "hold-in": "Pause only if comfortable",
  exhale: "Breathe out gently",
  "hold-out": "Pause only if comfortable",
} as const;

export const EMOTION_OPTIONS = [
  ["anxiety", "Anxiety"],
  ["fear", "Fear"],
  ["sadness", "Sadness"],
  ["loneliness", "Loneliness"],
  ["anger", "Anger"],
  ["shame", "Shame"],
  ["calm", "Calm"],
  ["joy", "Joy"],
  ["neutral", "Neutral / unsure"],
] as const;

export function formatDuration(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return [minutes > 0 ? `${minutes} min` : "", remainder > 0 ? `${remainder} sec` : ""]
    .filter(Boolean)
    .join(" ");
}
