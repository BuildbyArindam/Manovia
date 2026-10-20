# API reference

> **Generated file — do not edit by hand.** Run `python backend/scripts/export_openapi.py` from the repository root (or `cd backend && python scripts/export_openapi.py`) after changing a route. A test fails when this file is out of date.

Manovia API **1.0.0** · 93 operations across 81 paths. The machine-readable schema is [`docs/openapi.json`](openapi.json). The same schema is served live at `/openapi.json`, with interactive docs at `/docs`, when the API runs.

## Conventions

- **Base path.** Every route is under `/api/v1`, except the Prometheus scrape endpoint `/metrics`, which is served at the root when metrics are enabled.
- **Format.** JSON in and out. Streaming replies use `text/event-stream` (see the chat section).
- **Sessions.** A signed-in request sends `Authorization: Bearer <access token>`. Tokens come from `POST /api/v1/auth/guest`, `POST /api/v1/auth/login` or `POST /api/v1/auth/register`. The **Session** column says where the bearer token is required. `yes + consent` also needs the named consent before the route runs.
- **Refresh.** `POST /api/v1/auth/refresh` takes the refresh token in the JSON body and rotates it. Refresh tokens are not set as cookies. See [PROGRESS.md](../PROGRESS.md) for the open item on moving them out of client storage.
- **Errors.** Every error returns one envelope: `{"code": "...", "message": "...", "request_id": "..."}`. Validation failures (`422`) use the same envelope with `code: validation_error`. The `422` schema shown in the tables below is FastAPI's default, which the server does not return.
- **Privacy of the body.** Request bodies are not echoed in errors, and the logs carry no message text (see [docs/privacy-and-threat-model.md](privacy-and-threat-model.md)).

## Known documentation gaps

- The OpenAPI document does **not** declare a security scheme, so the bearer requirement appears only in the **Session** column and in this prose.
- There is no GET streaming route. A GET would carry the user's message in the query string, so the route was removed on Day 28. Streaming uses POST only.

## Contents

- [Health and readiness](#health-and-readiness)
- [Authentication](#authentication)
- [Consent](#consent)
- [Chat](#chat)
- [Knowledge base](#knowledge-base)
- [Mood check-ins](#mood-check-ins)
- [Guided exercises](#guided-exercises)
- [Journal](#journal)
- [CBT thought record](#cbt-thought-record)
- [Screenings (PHQ-9 and GAD-7)](#screenings-phq-9-and-gad-7)
- [Insights](#insights)
- [Crisis resources and assessment](#crisis-resources-and-assessment)
- [Daily plan](#daily-plan)
- [Settings](#settings)
- [Privacy, export and deletion](#privacy-export-and-deletion)
- [Development only (not in production)](#development-only-not-in-production)
- [Agent](#agent)
- [Meditation](#meditation)
- [Voice](#voice)
- [Drift](#drift)
- [Feature-Flags](#feature-flags)
- [Sharing](#sharing)
- [Sharing-Public](#sharing-public)

## Health and readiness

| Method | Path | Summary | Session | Status codes | Notes |
| --- | --- | --- | --- | --- | --- |
| `GET` | `/api/v1/health` | Health | no | 200 | Liveness probe: the process is up and serving requests. |
| `GET` | `/api/v1/ready` | Readiness | no | 200 | Readiness probe: configuration loads and the database answers. |

## Authentication

| Method | Path | Summary | Session | Status codes | Notes |
| --- | --- | --- | --- | --- | --- |
| `POST` | `/api/v1/auth/guest` | Create Guest Session | no | 201 | Create an anonymous account and sign it in. No email, no password. |
| `POST` | `/api/v1/auth/register` | Register | no | 201, 422 | Create an email/password account and sign it in. |
| `POST` | `/api/v1/auth/login` | Login | no | 200, 422 | Sign in with email + password. Every failure looks exactly the same. |
| `POST` | `/api/v1/auth/refresh` | Refresh | no | 200, 422 | Rotate a refresh token into a new pair. |
| `POST` | `/api/v1/auth/logout` | Logout | no | 200, 422 | Revoke the session this refresh token belongs to (idempotent). |
| `POST` | `/api/v1/auth/upgrade` | Upgrade Account | yes | 200, 422 | Turn the anonymous caller into a registered account, keeping all data. |
| `GET` | `/api/v1/auth/me` | Me | yes | 200 | The signed-in profile. The simplest protected endpoint. |

## Consent

| Method | Path | Summary | Session | Status codes | Notes |
| --- | --- | --- | --- | --- | --- |
| `GET` | `/api/v1/consent/requirements` | Requirements | no | 200 | The current document versions and the AI disclosure text. Public. |
| `POST` | `/api/v1/consent` | Record Consent | yes | 201, 422 | Record one or more consent decisions for the signed-in user. |

## Chat

| Method | Path | Summary | Session | Status codes | Notes |
| --- | --- | --- | --- | --- | --- |
| `GET` | `/api/v1/chat/memory` | List Chat Memory | yes | 200 | Show the user's summaries for saved chats (decrypted only for their owner). |
| `DELETE` | `/api/v1/chat/memory` | Delete Chat Memory | yes | 200 | Erase every summary and tombstone existing chats so memory cannot be rebuilt. |
| `POST` | `/api/v1/chat/sessions` | Create Chat Session | yes + consent | 201, 422 | Open a chat session. |
| `GET` | `/api/v1/chat/sessions/{session_id}` | Get Chat Session | yes | 200, 422 | A session's public info. 404 if it is not yours (or does not exist). |
| `GET` | `/api/v1/chat/sessions/{session_id}/messages` | List Chat Messages | yes | 200, 422 | The newest ``limit`` messages, oldest first. Owner only; 404 otherwise. |
| `POST` | `/api/v1/chat/sessions/{session_id}/messages` | Send Chat Message | yes + consent | 200, 422 | Send one message and get the whole reply as JSON. |
| `POST` | `/api/v1/chat/sessions/{session_id}/stream` | Stream Chat Message Post | yes + consent | 200, 422 | Send one message and stream the reply as Server-Sent Events. |

## Knowledge base

| Method | Path | Summary | Session | Status codes | Notes |
| --- | --- | --- | --- | --- | --- |
| `GET` | `/api/v1/knowledge` | List Knowledge | yes | 200 | Every curated document, summary form, sorted by id. |
| `GET` | `/api/v1/knowledge/{doc_id}` | Get Knowledge Doc | yes | 200, 422 | One document, body included. 404 if the id is not in the corpus. |

## Mood check-ins

| Method | Path | Summary | Session | Status codes | Notes |
| --- | --- | --- | --- | --- | --- |
| `GET` | `/api/v1/mood` | List Check Ins | yes + consent | 200, 422 | The caller's check-ins, newest first, optionally bounded by a local range. |
| `POST` | `/api/v1/mood` | Create Check In | yes + consent | 201, 422 | Record one check-in; screen the optional note before storing it. |
| `GET` | `/api/v1/mood/streak` | Read Streak | yes + consent | 200, 422 | The streak, counted in the caller's timezone with forgiving rules. |
| `DELETE` | `/api/v1/mood/{entry_id}` | Delete Check In | yes + consent | 200, 422 | Delete one of the caller's check-ins. |

## Guided exercises

| Method | Path | Summary | Session | Status codes | Notes |
| --- | --- | --- | --- | --- | --- |
| `GET` | `/api/v1/exercises` | List Exercises | no | 200 |  |
| `GET` | `/api/v1/exercises/recommendations` | Exercise Recommendations | no | 200, 422 |  |
| `GET` | `/api/v1/exercises/history` | Exercise History | yes + consent | 200, 422 |  |
| `GET` | `/api/v1/exercises/{exercise_id}` | Get Exercise | no | 200, 422 |  |
| `POST` | `/api/v1/exercises/{exercise_id}/complete` | Complete Exercise | yes + consent | 201, 422 |  |

## Journal

| Method | Path | Summary | Session | Status codes | Notes |
| --- | --- | --- | --- | --- | --- |
| `GET` | `/api/v1/journal/prompts` | List Journal Prompts | yes + consent | 200, 422 | Return the rotating curated set of localised writing prompts and templates. |
| `GET` | `/api/v1/journal` | List Journal Entries | yes + consent | 200, 422 | List the signed-in user's decrypted journal entries with pagination. |
| `POST` | `/api/v1/journal` | Create Journal Entry | yes + consent | 201, 422 | Screen, score in memory, envelope-encrypt, and persist a journal entry. |
| `GET` | `/api/v1/journal/{entry_id}` | Get Journal Entry | yes + consent | 200, 422 | Fetch and decrypt a single journal entry owned by the signed-in user. |
| `PUT` | `/api/v1/journal/{entry_id}` | Update Journal Entry | yes + consent | 200, 422 | Re-screen, re-score in memory, and re-encrypt an existing journal entry. |
| `DELETE` | `/api/v1/journal/{entry_id}` | Delete Journal Entry | yes + consent | 204, 422 | Permanently delete a journal entry owned by the signed-in user. |

## CBT thought record

| Method | Path | Summary | Session | Status codes | Notes |
| --- | --- | --- | --- | --- | --- |
| `POST` | `/api/v1/cbt/screen` | Screen Step | yes + consent | 200, 422 |  |
| `POST` | `/api/v1/cbt/traps` | Thinking Traps | yes + consent | 200, 422 |  |
| `GET` | `/api/v1/cbt/records` | List Records | yes + consent | 200 |  |
| `POST` | `/api/v1/cbt/records` | Create Record | yes + consent | 200, 422 |  |
| `GET` | `/api/v1/cbt/records/{record_id}` | Get Record | yes + consent | 200, 422 |  |
| `DELETE` | `/api/v1/cbt/records/{record_id}` | Delete Record | yes + consent | 204, 422 |  |

## Screenings (PHQ-9 and GAD-7)

| Method | Path | Summary | Session | Status codes | Notes |
| --- | --- | --- | --- | --- | --- |
| `GET` | `/api/v1/assessments` | List Assessments | no | 200 | The instruments this deployment ships, in a stable order. |
| `GET` | `/api/v1/assessments/{instrument}` | Get Assessment | no | 200, 422 | One instrument: official wording, options, bands and provenance. |
| `POST` | `/api/v1/assessments/{instrument}/submit` | Submit Assessment | yes + consent | 201, 422 | Score, store, and — if item 9 was answered — offer help immediately. |
| `GET` | `/api/v1/assessments/{instrument}/history` | Assessment History | yes + consent | 200, 422 | The caller's own runs for one instrument, newest first. |
| `GET` | `/api/v1/assessments/{instrument}/history/{result_id}` | Assessment Result | yes + consent | 200, 422 | One stored run, for the printable summary of an older result. |

## Insights

| Method | Path | Summary | Session | Status codes | Notes |
| --- | --- | --- | --- | --- | --- |
| `GET` | `/api/v1/insights/weekly` | Read Weekly | yes + consent | 200, 422 | The last ``weeks`` natural weeks, a week-over-week trend, and a summary. |
| `GET` | `/api/v1/insights/patterns` | Read Patterns | yes + consent | 200, 422 | Rolling averages, a trend, tag associations and the shape of the days. |
| `GET` | `/api/v1/insights/outlook` | Read Mood Outlook | yes + consent | 200, 422 | An explicit-opt-in, numeric-only, three-day personal-history estimate. |

## Crisis resources and assessment

| Method | Path | Summary | Session | Status codes | Notes |
| --- | --- | --- | --- | --- | --- |
| `GET` | `/api/v1/crisis/resources` | Crisis Resources | no | 200, 422 | Helplines for one region, in the order the client should show them. |
| `POST` | `/api/v1/crisis/assess` | Crisis Assess | no | 200, 422 | Assess one message: rules engine + ML ensemble, then the response plan. |

## Daily plan

| Method | Path | Summary | Session | Status codes | Notes |
| --- | --- | --- | --- | --- | --- |
| `GET` | `/api/v1/plan/today` | Plan Today | yes + consent | 200, 422 | Today's plan. Generated once per person per local day. |
| `POST` | `/api/v1/plan/{item_id}/done` | Plan Item Done | yes + consent | 200, 422 | Mark one suggestion done. |
| `POST` | `/api/v1/plan/{item_id}/skip` | Plan Item Skip | yes + consent | 200, 422 | Skip one suggestion. No reason is asked for, and none is stored. |

## Settings

| Method | Path | Summary | Session | Status codes | Notes |
| --- | --- | --- | --- | --- | --- |
| `GET` | `/api/v1/settings` | Get Settings | yes + consent | 200 | The caller's planner preferences (defaults if never saved). |
| `PUT` | `/api/v1/settings` | Put Settings | yes + consent | 200, 422 | Replace the caller's planner preferences. |

## Privacy, export and deletion

| Method | Path | Summary | Session | Status codes | Notes |
| --- | --- | --- | --- | --- | --- |
| `GET` | `/api/v1/privacy/settings` | Get Privacy Settings | yes | 200 | The caller's privacy choices (the product defaults if never saved). |
| `PATCH` | `/api/v1/privacy/settings` | Patch Privacy Settings | yes | 200, 422 | Change one or more privacy choices, creating the row on first write. |
| `GET` | `/api/v1/privacy/consents` | List Consent History | yes | 200 | Every agreement and withdrawal this account ever recorded, newest first. |
| `GET` | `/api/v1/privacy/export` | Export User Data | yes | 200, 422 | Every row Manovia holds for the caller, decrypted, as a download. |
| `DELETE` | `/api/v1/privacy/account` | Delete My Account | yes | 200, 422 | Permanently delete this account and everything that belongs to it. |

## Development only (not in production)

These routes exist only when `APP_ENV` is not `production`. They are for local debugging and must not be reachable from a deployed API.

| Method | Path | Summary | Session | Status codes | Notes |
| --- | --- | --- | --- | --- | --- |
| `POST` | `/api/v1/dev/analyze` | Analyze | no | 200, 422 | Analyze one string with the configured emotion chain. |
| `GET` | `/api/v1/dev/analyze/state` | Analyzer State | no | 200 | Chain health, cache counters and the detected model. No text, no keys. |

## Agent

| Method | Path | Summary | Session | Status codes | Notes |
| --- | --- | --- | --- | --- | --- |
| `GET` | `/api/v1/agent/tools` | List Agent Tools | no | 200 | The allow-listed toolset, for transparency. |
| `POST` | `/api/v1/agent/sessions` | Create Agent Session | yes + consent | 201 | Open a navigator session (ephemeral, in memory). |
| `POST` | `/api/v1/agent/sessions/{session_id}/chat` | Navigator Chat | yes + consent | 200, 422 | One navigator turn: safety gate first, then the agent. |
| `POST` | `/api/v1/agent/sessions/{session_id}/confirmations/{pending_id}` | Answer Confirmation | yes + consent | 200, 422 | Allow or deny a paused, effecting tool call. |

## Meditation

| Method | Path | Summary | Session | Status codes | Notes |
| --- | --- | --- | --- | --- | --- |
| `GET` | `/api/v1/meditation/themes` | List Themes | no | 200 | The reviewed catalogue (public, like the exercise list: reviewed content, not personal data). Labels are English, the content's review language; the UI localises its own chrome. |
| `POST` | `/api/v1/meditation/scripts` | Create Script | yes | 200, 422 | Compose one personalised script. Deterministic per request + seed. |
| `POST` | `/api/v1/meditation/audio` | Render Audio | yes | 200, 422 | Render the script locally and stream the WAV back. |
| `POST` | `/api/v1/meditation/audio/captions` | Render Captions | yes | 200, 422 | The transcript and per-section captions (estimated timing, said so). |

## Voice

| Method | Path | Summary | Session | Status codes | Notes |
| --- | --- | --- | --- | --- | --- |
| `POST` | `/api/v1/voice/transcribe` | Transcribe | yes | 200, 422 | Day 39 compatibility endpoint; live audio uses the documented WebSocket runtime. |
| `GET` | `/api/v1/voice/voices` | Get Voices | yes | 200 | List reviewed local language choices without exposing model paths. |
| `POST` | `/api/v1/voice/synthesize` | Synthesize | yes | 200, 422 | Legacy local TTS endpoint; sentence streaming uses the WebSocket runtime. |

## Drift

| Method | Path | Summary | Session | Status codes | Notes |
| --- | --- | --- | --- | --- | --- |
| `POST` | `/api/v1/drift/record` | Record Drift | yes | 200, 422 |  |
| `GET` | `/api/v1/drift/alerts` | Drift Alerts | yes | 200, 422 |  |
| `GET` | `/api/v1/drift/report` | Drift Report | yes | 200, 422 |  |
| `GET` | `/api/v1/drift/snapshot/{window}` | Drift Snapshot | yes | 200, 422 |  |

## Feature-Flags

| Method | Path | Summary | Session | Status codes | Notes |
| --- | --- | --- | --- | --- | --- |
| `GET` | `/api/v1/feature-flags` | List Flags | yes | 200 |  |
| `POST` | `/api/v1/feature-flags/{flag_name}` | Set Flag | yes | 200, 422 |  |
| `POST` | `/api/v1/feature-flags/{flag_name}/reset` | Reset Flag | yes | 200, 422 |  |

## Sharing

| Method | Path | Summary | Session | Status codes | Notes |
| --- | --- | --- | --- | --- | --- |
| `GET` | `/api/v1/shares/options` | Share Options | yes + consent | 200 | The catalogue, the defaults, and the entries that can be selected. |
| `GET` | `/api/v1/shares/preferences` | Get Share Preference | yes + consent | 200 | Read the switch (no row yet means off). |
| `POST` | `/api/v1/shares/preferences` | Set Share Preference | yes + consent | 200, 422 | Turn sharing on or off. Off by default; turning it on is the person's act. |
| `POST` | `/api/v1/shares/preview` | Preview Share | yes + consent | 200, 422 | Build the document without storing it, so the person can read it first. |
| `GET` | `/api/v1/shares` | List Shares | yes + consent | 200 | The person's own links, newest first. |
| `POST` | `/api/v1/shares` | Create Share | yes + consent | 201, 422 | Create one time-limited, passcode-protected, encrypted share link. |
| `POST` | `/api/v1/shares/revoke-all` | Revoke All Shares | yes + consent | 200 | One click: every live link stops working. |
| `GET` | `/api/v1/shares/{share_id}` | Share Detail | yes + consent | 200, 422 | One link, with its open count. |
| `GET` | `/api/v1/shares/{share_id}/audit` | Share Audit | yes + consent | 200, 422 | The audit log the owner can read: what happened, and when. |
| `POST` | `/api/v1/shares/{share_id}/revoke` | Revoke Share | yes + consent | 200, 422 | Revoke one link. Idempotent. |

## Sharing-Public

| Method | Path | Summary | Session | Status codes | Notes |
| --- | --- | --- | --- | --- | --- |
| `GET` | `/api/v1/public/shares/{token}` | Share Status | no | 200, 422 | Is this link usable, and what will it ask for? |
| `POST` | `/api/v1/public/shares/{token}/open` | Open Share | no | 200, 422 | Hand over the ciphertext, or refuse. |

## Regenerating

```bash
cd backend
python scripts/export_openapi.py          # rewrite docs/openapi.json and docs/api.md
python scripts/export_openapi.py --check  # staleness check, exits 1 if out of date
```
