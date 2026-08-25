"""Mock YouTube "posting" backend -- a stand-in for the real YouTube Data API
so the UI flow (connect -> pick channel -> post -> library) can be built and
tested end-to-end before any real Google/YouTube integration exists.

Nothing here talks to Google. "Connecting" just fabricates a channel and
"posting" just fabricates a video id/stats and stores a local record, using
the same JSON-file-per-record convention as quran_lib/facebook.py.
"""

import json
import random
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
YT_ACCOUNT_PATH = HERE / "yt_account.json"
YT_POSTS_DIR = HERE / "youtube_posts"
YT_POSTS_DIR.mkdir(exist_ok=True)

MOCK_CHANNEL = {
    "id": "mock-channel-1",
    "name": "Ayah Frame Studio (Test)",
    "handle": "@ayahframestudio",
    "picture": None,
}


# --------------------------------------------------------------------------
# yt_account.json -- fake "connected" state, no real OAuth involved
# --------------------------------------------------------------------------

def load_account():
    if not YT_ACCOUNT_PATH.exists():
        return None
    try:
        return json.loads(YT_ACCOUNT_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def connect_account():
    """Fabricates a connected account with one mock channel -- stands in for
    the OAuth handshake a real integration would need."""
    account = {
        "channel": MOCK_CHANNEL,
        "connected_at": time.time(),
    }
    YT_ACCOUNT_PATH.write_text(json.dumps(account), encoding="utf-8")
    return account


def clear_account():
    YT_ACCOUNT_PATH.unlink(missing_ok=True)


def find_channel(channel_id):
    account = load_account()
    if not account:
        return None
    channel = account.get("channel")
    if channel and channel["id"] == channel_id:
        return channel
    return None


# --------------------------------------------------------------------------
# Posting (mocked -- no upload, no real video id)
# --------------------------------------------------------------------------

def mock_upload_video(channel_id, caption):
    """Stands in for the real upload call: no file is sent anywhere, just a
    fake video id so the rest of the flow (record, detail screen) works."""
    return f"mock-{uuid.uuid4().hex[:11]}"


def mock_stats():
    views = random.randint(50, 5000)
    likes = round(views * random.uniform(0.03, 0.12))
    comments = round(likes * random.uniform(0.05, 0.2))
    return {"views": views, "likes": likes, "comments": comments}


# --------------------------------------------------------------------------
# youtube_posts/<video_id>.json -- our local record of what we "posted" where
# --------------------------------------------------------------------------

def _post_record_path(video_id):
    return YT_POSTS_DIR / f"{video_id}.json"


def load_post_record(video_id):
    path = _post_record_path(video_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_post_record(video_id, record):
    _post_record_path(video_id).write_text(json.dumps(record), encoding="utf-8")


def delete_post_record(video_id):
    _post_record_path(video_id).unlink(missing_ok=True)


def load_all_post_records():
    records = {}
    for path in YT_POSTS_DIR.glob("*.json"):
        try:
            records[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return records
