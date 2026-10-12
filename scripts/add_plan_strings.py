"""One-shot script: add plan strings + numerals to backend/app/content/i18n/*.json.

Day 23: the planner's user-facing copy moves from hard-coded English in
``services/plan/daily_plan.py`` into the localised content files. Run once,
commit the result, delete nothing — the script stays as a record of the edit.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
I18N = ROOT / "backend" / "app" / "content" / "i18n"

NUMERALS = {
    "en": list("0123456789"),
    "hi": ["०", "१", "२", "३", "४", "५", "६", "७", "८", "९"],
    "bn": ["০", "১", "২", "৩", "৪", "৫", "৬", "৭", "৮", "৯"],
}

EN = {
    "day_part.morning": "morning",
    "day_part.afternoon": "afternoon",
    "day_part.evening": "evening",
    "plan.first_check_in.title": "Take a first check-in",
    "plan.first_check_in.why": (
        "There is no mood check-in yet. Ten seconds helps Manovia learn your "
        "rhythm, and it is fine to skip if today is not the day."
    ),
    "plan.open_thought_record.title": "Finish a thought record",
    "plan.open_thought_record.why": (
        "A recent thought record has no 'what helped' yet. You can add that "
        "whenever you like, or leave the record as it is."
    ),
    "plan.low_mood.title": "Try a gentle exercise",
    "plan.low_mood.why": (
        "Your check-ins over the last few days are lower than the days before. "
        "{exercise_title} is short, and there is no right way to do it."
    ),
    "plan.anxiety.title": "Ground for a couple of minutes",
    "plan.anxiety.why": (
        "A recent check-in was tagged with anxiety or fear. "
        "{exercise_title} offers an outside focus, if you want one. It is optional."
    ),
    "plan.missed_check_in.title": "Check in when you are ready",
    "plan.missed_check_in.why": (
        "Your last check-in was {days} days ago. A quick one keeps the "
        "picture honest, and skipping costs nothing."
    ),
    "plan.exercise_habit.title": "A short exercise for the {day_part}",
    "plan.exercise_habit.why": (
        "You have done exercises on {days} of the last {week_days} days. "
        "{exercise_title} is a small next step. {preference_note}"
    ),
    "plan.preference_note.matched": "It matches the exercise types you chose in Settings.",
    "plan.preference_note.time_of_day": "It fits this part of the day.",
    "plan.rest.title": "Today can be a rest day",
    "plan.rest.why": (
        "You have been active on {days} of the last {week_days} days. "
        "Doing nothing today counts too."
    ),
    "plan.support.title": "Support options are here",
    "plan.support.why": (
        "Your most recent screening included an answer we show support options "
        "for. That is the only thing today's plan does; everything else is set aside."
    ),
}

HI = {
    "day_part.morning": "सुबह",
    "day_part.afternoon": "दोपहर",
    "day_part.evening": "शाम",
    "plan.first_check_in.title": "पहला मूड चेक-इन कीजिए",
    "plan.first_check_in.why": (
        "अभी तक कोई मूड चेक-इन नहीं हुआ है। दस सेकंड से मनोविया को आपकी लय समझने में "
        "मदद मिलती है; आज का दिन ठीक न लगे तो छोड़ देना भी बिल्कुल ठीक है।"
    ),
    "plan.open_thought_record.title": "एक विचार-रिकॉर्ड पूरा कीजिए",
    "plan.open_thought_record.why": (
        "हाल के एक विचार-रिकॉर्ड में 'क्या मदद कर गया' अभी खाली है। जब चाहें भर "
        "दीजिए, या रिकॉर्ड जैसा है वैसा ही रहने दीजिए।"
    ),
    "plan.low_mood.title": "एक हल्का-सा अभ्यास कीजिए",
    "plan.low_mood.why": (
        "पिछले कुछ दिनों के आपके चेक-इन उनसे पहले के दिनों की तुलना में नीचे हैं। "
        "{exercise_title} छोटा है, और इसे करने का कोई एक सही तरीका नहीं है।"
    ),
    "plan.anxiety.title": "दो-चार मिनट ग्राउंडिंग कीजिए",
    "plan.anxiety.why": (
        "हाल के एक चेक-इन पर चिंता या डर का टैग था। {exercise_title} बाहर की किसी "
        "चीज़ पर ध्यान देता है, अगर आप चाहें। यह पूरी तरह वैकल्पिक है।"
    ),
    "plan.missed_check_in.title": "जब तैयार हों, चेक-इन कर लीजिए",
    "plan.missed_check_in.why": (
        "आपका पिछला चेक-इन {days} दिन पहले हुआ था। एक छोटा चेक-इन तस्वीर को सच "
        "रखता है, और छोड़ने की कोई कीमत नहीं।"
    ),
    "plan.exercise_habit.title": "{day_part} के लिए एक छोटा अभ्यास",
    "plan.exercise_habit.why": (
        "पिछले {week_days} दिनों में आपने {days} दिन अभ्यास किए हैं। "
        "{exercise_title} एक छोटा अगला कदम है। {preference_note}"
    ),
    "plan.preference_note.matched": "यह उन अभ्यास प्रकारों से मेल खाता है जो आपने सेटिंग्स में चुने थे।",
    "plan.preference_note.time_of_day": "यह दिन के इस हिस्से के लिए ठीक बैठता है।",
    "plan.rest.title": "आज आराम का दिन हो सकता है",
    "plan.rest.why": (
        "पिछले {week_days} दिनों में आप {days} दिन सक्रिय रहे हैं। आज कुछ न करना "
        "भी गिना जाएगा।"
    ),
    "plan.support.title": "सहारे के विकल्प यहीं हैं",
    "plan.support.why": (
        "आपकी सबसे हाल की स्क्रीनिंग में एक जवाब ऐसा था जिसके लिए हम सहारे के विकल्प "
        "दिखाते हैं। आज की योजना सिर्फ़ इतना ही करती है; बाक़ी सब किनारे रख दिया गया है।"
    ),
}

BN = {
    "day_part.morning": "সকাল",
    "day_part.afternoon": "দুপুর",
    "day_part.evening": "সন্ধ্যা",
    "plan.first_check_in.title": "প্রথম মুড চেক-ইন করুন",
    "plan.first_check_in.why": (
        "এখনও কোনো মুড চেক-ইন হয়নি। দশ সেকেন্ডেই ম্যানোভিয়া আপনার ছন্দ বুঝতে শেখে; "
        "আজকের দিনটা ঠিক না লাগলে এড়িয়ে যাওয়াও একদম ঠিক আছে।"
    ),
    "plan.open_thought_record.title": "একটি থট রেকর্ড শেষ করুন",
    "plan.open_thought_record.why": (
        "সাম্প্রতিক একটি থট রেকর্ডে 'কী সাহায্য করেছিল' এখনো খালি। ইচ্ছে করলে "
        "যেকোনো সময় যোগ করুন, অথবা রেকর্ডটি যেমন আছে তেমনই থাকতে দিন।"
    ),
    "plan.low_mood.title": "একটি হালকা ব্যায়াম করুন",
    "plan.low_mood.why": (
        "গত কয়েক দিনের আপনার চেক-ইনগুলো আগের দিনগুলোর তুলনায় নিচে। "
        "{exercise_title} ছোট, আর এটি করার কোনো একটা সঠিক উপায় নেই।"
    ),
    "plan.anxiety.title": "কয়েক মিনিট গ্রাউন্ডিং করুন",
    "plan.anxiety.why": (
        "সাম্প্রতিক একটি চেক-ইনে উদ্বেগ বা ভয়ের ট্যাগ ছিল। {exercise_title} চাইলে "
        "বাইরের কোনো দিকে মনোযোগ দেয়। এটি পুরোপুরি ঐচ্ছিক।"
    ),
    "plan.missed_check_in.title": "তৈরি হলে চেক-ইন করুন",
    "plan.missed_check_in.why": (
        "আপনার শেষ চেক-ইন ছিল {days} দিন আগে। ছোট একটি চেক-ইন ছবিটা সৎ রাখে, "
        "আর এড়িয়ে গেলে কোনো ক্ষতি নেই।"
    ),
    "plan.exercise_habit.title": "{day_part}-এর জন্য একটি ছোট ব্যায়াম",
    "plan.exercise_habit.why": (
        "গত {week_days} দিনের মধ্যে আপনি {days} দিন ব্যায়াম করেছেন। "
        "{exercise_title} একটি ছোট পরের ধাপ। {preference_note}"
    ),
    "plan.preference_note.matched": "এটি সেটিংসে আপনার বেছে নেওয়া ব্যায়ামের ধরনগুলোর সাথে মেলে।",
    "plan.preference_note.time_of_day": "এটি দিনের এই সময়টার সাথে মানানসই।",
    "plan.rest.title": "আজ বিশ্রামের দিন হতে পারে",
    "plan.rest.why": (
        "গত {week_days} দিনের মধ্যে আপনি {days} দিন সক্রিয় ছিলেন। আজ কিছু না করাও "
        "হিসাবে ধরা হবে।"
    ),
    "plan.support.title": "সহায়তার উপায়গুলো এখানেই আছে",
    "plan.support.why": (
        "আপনার সবচেয়ে সাম্প্রতিক স্ক্রিনিংয়ে এমন একটি উত্তর ছিল যার জন্য আমরা "
        "সহায়তার উপায় দেখাই। আজকের পরিকল্পনা শুধু এটাই করে; বাকি সব সরিয়ে রাখা হয়েছে।"
    ),
}

STRINGS = {"en": EN, "hi": HI, "bn": BN}

WHITELIST = [
    "emergency_number",
    "exercise_title",
    "days",
    "week_days",
    "day_part",
    "preference_note",
]


def main() -> None:
    for locale in ("en", "hi", "bn"):
        path = I18N / f"{locale}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["numerals"] = NUMERALS[locale]
        payload["placeholder_whitelist"] = WHITELIST
        payload["strings"] = STRINGS[locale]
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"updated {path}")


if __name__ == "__main__":
    main()
