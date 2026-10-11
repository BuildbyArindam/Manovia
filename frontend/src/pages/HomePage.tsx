/**
 * The home screen: a greeting, today's plan, a ten-second check-in, and the
 * two doors people come here for — the chat and the help line.
 *
 * The greeting is a time of day and nothing else: the app does not know a
 * name or a gender, and it will not invent either (see `features/plan/
 * greeting.ts`). "Need help now?" sits on the page itself as well as in the
 * header, quiet and permanent, because the one control that must never be
 * far away should not depend on scrolling.
 */

import { useState, type FormEvent, type ReactElement } from "react";
import { Link } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";

import { CrisisHelpButton } from "@/components/CrisisHelpButton";
import { PageHeader } from "@/components/PageHeader";
import type { ApiClient } from "@/lib/api";
import { PlanCard, planKeys } from "@/features/plan/PlanCard";
import { greetingNow } from "@/features/plan/greeting";
import { useMoodCheckIn } from "@/features/mood/useMood";
import { FaceScale } from "@/features/mood/components/FaceScale";
import { EnergyScale } from "@/features/mood/components/EnergyScale";

export interface HomePageProps {
  /** Injectable for tests; the browser uses the shared client. */
  client?: ApiClient;
}

export function HomePage({ client }: HomePageProps = {}): ReactElement {
  const greeting = greetingNow();
  const queryClient = useQueryClient();
  const [valence, setValence] = useState<number | null>(null);
  const [energy, setEnergy] = useState(3);
  const [saved, setSaved] = useState(false);

  const checkIn = useMoodCheckIn(client);

  async function handleQuickCheckIn(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (valence === null || checkIn.isPending) {
      return;
    }
    await checkIn.mutateAsync({ valence, energy, emotions: [], factors: [] });
    setSaved(true);
    setValence(null);
    setEnergy(3);
    // A new check-in changes what tomorrow's plan can say; the day's plan is
    // already generated, but keep the caches honest anyway.
    await queryClient.invalidateQueries({ queryKey: planKeys.today });
  }

  return (
    <div className="space-y-6" data-testid="home-page">
      <PageHeader title={greeting.headline} lede={greeting.qualifier} />

      <PlanCard client={client} />

      <section
        aria-labelledby="quick-checkin-heading"
        className="rounded-2xl border border-border bg-surface p-6 shadow-soft"
        data-testid="quick-check-in"
      >
        <h2 id="quick-checkin-heading" className="text-lg">
          Quick check-in
        </h2>
        {saved ? (
          <div className="mt-4">
            <p className="text-sm text-ink" role="status">
              Saved. Thanks for checking in — that is the whole job.
            </p>
            <button
              type="button"
              className="mt-3 rounded-xl border border-border-strong px-4 py-2 text-sm font-semibold text-ink"
              onClick={() => setSaved(false)}
            >
              Check in again
            </button>
          </div>
        ) : (
          <form onSubmit={handleQuickCheckIn} className="mt-4 space-y-5">
            <FaceScale
              value={valence}
              onChange={(value) => {
                setValence(value);
                setSaved(false);
              }}
              legend="How do you feel right now?"
              hint="One tap is enough."
              name="quick-valence"
            />
            <EnergyScale
              value={energy}
              onChange={setEnergy}
              legend="How much energy is there?"
              hint="A guess is fine."
              name="quick-energy"
            />
            <div className="flex flex-wrap items-center gap-3">
              <button
                type="submit"
                disabled={valence === null || checkIn.isPending}
                className="rounded-xl bg-accent-bg px-4 py-2 text-sm font-semibold text-accent-fg disabled:cursor-not-allowed disabled:opacity-50"
              >
                {checkIn.isPending ? "Saving…" : "Save check-in"}
              </button>
              {valence === null && <p className="text-sm text-ink-muted">Pick a face to save.</p>}
              {checkIn.isError && (
                <p role="alert" className="text-sm text-danger">
                  The check-in could not be saved just now. Please try again.
                </p>
              )}
            </div>
          </form>
        )}
      </section>

      <section
        aria-labelledby="home-links-heading"
        className="rounded-2xl border border-border bg-surface p-6 shadow-soft"
      >
        <h2 id="home-links-heading" className="text-lg">
          Where to next?
        </h2>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <Link
            to="/chat"
            className="rounded-xl bg-accent-bg px-4 py-2 text-sm font-semibold text-accent-fg"
            data-testid="home-chat-link"
          >
            Talk it through
          </Link>
          <Link
            to="/mood"
            className="rounded-xl border border-border-strong px-4 py-2 text-sm font-semibold text-ink"
          >
            Full mood check-in
          </Link>
        </div>
      </section>

      <div className="flex justify-end">
        <CrisisHelpButton />
      </div>
    </div>
  );
}
