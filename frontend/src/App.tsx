/**
 * Application root: providers, then the router.
 *
 * Onboarding is a gate, not a route: until the agreements are recorded the app
 * renders the onboarding flow instead of the shell, so no surface (chat, journal,
 * mood) can be reached without them. The one exception is the accessibility
 * statement (Day 25): whether the app will work for you is something you should
 * be able to read *before* agreeing to anything.
 *
 * Code splitting (Day 25). Only the home screen ships in the first download;
 * every other page is its own chunk, fetched when it is first opened. On a
 * low-end phone on a weak connection the first screen is what the person is
 * waiting for, and parsing code for eight pages they have not opened yet is the
 * single largest cost of getting there. Once the service worker has installed,
 * every chunk except Insights is precached, so later navigations — and offline
 * use — do not wait for the network at all.
 */

import {
  Component,
  type ComponentType,
  Suspense,
  lazy,
  useState,
  type ErrorInfo,
  type ReactElement,
  type ReactNode,
} from "react";
import { BrowserRouter, Route, Routes, useLocation } from "react-router-dom";
import { QueryClientProvider } from "@tanstack/react-query";

import { AppShell } from "@/components/AppShell";
import type { ApiClient } from "@/lib/api";
import { t } from "@/lib/i18n/i18n";
import { I18nProvider } from "@/lib/i18n/I18nProvider";
import { OnboardingProvider, useOnboarding } from "@/onboarding/OnboardingProvider";
import { OnboardingFlow } from "@/onboarding/OnboardingFlow";
import { createQueryClient } from "@/lib/queryClient";
import { HomePage } from "@/pages/HomePage";
// Tiny, and the answer to a mistyped address should not need a download.
import { NotFoundPage } from "@/pages/NotFoundPage";
import { ThemeProvider } from "@/theme/ThemeProvider";

/**
 * `lazy()` for a named export, which is how every page in this app is written.
 * Pages take only optional props (an injectable client for tests), so the
 * router renders them with none.
 */
type PageComponent = ComponentType<{ client?: ApiClient }>;
function lazyPage<K extends string>(load: () => Promise<Record<K, PageComponent>>, name: K) {
  return lazy(() => load().then((module) => ({ default: module[name] })));
}

const ChatPage = lazyPage(() => import("@/pages/ChatPage"), "ChatPage");
const MoodPage = lazyPage(() => import("@/pages/MoodPage"), "MoodPage");
const JournalPage = lazyPage(() => import("@/pages/JournalPage"), "JournalPage");
const CBTPage = lazyPage(() => import("@/pages/CBTPage"), "CBTPage");
const AssessmentsPage = lazyPage(() => import("@/pages/AssessmentsPage"), "AssessmentsPage");
const ExercisesPage = lazyPage(() => import("@/pages/ExercisesPage"), "ExercisesPage");
/**
 * Insights carries the chart library — as large as the rest of the app put
 * together — and is the one page left out of the offline precache.
 */
const InsightsPage = lazyPage(() => import("@/pages/InsightsPage"), "InsightsPage");
const SettingsPage = lazyPage(() => import("@/pages/SettingsPage"), "SettingsPage");
const AccessibilityPage = lazyPage(() => import("@/pages/AccessibilityPage"), "AccessibilityPage");

function PageFallback(): ReactElement {
  return (
    <p
      role="status"
      className="rounded-2xl border border-border bg-surface p-6 text-ink-muted shadow-soft"
    >
      {t("app.loading")}
    </p>
  );
}

/**
 * A page chunk that cannot be fetched (offline before it was ever cached, or a
 * deploy that removed the old file) must not blank the whole app. The header —
 * with "Need help now?" — stays, and the page says what happened.
 */
class RouteErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  override state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    console.warn("Manovia: a page could not be loaded", error.name, info.componentStack?.length);
  }

  override render(): ReactNode {
    if (!this.state.failed) {
      return this.props.children;
    }
    const offline = typeof navigator !== "undefined" && navigator.onLine === false;
    return (
      <div
        role="alert"
        data-testid="route-load-error"
        className="space-y-3 rounded-2xl border border-border-strong bg-surface p-6"
      >
        <h1 className="text-2xl">
          {offline ? t("pwa.routeOfflineTitle") : t("pwa.routeErrorTitle")}
        </h1>
        <p className="text-ink-muted">
          {offline ? t("pwa.routeOfflineBody") : t("pwa.routeErrorBody")}
        </p>
        <button
          type="button"
          className="btn-secondary"
          onClick={() => {
            window.location.reload();
          }}
        >
          {t("pwa.routeRetry")}
        </button>
      </div>
    );
  }
}

function Page({ children }: { children: ReactNode }): ReactElement {
  const location = useLocation();
  return (
    // Keyed by path: leaving a page that failed to load clears the failure.
    <RouteErrorBoundary key={location.pathname}>
      <Suspense fallback={<PageFallback />}>{children}</Suspense>
    </RouteErrorBoundary>
  );
}

function AppRoutes(): ReactElement {
  const { isComplete } = useOnboarding();
  const location = useLocation();

  if (!isComplete) {
    if (location.pathname === "/accessibility") {
      return (
        <main id="main" tabIndex={-1} className="mx-auto max-w-3xl px-4 py-8">
          <Page>
            <AccessibilityPage />
          </Page>
        </main>
      );
    }
    return <OnboardingFlow />;
  }

  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route
          path="/chat"
          element={
            <Page>
              <ChatPage />
            </Page>
          }
        />
        <Route
          path="/mood"
          element={
            <Page>
              <MoodPage />
            </Page>
          }
        />
        <Route
          path="/journal"
          element={
            <Page>
              <JournalPage />
            </Page>
          }
        />
        <Route
          path="/cbt"
          element={
            <Page>
              <CBTPage />
            </Page>
          }
        />
        <Route
          path="/assessments"
          element={
            <Page>
              <AssessmentsPage />
            </Page>
          }
        />
        <Route
          path="/exercises"
          element={
            <Page>
              <ExercisesPage />
            </Page>
          }
        />
        <Route
          path="/insights"
          element={
            <Page>
              <InsightsPage />
            </Page>
          }
        />
        <Route
          path="/settings"
          element={
            <Page>
              <SettingsPage />
            </Page>
          }
        />
        <Route
          path="/accessibility"
          element={
            <Page>
              <AccessibilityPage />
            </Page>
          }
        />
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </AppShell>
  );
}

export function App(): ReactElement {
  const [queryClient] = useState(createQueryClient);

  return (
    // I18nProvider sits outside everything: a language switch re-renders the
    // theme shell and the router alike, and no surface is exempt from it.
    <I18nProvider>
      <ThemeProvider>
        <QueryClientProvider client={queryClient}>
          <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
            <OnboardingProvider>
              <AppRoutes />
            </OnboardingProvider>
          </BrowserRouter>
        </QueryClientProvider>
      </ThemeProvider>
    </I18nProvider>
  );
}
