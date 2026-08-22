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


# How much silence to leave in place at each trimmed edge. 0 = cut right up
# to the detected onset/offset of speech, so consecutive ayahs play back to
# back with no gap at all once concatenated.
_SILENCE_KEEP = 0.0


def get_silence_trim_bounds(path: Path, keep_silence: float = _SILENCE_KEEP):
    """Detects leading/trailing silence in an ayah's audio file and returns
    (start, end) trim points -- the window to actually play, leaving
    `keep_silence` seconds of padding at each trimmed edge. Returns
    (0.0, full_duration) if no confident leading/trailing silence is found.

    Each per-ayah file from everyayah.com typically has its own silence
    padding baked in at both ends (sounds like a normal short pause when
    played alone). When ayahs are placed back-to-back in the rendered video,
    one ayah's trailing padding plus the next one's leading padding stack
    into a noticeably longer gap than either file has on its own -- this is
    used to shrink that stack back down before ayahs are concatenated."""
    total_duration = get_audio_duration(path)
    # -35dB (used by split_basmala_audio, which looks for a much more
    # obvious silent gap *inside* the recitation) is too strict for the
    # quieter, low-level noise floor typically present in these edge
    # paddings -- it misses most of them. -20dB reliably catches the real
    # edge padding without tripping on quiet passages mid-recitation.
    result = subprocess.run(
        ["ffmpeg", "-i", str(path), "-af", "silencedetect=noise=-20dB:d=0.15", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    # Walk start/end markers in the order ffmpeg printed them (silence_start
    # always precedes its matching silence_end) to reconstruct silent
    # intervals. A trailing silence_start with no matching silence_end means
    # the silence runs all the way to EOF.
    events = re.findall(r"silence_(start|end):\s*([0-9.]+)", result.stderr)
    intervals = []
    pending_start = None
    for kind, val in events:
        val = float(val)
        if kind == "start":
            pending_start = val
        elif pending_start is not None:
            intervals.append((pending_start, val))
            pending_start = None
    if pending_start is not None:
        intervals.append((pending_start, total_duration))

    start_trim = 0.0
    if intervals and intervals[0][0] < 0.2:  # silence begins essentially at t=0
        start_trim = max(0.0, min(intervals[0][1] - keep_silence, total_duration))

    end_trim = total_duration
    if intervals and intervals[-1][1] > total_duration - 0.2:  # silence runs to (near) EOF
        end_trim = max(start_trim, min(intervals[-1][0] + keep_silence, total_duration))

    return start_trim, end_trim


def get_trimmed_ayah_audio(path: Path, keep_silence: float = _SILENCE_KEEP) -> Path:
    """Returns a silence-trimmed copy of this ayah's audio, cached next to
    the source file. Cuts with ffmpeg directly into a real file instead of
    moviepy's in-memory AudioFileClip.subclipped(), which only seeks
    compressed (mp3) audio approximately -- landing mid-frame at the cut
    point can leave a faint blip/glitch right at the boundary between two
    concatenated ayahs, which is audible as a stutter/restart rather than a
    clean cut.

    Re-encoded to WAV (pcm_s16le), not mp3: re-encoding the trimmed segment
    back to mp3 runs it through libmp3lame again, which inserts its own
    encoder "priming" delay -- a few dozen ms of silence -- at the start of
    *every* file it writes, including this one. That silently reintroduces
    almost exactly the kind of boundary glitch this function exists to
    remove. WAV is uncompressed PCM, so there's no encoder delay and no
    frame-alignment ambiguity -- the cut lands on an exact sample.

    Returns the original path unchanged if there's nothing worth trimming."""
    start, end = get_silence_trim_bounds(path, keep_silence)
    total_duration = get_audio_duration(path)
    if start <= 0.0 and end >= total_duration - 0.01:
        return path

    trimmed_path = path.with_name(f"{path.stem}_trimmed.wav")
    if trimmed_path.exists() and trimmed_path.stat().st_size > 0:
        return trimmed_path

    subprocess.run(
        ["ffmpeg", "-y", "-i", str(path), "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
         "-acodec", "pcm_s16le", str(trimmed_path)],
        capture_output=True, check=True,
    )
    return trimmed_path


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
