# Sessions separate from videos — implementation progress

**Status: Phases 1-5 done.**

## Goal (from user request)

- A "session" is created when the user starts a new video (not only once a
  video finishes generating, like today's `projects/<id>.json`).
- Sessions have a status: `incomplete` or `complete`.
  - `incomplete`: still being drafted in `new_video.html`/`timing.html`. Can't
    directly produce a video — must be opened in the edit flow and carried
    through to a successful first generate, which is what flips it to
    `complete`.
  - `complete`: locked — surah/range/reciter/translation/timing can no longer
    be edited — but can be used to generate more videos with a different
    theme/style (this is today's "regenerate with a different theme", moved
    from being a per-video action to a per-session action).
- Home screen (`index.html`) shows a **Sessions** list at the top (both
  incomplete — "continue editing" — and complete — "make a new video from
  this") and a **Videos** list below (static outputs: play/download/delete
  only, no regenerate button anymore — that only happens from a completed
  session).

This builds on the existing `projects/<id>.json` mechanism from
`session-and-projects-progress.md` (full generation config persisted so a
video can be regenerated with a different theme) but changes *when* that
config is created (now: session start, not first successful generate) and
adds the incomplete/complete lifecycle + status enforcement.

Note on naming collision: `timing.html` already has an unrelated
client-side-only "session" concept (`SESSION_KEY = 'afs_timing_session'`,
`persistSession()`/`loadSavedSession()`) for resuming mid-ayah marker
progress after closing the browser. That stays as-is; it's a different,
narrower thing (per-ayah marker state) than the server-side "session" this
doc is about (a whole video-creation project's lifecycle). Careful not to
conflate the two when reading/editing `timing.html`.

## Plan

- [x] Phase 1 — Backend: session storage + API
  - Rename the `projects/` concept to `sessions/` (`SESSIONS_DIR`), migrating
    any existing `projects/*.json` forward (defaulting `status: "complete"`
    since under the old code a project file only ever got created after a
    successful generate).
  - `POST /api/sessions` — create a blank incomplete session, return it.
  - `GET /api/sessions` — list all sessions (summary, no `timing` manifest),
    newest-updated first.
  - `GET /api/sessions/<id>` — full session config (for resuming edit).
  - `PUT /api/sessions/<id>` — merge-update fields; 409 if session is already
    `complete`.
  - `DELETE /api/sessions/<id>` — delete the session file only (never touches
    already-generated videos).
  - `/api/generate`: `projectId` -> `sessionId`. For a `complete` session,
    server-side enforce immutability: only `theme`/`themeName` may override,
    everything else is forced from the stored session regardless of what the
    request sends. For an `incomplete` (or brand new) session, all fields
    remain overridable like today. On job success (not just submission) the
    session is flipped to `complete` — a failed generate leaves it
    `incomplete` so the user can retry.
  - Video sidecar / `/api/videos`: `projectId` -> `sessionId` field rename.
- [x] Phase 2 — `index.html`: Sessions list UI
  - "Start a new video" now POSTs `/api/sessions` first, then navigates to
    `new_video.html?sessionId=<id>`.
  - New Sessions section above Videos: incomplete rows -> continue editing;
    complete rows -> open the existing theme-picker sheet (moved from
    per-video to per-session) to generate a new video.
  - Remove the per-video regenerate button/action from the Videos list.
- [x] Phase 3 — `new_video.html`: session-backed draft
  - Read `sessionId` from the query string (auto-create + `replaceState` if
    missing, for robustness/direct navigation). Load config via
    `GET /api/sessions/<id>` instead of (in addition to) `afs_draft`
    localStorage. Save via `PUT /api/sessions/<id>` at the same points that
    called `saveDraftState()` before.
  - "Continue" navigates to `timing.html?sessionId=<id>`.
- [x] Phase 4 — `timing.html`: session-backed draft + completion
  - Replace the synchronous `afs_draft` localStorage read at top-of-script
    with an async `GET /api/sessions/<id>` in `init()`.
  - Back button returns to `new_video.html?sessionId=<id>`.
  - Final generate call sends `{sessionId, theme, themeName, timing}` (theme
    fields only needed as an explicit override on top of the stored session,
    same pattern as the existing regenerate flow).
  - Guard against opening a `timing.html?sessionId=` for an already-`complete`
    session (shouldn't be reachable from the UI, but defend anyway).
- [x] Phase 5 — Verification
  - Scripted Flask `test_client` run covering: create session -> edit via PUT
    -> generate (marks complete) -> attempt PUT on a complete session (409)
    -> generate again with just a theme override (immutability enforced).
  - Live server smoke test on a throwaway port, cleaned up after.
  - Note anything not verified with a live browser click-through.

## Notes / decisions log

- Phase 1 (`app.py`): `PROJECTS_DIR` -> `SESSIONS_DIR`, with
  `_migrate_legacy_projects_dir()` copying any old `projects/*.json` forward
  into `sessions/` (adding `status: "complete"`) on process start. The old
  `projects/` dir is left untouched (not deleted), same conservative stance
  as the original phase 2/3 work.
  - `field(key)` (existing helper) now checks `locked` first and, if locked,
    *only* ever returns the stored session's value — request data is
    ignored entirely for locked sessions, even if present. A second helper
    `override_field(key)` is used for exactly `theme`/`themeName`, the two
    fields a locked session is still allowed to vary per-generate.
  - Session completion happens in `_run_job` on `returncode == 0` (i.e.
    actual encode success), not at `/api/generate` submission time, via new
    `_mark_session_complete()` — a crashed/failed job leaves the session
    `incomplete` so the user can go back and fix it rather than getting
    silently locked out of editing a broken config.
  - `/api/generate` still tolerates being called with no `sessionId` at all
    (creates one via `_new_session()` on the fly) — kept as a defensive
    fallback for direct API callers, not something the UI is expected to do
    once Phases 2-4 land.
  - Verified with a scripted `test_client` run (real `threading.Thread` and
    `_run_job` monkey-patched to run synchronously and fake success, so no
    real `quran_video.py` subprocess ran): create session (incomplete) ->
    appears in list -> PUT updates surah/ayah/reciter while incomplete ->
    generate flips it to `complete` and stores `themeName` -> a further PUT
    correctly gets 409 -> a further generate attempt that tries to smuggle
    `surah: 99` is correctly ignored (session's stored `surah` stays `2`) ->
    `/api/videos` surfaces the right `sessionId` -> deleting the session
    leaves the already-generated videos in place. Test-generated
    `output/*.json`/`.mp4` files were cleaned up afterward; the 3
    migrated `sessions/*.json` files (from pre-existing `projects/`) are
    expected to remain.
  - Not yet done at the time: Phases 2-4 (frontend). Now Phase 2 is done, see
    below.

- Phase 2 (`index.html`): added a "Your sessions" list above "Your videos",
  reusing the existing `.vid-row`/`.vid-list`/`.empty` styling (just a new
  `.sess-badge` pill for incomplete/complete). Session rows have no
  play/thumbnail-click behavior (unlike video rows) — clicking anywhere on
  an incomplete row navigates to `new_video.html?sessionId=<id>`; clicking a
  complete row opens the theme-picker sheet (same sheet component previously
  triggered from a per-video regenerate button).
  - "Start a new video" (`#newVideoBtn`) now `POST /api/sessions` first, then
    navigates to `/new_video.html?sessionId=<id>` (falls back to plain
    `/new_video.html` if the POST fails, so the button never fully dead-ends).
  - Removed `.vid-regen` entirely from video rows/CSS — regenerate-with-a-
    different-theme is now exclusively a completed-session action.
  - Renamed the sheet's internal `regenProjectId` -> `regenSessionId` and the
    `/api/generate` payload key `projectId` -> `sessionId` to match Phase 1.
  - Session delete (`DELETE /api/sessions/<id>`) reuses the same
    slide-out-then-remove-from-array pattern as video delete; deleting a
    session never touches its already-generated videos (per Phase 1).
  - After a sheet-triggered generate finishes, both `/api/videos` and
    `/api/sessions` are re-fetched and re-rendered (a regenerate doesn't
    change the session's `updatedAt`/status server-side... actually it
    doesn't touch the session at all when locked, so re-fetching sessions
    here is mostly a no-op today, but kept for correctness/future-proofing
    in case locked-session generates start touching e.g. a "last used theme"
    field later).
  - Verified with `node -c` against the extracted inline `<script>` block
    (syntax only) and a manual read-through of the diff; no leftover
    `projectId`/`vid-regen`/`regenProjectId` references remain in the file.
    No live browser click-through yet — deferred to Phase 5 alongside the
    other pages once Phases 3-4 land, so the whole flow can be tested
    together end-to-end.

- Phase 3 (`new_video.html`): replaced the `afs_draft` localStorage
  read/write entirely with a server-side session (`ensureSession()`,
  `saveSessionState()`).
  - `ensureSession()`: reads `?sessionId=` from the URL; if present and the
    session loads *and* is not `complete`, uses it as-is. If the session is
    missing (deleted) or somehow already `complete` (shouldn't happen from
    the UI, but this page must never let you edit a locked session), it
    falls through to creating a brand new session and rewrites the URL via
    `history.replaceState` so a refresh/bookmark keeps pointing at the right
    one. No `sessionId` at all in the URL (e.g. old bookmark, or reached
    without going through index's "Start a new video") also creates one.
  - `saveSessionState()` fires a `PUT /api/sessions/<id>` on every point that
    used to call `saveDraftState()` (range steppers, surah/reciter/theme
    pick, continue) — best-effort/fire-and-forget (`.catch` swallows
    network errors) since local UI state is authoritative for the current
    page regardless of whether the PUT lands.
  - Theme restore on load: since a session only stores `theme`
    (the resolved dict) + `themeName` (not the `'preset:x'`/`'saved:id'` key
    the UI uses), restore matches by `themeName` against
    `themeEntries()` after `loadSavedThemes()` resolves — same tolerance as
    the old `themeKey` restore (preset hidden locally / saved theme deleted
    since -> just comes back unselected, not an error).
  - "Continue" now navigates to `timing.html?sessionId=<id>` instead of
    relying on a shared localStorage key.
  - Left `frame_editor.html` roundtrip (add/edit custom theme) alone --
    it already returns via browser `history.back()`, which naturally lands
    back on this page's `?sessionId=...` URL, no explicit passthrough
    needed.
  - Verified with `node -c` against the extracted inline `<script>` block
    (syntax only); confirmed no leftover `afs_draft`/`loadDraftState`/
    `saveDraftState` references remain. No live browser click-through yet
    (deferred to Phase 5, once `timing.html` also switches over so the
    whole new_video -> timing -> generate path can be exercised together).

- Phase 4 (`timing.html`): replaced the synchronous top-of-script
  `afs_draft` localStorage read (which threw/redirected immediately if
  missing) with an async fetch inside `init()`, since a session lookup has
  to be a network call. `draft` is now `let draft = null` at module scope,
  populated by `init()` before `buildAyahSkeleton()`/etc. run (nothing reads
  it earlier at module-eval time, so this is safe).
  - Guards added: no `?sessionId=` in the URL -> back to `new_video.html`;
    session fetch 404s -> same; session already `complete` -> back to `/`
    (this page must never run against a locked session); session found but
    missing surah/range/reciter (e.g. someone jumped straight to
    `timing.html?sessionId=...` before finishing `new_video.html`) -> sent
    back to `new_video.html?sessionId=...` to finish the draft there.
  - The unrelated client-side-only "timing session" (`SESSION_KEY =
    'afs_timing_session'`, mid-ayah marker resume) is untouched — still
    keyed off `sessionKeyFor(draft)` (surah/from/to/reciterKey/translation),
    which still works fine since `draft` has the same shape as before, just
    sourced from the server now instead of localStorage.
  - Back button's `onclick` is rewritten once `sessionId` is known
    (`document.getElementById('backBtnTop').setAttribute('onclick', ...)`)
    so "back" returns to `new_video.html?sessionId=<id>` instead of a bare
    `new_video.html` that would've had to fall back to creating a new
    session.
  - `generateVideo()`'s payload now sends `sessionId: draft.id` alongside
    the explicit fields (server-side `field()`/`override_field()` from
    Phase 1 will just re-confirm these since the session isn't locked yet
    at this point -- this mirrors the existing "explicit request value wins"
    resolution, so no behavior change there). On success, session completion
    itself happens server-side in `_run_job` (Phase 1); this page just
    stops referencing `afs_draft` (removed) and still clears the unrelated
    timing-marker-resume cache before redirecting home.
  - Verified with `node -c` against the extracted inline `<script>` block
    (syntax only); confirmed zero remaining `afs_draft` references anywhere
    in `static/*.html`.

- Phase 5 (verification): ran a **real, live end-to-end pass** against a
  throwaway server instance (`PORT=5099`, same repo/data dirs as the user's
  actual dev server on 5050 -- did not touch or restart that one), driving
  the exact HTTP calls each page now makes:
  1. `POST /api/sessions` -> got a blank `incomplete` session.
  2. `PUT /api/sessions/<id>` with surah 112 (Al-Ikhlas)/ayah 1-4/reciter
     `abdul_basit` (what `new_video.html` now does on every field change).
  3. `GET /api/sessions/<id>` (what `timing.html` now does on load) ->
     correct config came back.
  4. `POST /api/generate {sessionId}` -> a **real** `quran_video.py` run (not
     mocked this time), polled `/api/status` to completion.
  5. `GET /api/sessions/<id>` afterward -> `status: "complete"`, confirming
     `_mark_session_complete()` fires on a genuine successful encode, not
     just in the earlier mocked unit test.
  6. `PUT` on that now-complete session -> `409` as expected.
  7. `POST /api/generate {sessionId, surah: 999, themeName: "Rose Dusk"}` (a
     locked-session "make a new video with a different theme" call that also
     tries to smuggle a bogus surah) -> job started and **completed using
     the real stored surah 112**, not 999 -- proves `field()`'s lock
     enforcement holds against a real generate, not just the field-resolution
     logic in isolation.
  8. `DELETE /api/sessions/<id>` -> both videos generated from it remained in
     `/api/videos` afterward, each still showing the correct `sessionId`.
  - All test-generated `output/*.mp4`/`.json` and `jobs/<id>/` dirs were
    deleted afterward; the throwaway server process was killed. The 4
    `sessions/*.json` files now present are the pre-existing migrated ones
    (3 from the original `projects/` dir, unrelated to this test run).
  - **Not verified**: an actual browser click-through of `index.html` /
    `new_video.html` / `timing.html` (no headless browser available in this
    environment) -- the HTTP-level flow each page's JS drives was verified
    directly instead, plus `node -c` syntax checks on all three files'
    inline scripts. If something in the DOM wiring itself (event listeners,
    element IDs) has a typo that a syntax check wouldn't catch, that would
    only surface in a real browser session.
