# ADR 0047 — Clinician-share bridge: a consented document, not an integration

Date: 2026-10-20. Status: accepted.

## Context

Day 47 asks for a bridge between a person's self-help data and the professional
they are about to see: choose exactly which items to include, see precisely what
will be shared, export a printable PDF and an HL7 FHIR R4 document, hand it over
through a link that is time-limited, revocable, passcode-protected, view-limited
and encrypted, and let the owner see "opened 2 times".

Manovia is a self-help companion, not a clinical system. It has no EHR
connection, no clinical governance, no human monitoring anything, and content
that is explicitly not diagnostic
([docs/ethics-and-limitations.md](../ethics-and-limitations.md),
[docs/model-card.md](../model-card.md)). A feature that moves screening scores
and thought records toward a clinician therefore has two failure modes that
matter more than any bug: a document that reads as a clinical claim it cannot
support, and a link that quietly becomes a standing credential to somebody's
mental-health data.

Everything below is decided against those two, and the reference document for the
result is [docs/clinical-sharing.md](../clinical-sharing.md).

## Decisions

### 1. Ship a document, not an integration

The feature produces artifacts — a PDF and a FHIR R4 *document* Bundle — that a
person hands over. No FHIR server, no SMART-on-FHIR registration, no HL7 v2
interface, no patient matching, no write-back. "Validated FHIR" means the
document conforms to the specification, not that Manovia participates in health
IT.

An integration would require an agreement with whoever runs the receiving system,
identity assurance Manovia cannot provide, and clinical governance that does not
exist here. A document requires only that the reader trusts the person who sent
it — which is the trust that already exists in the room.

### 2. Encryption at rest with an ephemeral, fragment-delivered key — and call it that

The content key is HKDF-SHA256 over a per-share random secret, the passcode and
the token; the secret travels in the URL **fragment** (`#k=…`) and is never sent
to the server; the database stores AES-256-GCM ciphertext, nonces, a token hash
and a PBKDF2 passcode hash, and has no key column at all. The viewer derives the
key in WebCrypto and decrypts locally.

This is *not* end-to-end encryption and is never described as one: the server
assembles the plaintext from rows it can already read (they are encrypted with
the deployment's field key, Day 17) and re-encrypts it under the share key. What
the arrangement buys is that a database dump, a backup or an API response is not
readable, and that the operator cannot read a share after the fact from the link
alone. True client-side assembly means porting the whole payload builder — mood
aggregation, assessment bands, decrypted thought records, exercise ratings — to
TypeScript and keeping it at parity with Python, which is a Day-41-scale project
of its own. It is in the parking lot, not in this ADR's scope.

### 3. One refusal for every unusable link

Unknown, expired, revoked and view-exhausted tokens all answer `404
share_unavailable` with the same message; only the per-request `request_id`
differs. A wrong passcode is `403 passcode_incorrect` with the number of tries
left in the message (the error envelope is fixed, and that count is the one
number an honest viewer needs); after five the link is `423 passcode_locked` with
`Retry-After`; the public routes are rate limited per address and per
(address, token) before any token comparison happens.

Distinguishing "expired" from "never existed" is a free enumeration oracle on a
22-character token space, and a lockout that the right passcode can bypass is not
a lockout. Both are pinned in `tests/security/test_share_security.py`.

### 4. Terminology: a code is either verified or it is ours

The two assessment totals use LOINC **44261-6** (PHQ-9 total) and **70274-6**
(GAD-7 total), read off loinc.org on 2026-10-19 and pinned with their source
URLs, fully-specified names, scale, method, status and example unit in
`backend/app/content/sharing/loinc.json`. The loader refuses a file whose URL
does not cite its own code, whose term is not active, or that presents a panel
code as a total; the panel codes are stored beside the totals precisely so
nobody "fixes" one later. Everything Manovia-specific — mood summary measures,
band labels, document kinds, the document type — is coded in
`https://manovia.app/fhir/CodeSystem/…` with a human `text`.

An invented LOINC or SNOMED code is not a formatting error; it is a false
clinical claim written into somebody's record, where it will be read by software
that trusts codes. An honest local code with a human label is worse for
interoperability and better for the patient.

Also deliberately omitted, because each would have been a claim rather than a
description: `Composition.attester` (nobody legally attests a self-help summary),
`Composition.confidentiality` (a required binding with no honest value here),
`QuestionnaireResponse.questionnaire` (there is no published Questionnaire
resource to point at), and `Provenance.entity`.

### 5. Provenance is the point of the export

`Provenance.agent[0]` is the **Patient** as `author`; `agent[1]` is the Manovia
**Device** as `assembler`, both from
`http://terminology.hl7.org/CodeSystem/provenance-participant-type`. Every
Observation carries a note, every DocumentReference a description, and the
Composition a title saying the same thing the PDF's banner says: patient-generated,
not a diagnosis. A reader's system can therefore tell, mechanically, that these
numbers were entered by the person about themselves and assembled by software.

### 6. Consent is the hash of the lines that were shown

`POST /shares/preview` returns the payload *and* the consent lines computed from
it, with `consent_sha256`. `POST /shares` refuses `409 consent_mismatch` unless
the body echoes that hash for the same selection. A checkbox would record that
somebody clicked; this records what they were looking at, and makes "show one
thing, share another" a failed request rather than a design possibility.

Adding a new `ConsentKind` was rejected: consent content is versioned and
localised, and a new kind would force edits to shipped consent text and break
parity across three languages for no gain. The share preference is its own row
(`SharePreference`, default off) and the consent hash travels with the share.

### 7. Off by default at two levels, and one click to undo everything

The deployment can turn the whole bridge off (`SHARE_ENABLED=false`), in which
case the routers are not mounted and every share route answers 404 — a missing
feature, not a disabled one. Each person's sharing is off until they switch it
on. Because a share is cheap to create and expensive to remember, "revoke
everything" is a first-class request (`POST /shares/revoke-all`, behind typing
`REVOKE` in the UI) rather than a loop over per-link buttons.

### 8. Expiry at read time; the audit log stores almost nothing

A link's expiry is evaluated when somebody asks for it, so correctness does not
depend on a scheduler; the retention job (Day 22) removes the rows later. The
audit table has exactly five columns — `id`, `share_id`, `kind`, `actor`,
`created_at`. It can say "opened 2 times of 5 allowed" and cannot say who opened
it, from where, or what they read, because the owner's question never needed
that.

### 9. Documents are reproducible, so they can be committed and reviewed

Resource ids are `uuid5` of the bundle id rather than `uuid4`, and the JSON is
serialised with a fixed key order and indent. The same share rebuilt is
byte-identical, which makes `backend/tests/fixtures/fhir/sample_share_bundle.json`
a reviewable artifact and lets a test assert that the committed sample is what
the current code emits. Non-reproducible documents would have made the CI
validator a check on a file nobody could tie to the code.

### 10. Validate with the official validator, and be explicit about what that proves

CI job `fhir-validation` regenerates the sample, fails on drift
(`git diff --exit-code`), runs the dependency-free structural check, then runs
`hapifhir/org.hl7.fhir.core` release 7.0.2 `validator_cli.jar` on temurin 17 with
`-version 4.0.1 -tx n/a`. The jar is sha256-pinned (the digest GitHub publishes
on the release asset) for the same reason the workflow's actions are SHA-pinned,
and the gate is the **exit code**: `ValidateCommand.call()` returns 1 when
`SystemExitManager.getError() > 0` and `ValidatorCli.main()` exits with it, both
read in the 7.0.2 sources rather than assumed from a log format.

`-tx n/a` means no terminology server, so the official run checks structure and
the R4 invariants but does not look codes up; code existence is guaranteed by
`loinc.json` plus the offline checker's "verified or ours" rule, and a
**non-blocking** second run against `tx.fhir.org` puts the terminology result in
the log without making the gate depend on somebody else's service.
`make fhir-validate` does the same locally and prints which validator it used.

Both CI invocations also assert that the validator's own `Sources to validate:`
line names the sample bundle. A validator given a path that does not exist exits 0
having checked nothing, and this step is `continue-on-error`, so a mistyped path
would have produced a permanently green step that proved nothing — which is
exactly what one commit of this branch did. The check is the tool's own statement
of what it read, not a guess about its log format.

*Outcome:* run `39337225868` — job success, `Success: 0 errors, 52 warnings,
6 notes` with `-tx n/a` and `Success: 0 errors, 43 warnings, 2 notes` against
`tx.fhir.org`. The 52 warnings are accepted deliberately and itemised in §8 of
the verification note; cutting them is Day-48 step 3.

### 11. A share token is half a credential

Tokens are redacted from access logs, unhandled-exception logs and Sentry URLs
(`redact_path()`, `SENSITIVE_LOG_FIELDS`), and the passcode, secret and derived
key are added to the fields the Sentry scrubber drops. The token alone does not
decrypt anything, but it identifies a link and shortens a brute-force search, and
"we never log secrets" is only true if the things that appear in URLs are treated
as secrets too.

### 12. The PDF says the disclaimer on every page, and never lies silently

The banner is boxed on page 1 and the footer repeats it with the page number on
every page, because a printed document gets separated from its cover sheet.
reportlab's built-in Helvetica cannot draw Devanagari or Bengali and the
repository ships no TrueType font, so `pdf_safe()` substitutes `?` and prints one
note on the page saying that it did. Silent holes in a clinical document would be
worse than an honest note; embedding a licensed font is parked.

## Consequences

* A person can hand a professional a real document, and can see whether it was
  read — without Manovia becoming a health IT system or claiming to be one.
* The database cannot be read to recover a shared document, and neither can a
  backup or an API response. The operator cannot open a share from the link
  alone after the fact.
* A lost fragment means an unreadable share. That is the point of not storing the
  key, and the mitigation is revoke-and-recreate, which the UI offers in one
  click.
* Two implementations of one derivation (Python and WebCrypto) now have to agree
  forever. They are pinned against a committed vector produced by the backend, so
  drift fails a test rather than a person's document.
* `reportlab` is a base dependency and brings pillow and charset-normalizer with
  it; both were pip-audited clean at the pinned versions when this landed.
* The FHIR sample is a committed artifact that must be regenerated whenever the
  builder changes. The freshness gate makes forgetting fail loudly.
* The bridge adds unauthenticated endpoints to the attack surface, mitigated by
  rate limiting, uniform refusals, a view budget, a passcode budget and a
  deployment kill switch.
* The official validator's real execution happens in CI, not on the development
  machine: this sandbox has no JVM and cannot download the jar. While it had not
  run, that was recorded as unrun-here rather than claimed as passed; it has now
  run (CI run `39337225868`) and reports 0 errors under both terminology
  configurations, so the consequence that remains is the 52 accepted warnings, not
  an unexecuted gate.

## Rejected alternatives

* **A FHIR server / SMART-on-FHIR app** — see Decision 1. Also rejected: exposing
  the person's whole record over a REST API to a recipient Manovia cannot
  identify.
* **True client-side document assembly** — the right long-term answer, and a
  port-plus-parity project of its own (Decision 2). Parked.
* **The `fhir.resources` Python package** for building or validating the bundle —
  its published releases cover R5, R4B, STU3 and DSTU2, not R4; it was installed,
  checked and removed rather than used to emit a bundle for a version it does not
  model.
* **Reusing the Day 17 envelope encryption for share documents** — the wrapped
  data key is stored server-side, which is exactly what a share must not do: the
  recipient's copy has to be unreadable to the operator.
* **A generated passcode only, or a passcode-free link** — the passcode is part
  of the key derivation, so removing it weakens the ciphertext as well as the
  door; letting people choose their own keeps it memorable without lowering the
  floor (`SHARE_PASSCODE_MIN_LENGTH`).
* **Distinguishing expired from unknown tokens in the error** — an enumeration
  oracle (Decision 3).
* **A new `ConsentKind` for sharing** — versioned, localised consent content and
  parity tests for no benefit over a preference row plus the consent hash
  (Decision 6).
* **SNOMED CT or LOINC codes for Manovia-specific concepts** (mood means, band
  labels, document kinds) — unverified claims in somebody's record (Decision 4).
* **Storing the audit trail with IP and user agent** — it would answer a question
  the owner does not ask and create a second log of sensitive access
  (Decision 8).

## Verification

[docs/verification/day47-clinician-share-bridge.md](../verification/day47-clinician-share-bridge.md)
records the real runs: the end-to-end walk-through transcript (ciphertext-only
row, viewer open, browser-side decryption, PDF banner, LOINC codes with their
source URLs, audit log, revoke), the 18 security tests and 14 integration tests,
and the 25 artifact tests. Suite totals are in
[PROGRESS.md](../../PROGRESS.md) under Day 47.
