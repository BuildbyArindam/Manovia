"""What each allow-listed tool actually does.

Every executor is deterministic and offline. Three of the
five tools do not touch an LLM at all — the *agent* decides
which tool to call, but the tool's work is templated and
reviewable, which is what makes its output safe to hand back
to a person:

* ``search_directory`` filters the curated directory.
* ``draft_message`` composes warm, plain copy from the
  person's own purpose — and the draft goes to *them*, to
  edit and send themselves. It is never sent by the agent.
* ``make_appointment_prep`` turns their bullet list into a
  prep sheet.
* ``set_reminder`` returns the notification record (the
  device delivers it; the backend keeps the record the
  person can see in their action log).
* ``explain_options`` returns reviewed explainer text.

Executors receive already-validated arguments; the loop is
the only caller and it validates before calling.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.services.agent.directory import DirectoryStore
from app.services.agent.sanitise import sanitise_text

DRAFT_OPENERS: dict[str, str] = {
    "professor": "Dear Professor",
    "manager": "Dear Manager",
    "counsellor": "Dear Counsellor",
    "doctor": "Dear Doctor",
    "friend": "Hi",
    "family": "Dear",
    "landlord": "Dear Landlord",
    "other": "Hello",
}

DRAFT_CLOSERS: dict[str, str] = {
    "professor": "With thanks,\n[Your name]\n[Your course/roll number]",
    "manager": "Best regards,\n[Your name]",
    "counsellor": "With thanks,\n[Your name]",
    "doctor": "With thanks,\n[Your name]",
    "friend": "Take care,\n[Your name]",
    "family": "With love,\n[Your name]",
    "landlord": "Best regards,\n[Your name]",
    "other": "Best regards,\n[Your name]",
}

#: Reviewed explainer text. Plain words, no promises, no
#: diagnosis — the same tone rules as the rest of Manovia.
_EXPLAINERS: dict[str, str] = {
    "therapy_types": (
        "Common talking therapies include CBT (learning to "
        "notice and change unhelpful thought patterns), "
        "counselling (a safe space to talk through what is "
        "happening), and guided self-help (structured "
        "workbooks or courses with support). Which one fits "
        "depends on what you are carrying; a counsellor or "
        "doctor can help you choose. Manovia does not "
        "recommend a specific therapy for you."
    ),
    "helpline_vs_therapy": (
        "A helpline is for right now: a trained listener, "
        "free, often 24/7, no appointment. Therapy is for "
        "the longer term: scheduled sessions with a "
        "professional working with you over weeks. You can "
        "use a helpline today and still wait for a therapy "
        "slot; they do not replace each other."
    ),
    "first_appointment": (
        "For a first appointment: note when the difficulty "
        "started, what makes it better or worse, and how it "
        "affects your day. Say the words you would use, not "
        "the 'right' ones. You can bring someone with you. "
        "Ask what happens next and how soon you can be seen. "
        "You do not have to have all the answers before you "
        "go."
    ),
    "costs": (
        "Free options exist: national helplines are free, "
        "campus counselling is usually free for students, "
        "and in England NHS talking therapies are free. "
        "Low-cost directories (for example Open Path in the "
        "US/Canada) charge a small membership for reduced "
        "session fees. Ask every provider about sliding "
        "scales — many adjust fees to income."
    ),
    "crisis_plan": (
        "A crisis plan is written before you need it: your "
        "early warning signs, what helps, who you would "
        "call, and the numbers for your area. If you are in "
        "danger right now, call your local emergency number "
        "or a crisis helpline first — a plan is for "
        "afterwards."
    ),
}


class Executors:
    """The five tool bodies, bound to one directory store."""

    def __init__(self, directory: DirectoryStore) -> None:
        self._directory = directory

    def search_directory(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Verified directory entries matching every filter."""
        results = self._directory.search(
            region=arguments.get("region"),
            language=arguments.get("language"),
            need=arguments.get("need"),
            budget=arguments.get("budget"),
        )
        return {
            "count": len(results),
            "filters": {
                "region": arguments.get("region"),
                "language": arguments.get("language"),
                "need": arguments.get("need"),
                "budget": arguments.get("budget"),
            },
            "results": results,
        }

    def draft_message(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """A draft the person edits and sends themselves."""
        recipient = str(arguments["recipient_type"])
        purpose = sanitise_text(str(arguments["purpose"]))
        opener = DRAFT_OPENERS.get(recipient, DRAFT_OPENERS["other"])
        closer = DRAFT_CLOSERS.get(recipient, DRAFT_CLOSERS["other"])
        body = (
            f"I'm writing because {purpose}. "
            "Could you let me know what options I have and any "
            "steps to take? Thank you for your time."
        )
        draft = f"{opener},\n\n{body}\n\n{closer}"
        return {
            "draft": draft,
            "recipient_type": recipient,
            "note": (
                "This is a draft for YOU to edit and send yourself. The agent cannot send messages."
            ),
        }

    def make_appointment_prep(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """A prep sheet: what to say, what to ask, what to bring."""
        items = [sanitise_text(str(item)) for item in arguments["summary_items"]]
        numbered = "\n".join(f"{index}. {item}" for index, item in enumerate(items, 1))
        return {
            "what_to_say": numbered,
            "what_to_ask": (
                "1. What happens next?\n"
                "2. How soon can I be seen?\n"
                "3. Is there anything I should do before the "
                "next appointment?"
            ),
            "what_to_bring": (
                "Any notes you made, a list of what has helped "
                "or made things worse, and someone you trust if "
                "you would like company."
            ),
            "note": "This is a prep sheet for YOUR appointment. "
            "The agent cannot book appointments.",
        }

    def set_reminder(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Record a local notification reminder (after confirmation)."""
        return {
            "reminder_text": sanitise_text(str(arguments["text"])),
            "reminder_when": str(arguments["when"]),
            "note": (
                "Reminder recorded for this device. The agent "
                "cannot call, text, email or book anything."
            ),
        }

    def explain_options(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Reviewed plain-language explainer text."""
        topic = str(arguments.get("topic", "therapy_types"))
        return {"topic": topic, "explainer": _EXPLAINERS[topic]}

    def execute(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Dispatch to the right executor (both already validated)."""
        method: Callable[[dict[str, Any]], dict[str, Any]] = getattr(self, tool)
        return method(arguments)
