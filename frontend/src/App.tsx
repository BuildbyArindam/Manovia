/**
 * Application root: providers, then the router.
 *
 * Onboarding is a gate, not a route: until the agreements are recorded the app
 * renders the onboarding flow instead of the shell, so no surface (chat, journal,
 * mood) can be reached without them.
 *
 * Day 25 (low-bandwidth & offline):
 * - Primary bottom-bar surfaces (`HomePage`, `ChatPage`, `MoodPage`,
 *   `ExercisesPage`, `SettingsPage`) are part of the core shell so tab switches
 *   have zero layout shift (CLS = 0) and work synchronously offline.
 * - Heavy secondary routes (`InsightsPage` + Recharts, `JournalPage`,
 *   `CBTPage`, `AssessmentsPage`, `AccessibilityPage`) are code-split via
 *   `React.lazy` so a low-end phone on a slow connection never downloads the
 *   charting engine or long-form tools until opened.
 * - Once mounted, `warmOfflineResources` prefetches the public crisis helplines
 *   and guided exercises into TanStack Query + local offline cache.
 */

import { Suspense, lazy, useEffect, useState, type ReactElement } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { QueryClientProvider, useQueryClient } from "@tanstack/react-query";

import { AppShell } from "@/components/AppShell";
import { fetchExercises } from "@/features/exercises/endpoints";
import { api } from "@/lib/api";
import { fetchCrisisResources } from "@/lib/endpoints";
import { t } from "@/lib/i18n/i18n";
import { I18nProvider } from "@/lib/i18n/I18nProvider";
import { warmOfflineResources } from "@/lib/pwa/offlineCache";
import { createQueryClient } from "@/lib/queryClient";
import { OnboardingFlow } from "@/onboarding/OnboardingFlow";
import { OnboardingProvider, useOnboarding } from "@/onboarding/OnboardingProvider";
import { ChatPage } from "@/pages/ChatPage";
import { ExercisesPage } from "@/pages/ExercisesPage";
import { HomePage } from "@/pages/HomePage";
import { MoodPage } from "@/pages/MoodPage";
import { NotFoundPage } from "@/pages/NotFoundPage";
import { SettingsPage } from "@/pages/SettingsPage";
import { ThemeProvider } from "@/theme/ThemeProvider";

const loadJournalPage = () =>
  import("@/pages/JournalPage").then((module) => ({ default: module.JournalPage }));
const loadCBTPage = () =>
  import("@/pages/CBTPage").then((module) => ({ default: module.CBTPage }));
const loadAssessmentsPage = () =>
  import("@/pages/AssessmentsPage").then((module) => ({ default: module.AssessmentsPage }));
const loadInsightsPage = () =>
  import("@/pages/InsightsPage").then((module) => ({ default: module.InsightsPage }));
const loadAccessibilityPage = () =>
  import("@/pages/AccessibilityPage").then((module) => ({
    default: module.AccessibilityPage,
  }));

const JournalPage = lazy(loadJournalPage);
const CBTPage = lazy(loadCBTPage);
const AssessmentsPage = lazy(loadAssessmentsPage);
const InsightsPage = lazy(loadInsightsPage);
const AccessibilityPage = lazy(loadAccessibilityPage);

function PageFallback(): ReactElement {
  return (
    <p
      role="status"
      aria-live="polite"
      className="min-h-[60vh] rounded-2xl border border-border bg-surface p-6 text-ink-muted shadow-soft"
    >
      {t("app.loading")}
    </p>
  );
}

function AppRoutes(): ReactElement {
  const { isComplete } = useOnboarding();
  const queryClient = useQueryClient();

  useEffect(() => {
    if (import.meta.env.MODE === "test") {
      return undefined;
    }
    void warmOfflineResources(
      queryClient,
      () => fetchCrisisResources(api),
      () => fetchExercises(api),
    );
    const timer = window.setTimeout(() => {
      void Promise.allSettled([loadJournalPage(), loadAccessibilityPage()]);
    }, 300);
    return () => {
      window.clearTimeout(timer);
    };
  }, [queryClient]);

  if (!isComplete) {
    return <OnboardingFlow />;
  }

  return (
    <AppShell>
      <Suspense fallback={<PageFallback />}>
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/chat" element={<ChatPage />} />
          <Route path="/mood" element={<MoodPage />} />
          <Route path="/journal" element={<JournalPage />} />
          <Route path="/cbt" element={<CBTPage />} />
          <Route path="/assessments" element={<AssessmentsPage />} />
          <Route path="/exercises" element={<ExercisesPage />} />
          <Route path="/insights" element={<InsightsPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/accessibility" element={<AccessibilityPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </Suspense>
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
