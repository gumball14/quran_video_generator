"""
Recitation audio: downloading per-ayah files from everyayah.com, reading
duration via ffprobe, and detecting where the Basmala ends within ayah 1's
audio via ffmpeg's silencedetect filter -- as a timestamp only, never by
cutting the file into pieces.

Also: for reciters listed in RANGE_AUDIO_SOURCES, downloading one genuinely
continuous per-surah recording plus its per-ayah boundary timestamps, so a
requested ayah range can be subclipped from ONE file instead of downloading
and gluing together one file per ayah. See download_surah_audio() and
get_surah_ayah_boundaries().
"""
import io
import re
import subprocess
import zipfile
from pathlib import Path

import requests

from .constants import CACHE_DIR, RANGE_AUDIO_SOURCES, RECITER_FOLDERS, SURAH_AYAH_COUNTS


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
    # -35dB (used by detect_basmala_split, which looks for a much more
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


def get_custom_ayah_audio(path: Path, crop_start: float = 0.0, crop_end: float = None) -> Path:
    """Returns a cropped copy of a user-uploaded ayah audio file (see
    quran_lib/audio_sources.py), cached next to the source -- same lazy,
    ffmpeg-direct-cut, WAV-output shape as get_trimmed_ayah_audio() and for
    the same reason (moviepy's compressed-audio seeking can leave an
    audible blip right at the cut point; WAV has no such ambiguity).

    crop_end=None means "to the end of the file". Returns the original
    path unchanged if the requested window already covers the whole file
    (nothing to actually crop).

    The cache filename encodes the crop bounds (not just the source name)
    so re-cropping the same upload with different bounds during editing
    doesn't serve a stale cut -- and is named "<stem>.crop_..." so it's
    still cleaned up by app.py's upload-delete glob (f"{ayah}.*"), which
    matches on the "<ayah>." prefix."""
    total_duration = get_audio_duration(path)
    end = total_duration if crop_end is None else crop_end
    if crop_start <= 0.0 and end >= total_duration - 0.01:
        return path

    cropped_path = path.parent / f"{path.stem}.crop_{crop_start:.3f}_{end:.3f}.wav"
    if cropped_path.exists() and cropped_path.stat().st_size > 0:
        return cropped_path

    subprocess.run(
        ["ffmpeg", "-y", "-i", str(path), "-ss", f"{crop_start:.3f}", "-to", f"{end:.3f}",
         "-acodec", "pcm_s16le", str(cropped_path)],
        capture_output=True, check=True,
    )
    return cropped_path


def detect_basmala_split(audio_path: Path):
    """Ayah-1 audio (for every surah except At-Tawbah) usually has the Bismillah
    recited right before the ayah itself, in one file, with a brief pause between.
    Detect that pause with ffmpeg's silencedetect filter and return the boundary
    timestamp (seconds into audio_path) where the Bismillah ends and the ayah
    begins. Returns None if no confident boundary is found.

    This only ever reports a TIME within the original file -- it never cuts or
    writes out a second audio file. The Bismillah and the ayah are two frames
    of the SAME continuous recitation, not two separate audio clips: anything
    that needs to isolate one of them (the timing editor, `build_video()`)
    plays/subclips `audio_path` itself using this timestamp as a frame
    boundary, so the audio is never physically split -- only its timing is."""
    result = subprocess.run(
        ["ffmpeg", "-i", str(audio_path), "-af", "silencedetect=noise=-35dB:d=0.15", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    starts = [float(x) for x in re.findall(r"silence_start:\s*([0-9.]+)", result.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*([0-9.]+)", result.stderr)]
    total_duration = get_audio_duration(audio_path)

    for s in starts:
        # Only trust a silence gap that falls in a plausible "between Basmala and
        # ayah" window -- not right at the very start or very end of the file.
        if total_duration * 0.10 < s < total_duration * 0.85:
            matching_ends = [e for e in ends if e > s]
            e = matching_ends[0] if matching_ends else s + 0.2
            return (s + e) / 2

    return None


def download_surah_audio(surah: int, reciter_key: str):
    """Downloads ONE genuinely continuous recording of the whole surah (not
    split per ayah) for reciters listed in RANGE_AUDIO_SOURCES. Returns None
    -- never raises -- if this reciter has no such source or the download
    fails, so callers can fall back to the per-ayah download_ayah_audio()
    pipeline unconditionally."""
    source = RANGE_AUDIO_SOURCES.get(reciter_key)
    if source is None:
        return None

    out_path = CACHE_DIR / "surah_audio" / reciter_key / f"{surah:03d}.mp3"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists() and out_path.stat().st_size > 0:
        return out_path

    url = source["audio_url"].format(surah=surah)
    try:
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
    except requests.RequestException:
        return None
    out_path.write_bytes(resp.content)
    return out_path


def get_surah_audio_for_subclipping(surah: int, reciter_key: str):
    """Returns a re-encoded WAV copy of download_surah_audio()'s continuous
    recording, cached next to it. A range-audio surah gets subclipped at
    every ayah boundary -- often dozens of cut points in one file -- and
    moviepy's AudioFileClip.subclipped() only seeks compressed (mp3) audio
    approximately, so landing mid-frame at any one of those cuts can leave a
    faint blip/glitch right at that ayah's edge. Same fix as
    get_trimmed_ayah_audio() uses for per-ayah audio, applied once to the
    whole surah instead of per cut. Returns None if the source mp3 isn't
    available."""
    mp3_path = download_surah_audio(surah, reciter_key)
    if mp3_path is None:
        return None

    wav_path = mp3_path.with_suffix(".wav")
    if wav_path.exists() and wav_path.stat().st_size > 0:
        return wav_path

    subprocess.run(
        ["ffmpeg", "-y", "-i", str(mp3_path), "-acodec", "pcm_s16le", str(wav_path)],
        capture_output=True, check=True,
    )
    return wav_path


def _ensure_timings_extracted(reciter_key: str):
    """Downloads and extracts this reciter's everyayah.com timings archive
    once, caching the extracted .txt files. Returns the extraction dir, or
    None if unavailable/download failed."""
    source = RANGE_AUDIO_SOURCES.get(reciter_key)
    if source is None:
        return None

    extract_dir = CACHE_DIR / "surah_timings" / reciter_key
    if extract_dir.exists() and any(extract_dir.iterdir()):
        return extract_dir

    try:
        resp = requests.get(source["timings_zip_url"], timeout=60)
        resp.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            extract_dir.mkdir(parents=True, exist_ok=True)
            zf.extractall(extract_dir)
    except (requests.RequestException, zipfile.BadZipFile):
        return None
    return extract_dir


def get_surah_ayah_boundaries(surah: int, reciter_key: str):
    """Returns {ayah_number: (start_sec, end_sec)} marking each ayah's span
    within the ONE continuous file download_surah_audio() returns for this
    surah/reciter -- boundaries only, never a reason to cut a second file.

    The timing data is one cumulative end-timestamp (ms) per ayah; some
    reciters' recordings additionally open with a spoken Isti'adhah before
    ayah 1, showing up as one extra leading value (detected by comparing the
    line count against the surah's real, canonical ayah count).

    Also cross-checks the last boundary against download_surah_audio()'s
    REAL duration: the timing file and the continuous recording are fetched
    from two different sites, so a reciter can have two recordings of the
    same surah at different paces (e.g. a broadcast take vs. the one the
    timing data was actually built from) -- caught here as a hard mismatch
    rather than silently subclipping the wrong span.

    Returns None -- never raises -- if this reciter/surah isn't covered, the
    timing file doesn't parse into a recognized shape, or it doesn't match
    the real audio's duration, so callers can fall back to the per-ayah
    pipeline unconditionally."""
    source = RANGE_AUDIO_SOURCES.get(reciter_key)
    ayah_count = SURAH_AYAH_COUNTS.get(surah)
    if source is None or ayah_count is None:
        return None

    audio_path = download_surah_audio(surah, reciter_key)
    if audio_path is None:
        return None

    extract_dir = _ensure_timings_extracted(reciter_key)
    if extract_dir is None:
        return None

    txt_path = extract_dir / source["timings_filename"].format(surah=surah)
    if not txt_path.exists():
        return None

    try:
        raw = txt_path.read_text(encoding="utf-8", errors="ignore")
        values_ms = [float(line) for line in raw.splitlines() if line.strip()]
    except (OSError, ValueError):
        return None

    if len(values_ms) == ayah_count + 1:
        values_ms = values_ms[1:]  # drop the leading Isti'adhah segment
    elif len(values_ms) != ayah_count:
        return None  # unrecognized shape for this surah/reciter -- fall back

    boundaries = {}
    prev_end = 0.0
    for ayah_number, end_ms in enumerate(values_ms, start=1):
        end_sec = end_ms / 1000.0
        boundaries[ayah_number] = (prev_end, end_sec)
        prev_end = end_sec

    # The last ayah's end should land at (or a little before, for trailing
    # padding) the real file's duration -- a large gap either way means the
    # timing file doesn't actually describe this recording.
    real_duration = get_audio_duration(audio_path)
    if not (0 <= real_duration - prev_end <= 5.0):
        return None

    return boundaries
