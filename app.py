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

OUTPUT_DIR.mkdir(exist_ok=True)
JOBS_DIR.mkdir(exist_ok=True)
THEMES_DIR.mkdir(exist_ok=True)

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")

from quran_lib.quran_api import fetch_verses, Verse
from quran_lib.audio import download_ayah_audio, split_basmala_audio
from quran_lib.constants import CACHE_DIR
from quran_lib.theme import load_theme
from quran_lib.text_render import render_verse_frame

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


# --------------------------------------------------------------------------
# Static pages
# --------------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


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


@app.route("/api/timing/audio")
def api_timing_audio():
    """Serve the ayah's recitation audio for the waveform editor. `part`
    controls whether you get the raw undivided file, or -- for an ayah 1
    that has the Bismillah prepended -- one half of the *exact same*
    ffmpeg split the real generator uses (so timings set against this
    audio line up perfectly with what build_video() will actually use)."""
    try:
        surah = int(request.args.get("surah"))
        ayah = int(request.args.get("ayah"))
    except (TypeError, ValueError):
        return jsonify({"error": "surah and ayah must be numbers."}), 400

    reciter = request.args.get("reciter", "yasser_al_dossary")
    if reciter not in RECITERS:
        return jsonify({"error": "Unknown reciter."}), 400

    part = request.args.get("part", "full")
    if part not in ("full", "basmala", "ayah"):
        return jsonify({"error": "part must be full, basmala, or ayah."}), 400

    try:
        audio_path = download_ayah_audio(surah=surah, ayah=ayah, reciter_key=reciter)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"Couldn't download that ayah's audio: {e}"}), 502

    if part == "full":
        return send_file(audio_path, mimetype="audio/mpeg")

    basmala_path, ayah_path = split_basmala_audio(audio_path, CACHE_DIR / "split")
    if not basmala_path or not ayah_path:
        return jsonify({
            "error": "Couldn't confidently detect the Bismillah/ayah boundary in this "
                     "audio -- the real generator would fall back to showing them as one "
                     "combined frame too. Use part=full and time it as a single frame."
        }), 409
    return send_file(basmala_path if part == "basmala" else ayah_path, mimetype="audio/mpeg")


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
    pointer_pos = data.get("pointer_pos")
    if pointer_pos is not None:
        try:
            pointer_pos = (float(pointer_pos[0]), float(pointer_pos[1]))
        except (TypeError, ValueError, IndexError):
            return jsonify({"error": "pointer_pos must be [x, y]."}), 400

    verse = Verse(
        number=int(verse_data.get("number") or 1),
        arabic=arabic,
        translation=translation,
        basmala_arabic=verse_data.get("basmala_arabic"),
    )
    surah_name_arabic = verse_data.get("surah_name_arabic") or ""

    job_id = uuid.uuid4().hex[:10]
    theme_path = JOBS_DIR / f"preview_{job_id}.json"
    theme_path.write_text(json.dumps(theme), encoding="utf-8")
    try:
        with THEME_LOCK:
            load_theme(theme_path)
            image, _word_boxes = render_verse_frame(
                verse, surah_name_arabic, size, show_translation,
                highlight_index, pointer_pos,
            )
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

    try:
        surah = int(data.get("surah"))
    except (TypeError, ValueError):
        return jsonify({"error": "Please choose a surah."}), 400
    if not (1 <= surah <= 114):
        return jsonify({"error": "Surah must be between 1 and 114."}), 400

    reciter = data.get("reciter", "yasser_al_dossary")
    if reciter not in RECITERS:
        return jsonify({"error": "Unknown reciter."}), 400

    orientation = data.get("orientation", "vertical")
    if orientation not in ("vertical", "horizontal"):
        return jsonify({"error": "Orientation must be vertical or horizontal."}), 400

    translation = (data.get("translation") or "en.sahih").strip()
    if not re.match(r"^[a-zA-Z0-9_.\-]+$", translation):
        return jsonify({"error": "That translation edition code doesn't look valid."}), 400

    ayah_start = data.get("ayahStart")
    ayah_end = data.get("ayahEnd")
    no_translation = bool(data.get("noTranslation"))
    no_split_basmala = bool(data.get("noSplitBasmala"))
    theme = data.get("theme")  # dict or None, as exported by the Ayah Frame Studio editor
    timing = data.get("timing")  # dict or None, a timing manifest (see quran_lib/timing.py)
    theme_name = data.get("themeName")  # display label only, for the library list

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

    if theme:
        theme_path = job_dir / "theme.json"
        theme_path.write_text(json.dumps(theme), encoding="utf-8")
        cmd += ["--theme", str(theme_path)]

    if timing:
        timing_path = job_dir / "timing.json"
        timing_path.write_text(json.dumps(timing), encoding="utf-8")
        cmd += ["--timing", str(timing_path)]

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

from quran_lib.audio import get_audio_duration

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

    print(f"\nAyah Frame Studio is running:")
    print(f"  On this computer -> http://127.0.0.1:{port}")
    if host == "0.0.0.0":
        lan_ip = _lan_ip()
        if lan_ip:
            print(f"  On your phone/other devices (same Wi-Fi) -> http://{lan_ip}:{port}")
        else:
            print("  On your phone/other devices (same Wi-Fi) -> http://<this-computer's-LAN-IP>:%d" % port)
    print()

    app.run(host=host, port=port, debug=False, threaded=True)
