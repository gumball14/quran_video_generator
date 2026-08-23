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

from flask import Flask, request, jsonify, send_from_directory, send_file, abort

HERE = Path(__file__).parent.resolve()
STATIC_DIR = HERE / "static"
OUTPUT_DIR = HERE / "output"          # matches quran_video.py's own default
JOBS_DIR = HERE / "jobs"              # per-job temp files (e.g. uploaded theme.json)
THEMES_DIR = HERE / "themes"          # user-saved custom themes, one JSON file per theme
SESSIONS_DIR = HERE / "sessions"      # one JSON per video-creation session: full generation config + incomplete/complete status
UPLOADS_DIR = HERE / "uploads"        # user-uploaded ayah audio (Phase 3), one subdir per session: uploads/<session_id>/<ayah>.<ext>

OUTPUT_DIR.mkdir(exist_ok=True)
JOBS_DIR.mkdir(exist_ok=True)
THEMES_DIR.mkdir(exist_ok=True)
SESSIONS_DIR.mkdir(exist_ok=True)
UPLOADS_DIR.mkdir(exist_ok=True)


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


from quran_lib.quran_api import fetch_verses, Verse
from quran_lib.audio import download_ayah_audio, detect_basmala_split, get_audio_duration, get_custom_ayah_audio
from quran_lib.constants import FONT_DIR
from quran_lib.theme import load_theme
from quran_lib.text_render import build_ayah_layout, draw_dynamic_layer
from quran_lib import theme as theme_mod

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
FRAME_RE = re.compile(r"rendering frame")
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")

SESSION_FIELDS = (
    "surah", "ayahStart", "ayahEnd", "reciter", "orientation", "translation",
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


def _new_session():
    now = time.time()
    session = {"id": uuid.uuid4().hex[:10], "status": "incomplete", "createdAt": now, "updatedAt": now}
    for key in SESSION_FIELDS:
        session[key] = None
    session["orientation"] = "vertical"
    session["translation"] = "en.sahih"
    for key in ("noTranslation", "noSplitBasmala", "noOutro"):
        session[key] = False
    return session


# --------------------------------------------------------------------------
# Sessions (a video-creation project: created when the user starts a new
# video, edited while "incomplete", locked to only theme/style changes once
# "complete" -- see sessions-refactor-progress.md)
# --------------------------------------------------------------------------

@app.route("/api/sessions", methods=["POST"])
def api_create_session():
    session = _new_session()
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
        return jsonify({"error": "This session is already complete and can't be edited."}), 409
    data = request.get_json(force=True, silent=True) or {}
    for key in SESSION_FIELDS:
        if key in data:
            session[key] = data[key]
    session["updatedAt"] = time.time()
    _save_session(session)
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
    # Once a session is "complete" it's locked: only theme/themeName may be
    # overridden by the request, everything else is forced from the stored
    # session even if the request sent something else.
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

    reciter = field("reciter", "yasser_al_dossary")
    if reciter not in RECITERS:
        return jsonify({"error": "Unknown reciter."}), 400

    orientation = field("orientation", "vertical")
    if orientation not in ("vertical", "horizontal"):
        return jsonify({"error": "Orientation must be vertical or horizontal."}), 400

    translation = (field("translation") or "en.sahih").strip()
    if not re.match(r"^[a-zA-Z0-9_.\-]+$", translation):
        return jsonify({"error": "That translation edition code doesn't look valid."}), 400

    ayah_start = field("ayahStart")
    ayah_end = field("ayahEnd")
    no_translation = bool(field("noTranslation"))
    no_split_basmala = bool(field("noSplitBasmala"))
    no_outro = bool(field("noOutro"))
    theme = override_field("theme")  # dict or None, as exported by the Ayah Frame Studio editor
    timing = field("timing")  # dict or None, a timing manifest (see quran_lib/timing.py)
    custom_audio = field("customAudio")  # dict or None, an audio-source manifest (see quran_lib/audio_sources.py)
    theme_name = override_field("themeName")  # display label only, for the library list

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
            "orientation": orientation,
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

    job_id = uuid.uuid4().hex[:10]
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
            if proc.returncode == 0:
                job["status"] = "done"
                job["percent"] = 100
                _write_video_sidecar(job)
                _mark_session_complete(job["meta"].get("sessionId"))
            else:
                job["status"] = "error"
                job["error"] = f"quran_video.py exited with code {proc.returncode} -- see log above."
    except FileNotFoundError:
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["error"] = "Couldn't launch Python to run quran_video.py."
    except Exception as e:  # noqa: BLE001
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["error"] = str(e)


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


@app.route("/api/status/<job_id>")
def api_status(job_id):
    since = int(request.args.get("since", 0))
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"error": "Unknown job."}), 404
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


@app.route("/api/download/<job_id>")
def api_download(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job or job["status"] != "done":
            abort(404)
        fname = job["output_file"]
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