#!/usr/bin/env python3
"""
Ayah Frame Studio -- local web server
======================================
Small Flask app that puts a browser UI in front of quran_video.py, so
you can pick a surah/reciter/theme in the browser and it runs the real
generator for you in the background, right here on your own machine.

Run:
    python app.py

Then open http://127.0.0.1:5050 in your browser.

Nothing here talks to any outside server except the two things
quran_video.py itself already talks to (alquran.cloud for text,
everyayah.com for audio) -- this Flask app just runs on localhost.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

import requests
from flask import Flask, request, jsonify, send_from_directory, send_file, abort, redirect

HERE = Path(__file__).parent.resolve()
STATIC_DIR = HERE / "static"
OUTPUT_DIR = HERE / "output"          # matches quran_video.py's own default
JOBS_DIR = HERE / "jobs"              # per-job temp files (e.g. uploaded theme.json)
THEMES_DIR = HERE / "themes"          # user-saved custom themes, one JSON file per theme
SESSIONS_DIR = HERE / "sessions"      # one JSON per video-creation session: full generation config + incomplete/complete status
UPLOADS_DIR = HERE / "uploads"        # user-uploaded ayah audio (Phase 3), one subdir per session: uploads/<session_id>/<ayah>.<ext>
CUSTOM_AUDIO_PRESETS_DIR = HERE / "custom_audio_presets"  # named snapshots of a custom_audio.html session -- one subdir per preset: <id>/meta.json + <id>/range.<ext> (+ basmala.<ext>)

OUTPUT_DIR.mkdir(exist_ok=True)
JOBS_DIR.mkdir(exist_ok=True)
THEMES_DIR.mkdir(exist_ok=True)
SESSIONS_DIR.mkdir(exist_ok=True)
UPLOADS_DIR.mkdir(exist_ok=True)
CUSTOM_AUDIO_PRESETS_DIR.mkdir(exist_ok=True)


def _migrate_legacy_projects_dir():
    """Older versions of this app only ever wrote projects/<id>.json after a
    successful generate, so every legacy file is safe to treat as a
    "complete" session. Copies forward (doesn't touch/delete the old dir)."""
    legacy_dir = HERE / "projects"
    if not legacy_dir.is_dir():
        return
    for legacy_path in legacy_dir.glob("*.json"):
        dest_path = SESSIONS_DIR / legacy_path.name
        if dest_path.exists():
            continue
        try:
            data = json.loads(legacy_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        data.setdefault("status", "complete")
        data.setdefault("id", legacy_path.stem)
        now = legacy_path.stat().st_mtime
        data.setdefault("createdAt", now)
        data.setdefault("updatedAt", now)
        dest_path.write_text(json.dumps(data), encoding="utf-8")


_migrate_legacy_projects_dir()

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")
# Phase 3 custom-audio uploads are the only large-file endpoint this app
# has -- cap it here so one accidental multi-hour recording can't fill the
# disk. Every other route only ever sends/receives small JSON payloads.
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25MB


@app.errorhandler(413)
def _handle_upload_too_large(e):
    return jsonify({"error": "That file is too large (25MB max)."}), 413


from quran_lib.quran_api import fetch_verses, Verse, fetch_translation_editions
from quran_lib.recognize import (
    transcribe_audio, match_transcript_to_quran, align_words_to_audio, align_ayah_starts,
)
from quran_lib.audio import (
    download_ayah_audio, download_audio_from_url_cached, detect_basmala_split, get_audio_duration, get_custom_ayah_audio,
    get_surah_ayah_boundaries, probe_remote_duration,
)
from quran_lib.constants import FONT_DIR, RANGE_AUDIO_SOURCES
from quran_lib.theme import load_theme
from quran_lib.text_render import build_ayah_layout, draw_dynamic_layer
from quran_lib import theme as theme_mod
from quran_lib import facebook as fb
from quran_lib import youtube as yt

RECITERS = {
    "yasser_al_dossary": "Yasser Al-Dossary",
    "alafasy": "Mishary Alafasy",
    "abdul_basit": "Abdul Basit",
    "sudais": "Abdurrahman As-Sudais",
    "ghamdi": "Saad Al-Ghamdi",
    "muaiqly": "Maher Al-Muaiqly",
    "hussary": "Mahmoud Al-Hussary",
    "hudhaifi": "Ali Al-Hudhaifi",
    "ayyub": "Muhammad Ayyub",
    "qatami": "Nasser Al-Qatami",

    "shuraim": "Saud Al-Shuraim",
    "minshawi": "Mohamed Al-Minshawi (Murattal)",
    "minshawi_mujawwad": "Mohamed Al-Minshawi (Mujawwad)",
    "hussary_mujawwad": "Mahmoud Al-Hussary (Mujawwad)",
    "hussary_muallim": "Mahmoud Al-Hussary (Muallim, teaching pace)",
    "basfar": "Abdullah Basfar",
    "shatri": "Abu Bakr Al-Shatri",
    "ajamy": "Ahmed Al-Ajmy",
    "abdul_samad": "Abdul Samad",
    "hani_rifai": "Hani Ar-Rifai",
    "qahtani": "Khalid Al-Qahtani",
    "jibreel": "Muhammad Jibreel",
    "al_qasim": "Muhsin Al-Qasim",
    "mustafa_ismail": "Mustafa Ismail",
    "budair": "Salah Al-Budair",
    "bukhatir": "Salah Bukhatir",
    "tablawi": "Mohamed Al-Tablawi",
    "matrood": "Abdullah Al-Matrood",
    "juhaynee": "Abdullah Al-Juhaynee",
    "neana": "Ahmed Neana",
    "alaqimy": "Akram Al-Alaqimy",
    "hajjaj": "Ali Hajjaj Al-Suesy",
    "ali_jaber": "Ali Jaber",
    "sowaid": "Ayman Sowaid",
    "fares_abbad": "Fares Abbad",
    "akhdar": "Ibrahim Al-Akhdar",
    "abdulkareem": "Muhammad Abdul Kareem",
    "nabil_rifai": "Nabil Ar-Rifai",
    "sahl_yassin": "Sahl Yassin",
    "yasser_salamah": "Yasser Salamah",
    "aziz_alili": "Aziz Alili",
    "tunaiji": "Khalifa Al-Tunaiji",
    "al_banna": "Mahmoud Ali Al-Banna",
    "mansoori": "Karim Mansoori",
    "parhizgar": "Shahriar Parhizgar",
}

JOBS = {}          # job_id -> dict
JOBS_LOCK = threading.Lock()

# quran_lib.theme.THEME is a single mutable module-level dict that
# load_theme()/render_verse_frame() read and write in place -- under the
# threaded dev server, concurrent preview requests must not interleave
# their theme loads/renders, so they all go through this lock.
THEME_LOCK = threading.Lock()

TOTAL_VERSES_RE = re.compile(r"—\s*(\d+)\s*verse")
FRAME_RE = re.compile(r"rendering\b.*frame")
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")

SESSION_FIELDS = (
    # Orientation isn't a session field -- it lives on the theme itself
    # (theme["orientation"]) since a style is inherently vertical or
    # horizontal; api_generate() below reads it out of the resolved theme.
    "surah", "ayahStart", "ayahEnd", "reciter", "translation",
    "noTranslation", "noSplitBasmala", "noOutro", "theme", "themeName", "timing",
    "customAudio",  # dict or None, an audio-source manifest (see quran_lib/audio_sources.py)
)


def _session_path(session_id):
    if not SESSION_ID_RE.match(session_id or ""):
        return None
    return SESSIONS_DIR / f"{session_id}.json"


def _load_session(session_id):
    path = _session_path(session_id)
    if path is None or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _save_session(session):
    path = _session_path(session["id"])
    path.write_text(json.dumps(session), encoding="utf-8")


def _new_session(entry=None):
    now = time.time()
    session = {"id": uuid.uuid4().hex[:10], "status": "incomplete", "createdAt": now, "updatedAt": now}
    for key in SESSION_FIELDS:
        session[key] = None
    # jobId/jobStatus/outputFile track the most recent generate job for this
    # session -- set directly by api_generate()/_finish_session_job(), never
    # PUT-editable (not in SESSION_FIELDS). JOBS (in-memory, see JOBS below)
    # is what generating.html/video_ready.html poll while the server is up,
    # but it's lost on a restart -- these three fields are the durable
    # fallback that lets those screens recover a job's last-known outcome
    # from sessionId alone (see api_status()/api_download()'s fallback).
    session["jobId"] = None
    session["jobStatus"] = None
    session["outputFile"] = None
    session["translation"] = "en.sahih"
    for key in ("noTranslation", "noSplitBasmala", "noOutro"):
        session[key] = False
    # "entry" ("custom" or "reciter") records which top-level flow the user
    # started from -- set once here, never in SESSION_FIELDS/PUT-editable,
    # so index.html's "continue editing" can route an incomplete session
    # back to the right screen (custom_audio.html vs new_video.html).
    session["entry"] = entry if entry in ("custom", "reciter") else None
    return session


# --------------------------------------------------------------------------
# Sessions (a video-creation project: created when the user starts a new
# video, edited while "incomplete", locked to only theme/style changes once
# "complete" -- see sessions-refactor-progress.md)
# --------------------------------------------------------------------------

@app.route("/api/sessions", methods=["POST"])
def api_create_session():
    data = request.get_json(force=True, silent=True) or {}
    session = _new_session(entry=data.get("entry"))
    _save_session(session)
    return jsonify(session)


@app.route("/api/sessions")
def api_list_sessions():
    sessions = []
    for path in SESSIONS_DIR.glob("*.json"):
        try:
            s = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        s.pop("timing", None)  # can be a large manifest; not needed for the list view
        sessions.append(s)
    sessions.sort(key=lambda s: s.get("updatedAt") or 0, reverse=True)
    return jsonify(sessions)


@app.route("/api/sessions/<session_id>")
def api_get_session(session_id):
    session = _load_session(session_id)
    if session is None:
        abort(404)
    return jsonify(session)


@app.route("/api/sessions/<session_id>", methods=["PUT"])
def api_update_session(session_id):
    session = _load_session(session_id)
    if session is None:
        abort(404)
    if session.get("status") == "complete":
        print(f"[session PUT] {session_id}: rejected (session already complete)")
        return jsonify({"error": "This session is already complete and can't be edited."}), 409
    data = request.get_json(force=True, silent=True) or {}
    updated_keys = [key for key in SESSION_FIELDS if key in data]
    for key in updated_keys:
        session[key] = data[key]
    session["updatedAt"] = time.time()
    _save_session(session)
    timing_info = "not sent"
    if "timing" in updated_keys:
        timing = data.get("timing")
        timing_info = f"{len(timing.get('ayahs', {}))} ayah(s)" if timing else "cleared/empty"
    print(f"[session PUT] {session_id}: updated fields={updated_keys} timing={timing_info}")
    return jsonify(session)


@app.route("/api/sessions/<session_id>", methods=["DELETE"])
def api_delete_session(session_id):
    path = _session_path(session_id)
    if path is None:
        abort(400)
    if not path.exists():
        abort(404)
    path.unlink(missing_ok=True)
    return jsonify({"ok": True})


# --------------------------------------------------------------------------
# Static pages
# --------------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/fonts/<path:filename>")
def fonts(filename):
    """Serves the bundled .ttf files so the browser preview (frame_editor.html's
    font picker) can @font-face the exact same files text_render.py renders
    with -- not just an approximation from Google Fonts."""
    return send_from_directory(FONT_DIR, filename)


@app.route("/api/reciters")
def api_reciters():
    return jsonify(RECITERS)


@app.route("/api/translations")
def api_translations():
    """List of available translation editions (any language -- see
    fetch_translation_editions()), for the "Translation" picker in
    new_video.html."""
    try:
        return jsonify(fetch_translation_editions())
    except Exception:  # noqa: BLE001 -- network hiccup; the picker falls back to its default
        return jsonify({"error": "Couldn't fetch the translation list."}), 502


@app.route("/api/reciters/gapless")
def api_reciters_gapless():
    """Reciter keys that HAVE a continuous-per-surah source configured (see
    RANGE_AUDIO_SOURCES in quran_lib/constants.py) -- i.e. candidates worth
    trying at all. This is only a static, hand-curated list of what URLs
    exist; it does NOT confirm any given reciter/surah pair actually
    resolves to a genuine gapless recording (a download can fail, or the
    timing data can mismatch the audio -- see get_surah_ayah_boundaries()).
    The picker in new_video.html uses this only to pick a sensible default
    reciter to try; the real, authoritative check for any specific
    surah/reciter pair is /api/reciters/verify-gapless below."""
    return jsonify(sorted(RANGE_AUDIO_SOURCES.keys()))


@app.route("/api/reciters/verify-gapless")
def api_reciters_verify_gapless():
    """Live, authoritative check for whether THIS reciter/surah pair will
    actually render from one continuous, gapless recording -- runs the same
    timing-data cross-check build_video() uses (get_surah_ayah_boundaries())
    but WITHOUT downloading the full recording: probe_remote_duration()
    reads the remote file's duration directly via ffprobe (a couple of
    small HTTP requests, not the whole body), which is what makes this
    cheap enough to run on every reciter pick even for a very long surah
    (Al-Baqarah's continuous recording is ~115MB). The timing archive
    itself is still downloaded and cached per-reciter (small, and shared
    across all of that reciter's surahs -- see _ensure_timings_extracted()).

    This deliberately does NOT trust the static candidate list above --
    that only says a URL pattern is configured for this reciter, not that
    today's recording for THIS surah actually is a matching, continuous
    take."""
    reciter = request.args.get("reciter", "")
    if reciter not in RECITERS:
        return jsonify({"error": "Unknown reciter."}), 400
    try:
        surah = int(request.args.get("surah"))
    except (TypeError, ValueError):
        return jsonify({"error": "surah must be a number."}), 400

    source = RANGE_AUDIO_SOURCES.get(reciter)
    if source is None:
        return jsonify({"gapless": False, "reason": "This reciter has no continuous recording source configured."})

    duration = probe_remote_duration(source["audio_url"].format(surah=surah))
    if duration is None:
        return jsonify({
            "gapless": False,
            "reason": "Couldn't reach or read this reciter's continuous recording for this surah.",
        })

    boundaries = get_surah_ayah_boundaries(surah, reciter, real_duration=duration)
    if boundaries is None:
        return jsonify({
            "gapless": False,
            "reason": "This reciter's continuous recording doesn't match its timing data for this surah.",
        })
    return jsonify({"gapless": True})


# --------------------------------------------------------------------------
# Timing editor support (Phase 2: waveform + manual word-timing editor)
# --------------------------------------------------------------------------

@app.route("/api/timing/text")
def api_timing_text():
    """Fetch one ayah's Arabic + translation for the timing editor. Same
    fetch_verses() call the real generator uses, so word splitting/indexing
    matches exactly what render_verse_frame() will do at generation time."""
    try:
        surah = int(request.args.get("surah"))
        ayah = int(request.args.get("ayah"))
    except (TypeError, ValueError):
        return jsonify({"error": "surah and ayah must be numbers."}), 400
    if not (1 <= surah <= 114) or ayah < 1:
        return jsonify({"error": "surah must be 1-114 and ayah must be >= 1."}), 400

    translation = (request.args.get("translation") or "en.sahih").strip()
    if not re.match(r"^[a-zA-Z0-9_.\-]+$", translation):
        return jsonify({"error": "That translation edition code doesn't look valid."}), 400

    try:
        verses, surah_name, surah_name_arabic = fetch_verses(
            surah, translation, ayah_start=ayah, ayah_end=ayah, split_basmala=True,
        )
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"Couldn't fetch that ayah: {e}"}), 502

    if not verses:
        return jsonify({"error": "No such ayah."}), 404
    v = verses[0]
    return jsonify({
        "surahName": surah_name,
        "surahNameArabic": surah_name_arabic,
        "number": v.number,
        "arabic": v.arabic,
        "translation": v.translation,
        "basmalaArabic": v.basmala_arabic,  # non-null only for ayah 1 of most surahs
    })


_AUDIO_MIMETYPES = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".ogg": "audio/ogg"}


def _upload_dir_for(session_id):
    return UPLOADS_DIR / session_id


# Same charset as quran_lib/audio_sources.py's _SAFE_FILENAME_RE -- used here
# to validate the optional `customFilename` param below (a plain filename
# inside this session's upload dir, never a path) before touching disk with it.
_SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")


def _resolve_ayah_audio(surah, ayah, reciter):
    """Resolves which audio file the timing editor should read for one
    ayah, and its mimetype. If `sessionId` (+ optional `cropStart`/
    `cropEnd`, seconds) is present in the request AND that session has a
    Phase-3 custom-audio upload for this ayah, that upload wins over the
    reciter download -- cropped on the fly via get_custom_ayah_audio() if
    crop bounds were given, exactly the same lazy/cached path
    build_video() will use at generation time. Falls back to the normal
    download_ayah_audio() pipeline otherwise, unchanged.

    `customFilename`, if given, addresses the upload directly by name
    instead of the default "<ayah>.<ext>" convention -- this is what lets
    several ayahs share ONE uploaded file (e.g. a whole surah recorded in
    one take that the user splits into ayahs themselves via different crop
    windows on the SAME filename; see static/timing.html's "split" flow).
    Falls back to the per-ayah glob when omitted, for backward
    compatibility with the original one-upload-per-ayah flow.

    Raises on download/ffprobe failure; callers turn that into a 502/400."""
    session_id = request.args.get("sessionId", "")
    if SESSION_ID_RE.match(session_id):
        upload_dir = _upload_dir_for(session_id)
        upload_path = None

        custom_filename = request.args.get("customFilename", "")
        if custom_filename and _SAFE_FILENAME_RE.match(custom_filename):
            candidate = upload_dir / custom_filename
            if candidate.is_file():
                upload_path = candidate
        elif upload_dir.is_dir():
            # Exclude cached crop outputs (named "<ayah>.crop_...") from the
            # source lookup -- only the original upload should be treated as
            # the source of truth for what to (re-)crop from.
            matches = [p for p in upload_dir.glob(f"{ayah}.*") if not p.name.startswith(f"{ayah}.crop_")]
            if matches:
                upload_path = matches[0]

        if upload_path is not None:
            crop_start = float(request.args.get("cropStart", 0.0) or 0.0)
            crop_end_raw = request.args.get("cropEnd")
            crop_end = float(crop_end_raw) if crop_end_raw not in (None, "") else None
            audio_path = get_custom_ayah_audio(upload_path, crop_start, crop_end)
            return audio_path, _AUDIO_MIMETYPES.get(audio_path.suffix.lower(), "audio/mpeg")

    audio_path = download_ayah_audio(surah=surah, ayah=ayah, reciter_key=reciter)
    return audio_path, "audio/mpeg"


@app.route("/api/timing/audio")
def api_timing_audio():
    """Serve the ayah's raw, undivided recitation audio for the waveform
    editor -- always the whole file, never a physically split piece of it.
    For an ayah 1 that has the Bismillah prepended, the editor uses
    /api/timing/basmala-split to get the Bismillah/ayah boundary as a
    timestamp within this same file and treats it as a frame divider, not a
    reason to fetch a different audio resource.

    A session with a Phase-3 custom-audio upload for this ayah (see
    _resolve_ayah_audio()) is served instead of the reciter download --
    pass sessionId (and cropStart/cropEnd once a crop is set) to opt in."""
    try:
        surah = int(request.args.get("surah"))
        ayah = int(request.args.get("ayah"))
    except (TypeError, ValueError):
        return jsonify({"error": "surah and ayah must be numbers."}), 400

    reciter = request.args.get("reciter", "yasser_al_dossary")
    if reciter not in RECITERS:
        return jsonify({"error": "Unknown reciter."}), 400

    try:
        audio_path, mimetype = _resolve_ayah_audio(surah, ayah, reciter)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"Couldn't load that ayah's audio: {e}"}), 502

    return send_file(audio_path, mimetype=mimetype)


@app.route("/api/timing/basmala-split")
def api_timing_basmala_split():
    """Detects -- but never cuts -- the Bismillah/ayah boundary in ayah 1's
    audio, so the timing editor can offer it as a starting guess for where
    the Bismillah frame ends and the ayah frame begins. Always reports a
    timestamp within the single undivided audio file returned by
    /api/timing/audio (including a Phase-3 custom upload, if one applies --
    see _resolve_ayah_audio()); see detect_basmala_split()."""
    try:
        surah = int(request.args.get("surah"))
        ayah = int(request.args.get("ayah"))
    except (TypeError, ValueError):
        return jsonify({"error": "surah and ayah must be numbers."}), 400

    reciter = request.args.get("reciter", "yasser_al_dossary")
    if reciter not in RECITERS:
        return jsonify({"error": "Unknown reciter."}), 400

    try:
        audio_path, _mimetype = _resolve_ayah_audio(surah, ayah, reciter)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"Couldn't load that ayah's audio: {e}"}), 502

    return jsonify({"splitAt": detect_basmala_split(audio_path)})


@app.route("/api/timing/auto-align", methods=["POST"])
def api_timing_auto_align():
    """Best-effort automatic per-word start-time detection for the timing
    editor's "auto-mark" button: resolves the same audio api_timing_audio()
    would serve (surah/ayah/reciter query params, same Phase-3
    custom-upload override), narrows it to one frame's window if rangeStart/
    rangeEnd (frame-relative seconds, JSON body) were given -- e.g. just the
    ayah half of a Bismillah/ayah-1 pair -- and runs it through
    align_words_to_audio() against the `words` list (also JSON body, in
    on-screen order) from quran_lib/recognize.py.

    Returns {"times": [...], "merges": [...]}: one start time per word in
    `words`, frame-relative (i.e. 0-based at rangeStart) exactly like a
    hand-placed marker's `time` -- callers that merge two words into one
    beat just use the first word's time for the pair, same as marking it
    by hand would -- plus word indices where an unusually tight gap
    suggests merging that word into the next one (never more than two
    words at a time; see align_words_to_audio())."""
    try:
        surah = int(request.args.get("surah"))
        ayah = int(request.args.get("ayah"))
    except (TypeError, ValueError):
        return jsonify({"error": "surah and ayah must be numbers."}), 400

    reciter = request.args.get("reciter", "yasser_al_dossary")
    if reciter not in RECITERS:
        return jsonify({"error": "Unknown reciter."}), 400

    data = request.get_json(silent=True) or {}
    words = data.get("words")
    if not isinstance(words, list) or not words or not all(isinstance(w, str) for w in words):
        return jsonify({"error": "words must be a non-empty list of strings."}), 400

    try:
        range_start = float(data.get("rangeStart") or 0.0)
        range_end = float(data["rangeEnd"]) if data.get("rangeEnd") is not None else None
    except (TypeError, ValueError):
        return jsonify({"error": "rangeStart/rangeEnd must be numbers."}), 400

    try:
        audio_path, _mimetype = _resolve_ayah_audio(surah, ayah, reciter)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"Couldn't load that ayah's audio: {e}"}), 502

    if range_start > 0 or range_end is not None:
        audio_path = get_custom_ayah_audio(audio_path, range_start, range_end)

    try:
        result = align_words_to_audio(audio_path, words)
    except Exception as e:  # noqa: BLE001 -- whisper/model errors surface as a plain message
        return jsonify({"error": f"Auto-detection failed: {e}"}), 500

    return jsonify(result)


# Phase 3: custom audio upload. Extensions accepted here are the only ones
# ffmpeg/moviepy already have to read elsewhere in this app, and the size
# cap keeps a single accidental multi-hour recording from filling the disk
# -- both are enforced before the file is even fully written (see
# app.config["MAX_CONTENT_LENGTH"] near the top of this file and the
# extension check below).
_ALLOWED_AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg"}


def _save_uploaded_audio(dest_dir, stem, file):
    """Shared save+validate logic for both the per-ayah and whole-range
    upload endpoints below: extension check, save as "<stem>.<ext>" inside
    dest_dir (clearing any other extension previously saved under the same
    stem so a re-upload with a different container doesn't leave a stale
    file behind), then ffprobe-validate via get_audio_duration() -- deleting
    the file again on failure rather than leaving a dead upload on disk.
    Returns (filename, duration). Raises ValueError(message) on a rejected
    file; callers turn that into a 400."""
    ext = Path(file.filename).suffix.lower()
    if ext not in _ALLOWED_AUDIO_EXTENSIONS:
        raise ValueError(
            f"Unsupported file type {ext or '(none)'!r}. "
            f"Accepted: {', '.join(sorted(_ALLOWED_AUDIO_EXTENSIONS))}."
        )

    dest_dir.mkdir(parents=True, exist_ok=True)
    for old in dest_dir.glob(f"{stem}.*"):
        old.unlink(missing_ok=True)
    dest_path = dest_dir / f"{stem}{ext}"
    file.save(dest_path)

    try:
        duration = get_audio_duration(dest_path)
    except Exception:  # noqa: BLE001 -- ffprobe rejected it, or it's not audio at all
        dest_path.unlink(missing_ok=True)
        raise ValueError("That file doesn't look like valid audio ffmpeg can read.")

    return dest_path.name, duration


@app.route("/api/timing/upload-audio", methods=["POST"])
def api_timing_upload_audio():
    """Accepts a user-recorded/uploaded ayah audio file, validates it's
    something ffmpeg can actually decode, and stores it at
    uploads/<session_id>/<ayah>.<ext> for the timing editor's waveform and
    (eventually) the real generator to use instead of the everyayah.com
    download -- see quran_lib/audio_sources.py for how a finished manifest
    references this file by name."""
    session_id = request.form.get("sessionId", "")
    if not SESSION_ID_RE.match(session_id) or _load_session(session_id) is None:
        return jsonify({"error": "Unknown or invalid sessionId."}), 400

    try:
        ayah = int(request.form.get("ayah"))
    except (TypeError, ValueError):
        return jsonify({"error": "ayah must be a number."}), 400
    if ayah < 0:
        return jsonify({"error": "ayah must be >= 0 (0 = the Basmala scene)."}), 400

    file = request.files.get("audio")
    if file is None or not file.filename:
        return jsonify({"error": "No audio file was uploaded."}), 400

    try:
        filename, duration = _save_uploaded_audio(_upload_dir_for(session_id), str(ayah), file)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify({"filename": filename, "duration": duration})


@app.route("/api/timing/upload-audio", methods=["DELETE"])
def api_timing_delete_upload_audio():
    """Removes a previously uploaded ayah audio file, reverting that ayah
    back to the reciter download. No-op (not an error) if nothing was
    uploaded for it."""
    session_id = request.args.get("sessionId", "")
    if not SESSION_ID_RE.match(session_id):
        return jsonify({"error": "Invalid sessionId."}), 400
    try:
        ayah = int(request.args.get("ayah"))
    except (TypeError, ValueError):
        return jsonify({"error": "ayah must be a number."}), 400

    dest_dir = _upload_dir_for(session_id)
    for old in dest_dir.glob(f"{ayah}.*"):
        old.unlink(missing_ok=True)
    return jsonify({"ok": True})


@app.route("/api/timing/upload-range-audio", methods=["POST"])
def api_timing_upload_range_audio():
    """Accepts ONE continuous recording covering multiple ayahs (e.g. a
    beautiful recitation downloaded from elsewhere that the user wants to
    split into ayahs themselves), stored as uploads/<session_id>/range.<ext>
    -- a single shared file that the frontend later slices per ayah via
    different crop_start/crop_end windows on the SAME filename (see
    static/timing.html's "split your recording" flow, and
    _resolve_ayah_audio()'s `customFilename` param above). Same validation
    as the per-ayah endpoint; only the fixed "range" stem differs, since
    this upload isn't owned by any one ayah."""
    session_id = request.form.get("sessionId", "")
    if not SESSION_ID_RE.match(session_id) or _load_session(session_id) is None:
        return jsonify({"error": "Unknown or invalid sessionId."}), 400

    file = request.files.get("audio")
    if file is None or not file.filename:
        return jsonify({"error": "No audio file was uploaded."}), 400

    try:
        filename, duration = _save_uploaded_audio(_upload_dir_for(session_id), "range", file)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify({"filename": filename, "duration": duration})


@app.route("/api/timing/upload-range-audio", methods=["DELETE"])
def api_timing_delete_range_audio():
    """Removes the whole-range upload. Does NOT by itself revert any ayah
    that was assigned a slice of it -- the frontend clears its own
    customAudioByRealAyah entries for the range when the user removes it
    from the UI; this just deletes the underlying file."""
    session_id = request.args.get("sessionId", "")
    if not SESSION_ID_RE.match(session_id):
        return jsonify({"error": "Invalid sessionId."}), 400
    dest_dir = _upload_dir_for(session_id)
    for old in dest_dir.glob("range.*"):
        old.unlink(missing_ok=True)
    return jsonify({"ok": True})


def _download_range_audio(dest_dir, stem, url):
    """Download+validate counterpart to _save_uploaded_audio() above, for a
    shared video URL instead of a direct file upload -- see
    download_audio_from_url_cached() (URL-keyed cache, so re-pasting the
    same URL skips yt-dlp entirely). Raises ValueError(message) on any
    failure; callers turn that into a 400."""
    try:
        dest_path = download_audio_from_url_cached(url, dest_dir, stem)
    except RuntimeError as e:
        raise ValueError(str(e))

    try:
        duration = get_audio_duration(dest_path)
    except Exception:  # noqa: BLE001 -- ffprobe rejected it, or it's not audio at all
        dest_path.unlink(missing_ok=True)
        raise ValueError("That download doesn't look like valid audio ffmpeg can read.")

    return dest_path.name, duration


@app.route("/api/timing/download-range-audio", methods=["POST"])
def api_timing_download_range_audio():
    """Fetches ONE continuous recording from a shareable video URL (Facebook
    Reels/videos, Instagram, TikTok, YouTube, ...) via yt-dlp and stores it
    exactly the way api_timing_upload_range_audio() stores a direct file
    upload -- as uploads/<session_id>/range.<ext> -- so
    static/custom_audio.html's "split your recording" flow can't tell a
    download from an upload."""
    data = request.get_json(silent=True) or {}
    session_id = data.get("sessionId", "")
    if not SESSION_ID_RE.match(session_id) or _load_session(session_id) is None:
        return jsonify({"error": "Unknown or invalid sessionId."}), 400

    url = (data.get("url") or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return jsonify({"error": "That doesn't look like a valid URL."}), 400

    try:
        filename, duration = _download_range_audio(_upload_dir_for(session_id), "range", url)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify({"filename": filename, "duration": duration})


@app.route("/api/timing/detect-range-audio", methods=["POST"])
def api_timing_detect_range_audio():
    """Best-effort "what surah/ayahs is this?" detection for a range
    recording of unknown provenance -- transcribes it with faster-whisper
    and fuzzy-matches the result against the full Quran text (see
    quran_lib/recognize.py). Only meaningful for a recording fetched from a
    URL, since a direct file upload has no source to transcribe an identity
    out of that the person picking the file doesn't already know."""
    data = request.get_json(silent=True) or {}
    session_id = data.get("sessionId", "")
    if not SESSION_ID_RE.match(session_id) or _load_session(session_id) is None:
        return jsonify({"error": "Unknown or invalid sessionId."}), 400

    matches = list(_upload_dir_for(session_id).glob("range.*"))
    if not matches:
        return jsonify({"error": "No range recording found for this session."}), 400

    try:
        transcript = transcribe_audio(matches[0])
    except Exception as e:  # noqa: BLE001 -- whisper/model errors surface as a plain message
        return jsonify({"error": f"Transcription failed: {e}"}), 500

    result = match_transcript_to_quran(transcript)
    if result is None:
        return jsonify({"error": "Couldn't confidently match this recording to any surah.", "transcript": transcript})

    result["transcript"] = transcript
    return jsonify(result)


@app.route("/api/timing/detect-ayah-starts", methods=["POST"])
def api_timing_detect_ayah_starts():
    """Best-effort automatic ayah-start marking for the custom-audio flow's
    whole-range recording (static/custom_audio.html's "auto-mark" tool):
    transcribes uploads/<session_id>/range.<ext> once with faster-whisper
    and fuzzy-matches it against every ayah's word list in `ayahs` (JSON
    body, in recitation order) via align_ayah_starts() -- see
    quran_lib/recognize.py.

    Returns {"times": [...]}: one start time (seconds into the range
    recording) per entry in `ayahs`, in the same order -- the frontend
    pairs each with its ayah number to build/replace the mark list, same
    as a hand-placed mark."""
    data = request.get_json(silent=True) or {}
    session_id = data.get("sessionId", "")
    if not SESSION_ID_RE.match(session_id) or _load_session(session_id) is None:
        return jsonify({"error": "Unknown or invalid sessionId."}), 400

    ayahs = data.get("ayahs")
    if not isinstance(ayahs, list) or not ayahs or not all(
        isinstance(a, list) and a and all(isinstance(w, str) for w in a) for a in ayahs
    ):
        return jsonify({"error": "ayahs must be a non-empty list of non-empty word lists."}), 400

    matches = list(_upload_dir_for(session_id).glob("range.*"))
    if not matches:
        return jsonify({"error": "No range recording found for this session."}), 400

    try:
        result = align_ayah_starts(matches[0], ayahs)
    except Exception as e:  # noqa: BLE001 -- whisper/model errors surface as a plain message
        return jsonify({"error": f"Auto-detection failed: {e}"}), 500

    return jsonify(result)


# --------------------------------------------------------------------------
# Custom-audio presets: a named snapshot of a custom_audio.html session --
# range recording (+ optional Basmala clip), ayah marks and trim windows --
# saved independently of any one session so it can be re-loaded into a
# DIFFERENT session later. Audio is physically copied into
# custom_audio_presets/<id>/ rather than referenced by session id, since a
# session's own uploads/<session_id>/ dir isn't guaranteed to still exist by
# the time someone comes back to load a preset.
# --------------------------------------------------------------------------

PRESET_ID_RE = SESSION_ID_RE  # same charset -- both are uuid4().hex[:10]


def _preset_dir(preset_id):
    return CUSTOM_AUDIO_PRESETS_DIR / preset_id


def _load_preset_meta(preset_id):
    if not PRESET_ID_RE.match(preset_id or ""):
        return None
    path = _preset_dir(preset_id) / "meta.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


@app.route("/api/custom-audio-presets", methods=["POST"])
def api_save_custom_audio_preset():
    data = request.get_json(silent=True) or {}
    session_id = data.get("sessionId", "")
    if not SESSION_ID_RE.match(session_id) or _load_session(session_id) is None:
        return jsonify({"error": "Unknown or invalid sessionId."}), 400

    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Name is required."}), 400
    if len(name) > 80:
        return jsonify({"error": "Name is too long (80 characters max)."}), 400

    try:
        surah = int(data.get("surah"))
        ayah_start = int(data.get("ayahStart"))
        ayah_end = int(data.get("ayahEnd"))
    except (TypeError, ValueError):
        return jsonify({"error": "surah/ayahStart/ayahEnd must be numbers."}), 400

    marks = data.get("marks")
    if not isinstance(marks, list) or not all(
        isinstance(m, dict) and isinstance(m.get("ayah"), int) and isinstance(m.get("time"), (int, float))
        for m in marks
    ):
        return jsonify({"error": "marks must be a list of {ayah, time}."}), 400

    upload_dir = _upload_dir_for(session_id)
    range_matches = list(upload_dir.glob("range.*"))
    if not range_matches:
        return jsonify({"error": "Upload a recording before saving."}), 400

    preset_id = uuid.uuid4().hex[:10]
    dest_dir = _preset_dir(preset_id)
    dest_dir.mkdir(parents=True, exist_ok=True)

    range_src = range_matches[0]
    range_dest = dest_dir / f"range{range_src.suffix.lower()}"
    shutil.copyfile(range_src, range_dest)
    try:
        range_duration = get_audio_duration(range_dest)
    except Exception:  # noqa: BLE001
        shutil.rmtree(dest_dir, ignore_errors=True)
        return jsonify({"error": "Couldn't read the recording's duration."}), 500

    basmala_audio = None
    basmala_matches = [p for p in upload_dir.glob("0.*") if not p.name.startswith("0.crop_")]
    if basmala_matches:
        basmala_src = basmala_matches[0]
        basmala_dest = dest_dir / f"basmala{basmala_src.suffix.lower()}"
        shutil.copyfile(basmala_src, basmala_dest)
        try:
            basmala_duration = get_audio_duration(basmala_dest)
        except Exception:  # noqa: BLE001
            basmala_dest.unlink(missing_ok=True)
        else:
            basmala_audio = {"filename": basmala_dest.name, "duration": basmala_duration}

    def _num_or_none(v):
        return float(v) if isinstance(v, (int, float)) else None

    meta = {
        "id": preset_id,
        "name": name,
        "surah": surah,
        "ayahStart": ayah_start,
        "ayahEnd": ayah_end,
        "translation": data.get("translation") or "en.sahih",
        "marks": [{"ayah": int(m["ayah"]), "time": float(m["time"])} for m in marks],
        "mainTrimStart": _num_or_none(data.get("mainTrimStart")),
        "mainTrimEnd": _num_or_none(data.get("mainTrimEnd")),
        "rangeAudio": {"filename": range_dest.name, "duration": range_duration},
        "basmalaAudio": basmala_audio,
        "basmalaTrimStart": _num_or_none(data.get("basmalaTrimStart")),
        "basmalaTrimEnd": _num_or_none(data.get("basmalaTrimEnd")),
        "createdAt": time.time(),
    }
    (dest_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return jsonify(meta)


@app.route("/api/custom-audio-presets")
def api_list_custom_audio_presets():
    presets = []
    for path in CUSTOM_AUDIO_PRESETS_DIR.glob("*/meta.json"):
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        presets.append({
            "id": meta["id"],
            "name": meta["name"],
            "surah": meta.get("surah"),
            "ayahStart": meta.get("ayahStart"),
            "ayahEnd": meta.get("ayahEnd"),
            "marksCount": len(meta.get("marks") or []),
            "hasBasmala": bool(meta.get("basmalaAudio")),
            "createdAt": meta.get("createdAt"),
        })
    presets.sort(key=lambda p: p["createdAt"] or 0, reverse=True)
    return jsonify(presets)


@app.route("/api/custom-audio-presets/<preset_id>", methods=["DELETE"])
def api_delete_custom_audio_preset(preset_id):
    if not PRESET_ID_RE.match(preset_id):
        return jsonify({"error": "Invalid preset id."}), 400
    shutil.rmtree(_preset_dir(preset_id), ignore_errors=True)
    return jsonify({"ok": True})


@app.route("/api/custom-audio-presets/<preset_id>/load", methods=["POST"])
def api_load_custom_audio_preset(preset_id):
    meta = _load_preset_meta(preset_id)
    if meta is None:
        return jsonify({"error": "Preset not found."}), 404

    data = request.get_json(silent=True) or {}
    session_id = data.get("sessionId", "")
    if not SESSION_ID_RE.match(session_id) or _load_session(session_id) is None:
        return jsonify({"error": "Unknown or invalid sessionId."}), 400

    src_dir = _preset_dir(preset_id)
    dest_dir = _upload_dir_for(session_id)
    dest_dir.mkdir(parents=True, exist_ok=True)

    range_src = src_dir / meta["rangeAudio"]["filename"]
    if not range_src.is_file():
        return jsonify({"error": "This preset's recording is missing on the server."}), 404
    for old in dest_dir.glob("range.*"):
        old.unlink(missing_ok=True)
    range_dest = dest_dir / f"range{range_src.suffix.lower()}"
    shutil.copyfile(range_src, range_dest)

    basmala_audio = None
    if meta.get("basmalaAudio"):
        basmala_src = src_dir / meta["basmalaAudio"]["filename"]
        if basmala_src.is_file():
            for old in dest_dir.glob("0.*"):
                old.unlink(missing_ok=True)
            basmala_dest = dest_dir / f"0{basmala_src.suffix.lower()}"
            shutil.copyfile(basmala_src, basmala_dest)
            basmala_audio = {"filename": basmala_dest.name, "duration": meta["basmalaAudio"]["duration"]}

    return jsonify({
        "surah": meta["surah"], "ayahStart": meta["ayahStart"], "ayahEnd": meta["ayahEnd"],
        "translation": meta.get("translation") or "en.sahih",
        "marks": meta["marks"],
        "mainTrimStart": meta.get("mainTrimStart"), "mainTrimEnd": meta.get("mainTrimEnd"),
        "rangeAudio": {"filename": range_dest.name, "duration": meta["rangeAudio"]["duration"]},
        "basmalaAudio": basmala_audio,
        "basmalaTrimStart": meta.get("basmalaTrimStart"), "basmalaTrimEnd": meta.get("basmalaTrimEnd"),
    })


@app.route("/api/preview-frame", methods=["POST"])
def api_preview_frame():
    """Renders one real frame with the exact same code path the video
    generator uses (load_theme() + render_verse_frame()), so the editor
    can show a pixel-accurate preview instead of its CSS approximation."""
    data = request.get_json(force=True, silent=True) or {}

    theme = data.get("theme")
    if not isinstance(theme, dict):
        return jsonify({"error": "theme must be an object."}), 400

    verse_data = data.get("verse")
    if not isinstance(verse_data, dict):
        return jsonify({"error": "verse must be an object."}), 400
    arabic = verse_data.get("arabic")
    translation = verse_data.get("translation")
    if not arabic or not translation:
        return jsonify({"error": "verse.arabic and verse.translation are required."}), 400

    size = data.get("size") or [1080, 1920]
    try:
        size = (int(size[0]), int(size[1]))
    except (TypeError, ValueError, IndexError):
        return jsonify({"error": "size must be [width, height]."}), 400

    show_translation = bool(data.get("show_translation", True))
    highlight_index = int(data.get("highlight_index", -1))

    verse = Verse(
        number=int(verse_data.get("number") or 1),
        arabic=arabic,
        translation=translation,
        basmala_arabic=verse_data.get("basmala_arabic"),
    )
    surah_name_arabic = verse_data.get("surah_name_arabic") or ""
    surah_name_text = verse_data.get("surah_name_text") or ""

    job_id = uuid.uuid4().hex[:10]
    theme_path = JOBS_DIR / f"preview_{job_id}.json"
    theme_path.write_text(json.dumps(theme), encoding="utf-8")
    try:
        with THEME_LOCK:
            load_theme(theme_path)
            # Build the layout once so we know the highlighted word's box,
            # then derive the pointer's position from it -- same as the
            # video generator's automatic-pace scene does (see
            # _add_word_highlighted_scene() in video_build.py). The pointer
            # rests directly on the highlighted word; there's no gliding
            # here since this is a single static preview frame, not a
            # sequence of frames across an ayah's audio.
            THEME = theme_mod.THEME
            base_img, layout = build_ayah_layout(
                verse, surah_name_arabic, size, show_translation, surah_name_text=surah_name_text,
            )
            word_boxes = layout["word_boxes"]
            pointer_pos = None
            if THEME["highlight_pointer_enabled"] and 0 <= highlight_index < len(word_boxes):
                box = word_boxes[highlight_index]
                pointer_pos = {"x": box["cx"], "top": box["top"], "font_size": box["font_size"]}
            image = draw_dynamic_layer(base_img, layout, highlight_index, pointer_pos)
            buf = BytesIO()
            image.save(buf, format="PNG")
            buf.seek(0)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"Couldn't render that frame: {e}"}), 500
    finally:
        theme_path.unlink(missing_ok=True)

    return send_file(buf, mimetype="image/png")


# --------------------------------------------------------------------------
# Saved themes library (user-created custom themes, persisted to disk)
# --------------------------------------------------------------------------

THEME_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")


def _read_theme_file(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return {
        "id": data.get("id") or path.stem,
        "name": data.get("name") or "Untitled theme",
        "theme": data.get("theme") or {},
        "createdAt": data.get("createdAt"),
        "updatedAt": data.get("updatedAt"),
    }


@app.route("/api/themes")
def api_themes():
    themes = []
    for theme_path in THEMES_DIR.glob("*.json"):
        record = _read_theme_file(theme_path)
        if record:
            themes.append(record)
    themes.sort(key=lambda t: t["createdAt"] or 0)
    return jsonify(themes)


@app.route("/api/themes", methods=["POST"])
def api_create_theme():
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()[:80]
    theme = data.get("theme")
    if not name:
        return jsonify({"error": "Theme name is required."}), 400
    if not isinstance(theme, dict):
        return jsonify({"error": "theme must be an object."}), 400

    theme_id = uuid.uuid4().hex[:10]
    now = time.time()
    record = {"id": theme_id, "name": name, "theme": theme, "createdAt": now, "updatedAt": now}
    (THEMES_DIR / f"{theme_id}.json").write_text(json.dumps(record), encoding="utf-8")
    return jsonify(record)


@app.route("/api/themes/<theme_id>", methods=["PUT"])
def api_update_theme(theme_id):
    if not THEME_ID_RE.match(theme_id):
        abort(400)
    theme_path = THEMES_DIR / f"{theme_id}.json"
    record = _read_theme_file(theme_path)
    if not record:
        abort(404)

    data = request.get_json(force=True, silent=True) or {}
    if "name" in data:
        name = (data.get("name") or "").strip()[:80]
        if not name:
            return jsonify({"error": "Theme name is required."}), 400
        record["name"] = name
    if "theme" in data:
        theme = data.get("theme")
        if not isinstance(theme, dict):
            return jsonify({"error": "theme must be an object."}), 400
        record["theme"] = theme
    record["updatedAt"] = time.time()
    theme_path.write_text(json.dumps(record), encoding="utf-8")
    return jsonify(record)


@app.route("/api/themes/<theme_id>", methods=["DELETE"])
def api_delete_theme(theme_id):
    if not THEME_ID_RE.match(theme_id):
        abort(400)
    theme_path = THEMES_DIR / f"{theme_id}.json"
    if not theme_path.exists():
        abort(404)
    theme_path.unlink()
    return jsonify({"ok": True})


# --------------------------------------------------------------------------
# Generate
# --------------------------------------------------------------------------

@app.route("/api/generate", methods=["POST"])
def api_generate():
    data = request.get_json(force=True, silent=True) or {}

    # A request can reuse a previously-created session's config (surah, ayah
    # range, reciter, translation, timing manifest, ...) by passing
    # `sessionId` and only overriding the fields it wants to change -- this
    # is how "make a new video from this (completed) session with a
    # different theme" works, without resending the whole timing manifest.
    #
    # Once a session is "complete" it's locked: only theme/themeName and
    # timing may be overridden by the request, everything else (surah, ayah
    # range, reciter, audio) is forced from the stored session even if the
    # request sent something else. timing is allowed through so a completed
    # video's word-by-word highlight timing can still be corrected -- e.g.
    # when it drifted out of sync with the audio (see
    # _clamp_frames_to_duration() in quran_lib/video_build.py) or an ayah
    # was left on automatic pacing and needs a manual fix -- without
    # reopening the surah/ayah-range/reciter/audio choices that already
    # produced a finished video.
    session_id = data.get("sessionId")
    session = None
    if session_id:
        session = _load_session(session_id)
        if session is None:
            return jsonify({"error": "That session could no longer be found."}), 404

    locked = session is not None and session.get("status") == "complete"

    def field(key, default=None):
        if locked:
            return session.get(key, default)
        if key in data and data[key] not in (None, ""):
            return data[key]
        if session is not None and key in session:
            return session[key]
        return default

    def override_field(key, default=None):
        # Always overridable, even on a locked/complete session (theme/themeName).
        if key in data and data[key] not in (None, ""):
            return data[key]
        if session is not None and key in session:
            return session[key]
        return default

    try:
        surah = int(field("surah"))
    except (TypeError, ValueError):
        return jsonify({"error": "Please choose a surah."}), 400
    if not (1 <= surah <= 114):
        return jsonify({"error": "Surah must be between 1 and 114."}), 400

    # field("reciter", default) alone isn't enough: a custom-audio session
    # always has an explicit "reciter": None key (it's never picked on that
    # flow), and field() returns a present-but-None session value as-is
    # rather than falling through to its default -- so fall back explicitly
    # here. quran_video.py barely uses --reciter once --custom-audio is
    # passed (only as an output-filename fallback), but api_generate()
    # still needs *some* valid value to pass along and validate against
    # RECITERS below.
    reciter = field("reciter") or "yasser_al_dossary"
    if reciter not in RECITERS:
        return jsonify({"error": "Unknown reciter."}), 400

    translation = (field("translation") or "en.sahih").strip()
    if not re.match(r"^[a-zA-Z0-9_.\-]+$", translation):
        return jsonify({"error": "That translation edition code doesn't look valid."}), 400

    ayah_start = field("ayahStart")
    ayah_end = field("ayahEnd")
    no_translation = bool(field("noTranslation"))
    no_split_basmala = bool(field("noSplitBasmala"))
    no_outro = bool(field("noOutro"))
    theme = override_field("theme")  # dict or None, as exported by the Ayah Frame Studio editor
    timing = override_field("timing")  # dict or None, a timing manifest (see quran_lib/timing.py) -- overridable even when locked, see above
    custom_audio = field("customAudio")  # dict or None, an audio-source manifest (see quran_lib/audio_sources.py)
    theme_name = override_field("themeName")  # display label only, for the library list

    # Orientation lives on the theme itself (a style is inherently vertical
    # or horizontal), not as its own session field -- the theme is
    # authoritative here rather than trusting whatever the client separately
    # sent, so it can't drift from the theme's own setting. No theme
    # selected (default look) falls back to vertical.
    orientation = theme.get("orientation") if isinstance(theme, dict) else None
    if orientation not in ("vertical", "horizontal"):
        orientation = "vertical"

    if session is None:
        # No pre-created session (e.g. a direct API call) -- create one now
        # so this generate still has somewhere to record its config.
        session = _new_session()
        session_id = session["id"]

    if not locked:
        session.update({
            "surah": surah,
            "ayahStart": ayah_start,
            "ayahEnd": ayah_end,
            "reciter": reciter,
            "translation": translation,
            "noTranslation": no_translation,
            "noSplitBasmala": no_split_basmala,
            "noOutro": no_outro,
            "theme": theme,
            "themeName": theme_name,
            "timing": timing,
            "customAudio": custom_audio,
        })
        session["updatedAt"] = time.time()
        _save_session(session)
    elif "timing" in data:
        # Locked session, but the request is a word-timing correction (see
        # the "locked" comment above) -- persist just that so the library's
        # stored config reflects the fix too, without touching anything
        # else the lock protects.
        session["timing"] = timing
        session["updatedAt"] = time.time()
        _save_session(session)

    job_id = uuid.uuid4().hex[:10]

    # Persist the session <-> job linkage BEFORE the render thread starts --
    # this is what lets generating.html/video_ready.html recover a job's
    # status from sessionId alone if the in-memory JOBS entry is ever gone
    # (server restart) or the jobId falls out of the URL (see api_status()'s
    # and api_download()'s fallback below).
    session["jobId"] = job_id
    session["jobStatus"] = "running"
    session["updatedAt"] = time.time()
    _save_session(session)

    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, "-u", str(HERE / "quran_video.py"),
        "--surah", str(surah),
        "--reciter", reciter,
        "--translation", translation,
        "--orientation", orientation,
    ]
    if ayah_start not in (None, ""):
        cmd += ["--ayah-start", str(int(ayah_start))]
    if ayah_end not in (None, ""):
        cmd += ["--ayah-end", str(int(ayah_end))]
    if no_translation:
        cmd += ["--no-translation"]
    if no_split_basmala:
        cmd += ["--no-split-basmala"]
    if no_outro:
        cmd += ["--no-outro"]

    if theme:
        theme_path = job_dir / "theme.json"
        theme_path.write_text(json.dumps(theme), encoding="utf-8")
        cmd += ["--theme", str(theme_path)]

    if timing:
        timing_path = job_dir / "timing.json"
        timing_path.write_text(json.dumps(timing), encoding="utf-8")
        cmd += ["--timing", str(timing_path)]
        print(f"[generate] sessionId={session_id}: using manual timing for {len(timing.get('ayahs', {}))} ayah(s)")
    else:
        print(f"[generate] sessionId={session_id}: no manual timing -- automatic pacing for every ayah")

    if custom_audio and isinstance(custom_audio, dict) and custom_audio.get("ayahs"):
        # Copy this session's referenced uploads into the job dir alongside
        # the manifest -- quran_video.py resolves filenames relative to the
        # manifest's own directory (see its --custom-audio help text), so
        # the job needs its own self-contained copy rather than reaching
        # back into uploads/<session_id>/ (which may later be cleaned up
        # independently of any given job). A referenced file that's gone
        # missing is silently skipped here; quran_video.py's own ffmpeg call
        # will fail loudly on the missing file if that happens, same as any
        # other bad manifest entry.
        custom_audio_dest_dir = job_dir / "custom_audio"
        custom_audio_dest_dir.mkdir(parents=True, exist_ok=True)
        src_upload_dir = _upload_dir_for(session_id) if session_id else None
        for entry in custom_audio["ayahs"].values():
            filename = isinstance(entry, dict) and entry.get("filename")
            if filename and src_upload_dir:
                src = src_upload_dir / filename
                if src.exists():
                    shutil.copy2(src, custom_audio_dest_dir / filename)
        custom_audio_path = custom_audio_dest_dir / "manifest.json"
        custom_audio_path.write_text(json.dumps(custom_audio), encoding="utf-8")
        cmd += ["--custom-audio", str(custom_audio_path)]

    range_bit = ""
    if ayah_start not in (None, "") and ayah_end not in (None, ""):
        range_bit = f"_{ayah_start}-{ayah_end}"
    out_name = f"surah_{surah}{range_bit}_{reciter}_{job_id}.mp4"
    out_path = OUTPUT_DIR / out_name
    cmd += ["--output", str(out_path)]

    with JOBS_LOCK:
        JOBS[job_id] = {
            "status": "running",
            "log": [],
            "percent": 1,
            "output_file": out_name,
            "error": None,
            "total_verses": None,
            "done_frames": 0,
            "started": time.time(),
            "proc": None,
            "cancel_requested": False,
            "meta": {
                "surah": surah,
                "ayahStart": int(ayah_start) if ayah_start not in (None, "") else None,
                "ayahEnd": int(ayah_end) if ayah_end not in (None, "") else None,
                "reciter": reciter,
                "themeName": theme_name,
                "sessionId": session_id,
            },
        }

    threading.Thread(target=_run_job, args=(job_id, cmd), daemon=True).start()
    return jsonify({"jobId": job_id})


def _run_job(job_id, cmd):
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(HERE),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        with JOBS_LOCK:
            JOBS[job_id]["proc"] = proc
        for raw_line in proc.stdout:
            line = raw_line.rstrip("\n").rstrip("\r")
            if not line:
                continue
            with JOBS_LOCK:
                job = JOBS[job_id]
                job["log"].append(line)
                m = TOTAL_VERSES_RE.search(line)
                if m:
                    job["total_verses"] = int(m.group(1))
                if FRAME_RE.search(line):
                    job["done_frames"] += 1
                if job["total_verses"]:
                    # rendering is most of the work; leave headroom for final encode
                    job["percent"] = min(92, int(job["done_frames"] / job["total_verses"] * 92))
        proc.wait()
        with JOBS_LOCK:
            job = JOBS[job_id]
            job["proc"] = None
            if job["cancel_requested"]:
                job["status"] = "cancelled"
                out_path = OUTPUT_DIR / job["output_file"]
                out_path.unlink(missing_ok=True)
                _finish_session_job(job["meta"].get("sessionId"), "cancelled")
            elif proc.returncode == 0:
                job["status"] = "done"
                job["percent"] = 100
                _write_video_sidecar(job)
                _mark_session_complete(job["meta"].get("sessionId"))
                _finish_session_job(job["meta"].get("sessionId"), "done", output_file=job["output_file"])
            else:
                job["status"] = "error"
                job["error"] = f"quran_video.py exited with code {proc.returncode} -- see log above."
                _finish_session_job(job["meta"].get("sessionId"), "error")
    except FileNotFoundError:
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["error"] = "Couldn't launch Python to run quran_video.py."
            _finish_session_job(JOBS[job_id]["meta"].get("sessionId"), "error")
    except Exception as e:  # noqa: BLE001
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["error"] = str(e)
            _finish_session_job(JOBS[job_id]["meta"].get("sessionId"), "error")


@app.route("/api/jobs/<job_id>/cancel", methods=["POST"])
def api_cancel_job(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"error": "Unknown job."}), 404
        if job["status"] != "running":
            return jsonify({"status": job["status"]})
        job["cancel_requested"] = True
        proc = job["proc"]
    if proc is not None:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
    return jsonify({"status": "cancelling"})


def _finish_session_job(session_id, status, output_file=None):
    """Persists a job's terminal outcome (done/error/cancelled) onto its
    session -- the durable counterpart to the in-memory JOBS entry (see the
    "jobId/jobStatus/outputFile" comment in _new_session()). Called from
    every terminal branch in _run_job() so api_status()/api_download() can
    still answer correctly from sessionId alone after JOBS has forgotten
    this job (server restart) or the jobId has fallen out of the URL."""
    if not session_id:
        return
    session = _load_session(session_id)
    if session is None:
        return
    session["jobStatus"] = status
    if output_file is not None:
        session["outputFile"] = output_file
    session["updatedAt"] = time.time()
    _save_session(session)


def _mark_session_complete(session_id):
    """A session becomes "complete" only once a generate for it actually
    succeeds -- a failed job leaves it "incomplete" so the user can go back
    and retry, instead of silently locking a broken config."""
    if not session_id:
        return
    session = _load_session(session_id)
    if session and session.get("status") != "complete":
        session["status"] = "complete"
        session["updatedAt"] = time.time()
        _save_session(session)


def _write_video_sidecar(job):
    """Write a small <output>.json next to a finished mp4 so the library
    (GET /api/videos) can list it without keeping JOBS around forever --
    JOBS is in-memory only and doesn't survive a server restart."""
    out_path = OUTPUT_DIR / job["output_file"]
    sidecar_path = out_path.with_suffix(".json")
    meta = dict(job["meta"])
    meta["createdAt"] = time.time()
    sidecar_path.write_text(json.dumps(meta), encoding="utf-8")


def _session_job_fallback(job_id):
    """Reconstructs a job's status from its session when JOBS no longer
    knows about it (see _finish_session_job()) -- api_status()/
    api_download() both fall back to this rather than a bare 404, as long
    as the caller passes back the sessionId it was originally given.
    Returns None if there's nothing to reconstruct from."""
    session_id = request.args.get("sessionId")
    if not session_id:
        return None
    session = _load_session(session_id)
    if not session or session.get("jobId") != job_id or not session.get("jobStatus"):
        return None
    return session


@app.route("/api/status/<job_id>")
def api_status(job_id):
    since = int(request.args.get("since", 0))
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job:
            resp = {
                "status": job["status"],
                "percent": job["percent"],
                "log": job["log"][since:],
                "logLength": len(job["log"]),
                "error": job["error"],
            }
            if job["status"] == "done":
                resp["downloadUrl"] = f"/api/download/{job_id}"
            return jsonify(resp)

    session = _session_job_fallback(job_id)
    if session is None:
        return jsonify({"error": "Unknown job."}), 404

    status = session["jobStatus"]
    if status == "running":
        # The process that would finish this job is gone (JOBS only forgets
        # a job on a server restart) -- there's nothing left to poll for, so
        # surface that as an error instead of leaving the caller polling a
        # job that will never report back.
        return jsonify({
            "status": "error", "percent": 0, "log": [], "logLength": 0,
            "error": "Generation was interrupted (the server restarted). Please try again.",
        })
    resp = {"status": status, "percent": 100 if status == "done" else 0, "log": [], "logLength": 0, "error": None}
    if status == "done":
        resp["downloadUrl"] = f"/api/download/{job_id}?sessionId={session['id']}"
    return jsonify(resp)


@app.route("/api/download/<job_id>")
def api_download(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        fname = job["output_file"] if job and job["status"] == "done" else None

    if fname is None:
        session = _session_job_fallback(job_id)
        if session and session["jobStatus"] == "done":
            fname = session.get("outputFile")

    if fname is None:
        abort(404)
    path = OUTPUT_DIR / fname
    if not path.exists():
        abort(404)
    return send_file(path, as_attachment=True, download_name=fname)


# --------------------------------------------------------------------------
# Video library (list/delete/play finished videos)
# --------------------------------------------------------------------------

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")


@app.route("/api/videos")
def api_videos():
    videos = []
    for sidecar_path in OUTPUT_DIR.glob("*.json"):
        video_path = sidecar_path.with_suffix(".mp4")
        if not video_path.exists():
            continue
        try:
            meta = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        try:
            duration = get_audio_duration(video_path)
        except Exception:  # noqa: BLE001
            duration = None
        videos.append({
            "id": video_path.stem,
            "file": video_path.name,
            "surah": meta.get("surah"),
            "ayahStart": meta.get("ayahStart"),
            "ayahEnd": meta.get("ayahEnd"),
            "reciter": meta.get("reciter"),
            "themeName": meta.get("themeName"),
            "duration": duration,
            "createdAt": meta.get("createdAt"),
            "sessionId": meta.get("sessionId"),
        })
    videos.sort(key=lambda v: v["createdAt"] or 0, reverse=True)
    return jsonify(videos)


@app.route("/api/videos/<video_id>", methods=["DELETE"])
def api_delete_video(video_id):
    if not VIDEO_ID_RE.match(video_id):
        abort(400)
    video_path = OUTPUT_DIR / f"{video_id}.mp4"
    sidecar_path = OUTPUT_DIR / f"{video_id}.json"
    if not sidecar_path.exists():
        abort(404)
    sidecar_path.unlink(missing_ok=True)
    video_path.unlink(missing_ok=True)
    return jsonify({"ok": True})


@app.route("/output/<path:filename>")
def api_output_file(filename):
    return send_from_directory(OUTPUT_DIR, filename)


# --------------------------------------------------------------------------
# Facebook posting (real Graph API -- see quran_lib/facebook.py and
# facebook-integration-progress.md for the fb_config.json setup steps)
# --------------------------------------------------------------------------

FB_REDIRECT_PATH = "/facebook/oauth/callback"


def _fb_redirect_uri():
    # Must exactly match a "Valid OAuth Redirect URI" registered on the
    # Facebook App -- localhost-only setup, so this is stable across runs
    # as long as the app is always opened via the same host (see progress
    # doc: use http://localhost:5050, not 127.0.0.1).
    return request.host_url.rstrip("/") + FB_REDIRECT_PATH


def _resolve_output_file(job_id, session_id):
    """Same fallback chain api_status()/api_download() use: live JOBS entry
    first, then the session's last-known output file."""
    with JOBS_LOCK:
        job = JOBS.get(job_id) if job_id else None
        if job and job["status"] == "done":
            return job["output_file"]
    if session_id:
        session = _load_session(session_id)
        if session and session.get("jobStatus") == "done":
            return session.get("outputFile")
    return None


@app.route("/facebook/oauth/start")
def facebook_oauth_start():
    if not fb.is_configured():
        return _facebook_error_redirect(request.args.get("returnTo"), "not_configured")
    return_to = request.args.get("returnTo") or "/facebook_account.html"
    return redirect(fb.oauth_dialog_url(_fb_redirect_uri(), state=return_to))


@app.route("/facebook/oauth/callback")
def facebook_oauth_callback():
    return_to = request.args.get("state") or "/facebook_account.html"
    if request.args.get("error"):
        return _facebook_error_redirect(return_to, "denied")
    code = request.args.get("code")
    if not code:
        return _facebook_error_redirect(return_to, "denied")
    try:
        fb.connect_account(code, _fb_redirect_uri())
    except fb.FacebookAPIError as e:
        return _facebook_error_redirect(return_to, str(e))
    return redirect(return_to)


def _facebook_error_redirect(return_to, message):
    return_to = return_to or "/facebook_account.html"
    sep = "&" if "?" in return_to else "?"
    return redirect(f"{return_to}{sep}fbError={requests.utils.quote(message)}")


@app.route("/api/facebook/status")
def api_facebook_status():
    account = fb.load_account()
    if not account:
        return jsonify({"configured": fb.is_configured(), "connected": False})
    return jsonify({
        "configured": True,
        "connected": True,
        "user": {"name": account.get("user_name")},
        "pages": [
            {"id": p["id"], "name": p["name"], "category": p.get("category"), "picture": p.get("picture")}
            for p in account.get("pages", [])
        ],
    })


@app.route("/api/facebook/disconnect", methods=["POST"])
def api_facebook_disconnect():
    fb.clear_account()
    return jsonify({"ok": True})


@app.route("/api/facebook/pages")
def api_facebook_pages():
    account = fb.load_account()
    if not account:
        return jsonify([])
    return jsonify([
        {"id": p["id"], "name": p["name"], "category": p.get("category"), "picture": p.get("picture")}
        for p in account.get("pages", [])
    ])


@app.route("/api/facebook/post", methods=["POST"])
def api_facebook_post():
    data = request.get_json(silent=True) or {}
    page_id = data.get("pageId")
    caption = data.get("caption") or ""
    if not page_id:
        return jsonify({"ok": False, "error": "No Page selected."}), 400

    page = fb.find_page(page_id)
    if not page:
        return jsonify({"ok": False, "error": "Connect your Facebook account and pick a Page first."}), 400

    video_id = data.get("videoId")
    if video_id:
        if not VIDEO_ID_RE.match(video_id):
            return jsonify({"ok": False, "error": "Invalid video id."}), 400
        output_file = f"{video_id}.mp4" if (OUTPUT_DIR / f"{video_id}.mp4").exists() else None
    else:
        output_file = _resolve_output_file(data.get("jobId"), data.get("sessionId"))
        video_id = Path(output_file).stem if output_file else None

    if not output_file:
        return jsonify({"ok": False, "error": "That video isn't ready yet."}), 400

    video_path = OUTPUT_DIR / output_file
    if not video_path.exists():
        return jsonify({"ok": False, "error": "That video file is missing."}), 404

    try:
        fb_video_id = fb.upload_video(page_id, page["access_token"], video_path, caption)
    except fb.FacebookAPIError as e:
        return jsonify({"ok": False, "error": str(e)}), 502

    try:
        permalink = fb.fetch_video_permalink(fb_video_id, page["access_token"])
    except fb.FacebookAPIError:
        permalink = None  # video may still be processing right after upload -- not fatal

    record = {
        "videoId": video_id,
        "pageId": page_id,
        "pageName": page["name"],
        "pagePicture": page.get("picture"),
        "fbVideoId": fb_video_id,
        "caption": caption,
        "permalink": permalink,
        "postedAt": time.time(),
    }
    fb.save_post_record(video_id, record)
    return jsonify({"ok": True, "postUrl": permalink, "videoId": video_id, "fbVideoId": fb_video_id})


@app.route("/api/facebook/posts")
def api_facebook_posts():
    return jsonify(fb.load_all_post_records())


@app.route("/api/facebook/posts/<video_id>")
def api_facebook_post_detail(video_id):
    record = fb.load_post_record(video_id)
    if not record:
        return jsonify({"error": "Not found."}), 404
    page = fb.find_page(record["pageId"])
    if page:
        if not record.get("permalink"):
            try:
                record["permalink"] = fb.fetch_video_permalink(record["fbVideoId"], page["access_token"])
                fb.save_post_record(video_id, record)
            except fb.FacebookAPIError:
                pass
        try:
            record = dict(record, stats=fb.fetch_video_stats(record["fbVideoId"], page["access_token"]))
        except fb.FacebookAPIError:
            record = dict(record, stats={"views": None, "likes": None})
    return jsonify(record)


@app.route("/api/facebook/posts/<video_id>", methods=["PUT"])
def api_facebook_post_update(video_id):
    record = fb.load_post_record(video_id)
    if not record:
        return jsonify({"error": "Not found."}), 404
    data = request.get_json(silent=True) or {}
    caption = data.get("caption", "")
    page = fb.find_page(record["pageId"])
    if not page:
        return jsonify({"error": "That Page is no longer connected."}), 400
    try:
        fb.edit_video_caption(record["fbVideoId"], page["access_token"], caption)
    except fb.FacebookAPIError as e:
        return jsonify({"error": str(e)}), 502
    record["caption"] = caption
    fb.save_post_record(video_id, record)
    return jsonify({"ok": True})


@app.route("/api/facebook/posts/<video_id>", methods=["DELETE"])
def api_facebook_post_delete(video_id):
    record = fb.load_post_record(video_id)
    if not record:
        return jsonify({"error": "Not found."}), 404
    page = fb.find_page(record["pageId"])
    if page:
        try:
            fb.delete_video(record["fbVideoId"], page["access_token"])
        except fb.FacebookAPIError as e:
            return jsonify({"error": str(e)}), 502
    fb.delete_post_record(video_id)
    return jsonify({"ok": True})


# --------------------------------------------------------------------------
# YouTube posting -- MOCKED for now (see quran_lib/youtube.py). No real
# Google/YouTube API calls happen here; "connect" and "post" just fabricate
# local state so the UI flow can be built and tested end-to-end.
# --------------------------------------------------------------------------

@app.route("/api/youtube/status")
def api_youtube_status():
    account = yt.load_account()
    if not account:
        return jsonify({"connected": False})
    return jsonify({"connected": True, "channel": account["channel"]})


@app.route("/api/youtube/connect", methods=["POST"])
def api_youtube_connect():
    account = yt.connect_account()
    return jsonify({"ok": True, "channel": account["channel"]})


@app.route("/api/youtube/disconnect", methods=["POST"])
def api_youtube_disconnect():
    yt.clear_account()
    return jsonify({"ok": True})


@app.route("/api/youtube/channels")
def api_youtube_channels():
    account = yt.load_account()
    if not account:
        return jsonify([])
    return jsonify([account["channel"]])


@app.route("/api/youtube/post", methods=["POST"])
def api_youtube_post():
    data = request.get_json(silent=True) or {}
    channel_id = data.get("channelId")
    caption = data.get("caption") or ""
    if not channel_id:
        return jsonify({"ok": False, "error": "No channel selected."}), 400

    channel = yt.find_channel(channel_id)
    if not channel:
        return jsonify({"ok": False, "error": "Connect your YouTube account and pick a channel first."}), 400

    video_id = data.get("videoId")
    if video_id:
        if not VIDEO_ID_RE.match(video_id):
            return jsonify({"ok": False, "error": "Invalid video id."}), 400
        output_file = f"{video_id}.mp4" if (OUTPUT_DIR / f"{video_id}.mp4").exists() else None
    else:
        output_file = _resolve_output_file(data.get("jobId"), data.get("sessionId"))
        video_id = Path(output_file).stem if output_file else None

    if not output_file:
        return jsonify({"ok": False, "error": "That video isn't ready yet."}), 400

    yt_video_id = yt.mock_upload_video(channel_id, caption)

    record = {
        "videoId": video_id,
        "channelId": channel_id,
        "channelName": channel["name"],
        "channelPicture": channel.get("picture"),
        "ytVideoId": yt_video_id,
        "caption": caption,
        "postedAt": time.time(),
        "stats": yt.mock_stats(),
    }
    yt.save_post_record(video_id, record)
    return jsonify({"ok": True, "videoId": video_id, "ytVideoId": yt_video_id})


@app.route("/api/youtube/posts")
def api_youtube_posts():
    return jsonify(yt.load_all_post_records())


@app.route("/api/youtube/posts/<video_id>")
def api_youtube_post_detail(video_id):
    record = yt.load_post_record(video_id)
    if not record:
        return jsonify({"error": "Not found."}), 404
    return jsonify(record)


@app.route("/api/youtube/posts/<video_id>", methods=["PUT"])
def api_youtube_post_update(video_id):
    record = yt.load_post_record(video_id)
    if not record:
        return jsonify({"error": "Not found."}), 404
    data = request.get_json(silent=True) or {}
    record["caption"] = data.get("caption", "")
    yt.save_post_record(video_id, record)
    return jsonify({"ok": True})


@app.route("/api/youtube/posts/<video_id>", methods=["DELETE"])
def api_youtube_post_delete(video_id):
    record = yt.load_post_record(video_id)
    if not record:
        return jsonify({"error": "Not found."}), 404
    yt.delete_post_record(video_id)
    return jsonify({"ok": True})


def _lan_ip():
    """Best-effort guess at this machine's LAN IP, for the startup message."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return None
    finally:
        s.close()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5050"))
    # 0.0.0.0 = listen on every network interface, so other devices on your
    # Wi-Fi (like your phone) can reach it too. Set HOST=127.0.0.1 in the
    # environment if you'd rather restrict it to this machine only.
    host = os.environ.get("HOST", "0.0.0.0")

    # DEV=1 turns on Werkzeug's file-watcher/reloader, so editing app.py or
    # quran_lib/*.py auto-restarts the process -- no more manually killing
    # and relaunching after every change. This is *not* the same as Flask's
    # debug=True: that also turns on the interactive in-browser debugger,
    # which lets anyone who can reach this server (including other devices
    # on the same Wi-Fi, since host defaults to 0.0.0.0) run arbitrary code.
    # We only want the reload behavior, so debug stays False either way.
    dev_mode = os.environ.get("DEV") == "1"

    print(f"\nAyah Frame Studio is running:")
    print(f"  On this computer -> http://127.0.0.1:{port}")
    if host == "0.0.0.0":
        lan_ip = _lan_ip()
        if lan_ip:
            print(f"  On your phone/other devices (same Wi-Fi) -> http://{lan_ip}:{port}")
        else:
            print("  On your phone/other devices (same Wi-Fi) -> http://<this-computer's-LAN-IP>:%d" % port)
    if dev_mode:
        print("  Auto-reload is ON (DEV=1) -- code changes restart the server automatically.")
    print()

    app.run(host=host, port=port, debug=False, use_reloader=dev_mode, threaded=True)