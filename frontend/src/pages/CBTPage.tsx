import { useState, type ReactElement } from "react";
import { CrisisCard } from "@/components/CrisisCard";
import { api, type ApiClient } from "@/lib/api";
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
const steps = [
  "Situation",
  "Automatic thought",
  "Emotions + intensity",
  "Evidence for",
  "Evidence against",
  "Alternative balanced thought",
  "Re-rate emotion intensity",
] as const;
const fields: Partial<Record<number, TextKey>> = {
  0: "situation",
  1: "automatic_thought",
  3: "evidence_for",
  4: "evidence_against",
  5: "balanced_thought",
};
const emotions = ["anxiety", "sadness", "anger", "fear", "shame", "guilt", "frustration", "other"];
const tags = [
  "looking at evidence",
  "another perspective",
  "taking a pause",
  "talking to someone",
  "writing it down",
];
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
      setError("We couldn't load your records. Try again later.");
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
          setError("Please write a few words first.");
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
      setError("We couldn't check this step. Nothing was saved. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  const field = fields[step];
  return (
    <section className="mx-auto max-w-2xl space-y-6" aria-label="Thought records">
      <header>
        <h1 className="text-3xl font-semibold">Thought record</h1>
        <p className="text-ink-muted">
          A space to notice a thought, look at the evidence, and find your own perspective. This is
          not a diagnosis.
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
            className="rounded-xl bg-accent px-5 py-3 text-white"
            onClick={() => {
              setActive(true);
              setCrisis(null);
              void refresh();
            }}
          >
            {step > 0 ? "Resume reflection" : "Start a reflection"}
          </button>
          <h2 className="text-xl font-semibold">Your progress</h2>
          {records.length === 0 && <p>No saved reflections yet.</p>}
          {records.map((record) => (
            <article key={record.id} className="rounded-xl border border-border bg-surface p-4">
              <h3 className="font-semibold">{record.situation}</h3>
              <p>
                {record.emotion}: before {record.intensity_before}/100 → after{" "}
                {record.intensity_after}/100
              </p>
              <p>
                What helped:{" "}
                {record.what_helped.length ? record.what_helped.join(", ") : "Not noted"}
              </p>
              <button
                type="button"
                className="underline"
                onClick={async () => {
                  try {
                    await client.request(`/cbt/records/${record.id}`, { method: "DELETE" });
                    await refresh();
                  } catch {
                    setError("We couldn't delete this record. Try again.");
                  }
                }}
              >
                Delete record
              </button>
            </article>
          ))}
        </div>
      )}
      {active && crisis && (
        <div className="space-y-4" role="alert">
          <h2 className="text-xl font-semibold">Let's pause here</h2>
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
            Continue later
          </button>
          <button
            type="button"
            className="rounded-xl border border-border px-5 py-3"
            onClick={() => setCrisis(null)}
          >
            Edit this step
          </button>
          <p className="text-sm text-ink-muted">
            This unfinished reflection is only held in this tab. Leaving or reloading will clear it.
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
          <p className="text-sm text-ink-muted">Step {step + 1} of 7</p>
          <h2 className="text-xl font-semibold">{steps[step]}</h2>
          {field && (
            <label className="block space-y-2" htmlFor="cbt-text">
              <span>{steps[step]}</span>
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
                  aria-label="Possible thinking traps"
                  className="rounded-lg bg-accent-soft p-4"
                >
                  <h3 className="font-semibold">Possible thinking traps</h3>
                  <p>These might be worth exploring, not facts about you.</p>
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
                Emotion
              </label>
              <select
                id="cbt-emotion"
                value={draft.emotion}
                onChange={(event) => setDraft({ ...draft, emotion: event.target.value })}
                className="rounded-lg border p-2"
              >
                {emotions.map((emotion) => (
                  <option key={emotion}>{emotion}</option>
                ))}
              </select>
              <label className="block" htmlFor="cbt-before">
                Intensity before (0–100)
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
                Intensity after (0–100)
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
                <legend>What helped? (optional)</legend>
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
                    {tag}
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
                Back
              </button>
            )}
            <button
              type="submit"
              disabled={busy}
              className="rounded-xl bg-accent px-5 py-3 text-white disabled:opacity-50"
            >
              {step === 6 ? "Save reflection" : "Next"}
            </button>
            <button
              type="button"
              className="rounded-xl border px-5 py-3"
              onClick={() => setActive(false)}
            >
              Continue later
            </button>
          </div>
        </form>
      )}
    </section>
  );
}
