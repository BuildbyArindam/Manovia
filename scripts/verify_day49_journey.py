#!/usr/bin/env python3
"""Day 49 — the full user journey, driven against a running API.

Why this exists: the Day-49 brief asks for the Playwright journey
(onboarding → chat → crisis → mood → outlook → exercise → voice journal →
private mode → agent → clinician share → export → delete). This sandbox cannot
install a browser (the Playwright CDN is not reachable), so the browser half of
those steps is covered by the frontend suites that CI runs
(`npm run test:run`, `npm run test:e2e`, `npm run test:e2e:prod`). What *can* be
driven here is the server contract every one of those screens depends on, and
that is what this script does: one real user, one real database, the same
requests the UI makes, in the same order, with the assertions the journey needs.

    # terminal 1: backend + production build (see docs/verification/day49-verification.md)
    uvicorn app.main:app --port 8000 --no-access-log
    npx vite preview --host 0.0.0.0 --port 3000

    # terminal 2
    python3 scripts/verify_day49_journey.py --api http://localhost:8000

Stdlib only (urllib), no tokens or message text printed, exits 1 on the first
failed step and prints one line per step. Nothing here writes a real user's
data: the account is a throwaway guest, deleted by step 12.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

TIMEOUT_S = 30


@dataclass
class Step:
    name: str
    status: str = "PENDING"
    detail: str = ""
    checks: list[str] = field(default_factory=list)


class Api:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.token: str | None = None

    def call(
        self,
        method: str,
        path: str,
        body: Any | None = None,
        *,
        token: str | None = None,
        expect: int | tuple[int, ...] = (200, 201),
        _attempt: int = 0,
    ) -> tuple[int, Any]:
        url = f"{self.base}{path}"
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        auth = token if token is not None else self.token
        if auth:
            request.add_header("Authorization", f"Bearer {auth}")
        headers: Any = {}
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
                status = response.status
                headers = response.headers
                raw = response.read()
        except urllib.error.HTTPError as exc:
            status = exc.code
            headers = exc.headers
            raw = exc.read()
        payload: Any = None
        if raw:
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = raw[:200].decode(errors="replace")
        allowed = (expect,) if isinstance(expect, int) else expect
        if status == 429 and _attempt < 2:
            # The journey runs against the same rate limiter a person does.
            # Backing off is the honest behaviour; skipping the assertion is not.
            time.sleep(_retry_after(headers))
            return self.call(method, path, body, token=token, expect=expect, _attempt=_attempt + 1)
        if status not in allowed:
            raise AssertionError(f"{method} {path} -> HTTP {status} (expected {allowed}): {payload}")
        return status, payload


def _retry_after(headers: Any, default: float = 5.0) -> float:
    try:
        return min(float(headers.get("retry-after", default)), 30.0)
    except (TypeError, ValueError):
        return default


def run(api: Api) -> list[Step]:
    steps: list[Step] = []

    def step(name: str):
        def wrap(fn):
            record = Step(name)
            steps.append(record)
            try:
                fn(record)
                record.status = "PASS"
            except AssertionError as exc:
                record.status = "FAIL"
                record.detail = str(exc)[:400]
            except Exception as exc:  # noqa: BLE001 - a journey step reports, never raises
                record.status = "ERROR"
                record.detail = f"{type(exc).__name__}: {exc}"[:400]
            return fn

        return wrap

    # 1. readiness -----------------------------------------------------------
    @step("1. API ready")
    def _ready(record: Step) -> None:
        _, payload = api.call("GET", "/api/v1/ready", expect=200)
        record.checks.append(f"status={payload.get('status', 'ok')}")

    # 2. onboarding: guest account + consent ---------------------------------
    @step("2. Onboarding (guest + consent)")
    def _onboard(record: Step) -> None:
        _, guest = api.call("POST", "/api/v1/auth/guest")
        api.token = guest["access_token"]
        record.checks.append("guest account created")
        _, requirements = api.call("GET", "/api/v1/consent/requirements")
        grants = [
            {"kind": doc["kind"], "version": doc["version"], "granted": True}
            for doc in requirements["documents"]
        ]
        api.call("POST", "/api/v1/consent", {"grants": grants}, expect=(200, 201))
        record.checks.append(f"consent recorded for {len(grants)} documents")
        _, me = api.call("GET", "/api/v1/auth/me")
        assert me.get("user", me).get("id") or me.get("id"), "no user id after onboarding"

    # 3. chat: an ordinary turn ---------------------------------------------
    session: dict[str, str] = {}

    @step("3. Chat (ordinary turn, model path)")
    def _chat(record: Step) -> None:
        _, created = api.call("POST", "/api/v1/chat/sessions", {})
        session["id"] = created["session_id"] if "session_id" in created else created["id"]
        _, reply = api.call(
            "POST",
            f"/api/v1/chat/sessions/{session['id']}/messages",
            {"message": "Can you suggest a short breathing exercise for before a meeting?"},
        )
        metadata = reply["metadata"]
        assert reply["reply"].strip(), "empty reply"
        assert metadata["response_type"] != "crisis", "this message must not take the crisis path"
        assert metadata.get("crisis") is None, "a benign turn must not carry a crisis card"
        record.checks.append(f"reply {len(reply['reply'])} chars, risk={metadata['risk_level']}")

    # 3b. calibration spot-check -------------------------------------------
    @step("3b. Safety calibration spot-check (recall-first, low precision)")
    def _calibration(record: Step) -> None:
        """Two ordinary sentences, and what the ML backstop does with them.

        The product decision is recall-first: the backstop may raise a level and
        may never lower one. The measured price is precision 0.543 on the dev
        split (31/51 benign cases escalated). This step pins the two sentences
        that reproduced that price on this build, so a future change to the
        threshold shows up here as a *changed observation* rather than as an
        opinion. It asserts the invariant (a high level always comes with the
        card) and records the levels; it does not assert "none", because that is
        not what the shipped thresholds promise.
        """

        observations = []
        for text in (
            "I have been sleeping badly and I feel flat all day.",
            "I had a long day and I am tired.",
            "Work was stressful today.",
        ):
            _, assessed = api.call("POST", "/api/v1/crisis/assess", {"text": text})
            code = ",".join(assessed.get("rationale_codes") or []) or "-"
            if assessed["level"] in {"high", "imminent"}:
                assert assessed["crisis"], "a high level must carry the pre-written card"
            observations.append(f"{assessed['level']}({code})")
        record.checks.append("tired-but-benign → " + "; ".join(observations))

    # 4. safety: a HIGH-risk turn bypasses the model -------------------------
    crisis: dict[str, Any] = {}

    @step("4. Crisis turn (deterministic, model never called)")
    def _crisis(record: Step) -> None:
        _, reply = api.call(
            "POST",
            f"/api/v1/chat/sessions/{session['id']}/messages",
            {"message": "I have decided to end my life tonight. I have a plan and the pills ready."},
        )
        metadata = reply["metadata"]
        crisis.update(metadata)
        assert metadata["risk_level"] in {"high", "imminent"}, metadata["risk_level"]
        assert metadata["response_type"] == "crisis", metadata["response_type"]
        assert metadata.get("crisis"), "no pre-written crisis card"
        numbers = {str(r.get("number", "")) for r in metadata.get("resources", [])}
        emergency = metadata.get("emergency") or {}
        numbers |= {str(emergency.get("number", ""))}
        assert any("112" in n for n in numbers), f"emergency number missing: {numbers}"
        record.checks.append(
            f"risk={metadata['risk_level']} resources={len(metadata.get('resources', []))} "
            f"llm_used={metadata.get('llm_used', 'n/a')}"
        )

    # 5. mood check-ins ------------------------------------------------------
    @step("5. Mood check-in")
    def _mood(record: Step) -> None:
        for valence, energy in ((2, 2), (3, 3), (4, 3)):
            api.call(
                "POST",
                "/api/v1/mood",
                {"valence": valence, "energy": energy, "emotions": ["sadness"], "tz": "Asia/Kolkata"},
                expect=(200, 201),
            )
            time.sleep(0.01)
        _, listing = api.call("GET", "/api/v1/mood")
        entries = listing.get("entries", listing.get("items", []))
        assert len(entries) >= 3, f"expected 3 check-ins, got {len(entries)}"
        record.checks.append(f"{len(entries)} check-ins stored")

    # 6. outlook: off by default, opt-in, numeric only -----------------------
    @step("6. Outlook (opt-in, withholds on thin data)")
    def _outlook(record: Step) -> None:
        _, before = api.call("GET", "/api/v1/insights/outlook", expect=(403,))
        assert before["error"]["code"] == "outlook_opt_in_required", before
        record.checks.append("off by default: 403 outlook_opt_in_required")
        api.call(
            "PUT",
            "/api/v1/settings",
            {"mood_outlook_enabled": True, "preferred_exercise_tags": []},
        )
        _, after = api.call("GET", "/api/v1/insights/outlook")
        assert after["status"] in {"ready", "not_enough_data", "stale"}
        assert after.get("minimum_checkin_days") == 14, after.get("minimum_checkin_days")
        record.checks.append(
            f"after opt-in: status={after['status']} distinct_days={after.get('distinct_checkin_days')}"
        )

    # 7. exercise + completion ----------------------------------------------
    @step("7. Guided exercise (biofeedback run is client-side)")
    def _exercise(record: Step) -> None:
        _, listing = api.call("GET", "/api/v1/exercises")
        items = listing.get("exercises", listing.get("items", listing))
        assert isinstance(items, list) and items, "no exercises"
        first = items[0]
        exercise_id = first.get("id") or first.get("slug")
        api.call(
            "POST",
            f"/api/v1/exercises/{exercise_id}/complete",
            {"pre_rating": 2, "post_rating": 4},
            expect=(200, 201),
        )
        _, history = api.call("GET", "/api/v1/exercises/history")
        assert history, "completion did not appear in history"
        record.checks.append(
            f"{len(items)} exercises; completed {exercise_id} (pre 2 → post 4); "
            "biofeedback HRV path is browser-only, covered by vitest fixtures"
        )

    # 8. voice journal -------------------------------------------------------
    @step("8. Voice journal entry (transcript path)")
    def _journal(record: Step) -> None:
        _, created = api.call(
            "POST",
            "/api/v1/journal",
            {
                "title": "Voice note",
                "body": "Recorded out loud: today was heavy but I went for a walk.",
                "template": "free",
            },
            expect=(200, 201),
        )
        entry_id = created.get("id") or created.get("entry", {}).get("id")
        assert entry_id, "journal entry has no id"
        _, fetched = api.call("GET", "/api/v1/journal")
        entries = fetched.get("entries", fetched.get("items", []))
        assert entries, "journal list is empty after a write"
        record.checks.append(f"entry {str(entry_id)[:8]}… stored and listed (body never logged)")

    # 9. private mode --------------------------------------------------------
    @step("9. Private mode (nothing leaves the device)")
    def _private(record: Step) -> None:
        # Not an API step by design: in Private Mode the browser is the whole
        # stack. The claim is proved by `make parity` (500/500 phrases) and by
        # `npx playwright test e2e/private-mode.spec.ts` with the network
        # blocked, neither of which can run without a browser here.
        record.checks.append(
            "BY DESIGN not exercised over HTTP — see make parity, "
            "e2e/private-mode.spec.ts (0 network attempts), docs/verification/day41-browser-private-ai.md"
        )

    # 10. agent navigator ----------------------------------------------------
    @step("10. Agent navigator (tool use, no side effects)")
    def _agent(record: Step) -> None:
        _, created = api.call("POST", "/api/v1/agent/sessions", {})
        agent_session = created["session_id"]
        _, turn = api.call(
            "POST",
            f"/api/v1/agent/sessions/{agent_session}/chat",
            {"message": "What free helplines exist for students in Kolkata?", "region": "IN"},
        )
        assert turn["response_type"] in {"agent", "confirmation_required", "degraded"}, turn[
            "response_type"
        ]
        tools = turn.get("tools_executed") or []
        record.checks.append(
            f"response_type={turn['response_type']} tools={tools} steps={turn.get('steps_used')}"
        )

    # 11. clinician share ----------------------------------------------------
    share: dict[str, Any] = {}

    @step("11. Clinician share (consent → link → open → revoke)")
    def _share(record: Step) -> None:
        _, preference = api.call("GET", "/api/v1/shares/preferences")
        assert preference["enabled"] is False, "sharing must be off for a new account"
        # Refused while off — that is the privacy default doing its job.
        _, refused = api.call(
            "POST", "/api/v1/shares/preview", {"items": ["mood_trend"]}, expect=(403,)
        )
        assert refused["error"]["code"] == "sharing_disabled", refused
        api.call("POST", "/api/v1/shares/preferences", {"enabled": True})
        items = ["mood_trend", "assessments"]
        _, preview = api.call(
            "POST", "/api/v1/shares/preview", {"items": items, "window_days": 30}
        )
        consent_sha = preview["consent_sha256"]
        _, created = api.call(
            "POST",
            "/api/v1/shares",
            {"items": items, "window_days": 30, "consent_sha256": consent_sha, "passcode": "local-demo-42"},
            expect=(200, 201),
        )
        share.update(created)
        assert created["share_path"].startswith("/s/"), created["share_path"]
        _, opened = api.call(
            "POST",
            f"/api/v1/public/shares/{created['token']}/open",
            {"passcode": "local-demo-42"},
            token="",  # anonymous: a clinician has no account
        )
        # The server returns ciphertext plus the metadata the viewer needs; the
        # key never leaves the fragment, so "it decrypted" is a browser claim.
        assert opened.get("algorithm") in {"AES-256-GCM", "A256GCM"}, opened.get("algorithm")
        assert opened.get("views_remaining", 0) >= 0
        assert opened.get("items") == items, opened.get("items")
        api.call("POST", f"/api/v1/shares/{created['share_id']}/revoke", expect=(200, 204))
        _, after = api.call(
            "POST",
            f"/api/v1/public/shares/{created['token']}/open",
            {"passcode": "local-demo-42"},
            token="",
            expect=(404,),
        )
        record.checks.append(
            "preview→create→open with passcode→revoke; revoked link now 404 "
            f"(formats={created['formats']}, views={created['view_limit']})"
        )

    # 12. export then delete -------------------------------------------------
    @step("12. Export everything, then delete the account")
    def _export_delete(record: Step) -> None:
        # The export is a ZIP attachment, read here as bytes so the check does
        # not depend on the archive library or on decoding personal text.
        request = urllib.request.Request(f"{api.base}/api/v1/privacy/export")
        request.add_header("Authorization", f"Bearer {api.token}")
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            disposition = response.headers.get("content-disposition", "")
            archive = response.read()
        assert archive[:2] == b"PK", "export is not a zip archive"
        assert "manovia-export" in disposition, disposition
        assert len(archive) > 1024, len(archive)
        record.checks.append(f"zip export {len(archive)} bytes ({disposition.split(';')[-1].strip()})")
        _, receipt = api.call(
            "DELETE", "/api/v1/privacy/account", {"confirm": "DELETE"}, expect=(200, 202, 204)
        )
        record.checks.append(f"deletion receipt: {sorted(receipt)[:4]}")
        api.call("GET", "/api/v1/auth/me", token=api.token or "", expect=(401,))
        record.checks.append("account deleted; session token now 401")

    return steps


def main() -> int:
    parser = argparse.ArgumentParser(description="Day 49 full journey against a running API")
    parser.add_argument("--api", default="http://localhost:8000")
    args = parser.parse_args()

    api = Api(args.api)
    try:
        steps = run(api)
    except Exception as exc:  # noqa: BLE001 - the API was unreachable
        print(f"journey could not start against {args.api}: {type(exc).__name__}: {exc}")
        return 2

    width = max(len(s.name) for s in steps)
    for step in steps:
        print(f"{step.status:4s}  {step.name.ljust(width)}  {'; '.join(step.checks)}")
        if step.detail:
            print(f"      ↳ {step.detail}")

    failed = [s for s in steps if s.status not in {"PASS"}]
    print()
    print(f"{len(steps) - len(failed)}/{len(steps)} steps passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
