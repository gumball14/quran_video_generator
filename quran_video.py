#!/usr/bin/env python3
"""
Quran Recitation Video Generator
=================================
Generates a vertical (or horizontal) video for a surah/verse range:
each ayah's Arabic text + translation fades in while its recitation
audio plays, then the next ayah fades in.

Reciter : Yasser Al-Dossary (everyayah.com audio, per-ayah files)
Text    : alquran.cloud API (Uthmani script + your chosen translation)

USAGE
-----
    python quran_video.py --surah 112
    python quran_video.py --surah 2 --ayah-start 1 --ayah-end 5
    python quran_video.py --surah 36 --translation en.sahih --orientation horizontal

Run `python quran_video.py --help` for all options.

REQUIREMENTS
------------
    pip install moviepy arabic-reshaper python-bidi pillow requests
    ffmpeg must be installed and on PATH.

NOTE ON FONTS
-------------
This script expects two font files in ./fonts/  (already downloaded if you
got this folder from Claude):
    fonts/NotoNaskhArabic-Regular.ttf
    fonts/NotoNaskhArabic-Bold.ttf
    fonts/NotoSans-Regular.ttf
If missing, download free versions from https://fonts.google.com/noto
(search "Noto Naskh Arabic" and "Noto Sans").

CODE LAYOUT
-----------
This file is just the CLI. The actual implementation lives in quran_lib/,
split by concern:
    quran_lib/constants.py    - paths, reciter list, output settings
    quran_lib/theme.py        - theme schema + loading (matches the HTML editor's export)
    quran_lib/quran_api.py    - fetching verse text/translation
    quran_lib/audio.py        - downloading + splitting recitation audio
    quran_lib/text_render.py  - drawing each frame (Arabic shaping, layout, backgrounds)
    quran_lib/video_build.py  - assembling frames + audio into the final video
"""
import argparse
import sys
from pathlib import Path

# Checked here (before importing quran_lib, which needs them) so a missing
# dependency gives a friendly one-line fix instead of a raw traceback.
try:
    import arabic_reshaper  # noqa: F401
    from bidi.algorithm import get_display  # noqa: F401
except ImportError:
    print("Missing dependency. Run: pip install arabic-reshaper python-bidi --break-system-packages")
    sys.exit(1)

try:
    import moviepy  # noqa: F401
except ImportError:
    print("Missing dependency. Run: pip install moviepy --break-system-packages")
    sys.exit(1)

from quran_lib.constants import RECITER_FOLDERS, OUTPUT_DIR
from quran_lib.theme import load_theme
from quran_lib.quran_api import fetch_verses
from quran_lib.video_build import build_video
from quran_lib.timing import load_timing_manifest, TimingManifestError


def main():
    parser = argparse.ArgumentParser(description="Generate a Quran recitation video.")
    parser.add_argument("--surah", type=int, required=True, help="Surah number (1-114)")
    parser.add_argument("--ayah-start", type=int, default=None)
    parser.add_argument("--ayah-end", type=int, default=None)
    parser.add_argument("--reciter", default="yasser_al_dossary", choices=RECITER_FOLDERS.keys())
    parser.add_argument("--translation", default="en.sahih",
                         help="alquran.cloud translation edition code, e.g. en.sahih, ur.jalandhry")
    parser.add_argument("--orientation", default="vertical", choices=["vertical", "horizontal"])
    parser.add_argument("--no-translation", action="store_true", help="Arabic only, no translation text")
    parser.add_argument("--no-split-basmala", action="store_true",
                         help="Keep Bismillah merged into ayah 1's frame instead of splitting it out")
    parser.add_argument("--no-outro", action="store_true",
                         help="Skip the short 'thank you for watching' closing card")
    parser.add_argument("--output", default=None, help="Output mp4 path")
    parser.add_argument("--theme", default=None,
                         help="Path to a theme.json exported from the Ayah Frame Studio editor "
                              "(colors, layout, word-highlighting, and pointer settings)")
    parser.add_argument("--timing", default=None,
                         help="Path to a timing manifest (see quran_lib/timing.py) giving explicit, "
                              "manually-set frame/word timings for one or more ayahs. Any ayah not "
                              "covered by it still uses the automatic pacing.")
    args = parser.parse_args()

    if args.theme:
        print(f"Loading theme from {args.theme}…")
        load_theme(Path(args.theme))

    timing_manifest = None
    if args.timing:
        print(f"Loading timing manifest from {args.timing}…")
        try:
            timing_manifest = load_timing_manifest(Path(args.timing))
        except TimingManifestError as e:
            print(f"Timing manifest error: {e}")
            sys.exit(1)

    size = (1080, 1920) if args.orientation == "vertical" else (1920, 1080)

    print(f"Fetching verses for surah {args.surah}…")
    verses, surah_name, surah_name_arabic = fetch_verses(
        args.surah, args.translation, args.ayah_start, args.ayah_end,
        split_basmala=not args.no_split_basmala,
    )
    print(f"  {surah_name} ({surah_name_arabic}) — {len(verses)} verse(s) to render")

    output_path = Path(args.output) if args.output else OUTPUT_DIR / f"surah_{args.surah}_{args.reciter}.mp4"

    build_video(
        verses, surah_name_arabic, args.surah, args.reciter, size, output_path,
        show_translation=not args.no_translation, timing_manifest=timing_manifest,
        surah_name_text=surah_name, outro_enabled=not args.no_outro,
    )
    print(f"\nDone! Video saved to: {output_path}")


if __name__ == "__main__":
    main()