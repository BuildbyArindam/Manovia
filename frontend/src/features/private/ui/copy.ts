/**
 * Every user-facing string the Private Mode surface can show.
 *
 * Same pattern as `src/features/chat/copy.ts`: the words live in
 * `src/lib/i18n/locales/{en,hi,bn}.json` under `private.*`, the accessors read
 * through i18next at call time, and tests assert on these constants rather than
 * on literals a copy edit would silently break.
 *
 * Two things are deliberately *not* translated here, and both are marked as
 * technical detail in the UI:
 *
 *   - `CapabilityAssessment.explanation` and `summarise(report)` — the device
 *     probe writes its own verdict in English. The panel shows a short
 *     translated sentence per tier instead, and keeps the probe's text inside a
 *     collapsed `<details>` labelled "why, in detail".
 *   - `ModelEntry.honest_note` and `installBlocker()` — these come from the
 *     build-time model manifest (`backend/app/content` + the model catalogue),
 *     so translating them means translating content files, not UI strings.
 */

import { t } from "@/lib/i18n/i18n";

export const PRIVATE_COPY = {
  get panelHeading() {
    return t("private.panelHeading");
  },
  get panelLede() {
    return t("private.panelLede");
  },
  get enable() {
    return t("private.enable");
  },
  get enabledHint() {
    return t("private.enabledHint");
  },
  get disabledHint() {
    return t("private.disabledHint");
  },
  get booting() {
    return t("private.booting");
  },
  get bootFailed() {
    return t("private.bootFailed");
  },
  get tierFull() {
    return t("private.tierFull");
  },
  get tierClassifiers() {
    return t("private.tierClassifiers");
  },
  get tierRules() {
    return t("private.tierRules");
  },
  get offerStandard() {
    return t("private.offerStandard");
  },
  get whyHeading() {
    return t("private.whyHeading");
  },
  get smallerModel() {
    return t("private.smallerModel");
  },

  get modelsHeading() {
    return t("private.modelsHeading");
  },
  get modelsLede() {
    return t("private.modelsLede");
  },
  modelConsent(label: string, size: string): string {
    return t("private.modelConsent", { label, size });
  },
  get modelPause() {
    return t("private.modelPause");
  },
  get modelResume() {
    return t("private.modelResume");
  },
  get modelCancel() {
    return t("private.modelCancel");
  },
  get modelVerify() {
    return t("private.modelVerify");
  },
  get modelDelete() {
    return t("private.modelDelete");
  },
  modelDeleteAll(size: string): string {
    return t("private.modelDeleteAll", { size });
  },
  modelBlocker(reason: string): string {
    return t("private.modelBlocker", { reason });
  },
  get modelVerified() {
    return t("private.modelVerified");
  },
  /** One label per lifecycle phase, so the manager never shows a raw enum. */
  phase(phase: string): string {
    return t(`private.phase-${phase}`);
  },

  get storageHeading() {
    return t("private.storageHeading");
  },
  get storageLede() {
    return t("private.storageLede");
  },
  get passphraseLabel() {
    return t("private.passphraseLabel");
  },
  get passphraseCreate() {
    return t("private.passphraseCreate");
  },
  get passphraseUnlock() {
    return t("private.passphraseUnlock");
  },
  get lock() {
    return t("private.lock");
  },
  get wipe() {
    return t("private.wipe");
  },
  get wipeConfirm() {
    return t("private.wipeConfirm");
  },
  get storeTranscript() {
    return t("private.storeTranscript");
  },
  get storedBadge() {
    return t("private.storedBadge");
  },
  get notStoredBadge() {
    return t("private.notStoredBadge");
  },
  answeredBy(runtime: string): string {
    return t("private.answeredBy", { runtime });
  },
  get guardReplaced() {
    return t("private.guardReplaced");
  },
  get voiceUnavailable() {
    return t("private.voiceUnavailable");
  },
  get crisisNoModel() {
    return t("private.crisisNoModel");
  },
};
