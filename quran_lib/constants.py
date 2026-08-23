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

# everyayah.com folder name for each supported reciter, 128-192kbps where
# available. Every key below was verified live (HTTP 200 on 001001.mp3)
# before being added -- see the display-name table in app.py's RECITERS
# for the human-readable label shown in the reciter selector.
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

    "abdul_samad": "AbdulSamad_64kbps_QuranExplorer.Com",
    "juhaynee": "Abdullaah_3awwaad_Al-Juhaynee_128kbps",
    "basfar": "Abdullah_Basfar_192kbps",
    "matrood": "Abdullah_Matroud_128kbps",
    "shatri": "Abu_Bakr_Ash-Shaatree_128kbps",
    "neana": "Ahmed_Neana_128kbps",
    "ajamy": "Ahmed_ibn_Ali_al-Ajamy_128kbps_ketaballah.net",
    "alaqimy": "Akram_AlAlaqimy_128kbps",
    "hajjaj": "Ali_Hajjaj_AlSuesy_128kbps",
    "ali_jaber": "Ali_Jaber_64kbps",
    "sowaid": "Ayman_Sowaid_64kbps",
    "fares_abbad": "Fares_Abbad_64kbps",
    "hani_rifai": "Hani_Rifai_192kbps",
    "hussary_mujawwad": "Husary_128kbps_Mujawwad",
    "hussary_muallim": "Husary_Muallim_128kbps",
    "akhdar": "Ibrahim_Akhdar_32kbps",
    "mansoori": "Karim_Mansoori_40kbps",
    "qahtani": "Khaalid_Abdullaah_al-Qahtaanee_192kbps",
    "minshawi": "Minshawy_Murattal_128kbps",
    "minshawi_mujawwad": "Minshawy_Mujawwad_192kbps",
    "tablawi": "Mohammad_al_Tablaway_128kbps",
    "abdulkareem": "Muhammad_AbdulKareem_128kbps",
    "jibreel": "Muhammad_Jibreel_128kbps",
    "al_qasim": "Muhsin_Al_Qasim_192kbps",
    "mustafa_ismail": "Mustafa_Ismail_48kbps",
    "nabil_rifai": "Nabil_Rifa3i_48kbps",
    "sahl_yassin": "Sahl_Yassin_128kbps",
    "bukhatir": "Salaah_AbdulRahman_Bukhatir_128kbps",
    "budair": "Salah_Al_Budair_128kbps",
    "shuraim": "Saood_ash-Shuraym_128kbps",
    "yasser_salamah": "Yaser_Salamah_128kbps",
    "aziz_alili": "aziz_alili_128kbps",
    "tunaiji": "khalefa_al_tunaiji_64kbps",
    "al_banna": "mahmoud_ali_al_banna_32kbps",
    "parhizgar": "Parhizgar_48kbps",
}

TEXT_EDITION = "quran-uthmani"  # Arabic script edition on alquran.cloud

# Reciters for whom a genuinely continuous, per-surah recording (not split
# per ayah) is available, paired with a per-ayah boundary-timestamp file
# into that exact recording -- see quran_lib.audio.download_surah_audio()
# and get_surah_ayah_boundaries(). Every URL here was verified live (HTTP
# 200 + a real ffprobe duration cross-checked against the timing data)
# before being added; a reciter simply absent from this table falls back to
# today's per-ayah RECITER_FOLDERS pipeline, unchanged.
#
# The timing data (everyayah.com/data/timings_files/) carries a
# VerseByVerseQuran.com disclaimer requiring a link-back credit to use it,
# and self-discloses that some reciters' timings were "fixed manually after
# splitting" and so aren't guaranteed 100% accurate -- see README.txt.
RANGE_AUDIO_SOURCES = {
    "yasser_al_dossary": {
        "audio_url": "https://download.quranicaudio.com/quran/yasser_ad-dussary/{surah:03d}.mp3",
        "timings_zip_url": "https://everyayah.com/data/timings_files/Yasser_Ad-Dussary_128kbps.zip",
        "timings_filename": "{surah:03d}.txt",
    },
    "abdul_basit": {
        "audio_url": "https://download.quranicaudio.com/quran/abdul_basit_murattal/{surah:03d}.mp3",
        "timings_zip_url": "https://everyayah.com/data/timings_files/Abdul_Basit_Murattal_Timings.zip",
        "timings_filename": "{surah:03d}.txt",
    },
    "sudais": {
        "audio_url": "https://download.quranicaudio.com/quran/abdurrahmaan_as-sudays/{surah:03d}.mp3",
        "timings_zip_url": "https://everyayah.com/data/timings_files/Sudais.zip",
        "timings_filename": "{surah:03d}.txt",
    },
    "qatami": {
        "audio_url": "https://server6.mp3quran.net/qtm/{surah:03d}.mp3",
        "timings_zip_url": "https://everyayah.com/data/timings_files/Nasser_Alqatami_128kbps.zip",
        "timings_filename": "{surah:03d}.txt",
    },
    # hussary intentionally excluded: the only working quranicaudio.com slug
    # found (mahmood_khaleel_al-husaree_iza3a, a "broadcast" take) turned out
    # to be a DIFFERENT, shorter recording than the one everyayah.com's
    # Husary_Timings.zip was built from -- e.g. surah 1's timings run to
    # 54.26s but that recording is only 34.28s long. get_surah_ayah_boundaries()
    # catches this mismatch and safely falls back, but there's no point
    # downloading a file every time just to discard it -- add an entry back
    # here if the correct matching slug is ever found.
    "ayyub": {
        "audio_url": "https://download.quranicaudio.com/quran/muhammad_ayyoob/{surah:03d}.mp3",
        "timings_zip_url": "https://everyayah.com/data/timings_files/Muhammad%20Ayyoob%20bin%20Muhammad%20Yoosuf%20Timings.zip",
        "timings_filename": "Chapter{surah:03d}.txt",
    },
}

# Canonical, unchanging ayah count per surah (1-114) -- from alquran.cloud's
# /meta endpoint. Used to detect whether a reciter's continuous-recording
# timing file includes one extra leading segment for the spoken Isti'adhah
# before ayah 1 (line count == count + 1) or not (line count == count).
SURAH_AYAH_COUNTS = {
    1: 7, 2: 286, 3: 200, 4: 176, 5: 120, 6: 165, 7: 206, 8: 75, 9: 129, 10: 109,
    11: 123, 12: 111, 13: 43, 14: 52, 15: 99, 16: 128, 17: 111, 18: 110, 19: 98, 20: 135,
    21: 112, 22: 78, 23: 118, 24: 64, 25: 77, 26: 227, 27: 93, 28: 88, 29: 69, 30: 60,
    31: 34, 32: 30, 33: 73, 34: 54, 35: 45, 36: 83, 37: 182, 38: 88, 39: 75, 40: 85,
    41: 54, 42: 53, 43: 89, 44: 59, 45: 37, 46: 35, 47: 38, 48: 29, 49: 18, 50: 45,
    51: 60, 52: 49, 53: 62, 54: 55, 55: 78, 56: 96, 57: 29, 58: 22, 59: 24, 60: 13,
    61: 14, 62: 11, 63: 11, 64: 18, 65: 12, 66: 12, 67: 30, 68: 52, 69: 52, 70: 44,
    71: 28, 72: 28, 73: 20, 74: 56, 75: 40, 76: 31, 77: 50, 78: 40, 79: 46, 80: 42,
    81: 29, 82: 19, 83: 36, 84: 25, 85: 22, 86: 17, 87: 19, 88: 26, 89: 30, 90: 20,
    91: 15, 92: 21, 93: 11, 94: 8, 95: 8, 96: 19, 97: 5, 98: 8, 99: 8, 100: 11,
    101: 11, 102: 8, 103: 3, 104: 9, 105: 5, 106: 4, 107: 7, 108: 3, 109: 6, 110: 3,
    111: 5, 112: 4, 113: 5, 114: 6,
}
