import { t } from "@/lib/i18n/i18n";

/**
 * Reviewable interface copy. Exercise instructions live in the backend YAML.
 *
 * Day 23: the words route through i18next (locales in `src/lib/i18n`). The
 * getters keep this module's shape, so every component and every test keeps
 * reading the same properties — and reads them in whatever language is live.
 */
export const EXERCISE_COPY = {
  get heading() {
    return t("exercises.heading");
  },
  get lede() {
    return t("exercises.lede");
  },
  get framing() {
    return t("exercises.framing");
  },
  get offline() {
    return t("exercises.offline");
  },
  get suggestions() {
    return t("exercises.suggestions");
  },
  get suggestionsNote() {
    return t("exercises.suggestionsNote");
  },
  get why() {
    return t("exercises.why");
  },
  get loading() {
    return t("exercises.loading");
  },
  get unavailable() {
    return t("exercises.unavailable");
  },
  get historyHeading() {
    return t("exercises.historyHeading");
  },
  get historyNote() {
    return t("exercises.historyNote");
  },
  get historyEmpty() {
    return t("exercises.historyEmpty");
  },
  get historyError() {
    return t("exercises.historyError");
  },
  get before() {
    return t("exercises.before");
  },
  get after() {
    return t("exercises.after");
  },
  get ratingHint() {
    return t("exercises.ratingHint");
  },
  get clearRating() {
    return t("exercises.clearRating");
  },
  get beforeBegin() {
    return t("exercises.beforeBegin");
  },
  get evidence() {
    return t("exercises.evidence");
  },
  get start() {
    return t("exercises.start");
  },
  get pause() {
    return t("exercises.pause");
  },
  get resume() {
    return t("exercises.resume");
  },
  get stop() {
    return t("exercises.stop");
  },
  get stopNote() {
    return t("exercises.stopNote");
  },
  get paused() {
    return t("exercises.paused");
  },
  get backgroundPause() {
    return t("exercises.backgroundPause");
  },
  get textOnly() {
    return t("exercises.textOnly");
  },
  get reducedMotion() {
    return t("exercises.reducedMotion");
  },
  get haptics() {
    return t("exercises.haptics");
  },
  get hapticsUnavailable() {
    return t("exercises.hapticsUnavailable");
  },
  get hapticsNote() {
    return t("exercises.hapticsNote");
  },
  get finished() {
    return t("exercises.finished");
  },
  get finishedNote() {
    return t("exercises.finishedNote");
  },
  get save() {
    return t("exercises.save");
  },
  get saving() {
    return t("exercises.saving");
  },
  get saved() {
    return t("exercises.saved");
  },
  get retrySave() {
    return t("exercises.retrySave");
  },
  get unsaved() {
    return t("exercises.unsaved");
  },
  get saveNote() {
    return t("exercises.saveNote");
  },
  get discard() {
    return t("exercises.discard");
  },
  get discardFailed() {
    return t("exercises.discardFailed");
  },
  get back() {
    return t("exercises.back");
  },
  get chooseAction() {
    return t("exercises.chooseAction");
  },
};

/** Breathing-guide cues, one i18n key per cue id. */
export const CUE_LABELS: Readonly<
  Record<"instruction" | "inhale" | "hold-in" | "exhale" | "hold-out", string>
> = {
  get instruction() {
    return t("exercises.cues.instruction");
  },
  get inhale() {
    return t("exercises.cues.inhale");
  },
  get "hold-in"() {
    return t("exercises.cues.holdIn");
  },
  get exhale() {
    return t("exercises.cues.exhale");
  },
  get "hold-out"() {
    return t("exercises.cues.holdOut");
  },
};

/** Suggestion-picker emotions with translated labels, in picker order. */
export function emotionOptions(): ReadonlyArray<readonly [string, string]> {
  return [
    ["anxiety", t("exercises.emotions.anxiety")],
    ["fear", t("exercises.emotions.fear")],
    ["sadness", t("exercises.emotions.sadness")],
    ["loneliness", t("exercises.emotions.loneliness")],
    ["anger", t("exercises.emotions.anger")],
    ["shame", t("exercises.emotions.shame")],
    ["calm", t("exercises.emotions.calm")],
    ["joy", t("exercises.emotions.joy")],
    ["neutral", t("exercises.emotions.neutral")],
  ];
}

/**
 * The same vocabulary as a stable constant for code that only needs the ids —
 * and for the test that pins them. The rendered UI uses {@link emotionOptions}.
 */
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
  return [
    minutes > 0 ? t("exercises.durationMin", { minutes }) : "",
    remainder > 0 ? t("exercises.durationSec", { seconds: remainder }) : "",
  ]
    .filter(Boolean)
    .join(" ");
}
