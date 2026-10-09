"""Offline language detection; romanised Hindi is returned as hi (Hinglish)."""

import re
from threading import Lock
from typing import Literal, cast

from langdetect import DetectorFactory, LangDetectException  # type: ignore[import-untyped]

Language = Literal["en", "hi", "bn", "other"]
_factory = DetectorFactory()
_lock = Lock()
_HINGLISH = {"hai", "hain", "kal", "bahut", "dar", "lag", "raha", "mujhe", "nahi", "mera", "kya"}


def detect_language(text: str) -> Language:
    sample = text[:4000]
    if re.search(r"[\u0900-\u097f]", sample):
        return "hi"
    if re.search(r"[\u0980-\u09ff]", sample):
        return "bn"
    words = set(re.findall(r"[a-z]+", sample.lower()))
    if len(words & _HINGLISH) >= 2:
        return "hi"
    if not words:
        return "other"
    if len(words & {"i", "am", "feel", "so", "the", "and", "lately", "alone", "job"}) >= 2:
        return "en"
    # Short common English utterances are unreliable in statistical detection.
    if len(words) <= 4 and words & {"i", "am", "fine", "hello", "happy", "sad"}:
        return "en"
    with _lock:
        if not _factory.langlist:
            from langdetect.detector_factory import PROFILES_DIRECTORY  # type: ignore[import-untyped]

            _factory.load_profile(PROFILES_DIRECTORY)
            _factory.seed = 0
        detector = _factory.create()
        detector.append(sample)
        try:
            language = detector.detect()
        except LangDetectException:
            return "other"
    return cast(Language, language) if language in {"en", "hi", "bn"} else "other"
