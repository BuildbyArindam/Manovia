# Manovia runbook

What to do when the pager goes off, written for the person half-awake. First
principles: this is a **self-help wellbeing companion** — people may be mid-
conversation during a crisis when it breaks. Availability of the crisis path
matters more than any feature; privacy constraints hold during incidents too
(never paste user content into tickets, Sentry, or Slack).

Triage cheat-sheet:

| Symptom | First check | Page |
| --- | --- | --- |
| API 5xx / down | `fly status`, Sentry issue, `/api/v1/health` | [API down / DB outage](#db-outage) |
| Replies feel canned, `llm_fallbacks_total` climbing | provider status, metrics | [LLM outage](#llm-outage) |
| `output_guard_total{outcome=blocked\|error}` spike | metrics, Sentry | [Safety incidents](#safety-incident) |
| A helpline number is wrong | content review flow | [Helpline data change](#helpline-data-change) |
| Key leaked / must rotate | this page | [Key rotation](#key-rotation) |
| Something else bad | [Incident response](#incident-response) | below |

Observability pointers: metrics live at `/metrics` (Prometheus format; labels
are route templates and closed-set categories only), errors in Sentry
(PII-scrubbed), request logs are structured JSON on stdout (`fly logs`).
Useful starting queries:

- error rate: `sum(rate(manovia_http_requests_total{status=~"5.."}[5m])) / sum(rate(manovia_http_requests_total[5m]))`
- LLM fallback rate: `sum(rate(manovia_llm_fallbacks_total[5m])) / sum(rate(manovia_llm_requests_total[5m]))`
- guard block rate: `sum(rate(manovia_output_guard_total{outcome="blocked"}[1h])) / sum(rate(manovia_output_guard_total[1h]))`
- safety events by level: `sum by (level) (rate(manovia_safety_events_total[1h]))`
- p95 latency: `histogram_quantile(0.95, sum by (le) (rate(manovia_http_request_seconds_bucket[5m])))`

## LLM outage

**Symptoms:** `llm_fallbacks_total{reason="provider_failed"}` rising;
`llm_requests_total{provider="canned",position="2"}` growing; users see the
pre-written supportive templates instead of generated replies.

**Why this is never a 500:** the chain is anthropic → ollama → canned
(ADR 0010); the terminal link cannot fail, and SSE responses still stream.
What degrades is richness, not availability — and the UI says the companion
is on a spare wheel.

1. Confirm scope: `curl -s https://<app>.fly.dev/api/v1/health` (is it the
   API or just the provider?). Check `manovia_llm_fallbacks_total` by
   `reason` — `provider_failed` = vendor trouble; `not_configured` = a
   misconfigured deploy (a secret got dropped — check `fly secrets list`).
2. Vendor status page (Anthropic status). If an incident is declared: no
   action needed; the system is designed for this. Note the fallback rate in
   the incident log.
3. `provider_crashed` climbing with no vendor incident: check Sentry — a
   broken provider integration (SDK upgrade?) ships as crashes. Mitigation:
   `fly deploy` of the last good release; file the bug with the scrubbed
   Sentry event.
4. Do **not** lower `LLM_TIMEOUT_SECONDS` or bypass the output guard to make
   replies richer during an outage. The guard runs regardless of provider;
   weakening it during an incident is how a bad day becomes a safety one.

## DB outage

**Symptoms:** `/api/v1/ready` returns 503 (`database` check failed) while
`/api/v1/health` stays 200; requests touching persistence fail; Fly's
liveness check keeps passing (deliberate — see fly.toml).

1. `fly status` and `fly postgres status manovia-db`. Most cases: the managed
   database is restarting or failed over — wait for it, the app recovers on
   the next request (pool reconnects).
2. Disk full: `fly postgres ... volumes list`; grow the volume.
3. Real data loss: follow the restore procedure in docs/backups.md (stop
   writes first, restore the newest verified dump into a scratch, verify row
   counts, then repoint). RPO 24 h with the nightly dump; say so plainly in
   the incident note.
4. While the DB is down the crisis path still answers: HIGH/IMMINENT triage
   is in-process (rules engine + pre-written replies with helplines) and does
   not need the database. Do not "fix" the outage by restarting machines in a
   loop — that interrupts the people mid-crisis-reply.

## Helpline data change

Helplines live **only** in `backend/app/content/helplines.json`, validated by
`helplines.schema.json` (imported at startup — a malformed file refuses to
boot by design), with a `last_verified` date per entry.

1. Change goes through a PR that (a) updates the entry, (b) updates
   `last_verified`, (c) notes the source (official site, verified call).
2. Tests: the crisis-content tests pin the primary numbers (Tele-MANAS 14416
   / 1800-891-4416, KIRAN 1800-599-0019, 112) — update the pinned fixtures in
   the same PR if and only if the number itself changed.
3. Never edit crisis reply *wording* casually: safe replies are reviewed copy
   (docs/safety-design.md). A wording change is a content review, not a
   hotfix.
4. Deploy as usual; verify one crisis fixture end-to-end on the staging or
   preview environment before production (`fly deploy`, then send a pinned
   HIGH message from the eval corpus — `evals/datasets/crisis_cases_dev.jsonl`
   — through a guest session and read the helplines off the card).

## Key rotation

Two distinct secrets, two procedures. Both live in `fly secrets`; neither is
ever in the repo.

**JWT signing key (`SECRET_KEY`)** — rotating logs everyone out (tokens are
short-lived; refresh tokens die with the old key). Low risk:

```sh
fly secrets set SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(64))')"
fly deploy            # secrets changes restart machines
```

Do it off-peak; announce; done. If the key *leaked*: rotate immediately and
treat all sessions as compromised (they are), check Sentry/metrics for odd
auth patterns first.

**Field encryption key (`FIELD_ENCRYPTION_KEY`)** — this one protects every
encrypted column; rotating wrong loses people's journals. Use the Day-22
tooling, never a hand-rolled swap:

1. Generate the new key. Build a keyring:
   `FIELD_ENCRYPTION_KEY="<new_id>:<new_key>,v1:<old_key>"` (first entry is
   active; old keys decrypt only).
2. `fly secrets set FIELD_ENCRYPTION_KEY="..."` — new writes use the new id;
   old rows still read via the previous keys.
3. Re-encrypt: `scripts/rotate_encryption_keys.py` (see its docstring) in a
   maintenance window; it re-writes rows under the active key and reports
   progress content-free.
4. After the re-encrypt reports zero rows on old keys, drop the old key from
   the keyring in a later change. Keep the old key in the platform secret
   history (Sentry-free, access-logged) until the next verified backup passes
   a drill.
5. Drill after any rotation: restore the newest dump against a scratch with
   the **new** keyring configured and confirm counts (docs/backups.md).

If `FIELD_ENCRYPTION_KEY` is missing entirely, the app refuses to write
plaintext and encrypted reads fail loudly — that is the designed failure; the
fix is restoring the secret, not generating a fresh one (fresh key = orphaned
ciphertext).

## Incident response

1. **Declare** (a note in the incident channel with the template: symptom,
   start time, current impact, on-call). No blame in the channel; facts only.
2. **Stabilise** — the fastest safe action first: `fly deploy` of the last
   good release, or `fly machines restart`. Never weaken a safety or privacy
   control to stabilise (no disabling the output guard, no
   `LLM_REDACT_PII=false`, no raising log verbosity to DEBUG in production —
   logs must not start carrying user text during an incident).
3. **Communicate** only what is true and useful; never include user data in
   any channel. If personal data was exposed, that is a privacy incident —
   follow the notification obligations in docs/privacy-and-threat-model.md
   and say so in the declaration.
4. **Verify recovery**: `/api/v1/health` 200, `/api/v1/ready` 200, error-rate
   query back to baseline, one chat turn end-to-end on a guest account.
5. **Follow-up within a week**: a written note (docs/verification/ style) —
   timeline, root cause, what regressed, the test or gate that now catches
   it. Every incident ends with either a regression test or an accepted,
   documented risk.

## Safety incident

Anything where a person may have been harmed, may be at risk, or the safety
pipeline itself misfired: a crisis message got no helplines, the guard passed
something it should have caught, an unsafe reply reached a user, the ML
classifier silently stopped loading, or a helpline number was wrong in
production. **These outrank every other incident.**

1. **Freeze the pipeline evidence, do not debug with user data.** Pull the
   Sentry issue and the metric series (`safety_events_total` by level/source,
   `output_guard_total` by outcome) around the window. These are
   content-free by design — that is what you triage on. Do not go read chat
   rows; decrypted content stays out of incident channels (and out of your
   terminal). If a specific user needs help *right now*, the contact route
   is the account's own data via the privacy export **with the user's
   consent**, not operator browsing.
2. **Contain:** if a misfire is live (guard broken, classifier erroring),
   remember the design: a guard *error* fails closed (safe template, not the
   raw model text); a missing ML artifact degrades to rules-only. If rules
   themselves are broken, deploy the last good release immediately
   (`fly deploy --image ghcr.io/.../manovia-api:<last-good-tag>`).
3. **Assess exposure:** exactly which replies went out unscreened, over which
   window (`http_requests_total` on chat routes, `chat_turn` log lines are
   content-free but count the turns). Bound the number.
4. **Escalate to a human reviewer** (the project's mental-health advisor)
   the same day with the bounded description — categories, counts, versions —
   never raw transcripts. If any person is potentially at risk of imminent
   harm and the product failed them, surface that to the advisor and legal
   contact immediately; that decision is not the on-call engineer's to sit on.
5. **Fix forward** with the eval harness as the gate: reproduce the miss in
   `evals/datasets/` (a case that must be caught), make it fail, fix the
   pattern/pipeline, `make eval` green (crisis recall gate ≥ 0.97), then
   deploy. Changing the *policy* (thresholds, escalation) additionally needs
   `FEATURE_ML_THRESHOLDS_UNLOCKED=true` and a PROGRESS.md note — that is a
   reviewed decision, not a hotfix.
6. **Close** with the incident note from the generic procedure above, plus
   the advisor's sign-off. Update docs/safety-design.md if the threat model
   changed.

## Appendix: what NOT to do (checklist)

- Do not log or paste user text anywhere during an incident.
- Do not disable the output guard, PII redaction, rate limits, or the origin
  guard "temporarily" in production.
- Do not run migrations by restarting containers (`RUN_MIGRATIONS` stays
  false; migrations ride the release step).
- Do not test crisis flows by writing real-sounding crisis text into the
  production database; use the pinned fixtures from the eval corpus on a
  guest account and delete nothing (the retention job owns deletion).
