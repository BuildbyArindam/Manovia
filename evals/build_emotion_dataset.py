"""Build the hand-labelled emotion evaluation dataset (Day 24).

Writes ``evals/datasets/emotion_cases.jsonl`` — 162 synthetic, hand-labelled
texts across the nine internal emotion labels (``joy``, ``sadness``, ``anger``,
``fear``, ``anxiety``, ``shame``, ``loneliness``, ``calm``, ``neutral``) and
three languages (``en``, ``hi``, ``bn``):

* 18 cases per emotion × 9 emotions = 162 cases (>= 150 floor);
* per emotion: 8 English (``en``), 5 Hindi (``hi``, Devanagari + romanised
  Hinglish), 5 Bengali (``bn``, Bengali script + romanised Banglish);
* two regimes per language/emotion:
  - ``in_domain`` — clear lexical emotional expressions in conversational
    self-help check-ins;
  - ``domain_shift`` — somatic descriptions, implicit social exclusion,
    understatement, academic/family context, or unmapped script terms that
    expose domain shift and lexicon boundaries.

Every text is synthetic, unique, contains no personal identifiers, and passes
the safe-messaging method-word screen from ``build_crisis_dataset.py``.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(REPO_ROOT / "evals"))

import re

from app.services.nlp.base import EMOTIONS  # noqa: E402
from build_crisis_dataset import _METHOD_WORDS_INDIC, _METHOD_WORDS_LATIN  # noqa: E402

FORBIDDEN_METHOD_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(w) for w in _METHOD_WORDS_LATIN) + r")\b",
    re.IGNORECASE,
)

DATASET_DIR = REPO_ROOT / "evals" / "datasets"
OUT_PATH = DATASET_DIR / "emotion_cases.jsonl"

LANGUAGES = ("en", "hi", "bn")
REGIMES = ("in_domain", "domain_shift")

# Each entry: (lang, script, regime, text, notes)
RAW_BY_EMOTION: dict[str, list[tuple[str, str, str, str, str]]] = {
    "joy": [
        ("en", "latin", "in_domain", "I got the internship offer today and I cannot stop smiling.", "direct joy + milestone"),
        ("en", "latin", "in_domain", "Feeling so grateful and happy after spending the afternoon with my sister.", "direct gratitude + joy"),
        ("en", "latin", "in_domain", "I passed my final viva and I feel genuinely proud of how hard I worked.", "pride + accomplishment"),
        ("en", "latin", "in_domain", "We laughed for two hours over dinner and it felt wonderful.", "shared laughter + warmth"),
        ("en", "latin", "in_domain", "I am thrilled that my project was selected for the college exhibition.", "excitement + recognition"),
        ("en", "latin", "in_domain", "Such a delightful morning walk in the sun, enjoying every bit of it.", "delight + enjoyment"),
        ("en", "latin", "domain_shift", "For the first time in months, I sang along to the radio while making tea.", "domain_shift: behavioural sign of returning joy without emotion words"),
        ("en", "latin", "domain_shift", "My code finally compiled on the first run and I did a little dance at my desk.", "domain_shift: situational elation without explicit lexicon hit"),
        ("hi", "latin", "in_domain", "Aaj result aaya aur main bahut khush hoon, mehnat rang layi.", "romanised Hindi: khush"),
        ("hi", "latin", "in_domain", "Doston ke saath cricket khel kar bada maza aaya, din mast gaya.", "romanised Hindi: maza + mast"),
        ("hi", "latin", "in_domain", "Meri chhoti behen ko scholarship mili, mujhe uspe bahut garv hai.", "romanised Hindi: garv"),
        ("hi", "devanagari", "in_domain", "आज बहुत दिनों बाद घर जाकर मन बहुत खुश है।", "Devanagari: खुश"),
        ("hi", "devanagari", "domain_shift", "माँ के हाथ की चाय पीते ही चेहरे पर अपने आप मुस्कान आ गई।", "domain_shift: Devanagari मुस्कान not in tiny keyword lexicon"),
        ("bn", "latin", "in_domain", "Ajke porikkhar result khub bhalo hoyeche, মনে khub anondo hocche.", "romanised Banglish: bhalo + anondo"),
        ("bn", "latin", "in_domain", "Bondhuder sathe adda diye din ta khub valo katlo.", "romanised Banglish: valo"),
        ("bn", "bengali", "in_domain", "অনেক দিন পর পরিবারের সবার সাথে দেখা হয়ে খুব ভালো লাগছে।", "Bengali script: ভালো"),
        ("bn", "bengali", "in_domain", "আজকের দিনটা দারুণ কাটল, মনটা বেশ ভালো আছে।", "Bengali script: ভালো"),
        ("bn", "bengali", "domain_shift", "ছাদে দাঁড়িয়ে গানের সুর গুনগুন করছি, আজ মনটা বেশ ফুরফুরে।", "domain_shift: colloquial Bengali ফুরফুরে outside lexicon"),
    ],
    "sadness": [
        ("en", "latin", "in_domain", "I have been feeling really sad and down all week without a clear reason.", "direct sadness + down"),
        ("en", "latin", "in_domain", "I cried on the bus ride home because everything feels so heavy and empty.", "crying + empty"),
        ("en", "latin", "in_domain", "Still grieving the loss of my grandfather and missing our evening talks.", "grief + missing"),
        ("en", "latin", "in_domain", "I feel heartbroken and disappointed after the conversation fell apart.", "heartbroken + disappointed"),
        ("en", "latin", "in_domain", "Today was a gloomy, tearful day and I just feel exhausted.", "gloomy + tearful"),
        ("en", "latin", "in_domain", "I feel so low and numb whenever I look at the old photographs.", "low + numb"),
        ("en", "latin", "domain_shift", "I stared at my dinner plate for twenty minutes and put it back in the fridge untouched.", "domain_shift: behavioural flat affect / appetite loss without emotion words"),
        ("en", "latin", "domain_shift", "Even my favourite songs sound grey and distant this week.", "domain_shift: anhedonia metaphor"),
        ("hi", "latin", "in_domain", "Aaj man bahut udaas hai aur kisi cheez mein dil nahi lag raha.", "romanised Hindi: udaas"),
        ("hi", "latin", "in_domain", "Result dekh kar bahut dukh hua aur rona aa raha hai.", "romanised Hindi: dukh + rona"),
        ("hi", "latin", "in_domain", "Main bahut niraash aur thaka hua mehsoos kar raha hoon.", "romanised Hindi: niraash + thaka"),
        ("hi", "devanagari", "in_domain", "आज सुबह से मन बहुत उदास है और अंदर से दुख हो रहा है।", "Devanagari: उदास + दुख"),
        ("hi", "devanagari", "domain_shift", "कमरे की बत्ती बुझाकर घंटों छत को देखता रहा, कुछ करने की इच्छा ही नहीं हुई।", "domain_shift: Devanagari behavioural withdrawal without lexicon hit"),
        ("bn", "latin", "in_domain", "Aj saradin khub kosto hocche, kanna thamate parchi na.", "romanised Banglish: kosto + kanna"),
        ("bn", "latin", "in_domain", "Mon ta ভীষণ kharap lagche ajke বিকেল theke.", "romanised Banglish: kharap"),
        ("bn", "bengali", "in_domain", "মনের ভেতরটা খুব খারাপ লাগছে, অনেক কষ্ট হচ্ছে আজ।", "Bengali script: খারাপ + কষ্ট"),
        ("bn", "bengali", "in_domain", "পুরনো কথা মনে পড়ে খুব কষ্ট পাচ্ছি।", "Bengali script: কষ্ট"),
        ("bn", "bengali", "domain_shift", "জানলার ধারে চুপচাপ বসে বৃষ্টি দেখছি, বুকের ভেতরটা কেমন ফাঁকা লাগছে।", "domain_shift: Bengali emptiness metaphor ফাঁকা"),
    ],
    "anger": [
        ("en", "latin", "in_domain", "I am so angry and frustrated that my manager took credit for my work.", "angry + frustrated"),
        ("en", "latin", "in_domain", "I am completely fed up with being interrupted in every single meeting.", "fed up"),
        ("en", "latin", "in_domain", "It makes me furious when people treat my boundaries like a joke.", "furious"),
        ("en", "latin", "in_domain", "Feeling really annoyed and irritated by the constant noise upstairs.", "annoyed + irritated"),
        ("en", "latin", "in_domain", "I felt insulted and resentful after the unfair feedback today.", "insulted + resentful + unfair"),
        ("en", "latin", "in_domain", "I am livid about how carelessly they handled our group submission.", "livid"),
        ("en", "latin", "domain_shift", "My jaw is clenched so tight from replaying that conversation that my teeth ache.", "domain_shift: somatic anger without lexicon words"),
        ("en", "latin", "domain_shift", "Oh wonderful, another last-minute rewrite dumped on my desk at 6 p.m.", "domain_shift: sarcastic frustration; surface word 'wonderful' misleads lexicons"),
        ("hi", "latin", "in_domain", "Mujhe uspe bahut gussa aa raha hai, usne bina puche mera samaan liya.", "romanised Hindi: gussa"),
        ("hi", "latin", "in_domain", "Main unke is बर्ताव se bahut naraaz hoon.", "romanised Hindi: naraaz"),
        ("hi", "latin", "in_domain", "Roz ki is jhanjhat se main jhallaya hua hoon aur gussaa badh raha hai.", "romanised Hindi: jhallaya + gussaa"),
        ("hi", "devanagari", "in_domain", "उनकी यह बात सुनकर मुझे बहुत गुस्सा आया।", "Devanagari: गुस्सा"),
        ("hi", "devanagari", "domain_shift", "मेरी सारी मेहनत पर पानी फेर दिया और ऊपर से मुझे ही सुना रहे हैं।", "domain_shift: Hindi idiom for indignation without गुस्सा"),
        ("bn", "latin", "in_domain", "Omon bebohar dekhe amar khub rag hocche.", "romanised Banglish: rag"),
        ("bn", "latin", "in_domain", "Barbar ek kotha shune amar ভীষণ raga utheche.", "romanised Banglish: raga"),
        ("bn", "bengali", "in_domain", "বারবার মিথ্যা কথা শুনে আমার প্রচণ্ড রাগ হচ্ছে (rag)।", "Bengali + romanised cue"),
        ("bn", "bengali", "domain_shift", "আমার কাজটা নিজের নামে চালিয়ে দিল, মাথাটা গরম হয়ে যাচ্ছে।", "domain_shift: Bengali idiom মাথা গরম (angry) not in lexicon"),
        ("bn", "bengali", "domain_shift", "অন্যায়ভাবে পুরো দোষটা আমার ঘাড়ে চাপিয়ে দিল।", "domain_shift: Bengali injustice complaint without explicit anger token"),
    ],
    "fear": [
        ("en", "latin", "in_domain", "I am really scared and afraid to walk back to the hostel alone in the dark.", "scared + afraid"),
        ("en", "latin", "in_domain", "I woke up from a nightmare with my heart racing in pure panic.", "nightmare + panic"),
        ("en", "latin", "in_domain", "I feel terrified of what the medical test results might show tomorrow.", "terrified"),
        ("en", "latin", "in_domain", "A wave of dread hit me when I heard shouting in the corridor.", "dread"),
        ("en", "latin", "in_domain", "I felt frightened and unsafe when the street lights went out.", "frightened + unsafe"),
        ("en", "latin", "in_domain", "I started panicking when the elevator got stuck between floors.", "panicking"),
        ("en", "latin", "domain_shift", "Every time my phone rings after 10 p.m. my stomach drops and my hands go cold.", "domain_shift: physiological fear response"),
        ("en", "latin", "domain_shift", "I froze on the staircase when I heard footsteps behind me.", "domain_shift: freeze response without fear keyword"),
        ("hi", "latin", "in_domain", "Andhere raste se aate waqt mujhe bahut dar lag raha tha.", "romanised Hindi: dar"),
        ("hi", "latin", "in_domain", "Kal ke Viva ke naam se hi dil mein darr aur khauf baith gaya hai.", "romanised Hindi: darr + khauf"),
        ("hi", "latin", "in_domain", "Achanak aisi aawaz sun kar main dara hua hoon.", "romanised Hindi: dara"),
        ("hi", "devanagari", "in_domain", "अकेले अस्पताल जाने के नाम से ही मुझे बहुत डर लग रहा है।", "Devanagari: डर"),
        ("hi", "devanagari", "domain_shift", "दरवाज़े पर अचानक ज़ोर की दस्तक हुई तो मेरे हाथ-पैर ठंडे पड़ गए।", "domain_shift: Devanagari somatic fear idiom हाथ-पैर ठंडे पड़ना"),
        ("bn", "latin", "in_domain", "Rasta ta eto faka chilo je amar khub bhoy lagchilo.", "romanised Banglish: bhoy"),
        ("bn", "latin", "in_domain", "Omon jhor brishti dekhe amar ভীষণ voy korche.", "romanised Banglish: voy"),
        ("bn", "bengali", "in_domain", "হঠাৎ অন্ধকার হয়ে যাওয়ায় আমার খুব ভয় লাগছে।", "Bengali script: ভয়"),
        ("bn", "bengali", "in_domain", "এই কথাটা ভাবলেই মনের ভেতর ভয় কাজ করছে।", "Bengali script: ভয়"),
        ("bn", "bengali", "domain_shift", "হঠাৎ বিকট শব্দ শুনে বুকটা ধড়াস করে উঠল।", "domain_shift: Bengali somatic startle বুকটা ধড়াস করে উঠল"),
    ],
    "anxiety": [
        ("en", "latin", "in_domain", "I have been feeling so anxious and worried about my semester exams.", "anxious + worried + exam"),
        ("en", "latin", "in_domain", "My mind keeps overthinking every small detail and I feel restless.", "overthinking + restless"),
        ("en", "latin", "in_domain", "I am super stressed and nervous before my presentation this afternoon.", "stressed + nervous"),
        ("en", "latin", "in_domain", "I have been on edge all day and cant relax for even ten minutes.", "on edge + cant relax"),
        ("en", "latin", "in_domain", "There is so much work pressure right now and my anxiety is spiking.", "pressure + anxiety"),
        ("en", "latin", "in_domain", "Feeling uneasy and tense because I still haven't heard back from the panel.", "uneasy + tense"),
        ("en", "latin", "domain_shift", "I have checked my alarm clock six times in the last twenty minutes just in case.", "domain_shift: checking behaviour / anticipatory anxiety"),
        ("en", "latin", "domain_shift", "My brain has forty tabs open at once and I keep pacing around the room.", "domain_shift: colloquial racing-mind metaphor"),
        ("hi", "latin", "in_domain", "Kal interview hai aur mujhe bahut chinta aur ghabrahat ho rahi hai.", "romanised Hindi: chinta + ghabrahat"),
        ("hi", "latin", "in_domain", "Ghar ke kharche ko lekar main bahut pareshan aur bechain hoon.", "romanised Hindi: pareshan + bechain"),
        ("hi", "latin", "in_domain", "Padhai ki tension ki wajah se dimaag ruk hi nahi raha.", "romanised Hindi: tension"),
        ("hi", "devanagari", "in_domain", "भविष्य के बारे में सोच-सोचकर बहुत चिंता हो रही है और मन परेशान है।", "Devanagari: चिंता + परेशान"),
        ("hi", "devanagari", "domain_shift", "सवालों की उलझन में पूरी रात करवटें बदलते बीत गई।", "domain_shift: Devanagari sleeplessness / rumination idiom"),
        ("bn", "latin", "in_domain", "Porikkhar jonno khub chinta hocche, kichutei mon boshate parchi na.", "romanised Banglish: chinta"),
        ("bn", "latin", "in_domain", "Chakrir khobor na peye khub tension e achi.", "romanised Banglish: tension"),
        ("bn", "bengali", "in_domain", "আগামীকালের ইন্টারভিউ নিয়ে খুব চিন্তা হচ্ছে।", "Bengali script: চিন্তা"),
        ("bn", "bengali", "in_domain", "বাড়ির পরিস্থিতি নিয়ে সারাক্ষণ চিন্তা মাথায় ঘুরছে।", "Bengali script: চিন্তা"),
        ("bn", "bengali", "domain_shift", "কাল কী হবে ভেবে ভেবে রাত তিনটে বাজে, এখনো দুচোখের পাতা এক করতে পারিনি।", "domain_shift: Bengali insomnia / anticipatory worry without चिंता keyword"),
    ],
    "shame": [
        ("en", "latin", "in_domain", "I feel deeply ashamed and embarrassed about forgetting my lines on stage.", "ashamed + embarrassed"),
        ("en", "latin", "in_domain", "I still feel so guilty for snapping at my roommate yesterday.", "guilty"),
        ("en", "latin", "in_domain", "That feedback meeting left me feeling humiliated and small.", "humiliated"),
        ("en", "latin", "in_domain", "I feel like a burden to my family whenever I ask for help.", "burden"),
        ("en", "latin", "in_domain", "Carrying a lot of guilt and shame about how late I replied to my friend.", "guilt + shame"),
        ("en", "latin", "in_domain", "I felt so stupid after making that mistake in front of the whole class.", "stupid +embarrassment"),
        ("en", "latin", "domain_shift", "I wish the floor had opened up and swallowed me during that seminar.", "domain_shift: idiomatic mortification without shame keywords"),
        ("en", "latin", "domain_shift", "I keep avoiding eye contact in the hallway because everyone saw me stumble.", "domain_shift: avoidance behaviour driven by embarrassment"),
        ("hi", "latin", "in_domain", "Sabke saamne galti ho gayi, main bahut sharminda hoon.", "romanised Hindi: galti + sharminda"),
        ("hi", "latin", "in_domain", "Apne doston ko jawab na de paane par mujhe sharm aa rahi hai.", "romanised Hindi: sharm"),
        ("hi", "latin", "in_domain", "Us din ki baat yaad karke apmaan aur sharminda jaisa lagta hai.", "romanised Hindi: apmaan + sharminda"),
        ("hi", "devanagari", "in_domain", "अपनी भूल याद करके मुझे बहुत शर्म आ रही है।", "Devanagari: शर्म"),
        ("hi", "devanagari", "domain_shift", "सबके सामने मेरी चूक पकड़ी गई तो मैं नज़रें भी नहीं मिला सका।", "domain_shift: Devanagari idiom नज़रें न मिला पाना"),
        ("bn", "latin", "in_domain", "Sobar samne bhul kore ami khub embarrassed bodh korchi.", "code-mixed Banglish: embarrassed"),
        ("bn", "latin", "in_domain", "Amar galti r jonno nijeke khub guilty মনে hocche.", "code-mixed Banglish: galti + guilty"),
        ("bn", "bengali", "domain_shift", "সবার সামনে ভুলটা হয়ে যাওয়ায় লজ্জায় মাথা নিচু হয়ে গেল।", "domain_shift: Bengali script লজ্জা (shame) is absent from LEXICON"),
        ("bn", "bengali", "domain_shift", "বন্ধুর বিপদে পাশে থাকতে পারিনি বলে অপরাধবোধ হচ্ছে।", "domain_shift: Bengali script অপরাধবোধ (guilt) is absent from LEXICON"),
        ("bn", "bengali", "domain_shift", "অপমানে আর সংকোচে কারো দিকে তাকাতে পারছিলাম না।", "domain_shift: Bengali script অপমান/সংকোচ absent from LEXICON"),
    ],
    "loneliness": [
        ("en", "latin", "in_domain", "I have been feeling so lonely since moving to this new city.", "lonely"),
        ("en", "latin", "in_domain", "Sitting alone in my hostel room all weekend makes me feel completely isolated.", "alone + isolated"),
        ("en", "latin", "in_domain", "I feel left out and unseen whenever my batchmates make plans.", "left out + unseen"),
        ("en", "latin", "in_domain", "There is nobody I can call tonight and the loneliness is heavy.", "nobody + loneliness"),
        ("en", "latin", "in_domain", "I feel disconnected and unheard even when the room is full of people.", "disconnected + unheard"),
        ("en", "latin", "in_domain", "I have no friends in this department and spend every lunch break by myself.", "no friends + by myself"),
        ("en", "latin", "domain_shift", "My phone battery lasted three whole days because not a single notification came in.", "domain_shift: implicit social isolation without lexicon words"),
        ("en", "latin", "domain_shift", "I cooked dinner for one again and left the television on just to hear another voice.", "domain_shift: behavioural loneliness cue"),
        ("hi", "latin", "in_domain", "Naye sheher mein main bilkul akela pad gaya hoon, koi nahi hai baat karne ko.", "romanised Hindi: akela + koi nahi"),
        ("hi", "latin", "in_domain", "Hostel mein reh kar bahut akelapan aur tanha mehsoos hota hai.", "romanised Hindi: akelapan + tanha"),
        ("hi", "latin", "in_domain", "Sab apne ghar chale gaye aur main yahan akeli reh gayi.", "romanised Hindi: akeli"),
        ("hi", "devanagari", "in_domain", "इस बड़े शहर में कभी-कभी बहुत अकेला महसूस करता हूँ।", "Devanagari: अकेला"),
        ("hi", "devanagari", "domain_shift", "त्योहार के दिन भी मेरे कमरे का दरवाज़ा किसी ने नहीं खटखटाया।", "domain_shift: Devanagari implicit isolation on a festival day"),
        ("bn", "latin", "in_domain", "Notun shohore eshe nijeke khub ekla মনে hocche.", "romanised Banglish: ekla"),
        ("bn", "latin", "in_domain", "Ajke saradin ghore ekla bose achi, kotha bolar keu nei.", "romanised Banglish: ekla"),
        ("bn", "bengali", "in_domain", "সবাই চলে যাওয়ার পর ঘরটায় ভীষণ একা লাগছে।", "Bengali script: একা"),
        ("bn", "bengali", "in_domain", "ভিড়ের মাঝেও নিজেকে খুব একা মনে হয়।", "Bengali script: একা"),
        ("bn", "bengali", "domain_shift", "সারাদিনে ফোনের স্ক্রিনে একটাও মেসেজ আসেনি, কথা বলার মতো কেউ পাশে নেই।", "domain_shift: Bengali implicit loneliness without একা token"),
    ],
    "calm": [
        ("en", "latin", "in_domain", "After ten minutes of slow breathing I feel calm and grounded.", "calm + grounded"),
        ("en", "latin", "in_domain", "Sitting by the window with warm tea, feeling peaceful and relaxed.", "peaceful + relaxed"),
        ("en", "latin", "in_domain", "I slept well last night and feel rested and steady today.", "slept well + rested + steady"),
        ("en", "latin", "in_domain", "Feeling relieved and safe now that the storm has passed.", "relieved + safe"),
        ("en", "latin", "in_domain", "Things feel manageable today and my mind is quiet.", "manageable + quiet"),
        ("en", "latin", "in_domain", "I am okay and comfortable taking the afternoon at my own pace.", "okay + comfortable"),
        ("en", "latin", "domain_shift", "My shoulders finally dropped away from my ears after the walk in the park.", "domain_shift: somatic release of tension without calm keywords"),
        ("en", "latin", "domain_shift", "Listening to the rain on the roof with nowhere I need to rush to.", "domain_shift: situational serenity without lexicon hit"),
        ("hi", "latin", "in_domain", "Pranayama karne ke baad man bahut shant aur sukoon mein hai.", "romanised Hindi: shant + sukoon"),
        ("hi", "latin", "in_domain", "Ab sab theek hai, thoda aaram mila.", "romanised Hindi: theek + aaram"),
        ("hi", "latin", "in_domain", "Shaam ki hawa mein baith kar man ko badi shanti mili.", "romanised Hindi: shanti"),
        ("hi", "devanagari", "in_domain", "थोड़ी देर ध्यान करने के बाद मन शांत है और अब सब ठीक लग रहा है।", "Devanagari: शांत + ठीक"),
        ("hi", "devanagari", "domain_shift", "पेड़ की छाँव में बैठकर ठंडी हवा का एहसास हो रहा है।", "domain_shift: Devanagari peaceful imagery without शांत/ठीक"),
        ("bn", "latin", "in_domain", "Ekhon shob thik ache, mon ta besh shant lagche.", "romanised Banglish: thik + shant"),
        ("bn", "latin", "in_domain", "Kichu khon bishram niye ekhon araam bodh korchi.", "romanised Banglish: araam"),
        ("bn", "bengali", "in_domain", "গভীর শ্বাস নেওয়ার পর মনটা এখন বেশ শান্ত।", "Bengali script: শান্ত"),
        ("bn", "bengali", "in_domain", "নদীর ধারে বসে মনটা খুব শান্ত লাগছে।", "Bengali script: শান্ত"),
        ("bn", "bengali", "domain_shift", "বারান্দায় বসে বিকেলের নরম আলো দেখছি, কোনো তাড়া নেই।", "domain_shift: Bengali unhurried scene without শান্ত token"),
    ],
    "neutral": [
        ("en", "latin", "in_domain", "I attended two lectures in the morning and then went to the library.", "plain factual routine"),
        ("en", "latin", "in_domain", "The bus arrived at eight thirty and I reached the office on time.", "commute statement"),
        ("en", "latin", "in_domain", "I need to buy notebooks and a pen from the stationery shop.", "errand note"),
        ("en", "latin", "in_domain", "Today is Tuesday and the semester timetable was updated on the portal.", "calendar update"),
        ("en", "latin", "in_domain", "I read three chapters of the history textbook after lunch.", "study log"),
        ("en", "latin", "in_domain", "We had rice and dal for dinner and washed the dishes afterwards.", "meal log"),
        ("en", "latin", "domain_shift", "The meeting about the frightening budget numbers was moved to Thursday.", "domain_shift: contains emotion word 'frightening' in an administrative noun phrase"),
        ("en", "latin", "domain_shift", "I am reading a chapter on how the Great Depression changed banking policy.", "domain_shift: historical term 'Depression' in academic context"),
        ("hi", "latin", "in_domain", "Main subah aath baje utha aur college ke liye nikal gaya.", "romanised Hindi factual routine"),
        ("hi", "latin", "in_domain", "Aaj dopahar mein maine do ghante library mein kitaab padhi.", "romanised Hindi study log"),
        ("hi", "latin", "in_domain", "Sham ko bazaar se sabzi aur doodh lekar ghar aaya.", "romanised Hindi errand"),
        ("hi", "devanagari", "in_domain", "आज सुबह दस बजे कक्षा शुरू हुई और दोपहर को समाप्त हुई।", "Devanagari factual schedule"),
        ("hi", "devanagari", "domain_shift", "मैंने आज मनोविज्ञान की किताब में चिंता के अध्याय के नोट्स बनाए।", "domain_shift: Devanagari academic mention of 'चिंता' in a study note"),
        ("bn", "latin", "in_domain", "Ami sokale uthe cha kheye bus dhorlam.", "romanised Banglish routine"),
        ("bn", "latin", "in_domain", "Dupure bhat kheye boierta dujon mile porlam.", "romanised Banglish study log"),
        ("bn", "bengali", "in_domain", "আজ সকালে লাইব্রেরিতে গিয়ে দুটি বই জমা দিলাম।", "Bengali script factual routine"),
        ("bn", "bengali", "in_domain", "বিকেলে বাজার থেকে সবজি কিনে বাড়ি ফিরেছি।", "Bengali script errand"),
        ("bn", "bengali", "domain_shift", "আজ ক্লাসে ভয়ের গল্পের সাহিত্যিক বিশ্লেষণ নিয়ে আলোচনা হলো।", "domain_shift: Bengali literary discussion mentioning 'ভয়ের'"),
    ],
}


def build_cases() -> list[dict[str, str]]:
    cases: list[dict[str, str]] = []
    counters: dict[str, int] = {lang: 0 for lang in LANGUAGES}
    for emotion in EMOTIONS:
        entries = RAW_BY_EMOTION.get(emotion, [])
        for lang, script, regime, text, notes in entries:
            counters[lang] += 1
            cases.append(
                {
                    "id": f"emo-{lang}-{counters[lang]:03d}",
                    "text": text,
                    "lang": lang,
                    "script": script,
                    "label": emotion,
                    "regime": regime,
                    "notes": notes,
                }
            )
    return cases


def validate_emotion_cases(cases: list[dict[str, str]]) -> list[str]:
    problems: list[str] = []
    if len(cases) < 150:
        problems.append(f"expected >= 150 cases, got {len(cases)}")
    seen_ids: set[str] = set()
    seen_texts: set[str] = set()
    for case in cases:
        cid = case["id"]
        if cid in seen_ids:
            problems.append(f"duplicate id: {cid}")
        seen_ids.add(cid)
        folded = case["text"].strip().casefold()
        if folded in seen_texts:
            problems.append(f"{cid}: duplicate text")
        seen_texts.add(folded)
        if case["label"] not in EMOTIONS:
            problems.append(f"{cid}: unknown emotion {case['label']!r}")
        if case["lang"] not in LANGUAGES:
            problems.append(f"{cid}: unknown lang {case['lang']!r}")
        if case["regime"] not in REGIMES:
            problems.append(f"{cid}: unknown regime {case['regime']!r}")
        if FORBIDDEN_METHOD_RE.search(case["text"]):
            problems.append(f"{cid}: contains forbidden self-harm method word")
        for word in _METHOD_WORDS_INDIC:
            if word in case["text"]:
                problems.append(f"{cid}: contains forbidden Indic method word {word!r}")
    return problems


def main() -> None:
    cases = build_cases()
    problems = validate_emotion_cases(cases)
    if problems:
        raise SystemExit("emotion dataset validation failed:\n  " + "\n  ".join(problems))
    DATASET_DIR.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(json.dumps(c, ensure_ascii=False) for c in cases) + "\n"
    OUT_PATH.write_text(payload, encoding="utf-8")
    digest = hashlib.sha256(OUT_PATH.read_bytes()).hexdigest()
    print(f"wrote {len(cases)} emotion cases to {OUT_PATH.relative_to(REPO_ROOT)} (sha256={digest[:16]}...)")


if __name__ == "__main__":
    main()
