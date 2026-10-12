/**
 * Today's plan: at most three small suggestions, each with its "why this?".
 *
 * The card is deliberately quiet. Done and Skip are the only actions, Skip
 * asks for nothing, and a finished item stays visible but settled instead of
 * disappearing — the plan is a record of the day, not a to-do list that
 * shames you with what is left. When quiet hours are active or the planner
 * has nothing to ask for, the card says so plainly instead of inventing
 * something to say.
 */

import { useState, type ReactElement } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { ApiError, type ApiClient } from "@/lib/api";
import { t } from "@/lib/i18n/i18n";
import { api as defaultClient } from "@/lib/api";
import { CrisisResourcesModal } from "@/components/CrisisResourcesModal";

import { fetchTodayPlan, markPlanItemDone, markPlanItemSkipped } from "./endpoints";
import type { PlanItem, PlanTodayResponse } from "./types";

export const planKeys = {
  today: ["plan", "today"] as const,
};

/** Where each kind of suggestion leads, when it leads anywhere at all. */
function linkFor(item: PlanItem): { to: string; label: string } | null {
  switch (item.kind) {
    case "check_in":
      return { to: "/mood", label: t("plan.linkToCheckIn") };
    case "exercise":
      return item.ref !== null
        ? { to: `/exercises/${item.ref}`, label: t("plan.linkOpenExercise") }
        : { to: "/exercises", label: t("plan.linkBrowseExercises") };
    case "thought_record":
      return { to: "/cbt", label: t("plan.linkOpenThoughtRecords") };
    default:
      return null;
  }
}

function statusWord(status: PlanItem["status"]): string {
  switch (status) {
    case "done":
      return t("plan.statusDone");
    case "skipped":
      return t("plan.statusSkipped");
    case "pending":
      return t("plan.statusPending");
  }
}

export interface PlanCardProps {
  client?: ApiClient;
}

export function PlanCard({ client = defaultClient }: PlanCardProps): ReactElement {
  const queryClient = useQueryClient();
  const [supportOpen, setSupportOpen] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const planQuery = useQuery<PlanTodayResponse, Error>({
    queryKey: planKeys.today,
    queryFn: () => fetchTodayPlan(client),
  });

  const settle = useMutation<PlanItem, Error, { id: string; action: "done" | "skip" }>({
    mutationFn: ({ id, action }) =>
      action === "done" ? markPlanItemDone(id, client) : markPlanItemSkipped(id, client),
    onSuccess: async () => {
      setErrorMessage(null);
      await queryClient.invalidateQueries({ queryKey: planKeys.today });
    },
    onError: (error) => {
      setErrorMessage(
        error instanceof ApiError
          ? t("plan.saveError")
          : t("plan.saveError"),
      );
    },
  });

  if (planQuery.isLoading) {
    return (
      <section
        aria-labelledby="plan-heading"
        className="rounded-2xl border border-border bg-surface p-6 shadow-soft"
        data-testid="plan-card"
      >
        <h2 id="plan-heading" className="text-lg">
          {t("plan.cardHeading")}
        </h2>
        <p className="mt-3 text-sm text-ink-muted">{t("plan.loading")}</p>
      </section>
    );
  }

  if (planQuery.isError || planQuery.data === undefined) {
    return (
      <section
        aria-labelledby="plan-heading"
        className="rounded-2xl border border-border bg-surface p-6 shadow-soft"
        data-testid="plan-card"
      >
        <h2 id="plan-heading" className="text-lg">
          {t("plan.cardHeading")}
        </h2>
        <p className="mt-3 text-sm text-ink-muted">
          {t("plan.loadError")}
        </p>
      </section>
    );
  }

  const plan = planQuery.data;

  return (
    <section
      aria-labelledby="plan-heading"
      className="rounded-2xl border border-border bg-surface p-6 shadow-soft"
      data-testid="plan-card"
    >
      <h2 id="plan-heading" className="text-lg">
        {t("plan.cardHeading")}
      </h2>

      {plan.quiet_hours_active && plan.items.length === 0 ? (
        <p className="mt-3 text-sm text-ink-muted" data-testid="plan-quiet-hours">
          {t("plan.quietHoursActive")}
        </p>
      ) : plan.items.length === 0 ? (
        <p className="mt-3 text-sm text-ink-muted" data-testid="plan-empty">
          {t("plan.empty")}
        </p>
      ) : (
        <ul className="mt-4 space-y-4">
          {plan.items.map((item) => {
            const link = item.kind === "support" ? null : linkFor(item);
            const settled = item.status !== "pending";
            return (
              <li
                key={item.id}
                className="rounded-xl border border-border p-4"
                data-testid={`plan-item-${item.kind}`}
                data-status={item.status}
              >
                <div className="flex items-start justify-between gap-3">
                  <h3 className="font-semibold">{item.title}</h3>
                  <span className="shrink-0 rounded-full bg-accent-soft px-2 py-0.5 text-xs font-semibold text-accent">
                    {item.kind === "support" ? t("plan.statusSupport") : statusWord(item.status)}
                  </span>
                </div>
                <p className="mt-1 text-sm text-ink-muted">
                  <span className="font-semibold">{t("plan.whyLabel")} </span>
                  {item.why}
                </p>
                <div className="mt-3 flex flex-wrap items-center gap-3">
                  {item.kind === "support" ? (
                    // Support is something you look at, not a task to settle:
                    // no Done, no Skip, so nobody is prompted to dismiss it.
                    <button
                      type="button"
                      onClick={() => setSupportOpen(true)}
                      className="rounded-xl border-2 border-danger bg-danger-soft px-3 py-2 text-sm font-semibold text-danger"
                    >
                      {t("plan.seeSupport")}
                    </button>
                  ) : (
                    <>
                      {link !== null && (
                        <Link
                          to={link.to}
                          className="inline-flex min-h-11 items-center rounded-xl border border-border-strong px-3 py-2 text-sm font-semibold text-ink"
                        >
                          {link.label}
                        </Link>
                      )}
                      {!settled && (
                        <>
                          <button
                            type="button"
                            onClick={() => settle.mutate({ id: item.id, action: "done" })}
                            className="rounded-xl bg-accent-bg px-3 py-2 text-sm font-semibold text-accent-fg"
                          >
                            {t("plan.doneButton")}
                          </button>
                          <button
                            type="button"
                            onClick={() => settle.mutate({ id: item.id, action: "skip" })}
                            className="rounded-xl border border-border px-3 py-2 text-sm text-ink-muted"
                          >
                            {t("plan.skipButton")}
                          </button>
                        </>
                      )}
                    </>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {errorMessage !== null && (
        <p role="alert" className="mt-3 text-sm text-danger">
          {errorMessage}
        </p>
      )}
      {supportOpen ? <CrisisResourcesModal onClose={() => setSupportOpen(false)} /> : null}
    </section>
  );
}
