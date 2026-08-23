"""
Timing manifest: an optional, additive override that lets a specific
ayah be rendered from explicit, user-supplied frame timings instead of
the automatic highlight_fallback_wps pacing in video_build.py.

SCHEMA
------
{
  "ayahs": {
    "<ayah_number>": {
      "frames": [
        {
          "start": 0.0,              // seconds from the start of this
          "end": 2.1,                // ayah's audio (post-crop, if cropped)
          "text": "بِسْمِ اللَّهِ",    // Arabic shown during this frame --
                                      // doesn't have to match the ayah's
                                      // own text verbatim; you can combine
                                      // or split words however you like
          "translation": "...",      // optional; falls back to the ayah's
                                      // own translation if omitted
          "highlight_words": [       // optional; word indices are into
            {"index": 0, "start": 0.0, "end": 0.9},   // *this frame's* text,
            {"index": 1, "start": 0.9, "end": 2.1}    // not the whole ayah
          ]
        }
      ]
    }
  }
}

Ayah number "0" is the Basmala scene (matches the number=0 convention
used elsewhere for the split-off Bismillah). Its frame times share the
SAME clock as ayah 1's own frames -- both are timed against ayah 1's one
undivided audio file (see quran_lib.audio.detect_basmala_split()), not
two separate clips -- so build_video() merges the "0" entry's frames in
front of ayah 1's before rendering, and plays them both from a single
audio subclip. Keys are strings because that's what JSON object keys
are -- get_ayah_frames() takes a normal int and does the str() conversion
for you.

A manifest is entirely optional, and so is any single ayah within it:
video_build.build_video() falls back to today's automatic pacing for any
ayah that has no entry here. Nothing existing breaks if you never pass
a manifest at all.
"""
import json
from pathlib import Path


class TimingManifestError(ValueError):
    """Raised when a timing manifest file is structurally invalid."""


def load_timing_manifest(path: Path) -> dict:
    """Load and validate a timing manifest. Raises TimingManifestError with
    a specific, actionable message on anything malformed -- this file is
    meant to be hand-edited or produced by a not-yet-built UI, so silent
    partial-acceptance would just hide mistakes until render time."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict) or "ayahs" not in data:
        raise TimingManifestError('Timing manifest must be a JSON object with an "ayahs" key.')
    if not isinstance(data["ayahs"], dict):
        raise TimingManifestError('"ayahs" must be a JSON object keyed by ayah number.')

    for ayah_key, entry in data["ayahs"].items():
        _validate_ayah_entry(ayah_key, entry)

    return data


def _validate_ayah_entry(ayah_key, entry):
    where = f'ayahs["{ayah_key}"]'
    try:
        int(ayah_key)
    except ValueError:
        raise TimingManifestError(f"{where}: key must be an ayah number (got {ayah_key!r}).")

    if not isinstance(entry, dict) or "frames" not in entry:
        raise TimingManifestError(f'{where}: must be an object with a "frames" list.')
    frames = entry["frames"]
    if not isinstance(frames, list) or not frames:
        raise TimingManifestError(f'{where}["frames"]: must be a non-empty list.')

    prev_end = None
    for i, frame in enumerate(frames):
        fwhere = f'{where}["frames"][{i}]'
        for key in ("start", "end", "text"):
            if key not in frame:
                raise TimingManifestError(f'{fwhere}: missing required field "{key}".')
        start, end, text = frame["start"], frame["end"], frame["text"]
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            raise TimingManifestError(f'{fwhere}: "start"/"end" must be numbers.')
        if end <= start:
            raise TimingManifestError(f'{fwhere}: "end" ({end}) must be greater than "start" ({start}).')
        if prev_end is not None and start < prev_end:
            raise TimingManifestError(
                f'{fwhere}: frames overlap -- starts at {start}s but the previous frame ends at {prev_end}s.'
            )
        prev_end = end
        if not isinstance(text, str) or not text.strip():
            raise TimingManifestError(f'{fwhere}: "text" must be a non-empty string.')

        for j, wt in enumerate(frame.get("highlight_words", [])):
            wwhere = f'{fwhere}["highlight_words"][{j}]'
            for key in ("index", "start", "end"):
                if key not in wt:
                    raise TimingManifestError(f'{wwhere}: missing required field "{key}".')
            if not isinstance(wt["index"], int) or wt["index"] < 0:
                raise TimingManifestError(f'{wwhere}: "index" must be a non-negative integer.')
            if wt["end"] <= wt["start"]:
                raise TimingManifestError(f'{wwhere}: "end" must be greater than "start".')
            if wt["start"] < start or wt["end"] > end:
                raise TimingManifestError(
                    f'{wwhere}: word timing [{wt["start"]}, {wt["end"]}]s falls outside '
                    f'its frame\'s range [{start}, {end}]s.'
                )


def get_ayah_frames(manifest, ayah_number: int):
    """Returns the frame list for ayah_number, sorted by start time, or
    None if the manifest has no entry for it (meaning: use the automatic
    pacing instead)."""
    if manifest is None:
        return None
    entry = manifest.get("ayahs", {}).get(str(ayah_number))
    if entry is None:
        return None
    return sorted(entry["frames"], key=lambda f: f["start"])
