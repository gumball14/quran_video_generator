"""
Verse text: the Verse data model, fetching from alquran.cloud, and
splitting the Basmala off of ayah 1 where the API includes it inline.
"""
from dataclasses import dataclass

import requests

from .constants import TEXT_EDITION

BASMALA_ARABIC = "بِسْمِ اللَّهِ الرَّحْمَٰنِ الرَّحِيمِ"
BASMALA_WORD_COUNT = 4  # Basmala is always exactly these 4 words in Uthmani script


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
        basmala_text, remaining_arabic = split_basmala_text(surah, n, a["text"]) if split_basmala else (None, a["text"])
        verses.append(Verse(number=n, arabic=remaining_arabic, translation=t["text"],
                             basmala_arabic=basmala_text))

    if not verses:
        raise RuntimeError("No verses found for the given range.")
    return verses, surah_name, surah_name_arabic
