"""
Recitation audio: downloading per-ayah files from everyayah.com, reading
duration via ffprobe, and detecting/cutting the Basmala out of ayah 1's
audio via ffmpeg's silencedetect filter.
"""
import re
import subprocess
from pathlib import Path

import requests

from .constants import CACHE_DIR, RECITER_FOLDERS


def download_ayah_audio(surah: int, ayah: int, reciter_key: str) -> Path:
    folder = RECITER_FOLDERS[reciter_key]
    fname = f"{surah:03d}{ayah:03d}.mp3"
    url = f"https://everyayah.com/data/{folder}/{fname}"

    out_path = CACHE_DIR / folder / fname
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if out_path.exists() and out_path.stat().st_size > 0:
        return out_path

    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    out_path.write_bytes(resp.content)
    return out_path


def get_audio_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def split_basmala_audio(audio_path: Path, out_dir: Path):
    """Ayah-1 audio (for every surah except At-Tawbah) usually has the Bismillah
    recited right before the ayah itself, in one file, with a brief pause between.
    Detect that pause with ffmpeg's silencedetect filter and cut the file into
    (basmala_clip, ayah_clip). Returns (None, None) if no confident split is found."""
    result = subprocess.run(
        ["ffmpeg", "-i", str(audio_path), "-af", "silencedetect=noise=-35dB:d=0.15", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    starts = [float(x) for x in re.findall(r"silence_start:\s*([0-9.]+)", result.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*([0-9.]+)", result.stderr)]
    total_duration = get_audio_duration(audio_path)

    split_point = None
    for s in starts:
        # Only trust a silence gap that falls in a plausible "between Basmala and
        # ayah" window -- not right at the very start or very end of the file.
        if total_duration * 0.10 < s < total_duration * 0.85:
            matching_ends = [e for e in ends if e > s]
            e = matching_ends[0] if matching_ends else s + 0.2
            split_point = (s + e) / 2
            break

    if split_point is None:
        return None, None

    out_dir.mkdir(parents=True, exist_ok=True)
    basmala_path = out_dir / f"{audio_path.stem}_basmala.mp3"
    ayah_path = out_dir / f"{audio_path.stem}_ayah.mp3"

    subprocess.run(
        ["ffmpeg", "-y", "-i", str(audio_path), "-t", f"{split_point:.3f}",
         "-acodec", "libmp3lame", "-q:a", "2", str(basmala_path)],
        capture_output=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(audio_path), "-ss", f"{split_point:.3f}",
         "-acodec", "libmp3lame", "-q:a", "2", str(ayah_path)],
        capture_output=True,
    )

    if (basmala_path.exists() and ayah_path.exists()
            and basmala_path.stat().st_size > 0 and ayah_path.stat().st_size > 0):
        return basmala_path, ayah_path
    return None, None
