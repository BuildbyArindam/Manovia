import { useState, type ReactElement } from "react";
import { CrisisCard } from "@/components/CrisisCard";
import { api, type ApiClient } from "@/lib/api";
import { t } from "@/lib/i18n/i18n";
import type { CrisisMessage, CrisisResource } from "@/lib/endpoints";

type TextKey =
  "situation" | "automatic_thought" | "evidence_for" | "evidence_against" | "balanced_thought";
interface Crisis {
  level: string;
  message: CrisisMessage | null;
  emergency: CrisisResource | null;
  resources: CrisisResource[];
}
interface ScreenResult {
  interrupted: boolean;
  crisis: Crisis | null;
}
interface TrapResult extends ScreenResult {
  candidates: { id: string; explanation: string }[];
  question: string;
  source: string;
}
interface RecordData {
  situation: string;
  automatic_thought: string;
  evidence_for: string;
  evidence_against: string;
  balanced_thought: string;
  emotion: string;
  intensity_before: number;
  intensity_after: number;
  what_helped: string[];
}
interface RecordView extends RecordData {
  id: string;
  created_at: string;
}
interface SaveResult extends ScreenResult {
  record: RecordView | null;
}
const stepLabelKeys = [
  "cbt.steps.situation",
  "cbt.steps.automaticThought",
  "cbt.steps.emotions",
  "cbt.steps.evidenceFor",
  "cbt.steps.evidenceAgainst",
  "cbt.steps.balanced",
  "cbt.steps.reRate",
] as const;

function stepLabel(index: number): string {
  const key = stepLabelKeys[index];
  return key !== undefined ? t(key) : "";
}
const fields: Partial<Record<number, TextKey>> = {
  0: "situation",
  1: "automatic_thought",
  3: "evidence_for",
  4: "evidence_against",
  5: "balanced_thought",
};
const emotions = ["anxiety", "sadness", "anger", "fear", "shame", "guilt", "frustration", "other"] as const;

function emotionLabel(id: string): string {
  return t(`cbt.emotions.${id}`, { defaultValue: id });
}
/**
 * The stored values are the English phrases (the backend validates them as a
 * fixed literal list), so `what_helped` data stays stable across languages;
 * only the rendered label is localised, via this phrase -> key lookup.
 */
const tagPhraseKeys: Readonly<Record<string, string>> = {
  "looking at evidence": "cbt.tags.lookingAtEvidence",
  "another perspective": "cbt.tags.anotherPerspective",
  "taking a pause": "cbt.tags.takingAPause",
  "talking to someone": "cbt.tags.talkingToSomeone",
  "writing it down": "cbt.tags.writingItDown",
};
const tags = Object.keys(tagPhraseKeys);

function tagLabel(phrase: string): string {
  const key = tagPhraseKeys[phrase];
  return key !== undefined ? t(key) : phrase;
}
const initial: RecordData = {
  situation: "",
  automatic_thought: "",
  evidence_for: "",
  evidence_against: "",
  balanced_thought: "",
  emotion: "anxiety",
  intensity_before: 50,
  intensity_after: 50,
  what_helped: [],
};

export function CBTPage({ client = api }: { client?: ApiClient }): ReactElement {
  const [records, setRecords] = useState<RecordView[]>([]);
  const [step, setStep] = useState(0);
  const [draft, setDraft] = useState<RecordData>(initial);
  const [active, setActive] = useState(false);
  const [crisis, setCrisis] = useState<Crisis | null>(null);
  const [traps, setTraps] = useState<TrapResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function refresh(): Promise<void> {
    try {
      setRecords(await client.get<RecordView[]>("/cbt/records"));
    } catch {
      setError(t("cbt.loadError"));
    }
  }

  async function next(): Promise<void> {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const field = fields[step];
      if (field) {
        if (!draft[field].trim()) {
          setError(t("cbt.writeFirst"));
          return;
        }
        const payload = { field, text: draft[field] };
        const result =
          step === 1
            ? await client.post<TrapResult>("/cbt/traps", payload)
            : await client.post<ScreenResult>("/cbt/screen", payload);
        if (result.interrupted) {
          setCrisis(result.crisis);
          return;
        }
        if (step === 1) setTraps(result as TrapResult);
      }
      if (step === 6) {
        const result = await client.post<SaveResult>("/cbt/records", draft);
        if (result.interrupted) {
          setCrisis(result.crisis);
          return;
        }
        setDraft(initial);
        setTraps(null);
        setStep(0);
        setActive(false);
        await refresh();
        return;
      }
      setStep((value) => value + 1);
    } catch {
      setError(t("cbt.stepError"));
    } finally {
      setBusy(false);
    }
  }

  const field = fields[step];
  return (
    <section className="mx-auto max-w-2xl space-y-6" aria-label={t("cbt.ariaLabel")}>
      <header>
        <h1 className="text-3xl font-semibold">{t("cbt.heading")}</h1>
        <p className="text-ink-muted">
          {t("cbt.lede")}
        </p>
      </header>
      {error && (
        <p role="alert" className="text-red-700">
          {error}
        </p>
      )}
      {!active && (
        <div className="space-y-3">
          <button
            type="button"
            className="min-h-11 rounded-xl bg-accent-bg px-5 py-3 text-accent-fg"
            onClick={() => {
              setActive(true);
              setCrisis(null);
              void refresh();
            }}
          >
            {step > 0 ? t("cbt.resumeButton") : t("cbt.startButton")}
          </button>
          <h2 className="text-xl font-semibold">{t("cbt.progressHeading")}</h2>
          {records.length === 0 && <p>{t("cbt.noRecords")}</p>}
          {records.map((record) => (
            <article key={record.id} className="rounded-xl border border-border bg-surface p-4">
              <h3 className="font-semibold">{record.situation}</h3>
              <p>
                {t("cbt.intensityLine", {
                  emotion: emotionLabel(record.emotion),
                  before: record.intensity_before,
                  after: record.intensity_after,
                })}
              </p>
              <p>
                {t("cbt.whatHelpedPrefix")}{" "}
                {record.what_helped.length ? record.what_helped.map(tagLabel).join(", ") : t("cbt.notNoted")}
              </p>
              <button
                type="button"
                className="underline"
                onClick={async () => {
                  try {
                    await client.request(`/cbt/records/${record.id}`, { method: "DELETE" });
                    await refresh();
                  } catch {
                    setError(t("cbt.deleteError"));
                  }
                }}
              >
                {t("cbt.deleteRecord")}
              </button>
            </article>
          ))}
        </div>
      )}
      {active && crisis && (
        <div className="space-y-4" role="alert">
          <h2 className="text-xl font-semibold">{t("cbt.pauseHeading")}</h2>
          <CrisisCard
            message={crisis.message}
            resources={crisis.resources}
            emergency={crisis.emergency}
          />
          <button
            type="button"
            className="rounded-xl border border-border px-5 py-3"
            onClick={() => setActive(false)}
          >
            {t("cbt.continueLater")}
          </button>
          <button
            type="button"
            className="rounded-xl border border-border px-5 py-3"
            onClick={() => setCrisis(null)}
          >
            {t("cbt.editThisStep")}
          </button>
          <p className="text-sm text-ink-muted">
            {t("cbt.crisisNote")}
          </p>
        </div>
      )}
      {active && !crisis && (
        <form
          className="space-y-5 rounded-xl border border-border bg-surface p-5"
          onSubmit={(event) => {
            event.preventDefault();
            void next();
          }}
        >
          <p className="text-sm text-ink-muted">{t("cbt.stepCounter", { current: step + 1, total: 7 })}</p>
          <h2 className="text-xl font-semibold">{stepLabel(step)}</h2>
          {field && (
            <label className="block space-y-2" htmlFor="cbt-text">
              <span>{stepLabel(step)}</span>
              <textarea
                id="cbt-text"
                required
                maxLength={4000}
                rows={4}
                value={draft[field]}
                className="w-full rounded-lg border border-border-strong bg-bg p-3"
                onChange={(event) => setDraft({ ...draft, [field]: event.target.value })}
              />
            </label>
          )}
          {step === 2 && (
            <>
              {traps && (
                <aside
                  aria-label={t("cbt.trapsLabel")}
                  className="rounded-lg bg-accent-soft p-4"
                >
                  <h3 className="font-semibold">{t("cbt.trapsLabel")}</h3>
                  <p>{t("cbt.trapsIntro")}</p>
                  <ul>
                    {traps.candidates.map((trap) => (
                      <li key={trap.id}>
                        {trap.id}: {trap.explanation}
                      </li>
                    ))}
                  </ul>
                  <p>{traps.question}</p>
                </aside>
              )}
              <label className="block" htmlFor="cbt-emotion">
                {t("cbt.emotionLabel")}
              </label>
              <select
                id="cbt-emotion"
                value={draft.emotion}
                onChange={(event) => setDraft({ ...draft, emotion: event.target.value })}
                className="rounded-lg border p-2"
              >
                {emotions.map((emotion) => (
                  <option key={emotion} value={emotion}>
                    {emotionLabel(emotion)}
                  </option>
                ))}
              </select>
              <label className="block" htmlFor="cbt-before">
                {t("cbt.intensityBefore")}
              </label>
              <input
                id="cbt-before"
                type="number"
                min="0"
                max="100"
                required
                value={draft.intensity_before}
                onChange={(event) =>
                  setDraft({ ...draft, intensity_before: Number(event.target.value) })
                }
                className="rounded-lg border p-2"
              />
            </>
          )}
          {step === 6 && (
            <>
              <label className="block" htmlFor="cbt-after">
                {t("cbt.intensityAfter")}
              </label>
              <input
                id="cbt-after"
                type="number"
                min="0"
                max="100"
                required
                value={draft.intensity_after}
                onChange={(event) =>
                  setDraft({ ...draft, intensity_after: Number(event.target.value) })
                }
                className="rounded-lg border p-2"
              />
              <fieldset>
                <legend>{t("cbt.whatHelpedLegend")}</legend>
                {tags.map((tag) => (
                  <label key={tag} className="block py-1">
                    <input
                      type="checkbox"
                      checked={draft.what_helped.includes(tag)}
                      onChange={(event) =>
                        setDraft({
                          ...draft,
                          what_helped: event.target.checked
                            ? [...draft.what_helped, tag]
                            : draft.what_helped.filter((item) => item !== tag),
                        })
                      }
                    />{" "}
                    {tagLabel(tag)}
                  </label>
                ))}
              </fieldset>
            </>
          )}
          <div className="flex gap-3">
            {step > 0 && (
              <button
                type="button"
                className="rounded-xl border px-5 py-3"
                onClick={() => setStep(step - 1)}
              >
                {t("cbt.backButton")}
              </button>
            )}
            <button
              type="submit"
              disabled={busy}
              className="min-h-11 rounded-xl bg-accent-bg px-5 py-3 text-accent-fg disabled:opacity-50"
            >
              {step === 6 ? t("cbt.saveButton") : t("cbt.nextButton")}
            </button>
            <button
              type="button"
              className="rounded-xl border px-5 py-3"
              onClick={() => setActive(false)}
            >
              {t("cbt.continueLater")}
            </button>
          </div>
        </form>
      )}
    </section>
  );
}
