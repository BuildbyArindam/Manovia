/**
 * The application shell: header, navigation, main landmark, footer.
 *
 * One navigation, two presentations — a side rail on desktop and a bottom bar on
 * mobile — chosen by a media query rather than by CSS alone, so the rendered
 * markup matches what the user can actually reach (and so the behaviour is
 * testable). Landmarks are explicit: `header`/`nav`/`main`/`footer`, plus a
 * skip link that is the first focusable element on the page.
 *
 * Day 25 additions, all about the moment *between* pages:
 *
 * * **Page titles.** Every route sets `document.title` ("Mood · Manovia"), so a
 *   screen reader announces where a navigation landed and a tab list is
 *   readable (WCAG 2.4.2).
 * * **Focus after navigation.** A client-side route change moves focus to the
 *   main landmark — unless the new page already put it somewhere on purpose
 *   (the chat composer, a confirmation heading) — so keyboard and screen-reader
 *   users start at the new content instead of on the link they just pressed.
 * * **Offline and update notices** sit in the page flow under the header, and
 *   the outbox sync that sends queued check-ins runs here, on every page.
 */

import { useEffect, useRef, type ReactElement, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";

import { BottomNav } from "@/components/BottomNav";
import { Header } from "@/components/Header";
import { SideNav } from "@/components/SideNav";
import { routeTitle } from "@/components/navigation";
import { OutboxSync } from "@/features/mood/OutboxSync";
import { OverwhelmedBar } from "@/features/sensory/OverwhelmedBar";
import { useSensory } from "@/features/sensory/SensoryProvider";
import { t } from "@/lib/i18n/i18n";
import { useI18nState } from "@/lib/i18n/I18nProvider";
import { useMediaQuery } from "@/lib/useMediaQuery";
import { OfflineBanner } from "@/pwa/OfflineBanner";
import { UpdateNotice } from "@/pwa/UpdateNotice";

const DESKTOP_QUERY = "(min-width: 768px)";

/** Title and focus housekeeping for client-side navigations. */
function useRouteAnnouncements(): void {
  const { pathname } = useLocation();
  const { language } = useI18nState();
  const firstRender = useRef(true);

  useEffect(() => {
    document.title = `${routeTitle(pathname)} · ${t("app.name")}`;
  }, [pathname, language]);

  useEffect(() => {
    if (firstRender.current) {
      // A fresh page load keeps the browser's own focus and scroll behaviour.
      firstRender.current = false;
      return;
    }
    const main = document.getElementById("main");
    if (main === null) {
      return;
    }
    // Child effects run first: a page that focused something on purpose wins.
    if (!main.contains(document.activeElement)) {
      main.focus({ preventScroll: true });
      window.scrollTo({ top: 0, behavior: "instant" });
    }
  }, [pathname]);
}

export function AppShell({ children }: { children: ReactNode }): ReactElement {
  // The first paint assumes desktop (the wider layout), then corrects itself.
  const isDesktop = useMediaQuery(DESKTOP_QUERY, true);
  const { overwhelmed } = useSensory();
  useRouteAnnouncements();

  return (
    <div className="min-h-screen bg-bg text-ink">
      <a className="skip-link" href="#main" data-print="hide">
        {t("app.skipLink")}
      </a>

      <Header />
      <OfflineBanner />
      <UpdateNotice />
      <OutboxSync />

      {overwhelmed ? <OverwhelmedBar /> : null}

      <div className="app-content mx-auto flex w-full max-w-6xl gap-8 px-4 pb-28 pt-6 md:pb-14">
        {/* Overwhelmed mode removes the full navigation and leaves only the
            four destinations in the bar above. The current page stays put. */}
        {isDesktop && !overwhelmed ? <SideNav /> : null}
        <main id="main" tabIndex={-1} className="min-w-0 flex-1">
          {children}
        </main>
      </div>

      {isDesktop || overwhelmed ? null : <BottomNav />}

      <footer className="border-t border-border bg-surface" data-print="hide">
        <div className="mx-auto max-w-6xl px-4 py-6 text-sm text-ink-muted">
          {/* Day 30 positioning: stays until the clinical review
              (docs/clinical-review.md) is complete. */}
          <p className="font-semibold text-ink">{t("app.footerPortfolio")}</p>
          <p className="mt-1">{t("app.footerDisclaimer")}</p>
          <p className="mt-1">{t("app.footerPrivacy")}</p>
          <p className="mt-3">
            <Link
              to="/accessibility"
              className="inline-flex min-h-11 items-center font-semibold text-ink underline underline-offset-4"
            >
              {t("app.footerAccessibility")}
            </Link>
          </p>
        </div>
      </footer>
    </div>
  );
}
