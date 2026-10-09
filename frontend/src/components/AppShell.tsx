/**
 * The application shell: header, navigation, main landmark, footer.
 *
 * One navigation, two presentations — a side rail on desktop and a bottom bar on
 * mobile — chosen by a media query rather than by CSS alone, so the rendered
 * markup matches what the user can actually reach (and so the behaviour is
 * testable). Landmarks are explicit: `header`/`nav`/`main`/`footer`, plus a
 * skip link that is the first focusable element on the page.
 */

import type { ReactElement, ReactNode } from "react";

import { BottomNav } from "@/components/BottomNav";
import { Header } from "@/components/Header";
import { SideNav } from "@/components/SideNav";
import { useMediaQuery } from "@/lib/useMediaQuery";

const DESKTOP_QUERY = "(min-width: 768px)";

export function AppShell({ children }: { children: ReactNode }): ReactElement {
  // The first paint assumes desktop (the wider layout), then corrects itself.
  const isDesktop = useMediaQuery(DESKTOP_QUERY, true);

  return (
    <div className="min-h-screen bg-bg text-ink">
      <a className="skip-link" href="#main">
        Skip to main content
      </a>

      <Header />

      <div className="mx-auto flex w-full max-w-6xl gap-8 px-4 pb-28 pt-6 md:pb-14">
        {isDesktop ? <SideNav /> : null}
        <main id="main" tabIndex={-1} className="min-w-0 flex-1">
          {children}
        </main>
      </div>

      {isDesktop ? null : <BottomNav />}

      <footer className="border-t border-border bg-surface">
        <div className="mx-auto max-w-6xl px-4 py-6 text-sm text-ink-muted">
          <p>
            Manovia is a self-help companion — not therapy, not a diagnosis, and not a crisis
            service.
          </p>
          <p className="mt-1">
            Your entries are encrypted at rest. Nothing here is sold, shared, or used to train
            anyone else&rsquo;s model.
          </p>
        </div>
      </footer>
    </div>
  );
}
