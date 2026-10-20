/**
 * Share → "Prepare for my appointment" (Day 47).
 *
 * One screen with two halves: the builder (choose exactly what goes in the
 * document, read the preview, create a link) and the list (what exists, how many
 * times each link was opened, and how to end them). They sit together because
 * the question a person actually has after sending a link — "did they open it?"
 * — is answered a scroll away from making the next one.
 *
 * What this page is not: it is not an EHR integration and it is not a way to get
 * help in a crisis. The copy says both, in the same place the crisis help is
 * everywhere else in the app.
 */

import type { ReactElement } from "react";

import { PageHeader } from "@/components/PageHeader";
import { CrisisHelpButton } from "@/components/CrisisHelpButton";
import { t } from "@/lib/i18n/i18n";
import { api, type ApiClient } from "@/lib/api";

import { ShareBuilder } from "@/features/sharing/ShareBuilder";
import { ShareLinks } from "@/features/sharing/ShareLinks";

export interface SharePageProps {
  client?: ApiClient;
}

export function SharePage({ client = api }: SharePageProps): ReactElement {
  return (
    <div className="space-y-6" data-testid="share-page">
      <PageHeader title={t("sharing.title")} lede={t("sharing.lede")} />

      <ShareBuilder client={client} />
      <ShareLinks client={client} />

      <div className="card space-y-2" data-testid="share-page-limits">
        <p className="text-sm text-ink-muted">{t("sharing.viewerFooter")}</p>
        <CrisisHelpButton />
      </div>
    </div>
  );
}
