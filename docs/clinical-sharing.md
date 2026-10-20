# Clinical sharing — the appointment bridge (Day 47)

What it is: a person using Manovia can prepare a summary of their own self-help
data for an appointment, choose exactly which items go into it, see precisely what
will be shared before it exists, and hand it to a professional through a link that
expires, can be revoked, is limited to a number of views, and is protected by a
passcode and by encryption whose key the server never holds.

This document is the reference for that bridge: the data flow, the limits, the
cryptography, the two standards artifacts (PDF and HL7 FHIR R4), and — stated
plainly, because the distinction matters more than the feature — **what this is
not**.

Read first: [docs/privacy-and-threat-model.md](privacy-and-threat-model.md) §6c,
[docs/adr/0047-clinician-share-bridge.md](adr/0047-clinician-share-bridge.md),
and the evidence run in
[docs/verification/day47-clinician-share-bridge.md](verification/day47-clinician-share-bridge.md).

---

## 1. The three sentences that matter most

1. **This is not an EHR integration.** Manovia does not read from, write to, or
   connect to any electronic health record, practice-management system, hospital
   system or health information exchange. There is no HL7 v2 interface, no FHIR
   *server*, no SMART-on-FHIR app registration and no patient matching. A share
   produces a **document** — a PDF or a FHIR R4 document Bundle — that a person
   hands over. Importing it, if anybody imports it at all, is a decision made
   outside Manovia by whoever receives it.
2. **This is not for emergencies.** Nothing here is monitored, triaged or routed
   to a human. A shared document may sit unopened for days; a link may be revoked
   or expire; the recipient may be asleep. If somebody is in danger or thinking
   about harming themselves, the answer is the crisis support inside the app
   ("Need help now?" on every page) or a local emergency number — not a share
   link. The documents say so on the page: *"Manovia is not an EHR and not a
   crisis service."*
3. **This is not a diagnosis and produces no clinical claims.** Every document
   carries the banner *"Patient-generated, not a diagnosis"*, screening band
   labels are marked as self-help labels (`band_is_a_diagnosis` is always
   `false`), item-level answers are never included, and the FHIR Provenance marks
   the patient as author and the software as assembler.

---

## 2. Data flow: from tick to viewer

```
  the person's browser                          Manovia's API                     the database
  ─────────────────────                          ─────────────                     ────────────
  1. Settings → sharing: OFF by default  ───────►  SharePreference row (enabled=false)
     switch on                             ───────►  enabled=true

  2. /shares  "Prepare for my appointment"
     GET /api/v1/shares/options            ◄───────  catalogue, limits, defaults,
                                                     the five never-included items
     tick items, pick a window, write a note
     (live preview = the real payload view)

  3. POST /api/v1/shares/preview            ───────►  build SharePayload from the
                                                      person's own rows
                                        ◄───────────  payload + consent_lines
                                                      + consent_sha256
     the person reads the consent lines and confirms

  4. POST /api/v1/shares                    ───────►  verify consent_sha256,
     (selection + consent_sha256)                     build payload / PDF / bundle,
                                                      encrypt each with AES-256-GCM
                                                      under a key derived from a
                                                      fresh secret + passcode + token
                                                                       ────────────►  ciphertext,
                                                                                      nonces,
                                                                                      token_hash,
                                                                                      passcode_hash.
                                                                                      No key. No token.
                                        ◄───────────  token, secret, fragment (#k=…),
                                                      passcode — once, never again

  5. the person copies  /s/<token>#k=<secret>  and the passcode, and sends them
     to the professional by whatever channel they choose

  6.                                        the professional's browser (no account)
     GET  /api/v1/public/shares/<token>     ───────►  available? passcode needed?
                                                      disclaimer text
     types the passcode
     POST /api/v1/public/shares/<token>/open ──────►  rate limit → lock check →
                                                      passcode verify (PBKDF2) →
                                                      atomic view claim → audit
                                        ◄───────────  ciphertext + nonces + AAD
                                                      + key_derivation sentence
     HKDF-SHA256(secret, passcode, token) → AES-256-GCM decrypt **in the browser**
     → the document, banner first, PDF/FHIR download buttons

  7. back in the owner's app
     GET /api/v1/shares                     ◄───────  "Opened 2 times of 5 allowed."
     GET /api/v1/shares/<id>/audit          ◄───────  created / opened / revoked / …
     POST /api/v1/shares/<id>/revoke        ───────►  dead from the next request
     POST /api/v1/shares/revoke-all         ───────►  every live link at once
```

Two properties of that flow are deliberate:

* The plaintext is assembled **server-side** from rows that are already encrypted
  at rest with the deployment's field key, then re-encrypted under the share key
  before it is stored. The server therefore does see the plaintext while building
  the document — it is the same server that holds the person's entries. What it
  never sees is the share key, and what it stores for the share is ciphertext.
* The viewer's browser does the decryption. The key is derived from the secret in
  the URL **fragment** (`#k=…`) plus the passcode plus the token. A fragment is
  not sent to a server in an HTTP request, so the secret does not appear in this
  API's logs — and the share token is redacted from access logs, unhandled-error
  logs and Sentry URLs anyway, because a token in a log is half a credential.

---

## 3. What the server stores for a share

`clinician_shares` — 23 columns, and the list is the guarantee:

| Column | What it holds |
| --- | --- |
| `id`, `user_id`, `created_at` | whose decision this was, and when |
| `token_hash` | SHA-256 of the URL token. The token itself is never stored. |
| `token_prefix` | the first 8 characters, so the owner can recognise a link in a list |
| `passcode_hash`, `passcode_salt` | PBKDF2-HMAC-SHA256, 210 000 iterations, 16-byte salt, 32-byte digest |
| `passcode_failures`, `locked_until` | the wrong-passcode budget and the lockout |
| `expires_at`, `revoked_at` | the lifetime, and the moment it was ended |
| `view_limit`, `view_count` | the view budget |
| `formats`, `items` | which exports, which items were ticked (names, not contents) |
| `payload_ciphertext` + `payload_nonce` | the structured document, AES-256-GCM |
| `pdf_ciphertext` + `pdf_nonce` | the printable summary |
| `fhir_ciphertext` + `fhir_nonce` | the R4 document bundle |
| `cipher` | `A256GCM`, recorded rather than assumed |
| `consent_summary_sha256` | the hash of the consent lines the person confirmed |

There is **no key column** — not for the share, not for the derived content key,
and the plaintext token appears nowhere. Measured on a real share: the three
ciphertexts are exactly 16 bytes longer than their plaintexts (the GCM tag), the
nonces are 12 bytes, and searching every column of the row for the note text, a
question, the token, the secret or the passcode finds none of them.

`share_access_events` has exactly five columns — `id`, `share_id`, `kind`,
`actor`, `created_at`. The audit log knows that a link was opened; it does not
know by whom, from what IP, or what was read, because none of that was needed to
show the owner "opened 2 times".

`share_preferences` holds one boolean per person, defaulting to **off**.

---

## 4. Cryptography, stated precisely

| Property | Value |
| --- | --- |
| Content cipher | AES-256-GCM (`A256GCM`), 12-byte nonce per document, 16-byte tag |
| Key derivation | HKDF-SHA256, `ikm = url_secret \|\| 0x00 \|\| utf8(passcode)`, `salt = SHA-256(token)`, `info = "manovia.share.v1.key"`, length 32 bytes |
| URL secret | 32 random bytes, base64url (43 characters), generated per share, returned once |
| URL token | 16 random bytes, base64url (22 characters); only its SHA-256 and first 8 characters are stored |
| Additional authenticated data | `manovia.share.v1.<token>.<document>` per document, so a ciphertext cannot be replayed into another share or another document slot |
| Passcode storage | PBKDF2-HMAC-SHA256, 210 000 iterations, 16-byte salt |
| Generated passcode | 6 digits, or the person's own (6–64 characters) |
| Failure behaviour | one error type and one message for a wrong key, wrong passcode, flipped byte, wrong nonce and wrong AAD — a difference between them would be an oracle |

The same derivation is implemented twice — Python (`cryptography`) for the tests
and WebCrypto (`frontend/src/features/sharing/crypto.ts`) for the viewer — and
they are proved equal against a committed vector
(`frontend/src/features/sharing/testSupport/shareVector.json`) that contains a
real ciphertext produced by the backend and the key the backend derived for it.

**What this protects.** Somebody who reads the database, a backup, or an HTTP
response gets ciphertext. Without the secret *and* the passcode they cannot read
the document, and they cannot forge one: GCM authenticates.

**What this does not protect.** The server while it builds the document (it holds
the field key and assembles the plaintext); a recipient who forwards the link with
the fragment and the passcode (that is a complete credential — the view limit and
the expiry are the mitigations, not the encryption); or a browser with malicious
extensions on either end. It is *encryption at rest with an ephemeral,
fragment-delivered key*, and it is described that way everywhere rather than as
end-to-end encryption. Assembling the document in the browser instead would be
true client-side encryption and is in the parking lot in
[PROGRESS.md](../PROGRESS.md).

---

## 5. Limits and how to change them

Every value is a `SHARE_*` environment variable, documented in
[.env.example](../.env.example); all ship blank, meaning "use the default". A
combination that cannot work (a default TTL outside its own min/max, a
zero-second lockout) fails at startup rather than at the first share.

| Limit | Default | Env var | Why |
| --- | --- | --- | --- |
| Link lifetime | 7 days | `SHARE_DEFAULT_TTL_DAYS` (min 1, max 30) | covers "before my appointment on Thursday"; a forwarded link should not outlive it |
| Views | 5 | `SHARE_DEFAULT_VIEW_LIMIT` (max 25) | one professional, one device, not circulation |
| Live shares per person | 10 | `SHARE_MAX_ACTIVE_PER_USER` | a pile of forgotten links is a pile of open doors |
| Passcode length | 6 minimum | `SHARE_PASSCODE_MIN_LENGTH` | it feeds the key derivation, so this is a cryptographic floor |
| Wrong passcodes | 5, then 15 minutes | `SHARE_PASSCODE_MAX_FAILURES`, `SHARE_LOCKOUT_SECONDS` | brute force on a 6-digit code |
| Public request rate | 10/minute per address and per (address, token) | `SHARE_RATE_LIMIT_PER_MINUTE` | enumeration and scripting |
| Document size | 4 000 000 bytes | `SHARE_MAX_DOCUMENT_BYTES` | memory-bomb guard |
| Whole feature | on | `SHARE_ENABLED` | operator kill switch: when off, the routers are not mounted and every share route answers 404 |

Content limits, fixed in code because they are product decisions: mood window of
14/30/90 days, note up to 2 000 characters, up to 10 questions of 300 characters
each, up to 10 thought records, up to 20 assessment results.

---

## 6. What can be shared, and what never is

Tickable items — **all off by default**, nothing is pre-selected:

| Item | What goes in | What stays out |
| --- | --- | --- |
| Mood trend summary | count of check-ins in the window, first/last, mean valence and energy, direction of the second half vs the first, top emotion and factor labels | the note attached to any check-in (`notes_included` is always `false`) |
| Screening history | instrument, date, **total**, band label, the verified LOINC term | individual item answers (`item_answers_included` is always `false`); no cut-off advice, no severity advice |
| Selected thought records | the five sections the person wrote, as they wrote them | records they did not tick |
| Exercise helpfulness | completions, mean before/after ratings, mean change, the exercise that moved most | any claim of causation — the caveat is in the document |
| Free-text note | the note, up to 2 000 characters | — |
| Questions to ask | up to 10 questions | — |
| Identify me | the account email | nothing else identifying |

Never included, and listed on the builder page rather than buried in a tooltip:
chat conversations with Manovia, journal entries, the notes attached to mood
check-ins, the password/email/account identifiers (unless "Identify me" is
ticked), and anything not ticked.

---

## 7. Consent and comprehension

* **Off by default** at two levels: the deployment (`SHARE_ENABLED`) and the
  person (`SharePreference.enabled = false`). With sharing off, the endpoints say
  so rather than pretending the feature is missing.
* **Preview before creation.** `POST /shares/preview` returns the payload *and*
  the consent lines built from it — for example "Screening history: 2 results
  (GAD-7, PHQ-9). Totals and band labels only — your individual answers are not
  included.", "Thought records: 1 you chose, with everything you wrote in them.",
  and the never-included list — plus `consent_sha256`.
* **Creation requires the hash.** `POST /shares` refuses with `409
  consent_mismatch` unless the body carries the hash of the lines the preview
  produced from the same selection. The client cannot confirm something it did
  not show, and the person cannot be shown one thing and share another.
* **The key is shown once**, with the sentence that explains why: *"Save this
  link now. The key is part of it and is not stored anywhere: no one — not you,
  not us — can open this share later from the link alone."*
* **Revoke everything** is one request (`POST /shares/revoke-all`) behind typing
  `REVOKE` in the UI. Revoking one link is one request too, and a revoked link is
  dead from the next request — the attempt is audited as `access_after_revoke`.
* Deleting the account deletes the shares, their audit lines and the preference
  row; the retention job removes expired share rows. A data export contains the
  share *decisions* and audit trail in `clinician-shares.json`, and never a
  ciphertext, token or passcode hash.

---

## 8. The audit log the owner sees

Event kinds: `created`, `opened`, `passcode_failed`, `locked`, `revoked`,
`expired`, `view_limit_reached`, `access_after_revoke`. Each row is kind, actor
(`owner` or `viewer`) and timestamp — nothing else is recorded.

`GET /api/v1/shares/<id>/audit` returns the events, per-kind counts and the
sentence the UI shows: **"Opened 2 times of 5 allowed."** The list view
(`GET /api/v1/shares`) carries the same sentence per link, so a person does not
have to open each one to see whether it has been read.

---

## 9. The PDF

A4, 18 mm margins, generated by reportlab from the same `SharePayload` the API
previewed — so the printable document cannot disagree with what the person
confirmed.

* The banner **"Patient-generated, not a diagnosis"** is boxed at the top of
  page 1, and every page carries the footer
  *"Patient-generated, not a diagnosis — Manovia is not an EHR and not a crisis
  service."* plus a page number.
* Sections appear only when the item was ticked and had content; empty sections
  are omitted rather than printed as headings with nothing under them.
* **Known limitation:** reportlab's built-in Helvetica cannot render Devanagari
  or Bengali, and the repository ships no TrueType font. `pdf_safe()` therefore
  substitutes `?` for glyphs the font cannot draw and prints one note on the page
  saying that this happened, so a reader is never silently given a document with
  holes in it. The HTML viewer and the FHIR bundle are unaffected (both are
  UTF-8). Embedding a licensed Devanagari/Bengali TTF is in the parking lot.

---

## 10. The FHIR R4 document

`app/services/sharing/fhir.py` emits a **document** Bundle at FHIR version
**4.0.1**: Composition first, content resources next, Provenance last, every
entry with a `urn:uuid:` fullUrl, a persistent identifier
(`https://manovia.app/fhir/share-bundle`) and a timestamp. For a full selection
the sample holds 16 entries: Composition, Patient, Device, 6 Observations, 2
QuestionnaireResponses, 4 DocumentReferences, Provenance.

| Content | Resource | Coding |
| --- | --- | --- |
| Mood summary numbers | Observation | Manovia's own `https://manovia.app/fhir/CodeSystem/mood-summary` (`mean-valence`, `mean-energy`, `check-in-count`, `direction`) |
| PHQ-9 total | Observation **and** QuestionnaireResponse | **LOINC `44261-6`** — "Patient Health Questionnaire 9 item (PHQ-9) total score [Reported]", <https://loinc.org/44261-6>, unit `{score}` (UCUM), category `survey` |
| GAD-7 total | Observation **and** QuestionnaireResponse | **LOINC `70274-6`** — "Generalized anxiety disorder 7 item (GAD-7) total score [Reported.PHQ]", <https://loinc.org/70274-6>, unit `{score}`, category `survey` |
| Thought records, exercise helpfulness, note, questions | DocumentReference with an inline `text/plain` attachment | Manovia's own `…/CodeSystem/share-document-kind` |
| Who made this | Provenance | `author` → the **Patient**, `assembler` → the Manovia **Device**, from `http://terminology.hl7.org/CodeSystem/provenance-participant-type` |

Terminology policy, in one rule: **a code is either verified or it is ours.** The
two LOINC terms were read off loinc.org on 2026-10-19 and are pinned with their
source URLs, fully-specified names, scale, method, status and example unit in
`backend/app/content/sharing/loinc.json`; the loader refuses a file whose URL
does not cite its own code, whose term is not active, or that offers a panel code
as a total. The questionnaire panel codes (`44249-1`, `69737-5`) are recorded
beside the totals precisely so nobody "fixes" a total-score Observation into a
panel code later — they are never used. Anything Manovia-specific is coded in
Manovia's own CodeSystem with a human `text`, because borrowing a LOINC or SNOMED
term nobody verified would put a wrong clinical claim into somebody's record.

Deliberate omissions: no `Composition.attester` and no
`Composition.confidentiality` (both carry required bindings and neither has an
honest value for a patient-generated summary), no
`QuestionnaireResponse.questionnaire` canonical (there is no published
Questionnaire resource to point at), and no `Provenance.entity`.

**Validation.** The bundle is written to a committed artifact,
`backend/tests/fixtures/fhir/sample_share_bundle.json`, by
`backend/scripts/export_fhir_sample.py` through the same `bundle_bytes()` the
endpoint uses. The CI job `fhir-validation`
([.github/workflows/ci.yml](../.github/workflows/ci.yml)) regenerates it, fails
if the committed file drifted, runs the dependency-free structural check, and
then validates it with the **official HL7 validator** — `hapifhir/org.hl7.fhir.core`
release 7.0.2 `validator_cli.jar`, sha256-pinned, on temurin 17, with
`-version 4.0.1 -tx n/a`. Locally: `make fhir-validate` (official validator when
java and the jar are present, otherwise
`backend/scripts/fhir_offline_check.py`, and it prints which one it ran);
`make fhir-validator` fetches and verifies the jar.

`-tx n/a` is the validator's documented "no terminology server" mode: it checks
structure and the R4 invariants without looking codes up online. Code existence
is guaranteed instead by `loinc.json` and by the offline checker's rule that
every coding system in the bundle must be LOINC-as-verified, UCUM, one of the two
terminology.hl7.org systems used here, or Manovia's own namespace. The job also
re-runs against `tx.fhir.org` as a **non-blocking** step so the terminology result
is in the log without making the gate depend on a third-party service. Both steps
assert that the validator's own `Sources to validate:` line names the sample: a
validator pointed at a wrong path exits 0 having checked nothing, so "the step
passed" is not evidence on its own.

**It has run.** CI run `39337225868`, `fhir-validation` job: **success**, with
`Success: 0 errors, 52 warnings, 6 notes` from the `-tx n/a` gate and
`Success: 0 errors, 43 warnings, 2 notes` from the `tx.fhir.org` run. No line of
either output mentions LOINC, `44261-6`, `70274-6` or an unknown code. The
warnings are itemised, with the reason each one is accepted, in §8 of
[the Day-47 verification note](verification/day47-clinician-share-bridge.md).

---

## 11. Endpoints

Owner (authenticated, and refused unless the person's sharing preference is on
and their consent record is current):

| Method and path | Purpose |
| --- | --- |
| `GET /api/v1/shares/options` | the catalogue, limits, defaults, never-included list |
| `GET /api/v1/shares/preferences`, `POST /api/v1/shares/preferences` | the on/off switch |
| `POST /api/v1/shares/preview` | the payload, the consent lines, `consent_sha256` |
| `POST /api/v1/shares` | create; returns token, secret, fragment and passcode once |
| `GET /api/v1/shares`, `GET /api/v1/shares/{id}` | the list and one link, with the audit sentence |
| `GET /api/v1/shares/{id}/audit` | events, counts, sentence |
| `POST /api/v1/shares/{id}/revoke`, `POST /api/v1/shares/revoke-all` | end one, or end all |

Public (no account, rate limited):

| Method and path | Purpose |
| --- | --- |
| `GET /api/v1/public/shares/{token}` | is it available, does it need a passcode, and the disclaimer text |
| `POST /api/v1/public/shares/{token}/open` | the passcode in, the ciphertext out, one view spent |

Error codes: `sharing_disabled` 403, `share_not_found` 404,
`share_request_invalid` 422, `consent_mismatch` 409, `share_limit` 409. On the
public side: **one** `share_unavailable` 404 ("This link is not available.") for
unknown, expired, revoked and view-exhausted tokens alike — telling them apart
would be an oracle — then `passcode_incorrect` 403 with the number of tries left
in the message, `passcode_locked` 423 with a `Retry-After` header, and
`rate_limited` 429.

The viewer page is `frontend/src/pages/ShareViewerPage.tsx` at `/s/:token`,
matched in `App.tsx` **before** the onboarding gate and rendered with no app
shell: a professional opening a link has no account and must not be asked to
create one. The owner's builder is `/shares`.

---

## 12. Security behaviour, and the tests that pin it

| Scenario | Behaviour | Test |
| --- | --- | --- |
| Expired link | 404 `share_unavailable`, identical to unknown; expiry is enforced at read time, not by a job | `test_an_expired_link_is_refused_and_says_nothing_about_itself`, `test_expiry_is_enforced_at_read_time_not_by_a_job` |
| Wrong passcode | 403 with tries left; after 5 the link locks for 15 minutes and the lock beats the *right* passcode; a wrong passcode never decrypts anything | `test_wrong_passcodes_lock_the_link_and_the_lock_beats_the_right_one`, `test_a_wrong_passcode_never_decrypts_anything` |
| Revoked link | dead from the next request; the attempt is audited | `test_a_revoked_link_stops_immediately_and_the_attempt_is_audited` |
| Tampered ciphertext | GCM rejects it, with the same single error as every other failure mode; documents are bound to their share and their slot by AAD | `test_tampering_with_the_ciphertext_is_detected_and_gives_no_oracle`, `test_documents_are_bound_to_their_own_share_and_name` |
| Link enumeration | unknown tokens are indistinguishable from dead ones; tokens are 16 random bytes; the rate limiter answers before any comparison | `test_unknown_tokens_are_indistinguishable_from_dead_ones`, `test_the_public_endpoints_are_rate_limited_per_address` |
| Rate limiting | 10/minute per address and per (address, token) on the public routes | as above |
| View budget | claimed atomically, so it cannot be overdrawn | `test_the_view_budget_cannot_be_overdrawn` |
| Secrets in logs | the token, secret and passcode never reach an access log, an unhandled-error log or a Sentry URL | `test_secrets_never_reach_a_log_line`, `test_redact_path_replaces_the_share_token_and_leaves_other_paths_alone` |
| Stored state | no token, no key, ever | `test_a_token_is_never_stored_and_neither_is_a_key` |

They live in `backend/tests/security/test_share_security.py` (18 tests) and
`backend/tests/integration/test_sharing_flow.py` (14), with the unit suites under
`backend/tests/unit/test_share_*.py` (terminology 25, crypto 33, passcode 16,
items 41, fhir 31, pdf 20) and the artifact suite in
`backend/tests/unit/test_share_fhir_sample.py` (28). The frontend adds 55 tests
under `frontend/src/features/sharing/` and 2 routing tests in
`frontend/src/App.test.tsx`.

---

## 13. Known limits

* **Not client-side assembly.** The server builds the plaintext and re-encrypts
  it; see §4.
* **The fragment is a credential.** `/s/<token>#k=<secret>` plus the passcode is
  everything needed to read the document. Links forwarded with the fragment are
  readable by whoever receives them, until they expire, hit the view limit or are
  revoked. Browsers also keep fragments in history on the *recipient's* device.
* **The rate limiter is in-memory per process.** With more than one worker or
  replica the budget is per process, not global; the same is true of the rest of
  Manovia's limiting (Day 27).
* **PDF glyphs** for Devanagari and Bengali are substituted, with a note on the
  page (§9).
* **No key recovery and no rotation.** A lost fragment means an unreadable share;
  the answer is to revoke it and make a new one.
* **No recipient identity.** The audit log records that a link was opened by
  "viewer". Manovia cannot tell a professional from anybody else who has the link.
* **One document per share.** A share is a snapshot: entries added afterwards are
  not in it, and the snapshot does not update.
