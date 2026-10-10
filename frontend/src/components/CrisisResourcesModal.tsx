/**
 * The crisis helpline dialog, opened by "Need help now?".
 *
 * The content comes from `GET /api/v1/crisis/resources` — a public endpoint,
 * because someone in trouble has not signed in and must never be asked to. The
 * emergency instruction in the introduction is static copy, so the dialog is
 * never empty even when the request fails: a failed fetch must not become a dead
 * end.
 *
 * Rendering the entries is delegated to {@link CrisisCard}, so the dialog and
 * the inline card a high-risk assessment produces cannot drift apart: one place
 * decides how a helpline is displayed and how big its tap targets are.
 */

import { useQuery } from "@tanstack/react-query";

import type { ReactElement } from "react";

import { api } from "@/lib/api";
import { fetchCrisisResources } from "@/lib/endpoints";
import { CrisisCard } from "@/components/CrisisCard";
import { Modal } from "@/components/Modal";

const TITLE_ID = "crisis-dialog-title";
const DESCRIPTION_ID = "crisis-dialog-description";

export function CrisisResourcesModal({
  onClose,
  region,
}: {
  onClose: () => void;
  /** Optional region code; without it the whole shipped list is served. */
  region?: string | null;
}): ReactElement {
  const { data, isPending, isError, refetch, isFetching } = useQuery({
    queryKey: ["crisis-resources", region ?? null],
    queryFn: () => fetchCrisisResources(api, region),
    staleTime: Number.POSITIVE_INFINITY,
    retry: false,
  });

  return (
    <Modal
      labelledBy={TITLE_ID}
      describedBy={DESCRIPTION_ID}
      onClose={onClose}
      testId="crisis-modal"
    >
      <div className="flex items-start justify-between gap-4">
        <h2 id={TITLE_ID} className="text-xl sm:text-2xl">
          Need help now?
        </h2>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="rounded-lg border border-border-strong px-3 py-1 text-sm font-semibold"
        >
          Close
        </button>
      </div>

      <p id={DESCRIPTION_ID} className="mt-3 text-ink-muted">
        You do not have to handle this alone. These lines are free, confidential, and staffed by
        trained people.{" "}
        <strong className="text-ink">
          If you are in immediate danger, call your local emergency number now
        </strong>{" "}
        (for example 911, 999, or 112).
      </p>
      <p className="mt-2 text-sm text-ink-muted">
        Manovia is not a crisis service. It cannot send help to you, and nothing you type here
        reaches a person.
      </p>

      {isPending ? (
        <p aria-busy="true" className="mt-6 rounded-xl bg-surface-muted p-4 text-ink-muted">
          Loading helplines…
        </p>
      ) : null}

      {isError ? (
        <div role="alert" className="mt-6 rounded-xl bg-danger-soft p-4">
          <p className="font-semibold text-danger">The helpline list could not be loaded.</p>
          <p className="mt-1 text-ink-muted">
            Please call your local emergency number, or a local helpline, right now.
          </p>
          <button
            type="button"
            onClick={() => {
              void refetch();
            }}
            className="mt-3 rounded-xl border border-border-strong px-4 py-2 font-semibold"
          >
            {isFetching ? "Trying again…" : "Try again"}
          </button>
        </div>
      ) : null}

      {data !== undefined ? (
        <div className="mt-6">
          <CrisisCard
            heading={null}
            resources={data.resources}
            emergency={data.emergency}
            lastVerified={data.last_verified}
            disclaimer={data.disclaimer}
            testId="crisis-modal-card"
          />
          {data.fallback_used ? (
            <p className="mt-3 text-sm text-ink-muted">
              We could not match the region you asked for, so these are international lines.
            </p>
          ) : null}
        </div>
      ) : null}
    </Modal>
  );
}
