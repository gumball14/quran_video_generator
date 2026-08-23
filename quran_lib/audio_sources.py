"""
Audio-source manifest: an optional, additive override that lets a specific
ayah be rendered from a user-uploaded recording (with an optional in/out
crop) instead of the everyayah.com per-ayah download or the range-audio
hybrid pipeline in quran_lib/audio.py.

SCHEMA
------
{
  "ayahs": {
    "<ayah_number>": {
      "filename": "3.mp3",     // name of a file inside this session's
                                // uploads/<session_id>/ dir -- never a
                                // path (see _validate_ayah_entry)
      "crop_start": 0.0,       // seconds into the uploaded file; optional,
      "crop_end": 12.4         // defaults to the whole file if omitted
    }
  }
}

Ayah number "0" is the Basmala scene (matches the convention used
throughout quran_lib.timing) -- it shares ayah 1's uploaded file and crop
window rather than pointing at a separate upload.

Crop is stored as offsets only, exactly like quran_lib.audio's silence-trim
functions -- nothing here ever cuts a second physical file. Resolving an
entry to a real (possibly trimmed) audio file happens lazily, at render
time, via quran_lib.audio.get_custom_ayah_audio().

IMPORTANT -- coordinate system: any timing manifest (quran_lib/timing.py)
frame times for an ayah that also has an entry here are relative to the
CROPPED window (i.e. time 0 = crop_start), not the raw upload. The editor
is responsible for only letting word-timing markers be placed after a
crop is committed; nothing in this module enforces that ordering itself.

A manifest is entirely optional, and so is any single ayah within it:
video_build.build_video() falls back to today's download/range-audio
pipeline for any ayah with no entry here. Nothing existing breaks if you
never pass one at all.
"""
import json
import re
from pathlib import Path


class AudioSourceManifestError(ValueError):
    """Raised when an audio-source manifest file is structurally invalid."""


# Filenames are looked up inside a session's uploads/ dir by simple
# concatenation (see app.py's upload endpoint) -- reject anything that
# isn't a plain, single-segment filename so a crafted manifest can't walk
# out of that directory (e.g. "../../etc/passwd").
_SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")


def load_audio_source_manifest(path: Path) -> dict:
    """Load and validate an audio-source manifest. Raises
    AudioSourceManifestError with a specific, actionable message on
    anything malformed -- same reasoning as timing.load_timing_manifest():
    this is meant to be produced by the timing editor UI (or hand-edited),
    so silent partial-acceptance would just hide mistakes until render time."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict) or "ayahs" not in data:
        raise AudioSourceManifestError('Audio-source manifest must be a JSON object with an "ayahs" key.')
    if not isinstance(data["ayahs"], dict):
        raise AudioSourceManifestError('"ayahs" must be a JSON object keyed by ayah number.')

    for ayah_key, entry in data["ayahs"].items():
        _validate_ayah_entry(ayah_key, entry)

    return data


def _validate_ayah_entry(ayah_key, entry):
    where = f'ayahs["{ayah_key}"]'
    try:
        int(ayah_key)
    except ValueError:
        raise AudioSourceManifestError(f"{where}: key must be an ayah number (got {ayah_key!r}).")

    if not isinstance(entry, dict) or "filename" not in entry:
        raise AudioSourceManifestError(f'{where}: must be an object with a "filename".')

    filename = entry["filename"]
    if not isinstance(filename, str) or not filename.strip():
        raise AudioSourceManifestError(f'{where}["filename"]: must be a non-empty string.')
    if not _SAFE_FILENAME_RE.match(filename) or filename in (".", ".."):
        raise AudioSourceManifestError(
            f'{where}["filename"]: {filename!r} must be a plain filename '
            f'(letters, numbers, "_", "-", "." only -- no path separators).'
        )

    crop_start = entry.get("crop_start", 0.0)
    crop_end = entry.get("crop_end")
    if not isinstance(crop_start, (int, float)):
        raise AudioSourceManifestError(f'{where}["crop_start"]: must be a number.')
    if crop_start < 0:
        raise AudioSourceManifestError(f'{where}["crop_start"]: must be >= 0 (got {crop_start}).')
    if crop_end is not None:
        if not isinstance(crop_end, (int, float)):
            raise AudioSourceManifestError(f'{where}["crop_end"]: must be a number.')
        if crop_end <= crop_start:
            raise AudioSourceManifestError(
                f'{where}["crop_end"] ({crop_end}) must be greater than "crop_start" ({crop_start}).'
            )


def get_ayah_audio_source(manifest, ayah_number: int):
    """Returns the {"filename", "crop_start", "crop_end"} entry for
    ayah_number (crop_start defaulted to 0.0, crop_end to None meaning
    "to the end of the file"), or None if the manifest has no entry for
    it -- meaning: use the normal download/range-audio pipeline instead."""
    if manifest is None:
        return None
    entry = manifest.get("ayahs", {}).get(str(ayah_number))
    if entry is None:
        return None
    return {
        "filename": entry["filename"],
        "crop_start": entry.get("crop_start", 0.0),
        "crop_end": entry.get("crop_end"),
    }
