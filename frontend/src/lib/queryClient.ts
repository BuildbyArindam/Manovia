import { QueryClient } from "@tanstack/react-query";

import { ApiError } from "@/lib/api";

/**
 * TanStack Query defaults tuned for a calm, low-bandwidth client: content is
 * revalidated rarely, background refetches do not fire on every window focus,
 * and a failed request is retried once — never for a 4xx, where retrying cannot
 * help and only delays the honest error.
 *
 * Network modes (Day 25). By default TanStack Query does not even *try* a
 * request while `navigator.onLine` is false. With a service worker that is
 * wrong for queries: the worker can answer the helplines and the exercise
 * catalogue from its cache, so queries are `offlineFirst` — one real attempt,
 * then retries wait for the connection. Mutations are `always`: a write that
 * cannot be sent fails now, where the screen can say so (or, for mood
 * check-ins, queue it), instead of pausing invisibly and firing hours later.
 */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 5 * 60 * 1000,
        gcTime: 30 * 60 * 1000,
        refetchOnWindowFocus: false,
        networkMode: "offlineFirst",
        retry: (failureCount, error) => {
          if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
            return false;
          }
          return failureCount < 1;
        },
      },
      mutations: {
        retry: false,
        networkMode: "always",
      },
    },
  });
}
