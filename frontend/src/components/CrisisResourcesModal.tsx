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
import { t } from "@/lib/i18n/i18n";

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
          {t("crisis.modal.title")}
        </h2>
        <button
          type="button"
          onClick={onClose}
          aria-label={t("crisis.modal.close")}
          className="rounded-lg border border-border-strong px-3 py-1 text-sm font-semibold"
        >
          {t("crisis.modal.close")}
        </button>
      </div>

      <p id={DESCRIPTION_ID} className="mt-3 text-ink-muted">
        {t("crisis.modal.intro")}{" "}
        <strong className="text-ink">{t("crisis.modal.introEmphasis")}</strong>{" "}
        {t("crisis.modal.introExamples")}
      </p>
      <p className="mt-2 text-sm text-ink-muted">{t("crisis.modal.notCrisis")}</p>

      {isPending ? (
        <p aria-busy="true" className="mt-6 rounded-xl bg-surface-muted p-4 text-ink-muted">
          {t("crisis.modal.loading")}
        </p>
      ) : null}

      {isError ? (
        <div role="alert" className="mt-6 rounded-xl bg-danger-soft p-4">
          <p className="font-semibold text-danger">{t("crisis.modal.errorTitle")}</p>
          <p className="mt-1 text-ink-muted">{t("crisis.modal.errorBody")}</p>
          <button
            type="button"
            onClick={() => {
              void refetch();
            }}
            className="mt-3 rounded-xl border border-border-strong px-4 py-2 font-semibold"
          >
            {isFetching ? t("crisis.modal.retrying") : t("crisis.modal.retry")}
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
            <p className="mt-3 text-sm text-ink-muted">{t("crisis.modal.regionFallback")}</p>
          ) : null}
        </div>
      ) : null}
    </Modal>
  );
}
