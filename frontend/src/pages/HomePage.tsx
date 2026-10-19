/**
 * The home screen: a greeting, a ten-second check-in, today's plan, and the
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
import { t } from "@/lib/i18n/i18n";
import type { ApiClient } from "@/lib/api";
import { PlanCard, planKeys } from "@/features/plan/PlanCard";
import { greetingNow } from "@/features/plan/greeting";
import { useMoodCheckIn } from "@/features/mood/useMood";
import { FaceScale } from "@/features/mood/components/FaceScale";
import { EnergyScale } from "@/features/mood/components/EnergyScale";
import { recordCheckIn } from "@/features/sensory/arousal";
import { SensorySuggestion } from "@/features/sensory/SensorySuggestion";

export interface HomePageProps {
  /** Injectable for tests; the browser uses the shared client. */
  client?: ApiClient;
}

export function HomePage({ client }: HomePageProps = {}): ReactElement {
  const greeting = greetingNow();
  const queryClient = useQueryClient();
  const [valence, setValence] = useState<number | null>(null);
  const [energy, setEnergy] = useState(3);
  const [saved, setSaved] = useState<"saved" | "queued" | null>(null);

  const checkIn = useMoodCheckIn(client);

  async function handleQuickCheckIn(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (valence === null || checkIn.isPending) {
      return;
    }
    const result = await checkIn.mutateAsync({ valence, energy, emotions: [], factors: [] });
    // Day 46: a local, timestamp-only count, so the optional "simpler view?"
    // offer can notice a run of activated, unpleasant check-ins.
    recordCheckIn({ valence, energy });
    // Day 25: offline, the check-in is queued on this device and sent later.
    setSaved(result.kind);
    setValence(null);
    setEnergy(3);
    // A new check-in changes what tomorrow's plan can say; the day's plan is
    // already generated, but keep the caches honest anyway.
    await queryClient.invalidateQueries({ queryKey: planKeys.today });
  }

  return (
    <div className="space-y-6" data-testid="home-page">
      <PageHeader title={greeting.headline} lede={greeting.qualifier} />

      <SensorySuggestion />

      <section
        aria-labelledby="quick-checkin-heading"
        className="rounded-2xl border border-border bg-surface p-6 shadow-soft"
        data-testid="quick-check-in"
      >
        <h2 id="quick-checkin-heading" className="text-lg">
          {t("home.quickCheckInHeading")}
        </h2>
        {saved !== null ? (
          <div className="mt-4">
            <p className="text-sm text-ink" role="status">
              {saved === "queued" ? t("home.quickQueued") : t("home.quickSaved")}
            </p>
            <button
              type="button"
              className="mt-3 min-h-11 rounded-xl border border-border-strong px-4 py-2 text-sm font-semibold text-ink"
              onClick={() => setSaved(null)}
            >
              {t("home.checkInAgain")}
            </button>
          </div>
        ) : (
          <form onSubmit={handleQuickCheckIn} className="mt-4 space-y-5">
            <FaceScale
              value={valence}
              onChange={(value) => {
                setValence(value);
                setSaved(null);
              }}
              legend={t("home.valenceLegend")}
              hint={t("home.valenceHint")}
              name="quick-valence"
            />
            <EnergyScale
              value={energy}
              onChange={setEnergy}
              legend={t("home.energyLegend")}
              hint={t("home.energyHint")}
              name="quick-energy"
            />
            <div className="flex flex-wrap items-center gap-3">
              <button
                type="submit"
                disabled={valence === null || checkIn.isPending}
                className="rounded-xl bg-accent-bg px-4 py-2 text-sm font-semibold text-accent-fg disabled:cursor-not-allowed disabled:opacity-50"
              >
                {checkIn.isPending ? t("home.saving") : t("home.save")}
              </button>
              {valence === null && <p className="text-sm text-ink-muted">{t("home.pickFace")}</p>}
              {checkIn.isError && (
                <p role="alert" className="text-sm text-danger">
                  {t("home.checkInError")}
                </p>
              )}
            </div>
          </form>
        )}
      </section>

      {/*
        Day 25: the plan comes after the check-in. The check-in needs no data
        and paints at once; the plan arrives from the API and grows when it
        does, and anything below it moves. With the plan first, the check-in
        jumped down under a reader's thumb as the page settled (Lighthouse
        measured it); now only content below the fold moves.
      */}
      <PlanCard client={client} />

      <section
        aria-labelledby="home-links-heading"
        className="rounded-2xl border border-border bg-surface p-6 shadow-soft"
      >
        <h2 id="home-links-heading" className="text-lg">
          {t("home.whereNext")}
        </h2>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <Link
            to="/chat"
            className="inline-flex min-h-11 items-center rounded-xl bg-accent-bg px-4 py-2 text-sm font-semibold text-accent-fg"
            data-testid="home-chat-link"
          >
            {t("home.talkItThrough")}
          </Link>
          <Link
            to="/mood"
            className="inline-flex min-h-11 items-center rounded-xl border border-border-strong px-4 py-2 text-sm font-semibold text-ink"
          >
            {t("home.fullMoodCheckIn")}
          </Link>
        </div>
      </section>

      <div className="flex justify-end">
        <CrisisHelpButton />
      </div>
    </div>
  );
}
