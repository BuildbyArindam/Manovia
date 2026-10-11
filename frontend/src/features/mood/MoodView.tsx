/**
 * Mood — the check-in surface (rendered by ``pages/MoodPage.tsx``).
 *
 * The whole surface in one screen, in the order it is used: the streak (a fact
 * about the past), the check-in itself, the reminder preference, the recent
 * entries. Nothing is behind a tab, because a ten-second check-in that needs
 * navigation first is not a ten-second check-in.
 *
 * State lives here rather than in the form: after a save the form is replaced by
 * the confirmation (so nobody re-submits the same check-in while wondering whether
 * it worked), and "Add another check-in" brings a fresh form back. The form owns
 * its own draft, so the transition cannot lose a half-written note — it is either
 * saved or still in the form.
 */

import { useState, type ReactElement } from "react";

import { PageHeader } from "@/components/PageHeader";
import type { ApiClient } from "@/lib/api";

import { MOOD_COPY } from "./copy";
import { MoodCheckInForm } from "./components/MoodCheckInForm";
import { MoodConfirmation } from "./components/MoodConfirmation";
import { MoodHistory } from "./components/MoodHistory";
import { ReminderPreference } from "./components/ReminderPreference";
import { StreakIndicator } from "./components/StreakIndicator";
import type { MoodCheckInResponse } from "./types";
import { useMoodStreak } from "./useMood";

export interface MoodViewProps {
  /** Injectable client: the tests drive the page against a fake transport. */
  client?: ApiClient;
}

export function MoodView({ client }: MoodViewProps): ReactElement {
  const [saved, setSaved] = useState<MoodCheckInResponse | null>(null);
  const streak = useMoodStreak(client);

  return (
    <div className="space-y-6" data-testid="mood-page">
      <PageHeader title={MOOD_COPY.heading} lede={MOOD_COPY.lede} />

      <StreakIndicator streak={streak.data} loading={streak.isPending} />

      {saved === null ? (
        <MoodCheckInForm
          client={client}
          onSaved={(result) => {
            setSaved(result);
          }}
        />
      ) : (
        <MoodConfirmation
          result={saved}
          onAnother={() => {
            setSaved(null);
          }}
        />
      )}

      <ReminderPreference />

      <MoodHistory client={client} />
    </div>
  );
}
