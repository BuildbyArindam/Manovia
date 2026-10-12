/**
 * Application root: providers, then the router.
 *
 * Onboarding is a gate, not a route: until the agreements are recorded the app
 * renders the onboarding flow instead of the shell, so no surface (chat, journal,
 * mood) can be reached without them.
 */

import { Suspense, lazy, useState, type ReactElement } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { QueryClientProvider } from "@tanstack/react-query";

import { AppShell } from "@/components/AppShell";
import { t } from "@/lib/i18n/i18n";
import { I18nProvider } from "@/lib/i18n/I18nProvider";
import { OnboardingProvider, useOnboarding } from "@/onboarding/OnboardingProvider";
import { OnboardingFlow } from "@/onboarding/OnboardingFlow";
import { createQueryClient } from "@/lib/queryClient";
import { AssessmentsPage } from "@/pages/AssessmentsPage";
import { CBTPage } from "@/pages/CBTPage";
import { ChatPage } from "@/pages/ChatPage";
import { ExercisesPage } from "@/pages/ExercisesPage";
import { HomePage } from "@/pages/HomePage";
import { JournalPage } from "@/pages/JournalPage";
import { MoodPage } from "@/pages/MoodPage";
import { NotFoundPage } from "@/pages/NotFoundPage";
import { SettingsPage } from "@/pages/SettingsPage";
import { ThemeProvider } from "@/theme/ThemeProvider";

/**
 * Insights is the one page loaded lazily, and the reason is bundle size: the
 * chart library it needs is about as large as the rest of the application put
 * together. Everybody pays for it on first load if it is imported eagerly, and
 * most people open the chat first. The split costs one small fallback and a
 * round trip the first time somebody opens this page.
 */
const InsightsPage = lazy(() =>
  import("@/pages/InsightsPage").then((module) => ({ default: module.InsightsPage })),
);

function PageFallback(): ReactElement {
  return (
    <p className="rounded-2xl border border-border bg-surface p-6 text-ink-muted shadow-soft">
      {t("app.loading")}
    </p>
  );
}

function AppRoutes(): ReactElement {
  const { isComplete } = useOnboarding();

  if (!isComplete) {
    return <OnboardingFlow />;
  }

  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/mood" element={<MoodPage />} />
        <Route path="/journal" element={<JournalPage />} />
        <Route path="/cbt" element={<CBTPage />} />
        <Route path="/assessments" element={<AssessmentsPage />} />
        <Route path="/exercises" element={<ExercisesPage />} />
        <Route
          path="/insights"
          element={
            <Suspense fallback={<PageFallback />}>
              <InsightsPage />
            </Suspense>
          }
        />
        <Route path="/settings" element={<SettingsPage />} />
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
