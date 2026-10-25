# Publish Manovia for free — forever, no Docker on your laptop

This guide wires up **fully free, legal, lifetime hosting** without installing Docker.
Every build happens remotely (GitHub Actions / Render builder / Cloudflare). You only `git push`.

> Manovia is a **portfolio demo — not a clinical service**. Every deployment must keep
> the footer `Portfolio demo — not a clinical service` (see `docs/deploy.md §6`). This guide preserves it.

---

## Pick your path

| Path | Frontend (free forever) | Backend (free forever) | DB (free forever) | Needs credit card | Cold start |
|------|-------------------------|------------------------|-------------------|-------------------|------------|
| **A — Recommended** | GitHub Pages (`*.github.io`, no card) | Render Free (via `render.yaml`) | Neon Free (0.5 GB, always free) | No (Pages+Neon); Render asks card but stays $0 | ~30s after 15m idle |
| **B — One-click static** | Cloudflare Pages (unlimited, no card) | same Render+Neon | same | No | same |
| **C — Already in repo** | Netlify (`netlify.toml` shipped) | Fly.io (`fly.toml` shipped, needs card) | Fly Postgres | Yes | none (`min_machines_running=1`) |

**This doc covers Path A** end-to-end. Path B/C notes at the end.

---

## 0. What you get after this

- Frontend live at `https://buildbyarindam.github.io/Manovia/` — HTTPS, PWA installable, works offline for breathing + helplines. **Lifetime free**, no bandwidth limit that a portfolio will hit.
- Backend live at `https://manovia-api.onrender.com` — HTTPS, Postgres, migrations auto-run. **Lifetime free** (Render free spins down after 15m; first request wakes in ~30s — fine for a demo).
- No Docker installed locally — `backend/Dockerfile` is built on Render's remote builder. `frontend/` is built on GitHub Actions.

Custom domain? Buy `manovia.app` (~₹900/yr on Cloudflare Registrar) and add it to Pages — still need yearly renewal (no TLD is free for life). Subdomain `manovia.github.io` *is* free for life and fully legal.

---

## 1. Frontend → GitHub Pages (2 minutes)

### 1.1 Turn on Pages

1. GitHub → your repo `BuildbyArindam/Manovia` → **Settings → Pages**
2. **Build and deployment → Source:** `GitHub Actions` (not `Deploy from a branch`)
3. Done. The workflow is already committed at `.github/workflows/deploy-pages.yml`.

### 1.2 Push and go live

```sh
git push origin main
# → Actions tab → "Deploy frontend — GitHub Pages" → green → visit:
# https://buildbyarindam.github.io/Manovia/
```

- Build uses `VITE_BASE=/Manovia/` so assets resolve under the project subpath. For a user site (`<you>.github.io`) or custom domain, change the workflow env to `VITE_BASE=/`.
- SPA routes work: `dist/404.html` is a copy of `index.html` (committed by the workflow) — GitHub Pages serves it for deep links like `/Manovia/mood`.

### 1.3 Wire to a backend (optional now, recommended later)

Repo → **Settings → Secrets and variables → Actions → Variables** → **New variable:**

- **Name:** `VITE_API_BASE_URL`
- **Value:** `https://manovia-api.onrender.com/api/v1` (after you deploy the backend in §2)

Re-run the workflow (Actions → Run workflow). Frontend will now call the live API. Leave empty for a pure offline demo — the PWA still shows helplines + breathing exercises offline.

---

## 2. Database → Neon (free forever, 2 minutes)

Why Neon over Render Postgres? Render's free Postgres expires after 90 days; Neon Free is **always free** (0.5 GB storage, 3 projects, no expiry, pauses when idle).

1. Go to **neon.tech** → Sign up via GitHub → **New Project** → Region `Singapore` (closest to India)
2. Copy the connection string: `postgres://user:password@ep-xxx.neon.tech/neondb?sslmode=require`
3. Keep it — you paste it into Render next.

> Manovia speaks both `postgres://` and `postgresql+asyncpg://` — Neon’s string works as-is.

---

## 3. Backend → Render (3 minutes, no Docker on laptop)

### 3.1 Create the service from blueprint

1. **dashboard.render.com** → **New → Blueprint** → **Connect** GitHub repo `BuildbyArindam/Manovia`
2. Render reads `render.yaml` at repo root and shows service `manovia-api` (Docker runtime, free plan, health check `/api/v1/health`, preDeploy migrations)
3. Before clicking **Apply**, fill the secrets Render asks for:

| Variable | Value | How |
|----------|-------|-----|
| `DATABASE_URL` | `postgres://...` from Neon | paste |
| `SECRET_KEY` | random 64 chars | Render → Generate (button) or `python3 -c "import secrets; print(secrets.token_urlsafe(64))"` |
| `FIELD_ENCRYPTION_KEY` | Fernet key | `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` — run in Render Shell, or locally if you have Python |
| `ALLOWED_ORIGINS` | `https://buildbyarindam.github.io` | add `https://your-custom-domain` later if you buy one |

4. **Apply** → Render builds `backend/Dockerfile` **remotely** (no local Docker), runs `alembic upgrade head`, then starts the API.

### 3.2 Verify

```sh
curl https://manovia-api.onrender.com/api/v1/health
# → {"status":"ok"}

curl https://manovia-api.onrender.com/api/v1/ready
# → {"status":"ready","checks":{"config":"ok","database":"ok"},"app_env":"production"}
```

If `/ready` is 503, check Render logs → usually `DATABASE_URL` typo or missing `SECRET_KEY`.

### 3.3 Keep it free

- Free tier sleeps after 15m inactivity — first request wakes in ~30s. The frontend shows a loading state; this is expected for a portfolio demo.
- Neon also auto-pauses when idle — first query wakes instantly.
- Both stay **$0 forever** at portfolio traffic.

---

## 4. Connect frontend ↔ backend

1. After backend is live, set the GitHub repo variable `VITE_API_BASE_URL` (§1.3) → re-run Pages workflow.
2. In Render → `manovia-api` → Environment → `ALLOWED_ORIGINS` must list the Pages origin exactly (no trailing slash): `https://buildbyarindam.github.io`
   - Add `https://buildbyarindam.github.io/Manovia` is NOT needed — CORS checks origin, not path.
   - If you later add a custom domain, append it: `https://buildbyarindam.github.io,https://manovia.app`

That's it. The PWA’s `vite-plugin-pwa` precaches the shell; “Need help now” + breathing work offline even if Render is sleeping.

---

## 5. Custom domain for lifetime (optional)

You cannot get a free `.com/.app/.in` for life — ICANN charges registrars yearly. Options:

- **Stay on `*.github.io` / `*.pages.dev` / `*.netlify.app`** — legal, free for life, HTTPS, fine for portfolio/resume.
- **Buy a domain (~₹800–1000/yr):** Cloudflare Registrar → `manovia.app` → Pages/Render custom domain → add to `ALLOWED_ORIGINS`.
- **Free 1-year offers:** Cloudflare Registrar promos, Namecheap first-year promos, GitHub Student Pack — not lifetime.

> Avoid Freenom `.tk/.ml/.ga` — domains get reclaimed; they fail security reviews.

---

## 6. Alternative hosts (same repo, no extra work)

### Cloudflare Pages (instead of GitHub Pages)

1. dash.cloudflare.com → Pages → Connect GitHub → pick `Manovia`
2. Build: **Root `frontend`**, **Build `npm run build`**, **Output `dist`**
3. Env: `VITE_API_BASE_URL=/api/v1` and add Redirect: `/api/* → https://manovia-api.onrender.com/api/:splat 200` (same as `netlify.toml`)
4. Live at `*.pages.dev` — unlimited bandwidth, free forever.

### Netlify (already configured)

Your `netlify.toml` already has build + SPA redirect + `/api` proxy + security headers copied from `docker/security-headers.conf`. Just:

1. app.netlify.com → Add site → Import from GitHub → pick repo → Deploy
2. Update `[[redirects]] from="/api/*" to="https://manovia-api.onrender.com/api/:splat"` in `netlify.toml` (or keep Fly URL if using Fly)
3. Set `ALLOWED_ORIGINS` on Render/Fly to the Netlify URL.

### Fly.io backend (instead of Render)

Already in `fly.toml` / `docs/deploy.md`. Fly builds remotely too (`fly deploy` uses remote builder — no local Docker). Needs card, stays within free allowance at demo traffic, and doesn’t sleep.

---

## 7. What was added in this PR

- `.github/workflows/deploy-pages.yml` — builds `frontend/` with `VITE_BASE=/Manovia/` and `VITE_API_BASE_URL` from repo variables → deploys `frontend/dist` to Pages with SPA fallback.
- `frontend/vite.config.ts` — respects `VITE_BASE` env for correct asset paths on Pages subpath.
- `render.yaml` — Render blueprint for `manovia-api` (Docker runtime, `preDeployCommand: alembic upgrade head`, free plan).
- `docs/publish-free.md` — this file.

No secrets are committed. `.env.example` already documents every env var the app reads.

---

## 8. Checklist

- [ ] Settings → Pages → Source: GitHub Actions
- [ ] Push to `main` → check Actions → Pages URL live
- [ ] Neon project → copy `DATABASE_URL`
- [ ] Render Blueprint → paste secrets → Apply → `/health` returns ok
- [ ] GitHub repo variable `VITE_API_BASE_URL` → re-run Pages workflow
- [ ] Verify footer says “Portfolio demo — not a clinical service” on live site

Help? See `docs/deploy.md` (Fly+Netlify), `docs/runbook.md` (incidents), and `frontend/README.md` (build commands).

