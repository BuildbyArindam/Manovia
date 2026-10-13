"""Sentry error reporting that respects privacy (Day 27).

The principle, applied to a third party this time: **Manovia can report that
something broke without telling the vendor what the person said.** Everything
runs through a ``before_send`` scrubber that:

1. drops the request body, query string and cookies outright;
2. reduces request headers to a short allowlist;
3. walks every remaining string value and (a) drops values under keys that may
   carry user text or credentials — the same blocklist the log pipeline uses —
   and (b) runs the Day 10 PII redactor over free-text fields such as
   exception messages, so an email or phone number that leaked into an error
   string goes out as a typed placeholder, never as the original;
4. strips the local variables from every stacktrace frame — that is where a
   parsed request body or a raw query string actually hides in a Sentry
   event, not in the obvious request block.

Nothing here is optional and there is deliberately no flag that turns the
scrubbing off: a kill-switch for privacy controls is a liability, not a
feature. Sentry itself is opt-in — without ``SENTRY_DSN`` the module is a
no-op and the app never contacts the network.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration

from app.core.logging import SENSITIVE_LOG_FIELDS
from app.services.nlp.redaction import Redactor

#: Request headers that may leave the process. Identity, session material and
#: anything request-specific stays behind.
_ALLOWED_REQUEST_HEADERS: Final[frozenset[str]] = frozenset(
    {"content-type", "content-length", "accept", "user-agent", "x-request-id"}
)

#: Request keys dropped before anything else looks at them.
_DROPPED_REQUEST_KEYS: Final[frozenset[str]] = frozenset(
    {"data", "query_string", "cookies", "json", "form", "files"}
)

_REDACTOR: Final[Redactor] = Redactor()


def _scrub_text(value: str) -> str:
    """Replace recognised identifiers with typed placeholders."""
    return _REDACTOR.redact(value).text


def _drop_blocked_keys(value: Any) -> Any:
    """Recursively drop values under keys that may carry user text/secrets."""
    if isinstance(value, Mapping):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in SENSITIVE_LOG_FIELDS:
                continue
            cleaned[str(key)] = _drop_blocked_keys(item)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [_drop_blocked_keys(item) for item in value]
    return value


def _scrub_free_text(value: Any) -> Any:
    """Recursively run the PII redactor over string leaves."""
    if isinstance(value, str):
        return _scrub_text(value)
    if isinstance(value, Mapping):
        return {key: _scrub_free_text(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub_free_text(item) for item in value]
    return value


def _strip_frame_locals(stacktrace: Any) -> Any:
    """Remove local variables from every stacktrace frame.

    Frame locals are the quiet leak: the request's parsed body, the ASGI
    scope's raw query string, endpoint function arguments — all of it shows up
    as ``vars`` on the frame where the exception passed through. Type, module,
    line numbers and source context lines stay; the values do not.
    """
    if not isinstance(stacktrace, Mapping):
        return stacktrace
    frames = stacktrace.get("frames")
    if not isinstance(frames, list):
        return stacktrace
    cleaned_frames = []
    for frame in frames:
        if isinstance(frame, Mapping):
            cleaned_frames.append({key: value for key, value in frame.items() if key != "vars"})
        else:
            cleaned_frames.append(frame)
    return {**stacktrace, "frames": cleaned_frames}


def _scrub_request(request: Any) -> Any:
    """Reduce an event's request context to the minimum that is still useful."""
    if not isinstance(request, Mapping):
        return request
    cleaned = {key: value for key, value in request.items() if key not in _DROPPED_REQUEST_KEYS}
    headers = cleaned.get("headers")
    if isinstance(headers, Mapping):
        cleaned["headers"] = {
            key: value
            for key, value in headers.items()
            if str(key).lower() in _ALLOWED_REQUEST_HEADERS
        }
    # The URL minus its query: the path is what an engineer needs; the query
    # string can carry anything a person (or a broken client) put there.
    for url_key in ("url",):
        url = cleaned.get(url_key)
        if isinstance(url, str) and "?" in url:
            cleaned[url_key] = url.split("?", 1)[0]
    if "query_string" in cleaned:
        cleaned["query_string"] = ""
    return _drop_blocked_keys(cleaned)


def scrub_sentry_event(event: dict[str, Any], hint: Any) -> dict[str, Any]:
    """The ``before_send`` hook: nothing leaves this function with user text."""
    cleaned = _drop_blocked_keys(dict(event))

    if isinstance(cleaned.get("request"), Mapping):
        cleaned["request"] = _scrub_request(cleaned["request"])

    # Exception messages are the likeliest place user text ends up (an f-string
    # that echoes a payload). Redact the value, keep the type, and strip the
    # frame locals (see _strip_frame_locals — that is where a request body or
    # query string actually hides).
    exceptions = cleaned.get("exception")
    if isinstance(exceptions, Mapping):
        values = exceptions.get("values")
        if isinstance(values, list):
            redacted_values = []
            for entry in values:
                if isinstance(entry, Mapping):
                    if isinstance(entry.get("value"), str):
                        entry = {**entry, "value": _scrub_text(entry["value"])}
                    entry = {**entry, "stacktrace": _strip_frame_locals(entry.get("stacktrace"))}
                redacted_values.append(entry)
            exceptions = {**exceptions, "values": redacted_values}
        cleaned["exception"] = exceptions

    # Thread dumps (events with "threads") carry frames too.
    threads = cleaned.get("threads")
    if isinstance(threads, Mapping) and isinstance(threads.get("values"), list):
        cleaned["threads"] = {
            **threads,
            "values": [
                {**entry, "stacktrace": _strip_frame_locals(entry.get("stacktrace"))}
                if isinstance(entry, Mapping)
                else entry
                for entry in threads["values"]
            ],
        }

    # Structured log capture and breadcrumbs: same treatment.
    for key in ("logentry", "message"):
        part = cleaned.get(key)
        if isinstance(part, Mapping):
            cleaned[key] = {
                sub_key: _scrub_text(sub_value) if isinstance(sub_value, str) else sub_value
                for sub_key, sub_value in part.items()
            }
        elif isinstance(part, str):
            cleaned[key] = _scrub_text(part)

    breadcrumbs = cleaned.get("breadcrumbs")
    if isinstance(breadcrumbs, Mapping) and isinstance(breadcrumbs.get("values"), list):
        cleaned["breadcrumbs"] = {
            **breadcrumbs,
            "values": [_scrub_free_text(crumb) for crumb in breadcrumbs["values"]],
        }

    extra = cleaned.get("extra")
    if isinstance(extra, Mapping):
        cleaned["extra"] = _scrub_free_text(dict(extra))

    # The user context carries at most the pseudonymous account id — no email,
    # no username, no IP (Sentry's own IP inference is off via
    # send_default_pii=False at init).
    user = cleaned.get("user")
    if isinstance(user, Mapping):
        cleaned["user"] = {"id": user["id"]} if "id" in user else {}

    result: dict[str, Any] = cleaned
    return result


def init_sentry(settings: Any) -> bool:
    """Initialise Sentry when a DSN is configured; return whether it started.

    Called from ``create_app`` before anything can fail. Without a DSN this is
    a contract-free no-op: the offline test suite and self-hosted deployments
    never touch the network.
    """
    dsn = str(getattr(settings, "sentry_dsn", "") or "").strip()
    if not dsn:
        return False

    environment = getattr(settings, "sentry_environment", None) or settings.app_env
    traces_sample_rate = float(getattr(settings, "sentry_traces_sample_rate", 0.0) or 0.0)

    sentry_sdk.init(
        dsn=dsn,
        environment=str(environment),
        release="manovia-api@0.1.0",
        before_send=scrub_sentry_event,
        # Privacy hard defaults: the SDK must not invent identity context.
        send_default_pii=False,
        attach_stacktrace=True,
        # Performance tracing is off unless explicitly enabled: traces carry
        # route timing only, but there is no reason to pay the volume by
        # default on a wellbeing service.
        traces_sample_rate=traces_sample_rate or None,
        integrations=[FastApiIntegration()],
    )
    return True


def capture_exception(exc: BaseException) -> None:
    """Report an exception to Sentry if (and only if) Sentry is active.

    The explicit call exists because the app's own ``unhandled_exception_handler``
    converts escapes into a response — nothing propagates out of the ASGI stack
    for the SDK's middleware to catch.
    """
    client = sentry_sdk.get_client()
    if client.is_active():
        sentry_sdk.capture_exception(exc)
