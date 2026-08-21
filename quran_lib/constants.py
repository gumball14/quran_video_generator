"""
Shared paths and constants used across quran_lib. Nothing here changes at
runtime -- for the mutable theme state, see theme.py.
"""
from pathlib import Path

# quran_lib/constants.py -> parents[0]=quran_lib, parents[1]=project root
HERE = Path(__file__).resolve().parents[1]
FONT_DIR = HERE / "fonts"
CACHE_DIR = HERE / "cache"
OUTPUT_DIR = HERE / "output"

FPS = 30
BG_COLOR_TOP = (10, 20, 35)      # deep navy -- default gradient top
BG_COLOR_BOTTOM = (25, 45, 70)   # lighter navy -- default gradient bottom
GOLD = (196, 164, 96)
WHITE = (240, 240, 235)

# everyayah.com folder name for each supported reciter, 128-192kbps
RECITER_FOLDERS = {
    "yasser_al_dossary": "Yasser_Ad-Dussary_128kbps",
    "alafasy": "Alafasy_128kbps",
    "abdul_basit": "Abdul_Basit_Murattal_192kbps",
    "sudais": "Abdurrahmaan_As-Sudais_192kbps",
    "ghamdi": "Ghamadi_40kbps",
    "muaiqly": "Maher_AlMuaiqly_64kbps",
    "hussary": "Husary_128kbps",
    "hudhaifi": "Hudhaify_128kbps",
    "ayyub": "Muhammad_Ayyoub_128kbps",
    "qatami": "Nasser_Alqatami_128kbps",
}

TEXT_EDITION = "quran-uthmani"  # Arabic script edition on alquran.cloud
