"""
Verse text: the Verse data model, fetching from alquran.cloud, and
splitting the Basmala off of ayah 1 where the API includes it inline.
"""
import re
from dataclasses import dataclass

import requests

from .constants import TEXT_EDITION

BASMALA_ARABIC = "بِسْمِ اللَّهِ الرَّحْمَٰنِ الرَّحِيمِ"
BASMALA_WORD_COUNT = 4  # Basmala is always exactly these 4 words in Uthmani script

_translation_editions_cache = None

# Uthmani waqf/pause marks (e.g. ۖ ۗ ۘ ۙ ۚ ۛ) -- print-Mushaf annotations
# telling a reader where pausing is allowed/preferred/required. Unicode-wise
# these are combining marks (category Mn) meant to sit stacked directly
# above the letter before them, but the alquran.cloud "quran-uthmani"
# edition writes them out as their own space-separated tokens (e.g.
# "...قَرِيبٌ ۖ أُجِيبُ..."), which is print-typesetting convention, not
# how they're meant to render. Left as their own token, two things go
# wrong: Pillow's raqm shaper has nothing to attach the mark to, so it
# floats as a small stray circle rather than sitting over the previous
# letter, AND the per-word highlighter/pointer counts it as an extra
# "word", throwing off word indices. Fixed by re-attaching each mark
# directly onto the end of the preceding word (no space) while keeping the
# single space before the next real word -- raqm then positions it as a
# proper combining mark over that word's last letter, and word-splitting
# sees one token instead of two.
_WAQF_MARK_RE = re.compile(r"\s+([ۖ-ۜ])\s*")


def _attach_waqf_marks(text: str) -> str:
    return _WAQF_MARK_RE.sub(r"\1 ", text).strip()


def fetch_translation_editions():
    """Fetches the full list of translation editions alquran.cloud offers
    (any language, not just English), for the "Translation" picker in
    new_video.html -- so a viewer isn't limited to whatever single edition
    happens to be hard-coded here. Cached in-memory for the life of the
    process: this app runs as a single long-lived local server and the
    catalog changes rarely, so there's no reason to re-fetch it on every
    page load.

    Returns a list of {"identifier", "language", "name", "englishName"}
    dicts, sorted by language then English name. Raises on network
    failure/unexpected response shape -- callers decide how to degrade
    (app.py's endpoint just reports the failure; the picker falls back to
    the fixed en.sahih default)."""
    global _translation_editions_cache
    if _translation_editions_cache is not None:
        return _translation_editions_cache

    url = "https://api.alquran.cloud/v1/edition?format=text&type=translation"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 200:
        raise RuntimeError(f"API error: {data}")

    editions = [
        {
            "identifier": e["identifier"],
            "language": e["language"],
            "name": e["name"],
            "englishName": e["englishName"],
        }
        for e in data["data"]
    ]
    editions.sort(key=lambda e: (e["language"], e["englishName"]))
    _translation_editions_cache = editions
    return editions


@dataclass
class Verse:
    number: int          # ayah number within surah (0 = special Basmala scene)
    arabic: str
    translation: str
    basmala_arabic: str = None  # set on ayah 1 when the Basmala was split off of it


def split_basmala_text(surah: int, ayah_number: int, arabic_text: str):
    """For ayah 1 of every surah except Al-Fatihah (1, where the Basmala IS ayah 1)
    and At-Tawbah (9, which has no Basmala), the fetched ayah-1 text has the
    Basmala prepended. Split it off. Returns (basmala_text_or_None, remaining_ayah_text)."""
    if ayah_number != 1 or surah in (1, 9):
        return None, arabic_text
    words = arabic_text.split(" ")
    if len(words) <= BASMALA_WORD_COUNT:
        return None, arabic_text
    return " ".join(words[:BASMALA_WORD_COUNT]), " ".join(words[BASMALA_WORD_COUNT:])


def fetch_verses(surah: int, translation_edition: str, ayah_start=None, ayah_end=None, split_basmala=True):
    """Fetch Arabic text + translation for a surah (optionally a verse range)."""
    url = f"https://api.alquran.cloud/v1/surah/{surah}/editions/{TEXT_EDITION},{translation_edition}"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 200:
        raise RuntimeError(f"API error: {data}")

    arabic_ayahs = data["data"][0]["ayahs"]
    translation_ayahs = data["data"][1]["ayahs"]
    surah_name = data["data"][0]["englishName"]
    surah_name_arabic = data["data"][0]["name"]

    verses = []
    for a, t in zip(arabic_ayahs, translation_ayahs):
        n = a["numberInSurah"]
        if ayah_start and n < ayah_start:
            continue
        if ayah_end and n > ayah_end:
            continue
        clean_text = _attach_waqf_marks(a["text"])
        basmala_text, remaining_arabic = split_basmala_text(surah, n, clean_text) if split_basmala else (None, clean_text)
        verses.append(Verse(number=n, arabic=remaining_arabic, translation=t["text"],
                             basmala_arabic=basmala_text))

    if not verses:
        raise RuntimeError("No verses found for the given range.")
    return verses, surah_name, surah_name_arabic
