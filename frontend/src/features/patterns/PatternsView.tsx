import { useMemo, useRef, useState, type ReactElement } from "react";
import { readPrivateEnabled } from "@/features/private/ui/preference";
import { EMPTY, exportSnapshot, parseSnapshot, type Snapshot } from "./input";
import {
  CAUTION,
  TOO_SMALL,
  constructGraph,
  factorsOf,
  layout,
  patternSentence,
  whatIf,
  withThemes,
} from "./model";
import { discoverThemes, mergeThemes } from "./themes";
import { analyseExperiment, createExperiment, dateAt, experimentSentence } from "./experiment";
import { loadSourceData } from "./source";
import type { Comparison } from "./stats";

const button =
  "rounded-lg border border-border bg-surface px-3 py-2 text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary disabled:opacity-50";
const field = "rounded-lg border border-border bg-surface p-2 text-ink";
function Distribution({ stats }: { stats: Comparison }): ReactElement {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left">
        <caption>Mood distribution, 1–5. Descriptive 95% bootstrap mean intervals.</caption>
        <thead>
          <tr>
            <th scope="col">Days</th>
            <th scope="col">n</th>
            <th scope="col">Mean (interval)</th>
            <th scope="col">1–&lt;2</th>
            <th scope="col">2–&lt;3</th>
            <th scope="col">3–&lt;4</th>
            <th scope="col">4–5</th>
          </tr>
        </thead>
        <tbody>
          {stats.distributions.map((values, i) => (
            <tr key={i}>
              <th scope="row">{i === 0 ? "Matching" : "Non-matching"}</th>
              <td>{values.length}</td>
              <td>
                {stats.means[i]!.toFixed(2)} ({stats.meanIntervals[i]![0].toFixed(2)}–
                {stats.meanIntervals[i]![1].toFixed(2)})
              </td>
              {[1, 2, 3, 4].map((n) => (
                <td key={n}>
                  {values.filter((x) => x >= n && (n === 4 ? x <= 5 : x < n + 1)).length}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <p>
        Difference: {stats.difference.toFixed(2)} points; 95% interval{" "}
        {stats.interval[0].toFixed(2)} to {stats.interval[1].toFixed(2)}. Standardised difference:{" "}
        {stats.d === null ? "undefined (no within-group spread)" : stats.d.toFixed(2)}.
      </p>
    </div>
  );
}
export function PatternsView(): ReactElement {
  const [snapshot, setSnapshot] = useState<Snapshot>(EMPTY);
  const [selected, setSelected] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [activity, setActivity] = useState("Try a 10-minute walk");
  const [start, setStart] = useState(new Date().toISOString().slice(0, 10));
  const [textOnly, setTextOnly] = useState(false);
  const [importKey, setImportKey] = useState(0);
  const board = useRef<HTMLDivElement>(null);
  const generation = useRef(0);
  const days = useMemo(
    () => withThemes(snapshot.days, snapshot.themes),
    [snapshot.days, snapshot.themes],
  );
  const graph = useMemo(() => constructGraph(days), [days]);
  const positions = useMemo(() => layout(graph), [graph]);
  const factors = useMemo(() => factorsOf(days), [days]);
  const selectedId = factors.some((f) => f.id === selected) ? selected : (factors[0]?.id ?? "");
  const comparison = useMemo(
    () => (selectedId ? whatIf(days, selectedId) : null),
    [days, selectedId],
  );
  const experiment = snapshot.experiment;
  const analysis = useMemo(() => (experiment ? analyseExperiment(experiment) : null), [experiment]);
  function replace(next: Snapshot): void {
    setSnapshot(next);
    setSelected("");
    setNotice(`${next.days.length} observed days loaded locally. Nothing derived was sent.`);
  }
  async function load(): Promise<void> {
    const token = ++generation.current;
    setBusy(true);
    try {
      const next = await loadSourceData();
      if (token === generation.current) replace(next);
    } catch (e) {
      if (token === generation.current)
        setNotice(e instanceof Error ? e.message : "Could not load source data.");
    } finally {
      if (token === generation.current) setBusy(false);
    }
  }
  function download(): void {
    const url = URL.createObjectURL(
      new Blob([exportSnapshot(snapshot)], { type: "application/json" }),
    );
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "manovia-local-patterns.json";
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 0);
    setNotice(
      "Exported. Keep this personal file somewhere safe; delete unwanted downloaded copies yourself.",
    );
  }
  return (
    <div className="space-y-6 pb-8">
      <header>
        <h1 className="text-2xl font-bold">Personal patterns</h1>
        <p>A private wellbeing knowledge graph, not a diagnosis or a prediction.</p>
        <p>
          Computed in this tab only, including in Private Mode. No graph, themes or experiment
          results are uploaded. Closing or leaving this page clears the working copy.
        </p>
      </header>
      <section
        aria-labelledby="source-heading"
        className="space-y-3 rounded-xl border border-border p-4"
      >
        <h2 id="source-heading" className="text-xl font-semibold">
          Choose your data
        </h2>
        <p>
          Standard mode can read existing mood, decrypted journal and exercise history only when you
          ask. Private Mode uses local files only. No people or places are extracted: include only
          tags you chose yourself.
        </p>
        <button
          type="button"
          className={button}
          disabled={busy || readPrivateEnabled()}
          onClick={() => void load()}
        >
          Read my existing history
        </button>
        <label className="block">
          Import local data or resume experiment (JSON, up to 2 MB)
          <input
            key={importKey}
            className="block max-w-full"
            type="file"
            accept=".json,application/json"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (!file) return;
              const token = ++generation.current;
              setBusy(false);
              if (file.size > 2_000_000) {
                setNotice("File exceeds 2 MB.");
                return;
              }
              void file
                .text()
                .then((raw) => {
                  if (token === generation.current) replace(parseSnapshot(raw));
                })
                .catch(() => {
                  if (token === generation.current)
                    setNotice("Invalid local data. Check the documented version 1 format.");
                });
            }}
          />
        </label>
        <details>
          <summary>Local file format and limits</summary>
          <p>
            Version 1: days contain date (YYYY-MM-DD), mood (1–5), factors (id, label, kind:
            factor/activity/person/place/emotion), and optional journal text. Duplicate dates are
            averaged; tags are combined. Limit: 365 days, 60 distinct tags. Export omits journal
            text; it retains editable themes and experiment assignments.
          </p>
          <pre className="overflow-auto text-sm">
            {
              '{"version":1,"days":[{"date":"2026-10-16","mood":3,"factors":[{"id":"walk","label":"A walk","kind":"activity"}]}]}'
            }
          </pre>
        </details>
        <p>
          {snapshot.days.length} observed days. Untagged means not recorded, not necessarily absent.
          Only days with mood are compared. Journal themes cover those days only.
        </p>
        <div className="flex flex-wrap gap-2">
          <button type="button" className={button} onClick={download}>
            Export graph / experiment
          </button>
          <button
            type="button"
            className={button}
            onClick={() => {
              generation.current++;
              setBusy(false);
              setSnapshot(EMPTY);
              setSelected("");
              setActivity("Try a 10-minute walk");
              setImportKey((k) => k + 1);
              setNotice(
                "Working graph, themes, journal copy and experiment deleted from this page. Source history and downloaded files are unchanged.",
              );
            }}
          >
            Delete local graph and experiment
          </button>
        </div>
        <p>
          Export is an unencrypted personal file, including mood, tags and theme membership. Save it
          to resume your two-week experiment; nothing is automatically saved in browser storage.
        </p>
        <p role="status">
          {notice}
          {busy ? " Reading source history…" : ""}
        </p>
      </section>
      <section aria-labelledby="themes-heading" className="space-y-3">
        <h2 id="themes-heading" className="text-xl font-semibold">
          Your journal themes
        </h2>
        <p>
          Local lexical clustering groups entries with shared words. These are not symptoms. Names
          are yours to choose. Discovery replaces the current themes.
        </p>
        <button
          type="button"
          className={button}
          onClick={() => {
            const themes = discoverThemes(snapshot.days);
            setSnapshot((s) => ({ ...s, themes }));
            setNotice(
              `${themes.length} unnamed themes found. Review entries by date before naming them.`,
            );
          }}
        >
          Discover themes locally
        </button>
        {snapshot.themes.map((theme) => (
          <div key={theme.id} className="space-y-2 rounded-lg border border-border p-3">
            <label>
              Theme name
              <input
                className={field}
                maxLength={120}
                value={theme.name}
                onBlur={() => {
                  if (!theme.name.trim())
                    setSnapshot((s) => ({
                      ...s,
                      themes: s.themes.map((t) =>
                        t.id === theme.id ? { ...t, name: "Unnamed theme" } : t,
                      ),
                    }));
                }}
                onChange={(e) => {
                  const name = e.target.value;
                  setSnapshot((s) => ({
                    ...s,
                    themes: s.themes.map((t) => (t.id === theme.id ? { ...t, name } : t)),
                  }));
                }}
              />
            </label>
            <p>
              {theme.dates.length} days: {theme.dates.join(", ")}
            </p>
            <details>
              <summary>Review local entries for {theme.name || "unnamed theme"}</summary>
              {snapshot.days
                .filter((d) => theme.dates.includes(d.date))
                .map((d) => (
                  <p key={d.date}>
                    {d.date}: {d.journal || "Journal text was not included in this file."}
                  </p>
                ))}
            </details>
            <label>
              Merge {theme.name || "unnamed theme"} into
              <select
                className={field}
                value=""
                onChange={(e) =>
                  setSnapshot((s) => ({
                    ...s,
                    themes: mergeThemes(s.themes, theme.id, e.target.value),
                  }))
                }
              >
                <option value="">Choose another theme</option>
                {snapshot.themes
                  .filter((t) => t.id !== theme.id)
                  .map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name || "Unnamed theme"}
                    </option>
                  ))}
              </select>
            </label>
            <button
              type="button"
              className={button}
              onClick={() =>
                setSnapshot((s) => ({ ...s, themes: s.themes.filter((t) => t.id !== theme.id) }))
              }
            >
              Delete {theme.name || "unnamed theme"}
            </button>
          </div>
        ))}
      </section>
      <section aria-labelledby="graph-heading" className="space-y-3">
        <h2 id="graph-heading" className="text-xl font-semibold">
          Your pattern graph
        </h2>
        <p>
          Lines connect tags to mood only when the Day 16 reporting rules are met. Isolated tags
          have no reportable association. The spring layout settles without animation, including
          with reduced motion.
        </p>
        <label className="block">
          <input
            type="checkbox"
            checked={textOnly}
            onChange={(e) => setTextOnly(e.target.checked)}
          />{" "}
          Text list only
        </label>
        {!textOnly && (
          <>
            <p id="graph-keys">
              Tab to a node. Arrow keys move between nodes; Home/End jump. Enter or Space selects a
              factor for the what-if explorer.
            </p>
            <div
              ref={board}
              role="group"
              data-sensory="chart"
              aria-label="Interactive pattern graph"
              aria-describedby="graph-keys"
              className="relative h-96 w-full rounded-xl border border-border bg-surface"
              onKeyDown={(e) => {
                const keys = ["ArrowRight", "ArrowDown", "ArrowLeft", "ArrowUp", "Home", "End"];
                if (!keys.includes(e.key)) return;
                const buttons = Array.from(
                  board.current?.querySelectorAll<HTMLButtonElement>("button") ?? [],
                );
                const current = buttons.indexOf(document.activeElement as HTMLButtonElement);
                if (current < 0 || !buttons.length) return;
                e.preventDefault();
                const index =
                  e.key === "Home"
                    ? 0
                    : e.key === "End"
                      ? buttons.length - 1
                      : (current +
                          (["ArrowLeft", "ArrowUp"].includes(e.key) ? -1 : 1) +
                          buttons.length) %
                        buttons.length;
                buttons[index]?.focus();
              }}
            >
              <svg
                aria-hidden="true"
                viewBox="0 0 600 400"
                preserveAspectRatio="none"
                className="absolute h-full w-full"
              >
                {graph.edges.map((edge) => {
                  const p = positions.find((p) => p.id === edge.factor.id)!;
                  return (
                    <line
                      key={edge.factor.id}
                      x1={300}
                      y1={200}
                      x2={p.x}
                      y2={p.y}
                      stroke="currentColor"
                      strokeWidth={2}
                    />
                  );
                })}
              </svg>
              <span
                className="absolute rounded bg-surface p-1"
                style={{ left: "50%", top: "50%", transform: "translate(-50%,-50%)" }}
              >
                Mood
              </span>
              {graph.nodes.map((node) => {
                const p = positions.find((p) => p.id === node.id)!;
                return (
                  <button
                    type="button"
                    key={node.id}
                    aria-label={`${node.label}, ${node.kind}`}
                    aria-pressed={selectedId === node.id}
                    title={node.label}
                    className={`${button} absolute max-w-24 truncate text-xs`}
                    style={{
                      left: `${p.x / 6}%`,
                      top: `${p.y / 4}%`,
                      transform: "translate(-50%,-50%)",
                    }}
                    onClick={() => setSelected(node.id)}
                  >
                    {node.label}
                  </button>
                );
              })}
            </div>
          </>
        )}
        <h3 className="font-semibold">Text alternative — all factors</h3>
        <ul className="space-y-2">
          {graph.nodes.map((node) => (
            <li key={node.id}>
              <button
                type="button"
                className={button}
                aria-pressed={selectedId === node.id}
                onClick={() => setSelected(node.id)}
              >
                {node.label} ({node.kind})
              </button>{" "}
              —{" "}
              {graph.edges.some((e) => e.factor.id === node.id)
                ? "association with mood; details below"
                : "no reportable association"}
            </li>
          ))}
        </ul>
        {!graph.nodes.length && <p>Choose data to see factors here.</p>}
      </section>
      <section aria-labelledby="cards-heading" className="space-y-3">
        <h2 id="cards-heading" className="text-xl font-semibold">
          Patterns I notice
        </h2>
        <p>{CAUTION}</p>
        {graph.edges.map((edge) => (
          <article key={edge.factor.id} className="rounded-xl border border-border p-4">
            <h3 className="font-semibold">{edge.factor.label}</h3>
            <p>{patternSentence(edge)}</p>
          </article>
        ))}
        {!graph.edges.length && (
          <p>
            No associations meet the reporting rules in this history. That is okay; it does not mean
            a factor is unimportant.
          </p>
        )}
      </section>
      <section aria-labelledby="whatif-heading" className="space-y-3">
        <h2 id="whatif-heading" className="text-xl font-semibold">
          What-if explorer
        </h2>
        <p>
          Explore history, not a forecast: on days when a factor was tagged, how did mood compare?
        </p>
        <p className="font-semibold">{CAUTION}</p>
        <label>
          Choose a factor
          <select
            className={field}
            value={selectedId}
            onChange={(e) => setSelected(e.target.value)}
          >
            <option value="" disabled>
              Choose a factor
            </option>
            {factors.map((f) => (
              <option key={f.id} value={f.id}>
                {f.label}
              </option>
            ))}
          </select>
        </label>
        {comparison ? <Distribution stats={comparison} /> : <p>{TOO_SMALL}</p>}
      </section>
      <section aria-labelledby="experiment-heading" className="space-y-3">
        <h2 id="experiment-heading" className="text-xl font-semibold">
          A gentle two-week experiment
        </h2>
        <p>
          Choose a safe, optional activity. The app assigns 7 of 14 days at random; on the others,
          follow your usual routine. You can skip or stop without judgement. Record mood 1–5 each
          day, whether or not you did the activity. Analysis uses assignment, not adherence.
        </p>
        <p>{CAUTION}</p>
        {!experiment ? (
          <form
            className="flex flex-wrap gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              try {
                setSnapshot((s) => ({ ...s, experiment: createExperiment(activity, start) }));
              } catch {
                setNotice("Choose an activity and a valid start date.");
              }
            }}
          >
            <label>
              Optional activity
              <input
                className={field}
                value={activity}
                maxLength={120}
                required
                onChange={(e) => setActivity(e.target.value)}
              />
            </label>
            <label>
              Start date
              <input
                className={field}
                type="date"
                value={start}
                required
                onChange={(e) => setStart(e.target.value)}
              />
            </label>
            <button className={button} type="submit">
              Randomise 14 days
            </button>
          </form>
        ) : (
          <>
            <h3 className="font-semibold">{experiment.activity}</h3>
            <p>
              Export now and after recording mood to resume later. Do not repeatedly re-randomise to
              seek a different result.
            </p>
            <ol className="space-y-2">
              {Array.from({ length: 14 }, (_, i) => {
                const date = dateAt(experiment.start, i);
                return (
                  <li key={date} className="flex flex-wrap items-center gap-2">
                    <span>
                      {date}:{" "}
                      {experiment.assigned.includes(i) ? "Optional activity day" : "Usual routine"}
                    </span>
                    <label>
                      Mood for {date}
                      <select
                        className={field}
                        value={experiment.moods[date] ?? ""}
                        onChange={(e) => {
                          const value = e.target.value;
                          setSnapshot((s) => {
                            if (!s.experiment) return s;
                            const moods = { ...s.experiment.moods };
                            if (value) moods[date] = Number(value);
                            else delete moods[date];
                            return { ...s, experiment: { ...s.experiment, moods } };
                          });
                        }}
                      >
                        <option value="">Not recorded</option>
                        {[1, 2, 3, 4, 5].map((n) => (
                          <option key={n}>{n}</option>
                        ))}
                      </select>
                    </label>
                  </li>
                );
              })}
            </ol>
            <p role="status">{experimentSentence(analysis)}</p>
            <button
              type="button"
              className={button}
              onClick={() => setSnapshot((s) => ({ ...s, experiment: null }))}
            >
              Stop and discard experiment
            </button>
          </>
        )}
      </section>
    </div>
  );
}
