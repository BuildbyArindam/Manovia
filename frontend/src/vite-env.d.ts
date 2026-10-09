/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Where the API lives. Defaults to `/api/v1`, which the dev server proxies. */
  readonly VITE_API_BASE_URL?: string;
  /** Backend origin the dev-server proxy forwards `/api` to. */
  readonly VITE_API_PROXY_TARGET?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
