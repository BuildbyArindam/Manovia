import { QueryClient } from "@tanstack/react-query";

import { ApiError } from "@/lib/api";

/**
 * TanStack Query defaults tuned for a calm, low-bandwidth client: content is
 * revalidated rarely, background refetches do not fire on every window focus,
 * and a failed request is retried once — never for a 4xx, where retrying cannot
 * help and only delays the honest error.
 */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 5 * 60 * 1000,
        gcTime: 30 * 60 * 1000,
        refetchOnWindowFocus: false,
        retry: (failureCount, error) => {
          if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
            return false;
          }
          return failureCount < 1;
        },
      },
      mutations: {
        retry: false,
      },
    },
  });
}
