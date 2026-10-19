/**
 * The owner's links: what exists, what happened to each one, and how to end them.
 *
 * The sentence the brief asks for — "opened 2 times" — comes from the server
 * (`ShareSummary.summary`), because the server is what counts. The list renders
 * it rather than computing its own version, so the number on screen and the
 * number in the audit log cannot disagree.
 *
 * "Revoke everything" is one click and irreversible, which is why it asks for a
 * typed word first: a mis-tap on a phone should not be able to end a link a
 * professional is about to open.
 */

import { useState, type ReactElement } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { isApiError, api as defaultClient, type ApiClient } from "@/lib/api";
import { formatDateTime } from "@/lib/dates";
import { t } from "@/lib/i18n/i18n";

import { fetchShareAudit, listShares, revokeAllShares, revokeShare } from "./endpoints";
import { shareKeys } from "./ShareBuilder";
import type { AuditOut, ShareSummary } from "./types";

/** The phrase that unlocks "revoke everything". */
export const REVOKE_ALL_CONFIRMATION = "REVOKE";

const STATUS_KEYS: Record<string, string> = {
  active: "sharing.statusActive",
  expired: "sharing.statusExpired",
  revoked: "sharing.statusRevoked",
  exhausted: "sharing.statusExhausted",
};

const EVENT_KEYS: Record<string, string> = {
  created: "sharing.eventCreated",
  opened: "sharing.eventOpened",
  passcode_failed: "sharing.eventPasscodeFailed",
  locked: "sharing.eventLocked",
  revoked: "sharing.eventRevoked",
  expired: "sharing.eventExpired",
  view_limit_reached: "sharing.eventViewLimitReached",
  access_after_revoke: "sharing.eventAccessAfterRevoke",
};

const ACTOR_KEYS: Record<string, string> = {
  owner: "sharing.actorOwner",
  viewer: "sharing.actorViewer",
  system: "sharing.actorSystem",
};

function statusLabel(status: string): string {
  const key = STATUS_KEYS[status];
  return key === undefined ? status : t(key);
}

function eventLabel(kind: string): string {
  const key = EVENT_KEYS[kind];
  return key === undefined ? kind : t(key);
}

function actorLabel(actor: string): string {
  const key = ACTOR_KEYS[actor];
  return key === undefined ? actor : t(key);
}

export interface ShareLinksProps {
  client?: ApiClient;
}

export function ShareLinks({ client = defaultClient }: ShareLinksProps): ReactElement {
  const queryClient = useQueryClient();
  const [openAudit, setOpenAudit] = useState<string | null>(null);
  const [revokePhrase, setRevokePhrase] = useState("");
  const [notice, setNotice] = useState<string | null>(null);

  const listQuery = useQuery({
    queryKey: shareKeys.list,
    queryFn: () => listShares(client),
    staleTime: 15_000,
  });

  const revokeOne = useMutation({
    mutationFn: (shareId: string) => revokeShare(shareId, client),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: shareKeys.list });
    },
  });

  const revokeAll = useMutation({
    mutationFn: () => revokeAllShares(client),
    onSuccess: (result) => {
      setRevokePhrase("");
      setNotice(t("sharing.revokedCount", { count: result.revoked }));
      void queryClient.invalidateQueries({ queryKey: shareKeys.list });
    },
  });

  const shares = listQuery.data ?? [];
  const live = shares.filter((share) => share.status === "active").length;

  return (
    <section className="space-y-4" aria-labelledby="share-list-heading" data-testid="share-links">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h2 id="share-list-heading" className="text-xl">
          {t("sharing.listHeading")}
        </h2>
        <div className="space-y-2">
          <label className="block text-sm" htmlFor="share-revoke-all">
            {t("sharing.revokeAllConfirm")}
          </label>
          <div className="flex gap-2">
            <input
              id="share-revoke-all"
              type="text"
              autoComplete="off"
              data-testid="share-revoke-all-input"
              className="w-32 rounded-xl border border-border-strong bg-surface px-3 py-2"
              value={revokePhrase}
              onChange={(event) => setRevokePhrase(event.target.value)}
            />
            <button
              type="button"
              className="btn-secondary"
              data-testid="share-revoke-all"
              disabled={
                live === 0 || revokePhrase !== REVOKE_ALL_CONFIRMATION || revokeAll.isPending
              }
              onClick={() => revokeAll.mutate()}
            >
              {t("sharing.revokeAll")}
            </button>
          </div>
          <p className="text-sm text-ink-muted">{t("sharing.revokeAllHelp")}</p>
        </div>
      </div>

      {notice !== null ? (
        <p role="status" className="text-sm" data-testid="share-revoked-notice">
          {notice}
        </p>
      ) : null}
      {revokeAll.isError || revokeOne.isError ? (
        <p role="alert" className="text-sm" data-testid="share-revoke-error">
          {isApiError(revokeAll.error ?? revokeOne.error)
            ? (revokeAll.error ?? revokeOne.error)?.message
            : t("sharing.errorGeneric")}
        </p>
      ) : null}

      {listQuery.isLoading ? (
        <p role="status" className="text-sm text-ink-muted" data-testid="share-list-loading">
          {t("app.loading")}
        </p>
      ) : shares.length === 0 ? (
        <p className="text-sm text-ink-muted" data-testid="share-list-empty">
          {t("sharing.listEmpty")}
        </p>
      ) : (
        <ul className="space-y-3">
          {shares.map((share) => (
            <li key={share.id} className="card space-y-2" data-testid={`share-row-${share.id}`}>
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h3 className="font-semibold" data-testid={`share-status-${share.id}`}>
                  {statusLabel(share.status)}
                  <span className="ml-2 font-mono text-xs text-ink-muted">
                    …{share.token_prefix}
                  </span>
                </h3>
                <span className="text-sm text-ink-muted">
                  {t("sharing.expiresOn", { date: formatDateTime(share.expires_at) })}
                </span>
              </div>

              <p className="text-sm" data-testid={`share-summary-${share.id}`}>
                {share.summary}
              </p>
              <p className="text-sm text-ink-muted">
                {share.items.join(", ")} · {share.formats.join(", ")}
              </p>
              {share.locked_until !== null ? (
                <p className="text-sm" data-testid={`share-locked-${share.id}`}>
                  {t("sharing.lockedUntil", { date: formatDateTime(share.locked_until) })}
                </p>
              ) : null}

              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  className="btn-secondary"
                  data-testid={`share-audit-${share.id}`}
                  aria-expanded={openAudit === share.id}
                  onClick={() => setOpenAudit(openAudit === share.id ? null : share.id)}
                >
                  {t("sharing.auditOpen")}
                </button>
                <button
                  type="button"
                  className="btn-secondary"
                  data-testid={`share-revoke-${share.id}`}
                  disabled={share.status !== "active" || revokeOne.isPending}
                  onClick={() => revokeOne.mutate(share.id)}
                >
                  {revokeOne.isPending && revokeOne.variables === share.id
                    ? t("sharing.revoking")
                    : t("sharing.revokeOne")}
                </button>
              </div>

              {openAudit === share.id ? <AuditPanel shareId={share.id} client={client} /> : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function AuditPanel({ shareId, client }: { shareId: string; client: ApiClient }): ReactElement {
  const auditQuery = useQuery({
    queryKey: shareKeys.audit(shareId),
    queryFn: () => fetchShareAudit(shareId, client),
    staleTime: 10_000,
  });

  if (auditQuery.isLoading) {
    return (
      <p role="status" className="text-sm text-ink-muted">
        {t("app.loading")}
      </p>
    );
  }
  if (auditQuery.isError || auditQuery.data === undefined) {
    return (
      <p role="alert" className="text-sm">
        {t("sharing.errorGeneric")}
      </p>
    );
  }

  const audit: AuditOut = auditQuery.data;
  return (
    <div className="space-y-2 rounded-xl border border-border px-3 py-2" data-testid="share-audit">
      <p className="text-sm font-semibold" data-testid="share-audit-summary">
        {audit.summary}
      </p>
      {audit.events.length === 0 ? (
        <p className="text-sm text-ink-muted">{t("sharing.auditEmpty")}</p>
      ) : (
        <ol className="space-y-1 text-sm">
          {audit.events.map((event, index) => (
            <li
              key={`${event.created_at}-${index}`}
              className="flex flex-wrap justify-between gap-2"
              data-testid="share-audit-event"
            >
              <span>
                {eventLabel(event.kind)} · {actorLabel(event.actor)}
              </span>
              <span className="text-ink-muted">{formatDateTime(event.created_at)}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

/** One row as a sentence, for tests and for a screen reader summary. */
export function describeShare(share: ShareSummary): string {
  return `${statusLabel(share.status)}: ${share.summary}`;
}
