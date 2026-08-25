"""Facebook Graph API client -- OAuth token exchange, Page listing, video
posting/editing/deleting. All local JSON-file storage here, matching the
rest of this app's convention (sessions/, themes/) rather than a database.

Nothing in this module talks to Facebook unless the caller has already put
an app_id/app_secret in fb_config.json (see facebook-integration-progress.md
for the developers.facebook.com setup steps).
"""

import json
import time
from pathlib import Path

import requests

GRAPH_VERSION = "v21.0"
GRAPH_URL = f"https://graph.facebook.com/{GRAPH_VERSION}"
GRAPH_VIDEO_URL = f"https://graph-video.facebook.com/{GRAPH_VERSION}"
OAUTH_DIALOG_URL = f"https://www.facebook.com/{GRAPH_VERSION}/dialog/oauth"

HERE = Path(__file__).resolve().parent.parent
FB_CONFIG_PATH = HERE / "fb_config.json"
FB_ACCOUNT_PATH = HERE / "fb_account.json"
FB_POSTS_DIR = HERE / "facebook_posts"
FB_POSTS_DIR.mkdir(exist_ok=True)

REQUIRED_SCOPES = "pages_show_list,pages_manage_posts,pages_read_engagement,public_profile"


class FacebookAPIError(Exception):
    """Wraps a Graph API error response so callers can show a real message
    instead of a generic 'request failed'."""


def _graph_error_message(resp):
    try:
        data = resp.json()
        return data.get("error", {}).get("message") or resp.text
    except ValueError:
        return resp.text or f"HTTP {resp.status_code}"


def _check(resp):
    if not resp.ok:
        raise FacebookAPIError(_graph_error_message(resp))
    return resp


# --------------------------------------------------------------------------
# fb_config.json -- app_id/app_secret, filled in by hand by the user
# --------------------------------------------------------------------------

def load_config():
    if not FB_CONFIG_PATH.exists():
        return None
    try:
        data = json.loads(FB_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not data.get("app_id") or not data.get("app_secret"):
        return None
    return data


def is_configured():
    return load_config() is not None


# --------------------------------------------------------------------------
# fb_account.json -- the connected user's long-lived token + their Pages
# --------------------------------------------------------------------------

def load_account():
    if not FB_ACCOUNT_PATH.exists():
        return None
    try:
        return json.loads(FB_ACCOUNT_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_account(account):
    FB_ACCOUNT_PATH.write_text(json.dumps(account), encoding="utf-8")


def clear_account():
    FB_ACCOUNT_PATH.unlink(missing_ok=True)


def find_page(page_id):
    account = load_account()
    if not account:
        return None
    for page in account.get("pages", []):
        if page["id"] == page_id:
            return page
    return None


# --------------------------------------------------------------------------
# OAuth
# --------------------------------------------------------------------------

def oauth_dialog_url(redirect_uri, state):
    config = load_config()
    params = {
        "client_id": config["app_id"],
        "redirect_uri": redirect_uri,
        "state": state,
        "scope": REQUIRED_SCOPES,
        "response_type": "code",
    }
    query = "&".join(f"{k}={requests.utils.quote(str(v), safe='')}" for k, v in params.items())
    return f"{OAUTH_DIALOG_URL}?{query}"


def exchange_code_for_token(code, redirect_uri):
    config = load_config()
    resp = _check(requests.get(f"{GRAPH_URL}/oauth/access_token", params={
        "client_id": config["app_id"],
        "client_secret": config["app_secret"],
        "redirect_uri": redirect_uri,
        "code": code,
    }, timeout=20))
    return resp.json()["access_token"]


def exchange_for_long_lived_token(short_lived_token):
    config = load_config()
    resp = _check(requests.get(f"{GRAPH_URL}/oauth/access_token", params={
        "grant_type": "fb_exchange_token",
        "client_id": config["app_id"],
        "client_secret": config["app_secret"],
        "fb_exchange_token": short_lived_token,
    }, timeout=20))
    return resp.json()["access_token"]


def fetch_user_profile(user_token):
    resp = _check(requests.get(f"{GRAPH_URL}/me", params={
        "fields": "id,name",
        "access_token": user_token,
    }, timeout=20))
    return resp.json()


def fetch_managed_pages(user_token):
    """Page access tokens returned here (from a long-lived *user* token) are
    themselves long-lived / non-expiring -- no separate Page token refresh
    needed."""
    pages = []
    url = f"{GRAPH_URL}/me/accounts"
    params = {
        "fields": "id,name,category,access_token,picture{url}",
        "access_token": user_token,
        "limit": 100,
    }
    while url:
        resp = _check(requests.get(url, params=params, timeout=20))
        data = resp.json()
        for p in data.get("data", []):
            pages.append({
                "id": p["id"],
                "name": p["name"],
                "category": p.get("category"),
                "access_token": p["access_token"],
                "picture": (p.get("picture") or {}).get("data", {}).get("url"),
            })
        url = (data.get("paging") or {}).get("next")
        params = None  # the "next" url already carries everything needed
    return pages


def connect_account(code, redirect_uri):
    """Runs the full OAuth callback exchange and persists the result to
    fb_account.json. Returns the saved account dict."""
    short_token = exchange_code_for_token(code, redirect_uri)
    long_token = exchange_for_long_lived_token(short_token)
    profile = fetch_user_profile(long_token)
    pages = fetch_managed_pages(long_token)
    account = {
        "user_id": profile.get("id"),
        "user_name": profile.get("name"),
        "user_token": long_token,
        "pages": pages,
        "connected_at": time.time(),
    }
    save_account(account)
    return account


# --------------------------------------------------------------------------
# Posting / managing videos on a Page
# --------------------------------------------------------------------------

def upload_video(page_id, page_token, video_path, caption):
    with open(video_path, "rb") as f:
        resp = _check(requests.post(
            f"{GRAPH_VIDEO_URL}/{page_id}/videos",
            data={"access_token": page_token, "description": caption or ""},
            files={"source": (video_path.name, f, "video/mp4")},
            timeout=600,
        ))
    return resp.json()["id"]


def fetch_video_permalink(video_id, page_token):
    resp = _check(requests.get(f"{GRAPH_URL}/{video_id}", params={
        "fields": "permalink_url",
        "access_token": page_token,
    }, timeout=20))
    permalink = resp.json().get("permalink_url")
    if permalink and permalink.startswith("/"):
        permalink = f"https://www.facebook.com{permalink}"
    return permalink


def fetch_video_stats(video_id, page_token):
    """Views need read_insights (may not be granted without App Review);
    likes/comments work with the base permissions this app requests.
    Returns whatever it could get -- callers should treat missing fields as
    'not available' rather than falling back to fake numbers."""
    stats = {"views": None, "likes": None}
    try:
        resp = requests.get(f"{GRAPH_URL}/{video_id}", params={
            "fields": "likes.summary(true),views",
            "access_token": page_token,
        }, timeout=20)
        if resp.ok:
            data = resp.json()
            stats["likes"] = (data.get("likes") or {}).get("summary", {}).get("total_count")
            stats["views"] = data.get("views")
    except requests.RequestException:
        pass
    return stats


def edit_video_caption(video_id, page_token, caption):
    _check(requests.post(f"{GRAPH_URL}/{video_id}", data={
        "description": caption or "",
        "access_token": page_token,
    }, timeout=20))


def delete_video(video_id, page_token):
    _check(requests.delete(f"{GRAPH_URL}/{video_id}", params={
        "access_token": page_token,
    }, timeout=20))


# --------------------------------------------------------------------------
# facebook_posts/<video_id>.json -- our local record of what we posted where
# --------------------------------------------------------------------------

def _post_record_path(video_id):
    return FB_POSTS_DIR / f"{video_id}.json"


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
    for path in FB_POSTS_DIR.glob("*.json"):
        try:
            records[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return records
