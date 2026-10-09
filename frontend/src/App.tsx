/**
 * Application root: providers, then the router.
 *
 * Onboarding is a gate, not a route: until the agreements are recorded the app
 * renders the onboarding flow instead of the shell, so no surface (chat, journal,
 * mood) can be reached without them.
 */

import { useState, type ReactElement } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { QueryClientProvider } from "@tanstack/react-query";

import { AppShell } from "@/components/AppShell";
import { OnboardingProvider, useOnboarding } from "@/onboarding/OnboardingProvider";
import { OnboardingFlow } from "@/onboarding/OnboardingFlow";
import { createQueryClient } from "@/lib/queryClient";
import { ChatPage } from "@/pages/ChatPage";
import { ExercisesPage } from "@/pages/ExercisesPage";
import { InsightsPage } from "@/pages/InsightsPage";
import { JournalPage } from "@/pages/JournalPage";
import { MoodPage } from "@/pages/MoodPage";
import { NotFoundPage } from "@/pages/NotFoundPage";
import { SettingsPage } from "@/pages/SettingsPage";
import { ThemeProvider } from "@/theme/ThemeProvider";

function AppRoutes(): ReactElement {
  const { isComplete } = useOnboarding();

  if (!isComplete) {
    return <OnboardingFlow />;
  }

  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<Navigate replace to="/chat" />} />
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/mood" element={<MoodPage />} />
        <Route path="/journal" element={<JournalPage />} />
        <Route path="/exercises" element={<ExercisesPage />} />
        <Route path="/insights" element={<InsightsPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </AppShell>
  );
}

export function App(): ReactElement {
  const [queryClient] = useState(createQueryClient);

  return (
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
          <OnboardingProvider>
            <AppRoutes />
          </OnboardingProvider>
        </BrowserRouter>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
