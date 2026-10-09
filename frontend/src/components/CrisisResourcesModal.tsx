/**
 * The crisis helpline dialog, opened by "Need help now?".
 *
 * The content comes from `GET /api/v1/crisis/resources` — a public endpoint,
 * because someone in trouble has not signed in and must never be asked to. The
 * emergency line in the introduction is static copy, so the dialog is never
 * empty even when the request fails: a failed fetch must not become a dead end.
 */

import { useQuery } from "@tanstack/react-query";

import type { ReactElement } from "react";

import { api } from "@/lib/api";
import { fetchCrisisResources, type CrisisResource } from "@/lib/endpoints";
import { Modal } from "@/components/Modal";

const TITLE_ID = "crisis-dialog-title";
const DESCRIPTION_ID = "crisis-dialog-description";

/** Kept as digits and a leading `+`, which is all a `tel:` URL may contain. */
function telHref(phone: string): string {
  const cleaned = phone.replace(/[^+\d]/g, "");
  return `tel:${cleaned}`;
}

function sortByPriority(resources: readonly CrisisResource[]): CrisisResource[] {
  return [...resources].sort((left, right) => left.priority - right.priority);
}

export function CrisisResourcesModal({ onClose }: { onClose: () => void }): ReactElement {
  const { data, isPending, isError, refetch, isFetching } = useQuery({
    queryKey: ["crisis-resources"],
    queryFn: () => fetchCrisisResources(api),
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
        <ul className="mt-6 space-y-4">
          {sortByPriority(data.resources).map((resource) => (
            <li key={resource.id} className="rounded-xl border border-border bg-surface-muted p-4">
              <h3 className="text-lg">{resource.name}</h3>
              <p className="text-sm text-ink-muted">
                {resource.region} · {resource.hours}
              </p>
              <p className="mt-2">{resource.description}</p>
              <div className="mt-3 flex flex-wrap items-center gap-3">
                {resource.phone !== null ? (
                  <a
                    href={telHref(resource.phone)}
                    className="rounded-xl bg-accent-bg px-4 py-2 font-semibold text-accent-fg"
                  >
                    Call {resource.phone}
                  </a>
                ) : null}
                {resource.sms !== null ? (
                  <a
                    href={`sms:${resource.sms}`}
                    className="rounded-xl border border-border-strong px-4 py-2 font-semibold"
                  >
                    Text {resource.sms}
                  </a>
                ) : null}
                {resource.url !== null ? (
                  <a
                    href={resource.url}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="rounded-xl border border-border-strong px-4 py-2 font-semibold text-accent"
                  >
                    Visit website
                  </a>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      ) : null}

      {data !== undefined ? (
        <p className="mt-6 text-sm text-ink-muted">
          Last checked by a person on {data.last_verified}. {data.disclaimer}
        </p>
      ) : null}
    </Modal>
  );
}
