import { render, type RenderResult } from "@testing-library/react";
import type { ReactElement } from "react";
import { QueryClientProvider } from "@tanstack/react-query";
import type { QueryClient } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { OnboardingProvider } from "@/onboarding/OnboardingProvider";
import { createQueryClient } from "@/lib/queryClient";
import { ThemeProvider } from "@/theme/ThemeProvider";

export interface RenderOptions {
  route?: string;
  queryClient?: QueryClient;
}

/** Render with every provider the app uses, inside a memory router. */
export function renderWithProviders(
  ui: ReactElement,
  options: RenderOptions = {},
): RenderResult & { queryClient: QueryClient } {
  const queryClient = options.queryClient ?? createQueryClient();
  const result = render(
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <MemoryRouter
          initialEntries={[options.route ?? "/"]}
          future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
        >
          <OnboardingProvider>{ui}</OnboardingProvider>
        </MemoryRouter>
      </QueryClientProvider>
    </ThemeProvider>,
  );
  return { ...result, queryClient };
}
