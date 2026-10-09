# syntax=docker/dockerfile:1
# Manovia web image. Stage 1 builds the static bundle with Vite (which also
# typechecks: `npm run build` is `tsc --noEmit && vite build`); stage 2 serves
# it with nginx, which also proxies /api to the api service so the browser
# only ever talks to its own origin (no CORS, no localhost calls).

# ---------------------------------------------------------------------------
# Build stage.
FROM node:22-alpine AS build

WORKDIR /app

# Lockfile first: dependency installs stay cached until the manifest changes.
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci

COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------------------
# Runtime stage: static files behind nginx.
FROM nginx:1.27-alpine AS runtime

# SPA fallback + /api proxy + security headers (see docker/nginx.conf).
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

# busybox wget is the only HTTP client in the nginx:alpine image.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1/"]

# nginx runs its master process as root and workers as the nginx user; the
# standard image ships no unprivileged variant. The API container (the one
# handling data and secrets) is the one mandated to run non-root.
CMD ["nginx", "-g", "daemon off;"]
