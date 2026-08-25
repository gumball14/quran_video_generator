# Real Facebook posting — implementation progress

**Status: all 8 phases complete. No live browser click-through yet (needs
the user's own Facebook App credentials + login) -- see "Setup steps"
below.**

## Goal (from user request)

Turn the 5 Facebook screens from mocked/localStorage-backed previews into a
real, working integration: OAuth-connect a Facebook account, list the
Pages the user manages, post a generated video to a chosen Page, and show/
edit/delete real posts — instead of `MOCK_PAGES`, `localStorage` flags, and
hash-based fake stats.

Decisions locked in with the user before starting:
- No existing Facebook Developer App — user will create one; I provide
  exact setup steps (App ID/Secret, OAuth redirect URI, permissions).
- Redirect URI: `http://localhost:5050/facebook/oauth/callback` (dev/local
  only — fine since only the user, as the app's Admin/Developer/Tester,
  will ever connect an account; no App Review needed for that).
- Credentials stored in a local gitignored JSON file (`fb_config.json`),
  matching this repo's existing JSON-file-storage convention (`sessions/`,
  `themes/`), not env vars.

## Plan

- [x] Phase 0 — This progress doc.
- [x] Phase 1 — Backend: `quran_lib/facebook.py` (Graph API client: OAuth
      token exchange, long-lived token exchange, list Pages, upload video,
      edit caption, delete post, fetch permalink) + `fb_config.json` /
      `fb_account.json` storage helpers.
- [x] Phase 2 — Backend: Flask routes in `app.py`
      (`/facebook/oauth/start`, `/facebook/oauth/callback`,
      `/api/facebook/status`, `/api/facebook/disconnect`,
      `/api/facebook/pages`, `/api/facebook/post`, `/api/facebook/posts`,
      `/api/facebook/posts/<video_id>` GET/PUT/DELETE). Syntax-checked
      (`py_compile`); live smoke test deferred to Phase 8.
- [x] Phase 3 — `static/facebook_account.html`: real connect/disconnect,
      real Pages list (with real profile pictures where Facebook returns
      one, deterministic color+initials fallback otherwise), error banner
      for `?fbError=` from the OAuth callback.
- [x] Phase 4 — `static/post_facebook.html`: real Pages picker
      (`/api/facebook/status`), real connected-state check, handles
      zero-Pages case.
- [x] Phase 5 — `static/post_facebook_progress.html`: its existing
      `POST /api/facebook/post` call already matched the real
      `{sessionId, jobId, pageId, caption}` -> `{ok, postUrl}` shape
      unchanged; swapped `MOCK_PAGES` for a real `/api/facebook/pages`
      lookup for the status header's name/picture.
- [x] Phase 6 — `static/facebook_reels.html` + `static/facebook_reel_detail.html`:
      real posted-videos list (`/api/facebook/posts`) replacing hash-based
      mock stats/pages, real delete (Graph API) and caption-edit (Graph
      API), real "Open on Facebook" link. Also extended
      `post_facebook.html`/`post_facebook_progress.html` with a `videoId`
      (+`from`) param so an unposted library video (reached from the reel
      detail screen's new "not posted yet" state, or the uploads grid) can
      be posted without needing a generation job/session. Dropped the
      `localStorage` deleted/caption-override hacks entirely (delete/edit
      are real now). Verified with `node --check` on each screen's
      extracted `<script>` body — all pass.
- [x] Phase 7 — `.gitignore`: added `fb_config.json`, `fb_account.json`,
      `facebook_posts/`.
- [x] Phase 8 — Verification: `py_compile` on `app.py`/`quran_lib/facebook.py`;
      `node --check` on every touched screen's extracted `<script>` body;
      scripted Flask `test_client` smoke test of every `/api/facebook/*` +
      `/facebook/oauth/*` route in both the unconfigured and
      configured-but-not-connected states (all passed, see log below).
      Manual browser click-through of a real connect -> post -> delete
      cycle is still on the user, once `fb_config.json` is filled in (needs
      a live Facebook login) -- not something this session can do headless.

## Setup steps for the user (do this before "Connect Facebook" works)

1. Go to https://developers.facebook.com/apps -> **Create App** -> choose
   **"Other"** -> **"Business"** as the app type. Name it anything (e.g.
   "Ayah Frame Studio").
2. In the app dashboard, add the **Facebook Login for Business** product
   (or plain "Facebook Login" if that's what's offered).
3. Facebook Login settings -> **Valid OAuth Redirect URIs**, add exactly:
   `http://localhost:5050/facebook/oauth/callback`
   (must match exactly -- always open the app via `http://localhost:5050`,
   not `http://127.0.0.1:5050`, or the redirect will be rejected.)
4. App Settings -> Basic: copy the **App ID** and **App Secret**.
5. Create `fb_config.json` in this repo's root (same folder as `app.py`) --
   there's an `fb_config.example.json` here to copy:
   ```json
   { "app_id": "<your App ID>", "app_secret": "<your App Secret>" }
   ```
   (already gitignored, never gets committed.)
6. The app starts in **Development Mode** -- that's fine and expected.
   In dev mode, only people with an Admin/Developer/Tester role on the app
   (Roles tab) can actually connect an account and post. Make sure the
   Facebook account you're going to connect with has one of those roles
   (the app's creator gets Admin automatically) and manages at least one
   real Facebook Page.
7. Restart the server (`python app.py`) so it picks up `fb_config.json`,
   open `http://localhost:5050`, go to Facebook Reels -> the account icon,
   and hit "Continue with Facebook".

No App Review is needed for this personal/local-use setup -- App Review is
only required once *other people* (not app Admins/Developers/Testers) need
to connect their own accounts.

## Notes / decisions log

- `quran_lib/facebook.py` centralizes all Graph API calls (`GRAPH_VERSION
  = v21.0`) and local JSON storage (`fb_config.json` for app credentials,
  `fb_account.json` for the connected user's long-lived token + their
  Pages, `facebook_posts/<video_id>.json` for our record of what got
  posted where) -- mirrors the existing `sessions/`/`themes/` convention
  rather than introducing a database.
- Page access tokens obtained via `GET /me/accounts` using a *long-lived*
  user token are themselves long-lived (effectively non-expiring unless
  the user changes their Facebook password or revokes the app) -- no
  separate Page-token refresh flow was needed.
- Video "views" require the `read_insights` permission, which may not be
  grantable without App Review even for an Admin/Tester in some cases;
  `fetch_video_stats()` treats a failed/partial stats fetch as "not
  available" (renders as omitted, not a fake number) rather than raising.
- Posting uses the plain `POST /{page-id}/videos` endpoint, not the
  Reels-specific resumable-upload API (`/video_reels`) -- much simpler
  (single multipart request, works with permissions already granted to
  Admin/Developer/Testers without App Review) and Facebook will still
  surface a vertical video in the Reels tab based on its aspect ratio.
- `app.py`'s new `/api/facebook/post` accepts `videoId` OR
  `jobId`/`sessionId` (reusing `api_download()`'s existing fallback
  chain) -- this let `post_facebook.html`/`post_facebook_progress.html`
  gain a `videoId`(+`from`) param for posting an already-generated library
  video directly, which `facebook_reel_detail.html`'s new "not posted yet"
  state (for a video reached via post_facebook_progress.html's uploads
  grid that has no Facebook post record) needed to link to.
- Removed all of the old mock/localStorage machinery (`afs_fb_connected`,
  `afs_fb_deleted_posts`, `afs_fb_captions`, `MOCK_PAGES`, the
  `hashString`-based fake stats/post-dates) now that there's a real
  backend to be the source of truth -- `hashString`/`colorFor` survive
  only as a deterministic *display* color for a Page's avatar when
  Facebook doesn't return a profile picture, not as fake data.
