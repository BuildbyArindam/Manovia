/**
 * The public share viewer: `/s/<token>#k=<key>` (Day 47).
 *
 * This page is deliberately *not* the app. It renders outside the onboarding
 * gate and outside the navigation shell, because the person opening it is a
 * professional who has never used Manovia and has no account: asking them to
 * agree to a privacy policy before reading a document somebody else shared with
 * them would be both strange and useless.
 *
 * It reads the token from the path and the key from the fragment. The fragment
 * never reaches a server, which is the whole reason the link is split this way;
 * this page is the only place in the product that touches it, and it does not
 * store it (no `localStorage`, no history entry, no analytics event).
 */

import { useMemo, type ReactElement } from "react";
import { useParams } from "react-router-dom";

import { keyFromUrl } from "@/features/sharing/crypto";
import { ShareViewer } from "@/features/sharing/ShareViewer";
import { t } from "@/lib/i18n/i18n";
import { api, type ApiClient } from "@/lib/api";

export interface ShareViewerPageProps {
  client?: ApiClient;
}

export function ShareViewerPage({ client = api }: ShareViewerPageProps): ReactElement {
  const params = useParams<{ token: string }>();
  const token = params.token ?? "";

  // Read the fragment once. Re-reading it on every render would be harmless but
  // noisy, and a memo makes it obvious that the key comes from the address bar
  // and nowhere else.
  const urlKey = useMemo(
    () => (typeof window === "undefined" ? null : keyFromUrl(window.location.href)),
    [],
  );

  return (
    <div className="mx-auto max-w-3xl space-y-4 px-4 py-8" data-testid="share-viewer-page">
      <header className="space-y-1">
        <p className="text-sm font-semibold text-ink-muted">{t("app.name")}</p>
        <h1 className="text-2xl">{t("sharing.viewerTitle")}</h1>
      </header>

      <ShareViewer token={token} urlKey={urlKey} client={client} />
    </div>
  );
}
