"""Day 45 verification: the meditation API end-to-end against a live server.

This is the operator-side half of the Day 45 verification step: it runs the
real HTTP surface (not the unit tests) against a backend started with
``LLM_PROVIDER=fake`` and prints real results.

Start the backend first, from ``backend/``::

    LLM_PROVIDER=fake uvicorn app.main:app --host 127.0.0.1 --port 8000

then (from the repository root)::

    .venv/bin/python evals/meditation/verify_api_e2e.py
    .venv/bin/python evals/meditation/verify_api_e2e.py --base-url http://127.0.0.1:8000

Checks
------
* public themes catalogue;
* compose three scripts (seated calm, driving focus, hi sleep) through the
  real pipeline;
* determinism: same seed regenerates the same content hash;
* local TTS render: real WAV bytes, content-hash header, cache hit on
  re-render, and a tampered transcript rejected with 422;
* captions endpoint (estimated timing, said so);
* 422 for a closed-eye request while driving (the eyes-open rule).

Exit code 0 = every check passed.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "evals" / "meditation"))

WAV_RIFF = b"RIFF"
WAV_WAVE = b"WAVE"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    api = f"{base}/api/v1"

    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, detail))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

    email = f"med-verify-{uuid.uuid4().hex[:8]}@example.com"
    with httpx.Client(timeout=60.0) as client:
        # --- session ------------------------------------------------------
        reg = client.post(
            f"{api}/auth/register", json={"email": email, "password": "verify-45-verify-45"}
        )
        assert reg.status_code == 201, reg.text
        token = reg.json()["access_token"]
        auth = {"Authorization": f"Bearer {token}"}

        # --- public catalogue ----------------------------------------------
        themes = client.get(f"{api}/meditation/themes")
        theme_ids = [t["id"] for t in themes.json()["themes"]]
        check(
            "themes catalogue (public)",
            themes.status_code == 200 and len(theme_ids) == 6,
            f"HTTP {themes.status_code}, themes={theme_ids}",
        )

        # --- compose three real scripts -------------------------------------
        payload = {
            "theme": "calm",
            "duration_seconds": 180,
            "voice_speed": 1.0,
            "language": "en",
            "hour_of_day": 21,
            "context": "seated",
            "eyes_open": True,
            "preferences": ["quiet", "warmth"],
            "seed": 45,
        }
        r1 = client.post(f"{api}/meditation/scripts", json=payload, headers=auth)
        check("compose seated calm (en)", r1.status_code == 200, f"HTTP {r1.status_code}")
        script = r1.json() if r1.status_code == 200 else {}

        again = client.post(f"{api}/meditation/scripts", json=payload, headers=auth)
        check(
            "determinism: same seed → same content hash",
            again.status_code == 200 and again.json().get("content_hash") == script.get("content_hash"),
            f"hash={str(script.get('content_hash'))[:16]}…",
        )

        driving = {
            "theme": "focus",
            "duration_seconds": 180,
            "voice_speed": 1.0,
            "language": "en",
            "context": "driving",
            "eyes_open": True,
            "seed": 46,
        }
        r2 = client.post(f"{api}/meditation/scripts", json=driving, headers=auth)
        check(
            "compose driving focus (en) with warning",
            r2.status_code == 200 and r2.json()["safety"]["driving_warning"] is True,
            f"HTTP {r2.status_code}, driving_warning={r2.json()['safety']['driving_warning'] if r2.status_code == 200 else '?'}",
        )
        driving_script = r2.json() if r2.status_code == 200 else {}

        sleep_hi = {
            "theme": "sleep",
            "duration_seconds": 300,
            "voice_speed": 1.0,
            "language": "hi",
            "context": "seated",
            "eyes_open": False,
            "seed": 47,
        }
        r3 = client.post(f"{api}/meditation/scripts", json=sleep_hi, headers=auth)
        check(
            "compose sleep (hi, eyes closed)",
            r3.status_code == 200 and r3.json()["language"] == "hi",
            f"HTTP {r3.status_code}, words={r3.json().get('word_count', '?')}",
        )

        # --- the closed-eye rule --------------------------------------------
        bad = dict(driving, eyes_open=False)
        r_bad = client.post(f"{api}/meditation/scripts", json=bad, headers=auth)
        check(
            "422: closed eyes while driving is rejected",
            r_bad.status_code == 422,
            f"HTTP {r_bad.status_code}",
        )

        # --- local TTS render -------------------------------------------------
        if script:
            a1 = client.post(f"{api}/meditation/audio", json={"script": script}, headers=auth)
            body = a1.content
            ok_wav = a1.status_code == 200 and body[:4] == WAV_RIFF and body[8:12] == WAV_WAVE
            check(
                "audio render: real WAV from the local provider",
                ok_wav,
                f"HTTP {a1.status_code}, {len(body)} bytes, "
                f"provider={a1.headers.get('x-render-provider')}, "
                f"cache={a1.headers.get('x-render-cache')}, "
                f"sha256={str(a1.headers.get('x-content-sha256'))[:16]}…",
            )
            a2 = client.post(f"{api}/meditation/audio", json={"script": script}, headers=auth)
            check(
                "audio cache hit on re-render (content-hash key)",
                a2.status_code == 200 and a2.headers.get("x-render-cache") == "hit",
                f"cache={a2.headers.get('x-render-cache')}, same bytes={a2.content == body}",
            )
            tampered = json.loads(json.dumps(script))
            tampered["sections"][0]["text"] = tampered["sections"][0]["text"] + " TAMPERED"
            r_t = client.post(f"{api}/meditation/audio", json={"script": tampered}, headers=auth)
            check(
                "422: tampered transcript rejected (hash mismatch)",
                r_t.status_code == 422,
                f"HTTP {r_t.status_code}",
            )

            caps = client.post(
                f"{api}/meditation/audio/captions", json={"script": script}, headers=auth
            )
            cj = caps.json() if caps.status_code == 200 else {}
            check(
                "captions with honest timing basis",
                caps.status_code == 200 and cj.get("timing_basis") == "estimated"
                and len(cj.get("captions", [])) == len(script["sections"]),
                f"HTTP {caps.status_code}, basis={cj.get('timing_basis')}, "
                f"captions={len(cj.get('captions', []))}, provider={cj.get('provider')}",
            )

        # --- tone review: two full scripts -------------------------------------
        out_dir = REPO_ROOT / "evals" / "reports"
        out_dir.mkdir(parents=True, exist_ok=True)
        for label, sc in (("seated-calm-en", script), ("driving-focus-en", driving_script)):
            if not sc:
                continue
            path = out_dir / f"day45-script-{label}.txt"
            lines = [f"# {label} — {sc['content_hash'][:16]}… (words={sc['word_count']})", ""]
            for section in sc["sections"]:
                lines.append(f"[{section['role']} / {section['source']}]")
                lines.append(section["text"])
                lines.append("")
            path.write_text("\n".join(lines), encoding="utf-8")
            print(f"      full script written: {path.relative_to(REPO_ROOT)}")

    failed = [r for r in results if not r[1]]
    print("=" * 70)
    print(f"api e2e: {len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
