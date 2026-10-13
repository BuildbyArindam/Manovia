# Day 28: manual test list

These checks need things the build sandbox does not have: Docker, a real
browser with a display, real phones, a licensed clinician, or a native speaker.
Nothing here was run in the Day 28 sandbox. Each item says what to do, what
"pass" looks like, and who should do it. Record the date, the person and the
result in the table at the bottom.

## 1. Docker stack (`docker compose up`)

Needs Docker Desktop, or Docker Engine with the compose plugin.

| Step | Command | Pass when |
| --- | --- | --- |
| 1.1 | `make up` | Build finishes. `docker compose ps` shows the api, web and postgres services running, and postgres healthy. |
| 1.2 | Open http://localhost:8000/api/v1/ready | Returns a ready response, not 5xx. |
| 1.3 | Open http://localhost:8000/docs | The interactive docs load, and the operations match [docs/api.md](../api.md). |
| 1.4 | `docker compose logs api` | Migrations run on start in development. The logs contain no message text (search the logs for a test word you typed). |
| 1.5 | `make down`, then `make up` | The database survives the restart. |

## 2. Smoke test (`make smoke`)

| Step | Command | Pass when |
| --- | --- | --- |
| 2.1 | `make smoke` (it starts the stack itself unless `SMOKE_SKIP_COMPOSE=1`) | `scripts/smoke.sh` reports readiness, a guest session, consent accepted, and the web app returning 200, and exits 0. |
| 2.2 | Repeat 2.1 with the stack already up | Still exits 0. The script does not leave state that breaks the next run. |

## 3. Production-shaped stack (`make prod-up`)

Needs a `.env` file with real values for `SECRET_KEY` and `FIELD_ENCRYPTION_KEY`.

| Step | Command | Pass when |
| --- | --- | --- |
| 3.1 | Start `make prod-up` **without** the two secrets | Compose refuses to start and names the missing variable. |
| 3.2 | Start with generated secrets in `.env` | The `migrate` service runs once and exits 0. The api waits for it and starts. |
| 3.3 | `make prod-migrate` | Runs again with no error (idempotent). |
| 3.4 | `make prod-down` | Stops the stack and keeps the volume. |

## 4. Screenshot regeneration

Uses Chromium 153 through `@sparticuz/chromium@153.0.0`, as described in
[docs/assets/README.md](../assets/README.md).

| Step | Command | Pass when |
| --- | --- | --- |
| 4.1 | `cd frontend && CAPTURE_SCREENSHOTS=1 npx playwright test -c playwright.prod.config.ts screenshots` | The run finishes. Files in `docs/assets/` are rewritten. |
| 4.2 | Open each regenerated image | Synthetic content only, no real personal data, and the README alt text still describes it. |
| 4.3 | `git diff --stat docs/assets` | Only the expected files changed. |

## 5. Browser checks that need a display

| Step | Command | Pass when |
| --- | --- | --- |
| 5.1 | `npm run test:e2e:prod` (in `frontend/`) | axe, keyboard, zoom, offline and installability checks pass. |
| 5.2 | `npm run lighthouse` | Mobile budgets pass. |
| 5.3 | `npm run budget` | Bundle budgets pass. |

## 6. Phones and real devices

| Step | Pass when |
| --- | --- |
| 6.1 | Install the PWA on an Android phone and on an iPhone. The app opens from the home screen. |
| 6.2 | Turn on airplane mode. "Need help now" shows the saved helplines with the offline note. Breathing exercises still run. |
| 6.3 | Check in while offline. The confirmation says it will be sent when online. Go online with Manovia open. The check-in is sent once. |
| 6.4 | Use a screen reader (VoiceOver or TalkBack) to read the crisis card and the check-in form. Each control has a sensible name. |

## 7. Human review (cannot be done by the sandbox)

| Step | Who | Pass when |
| --- | --- | --- |
| 7.1 | Licensed clinician | Each item in [docs/clinical-review.md](../clinical-review.md) is marked reviewed, with a name, date and any changes. |
| 7.2 | Native Hindi speaker and a mental-health advisor | The `hi` crisis copy and safe replies are approved, or changes are recorded in `translations/STATUS.md`. |
| 7.3 | Native Bengali speaker | Same as 7.2 for `bn`. |
| 7.4 | Someone who makes the calls | Phone each helpline in [helplines.json](../../backend/app/content/helplines.json) that is flagged `needs_verification` (KIRAN, iCall, AASRA, Kids Helpline Australia). Confirm the number, the hours and that the line is staffed. Update the entry and `last_verified`. |
| 7.5 | Maintainer | Decide the open items in PROGRESS: the LICENSE copyright line, private vulnerability reporting on GitHub, the age policy, the stream query-string issue, and the coverage gate (tests or floor). |

## 8. Demo recording

| Step | Pass when |
| --- | --- |
| 8.1 | Record [docs/demo-script.md](../demo-script.md) on a fresh guest account with synthetic text. Time each section against the shot list. |
| 8.2 | The narration avoids every line the script lists under "Lines to avoid". |

## Results

| Section | Date | Who | Result | Notes |
| --- | --- | --- | --- | --- |
|  |  |  |  |  |
