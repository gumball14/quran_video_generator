# Custom-audio entry + post-timing style selection — implementation progress

**Status: all 11 phases complete and verified (scripted). No live browser
click-through yet -- see Phase 11 notes.**

## Goal (from user request)

Two clear parallel top-level creation flows from `index.html`, replacing today's single path:

- **Scenario 1 (custom audio)**: index -> paste a URL (Facebook/Instagram/TikTok/
  YouTube/etc., cached by URL so a re-paste doesn't re-download) -> pick
  surah/range/translation -> mark ayah timing -> **Done** -> pick a style *or*
  save-and-exit -> generate (with a **cancel** button) -> a result screen with
  download/home.
- **Scenario 2 (existing reciter audio)**: index -> pick surah/range/reciter/
  translation (no custom-audio button, no style picker on this screen) -> mark
  timing -> same Done -> style-or-save -> generate-with-cancel -> same result
  screen.

Both scenarios converge on the same "Done" hand-off out of `timing.html`, so
most of the new work is one shared style-selection screen, one shared
generating screen (with cancel, new backend support), and one shared result
screen -- plus reshuffling which screen owns which picker.

Full plan with rationale: `/home/oem/.claude/plans/swift-chasing-kettle.md`
(also summarized in the Plan checklist below).

## Plan

- [ ] Phase 0 -- This progress doc.
- [x] Phase 1 -- Backend: cancel support (`app.py`)
  - Keep the `Popen` object on `JOBS[job_id]["proc"]`.
  - New `POST /api/jobs/<job_id>/cancel`: `proc.terminate()` (kill after a
    short grace period if still alive), `job["cancel_requested"] = True`.
  - `_run_job`, after `proc.wait()`: if cancelled, `status = "cancelled"`
    instead of done/error, skip `_mark_session_complete()`, remove any
    partial output file.
- [x] Phase 2 -- Backend: URL audio cache (`app.py` + `quran_lib/audio.py`)
  - New `audio_cache/` dir keyed by `sha256(url)`. Cache hit -> copy straight
    into `uploads/<session_id>/range.<ext>`, no network call. Cache miss ->
    existing download path, then also save into the cache.
- [x] Phase 3 -- Backend: session `entry` marker (`app.py`)
  - `POST /api/sessions` accepts optional `{"entry": "custom"|"reciter"}`,
    stored outside `SESSION_FIELDS` (set once, never edited) so `index.html`
    knows which screen to resume an incomplete session into.
- [x] Phase 4 -- `index.html`: two entry points + routing cleanup
  - "Start a new video" -> `POST /api/sessions {entry:'reciter'}` ->
    `new_video.html`. New "Create with custom audio" ->
    `POST /api/sessions {entry:'custom'}` -> `custom_audio.html`.
  - Incomplete-session rows route by `session.entry`.
  - Replace the inline regenerate-with-new-theme sheet's generate/poll code
    with `location.href = '/select_style.html?sessionId=...'`.
- [x] Phase 5 -- `new_video.html`: remove custom-audio button + style picker
- [x] Phase 6 -- `custom_audio.html`: become a real entry screen (adds
  surah/range/translation picker, drops the hard redirect guard)
- [x] Phase 7 -- `timing.html`: "Done" persists timing/customAudio via PUT
  then hands off to `select_style.html` (removes inline generate/poll)
- [x] Phase 8 -- New `static/select_style.html` (theme grid ported from
  `new_video.html`; Save & finish later / Generate)
- [x] Phase 9 -- New `static/generating.html` (progress + cancel)
- [x] Phase 10 -- New `static/video_ready.html` (preview/download/home)
- [x] Phase 11 -- Verification (syntax checks, scripted Flask test_client,
  live throwaway-server smoke test for both scenarios incl. one cancel)

## Notes / decisions log

- Phase 1 (`app.py`): `JOBS[job_id]` gained `"proc"` (the live `Popen`,
  cleared back to `None` once the job finishes) and `"cancel_requested"`.
  New `POST /api/jobs/<job_id>/cancel`: no-ops (returns the current status)
  if the job isn't `"running"`; otherwise sets `cancel_requested`, calls
  `proc.terminate()`, and waits up to 3s before `proc.kill()` as a fallback
  -- this blocks the request thread briefly, judged acceptable for a local
  single-user dev-server app.
  - `_run_job` checks `cancel_requested` right after `proc.wait()` returns
    (whether that return was natural exit or forced by the cancel call) and
    sets `status = "cancelled"` instead of evaluating `returncode`, deletes
    any partial output file, and skips `_mark_session_complete()` -- so a
    cancelled job's session is left `incomplete`, same as a failed one.
  - `proc.wait()` is safe to call concurrently from both the job thread and
    the cancel request thread -- `subprocess.Popen` serializes this
    internally.
  - Verified with `python3 -m py_compile app.py` (syntax only); behavioral
    verification (actually cancelling a running job) deferred to Phase 11's
    scripted test pass.

- Phase 2 (`quran_lib/audio.py` + `app.py`): reused the existing
  `CACHE_DIR` (`quran_lib/constants.py`, already used by
  `download_ayah_audio()` for per-ayah everyayah.com mp3s) rather than
  inventing a new cache root -- new `download_audio_from_url_cached()`
  keys a subdir `CACHE_DIR/url_audio/` by `sha256(url)`. On a hit it
  `shutil.copy2`s the cached file straight into `dest_dir/<stem>.<ext>`
  (clearing any old `<stem>.*` first, same as the uncached function did);
  on a miss it calls the original `download_audio_from_url()` unchanged
  and then copies the result into the cache.
  - `app.py`'s `_download_range_audio()` (used by both
    `/api/timing/download-range-audio` and, per its own docstring,
    `static/custom_audio.html`'s "split your recording" flow) now calls
    the cached wrapper instead of the raw function -- one-line swap, no
    endpoint signature/behavior change from the caller's perspective.
  - No eviction/TTL, matching the plan's "keep it simple" call.
  - Verified with `python3 -m py_compile app.py quran_lib/audio.py`
    (syntax only); a real hit-vs-miss run (confirming the second identical
    URL skips yt-dlp) is deferred to Phase 11.

- Phase 3 (`app.py`): `_new_session()` now takes `entry=None`
  (`"custom"`/`"reciter"`/anything else -> stored `None`), stamped onto
  `session["entry"]` directly in the dict -- deliberately kept out of
  `SESSION_FIELDS` (the PUT-editable list) so a session's `entry` is
  immutable after creation, matching how it'll be used purely for
  first-load routing. `POST /api/sessions` now reads an optional JSON body
  (`request.get_json(force=True, silent=True) or {}`, same tolerant
  pattern used elsewhere in this file) for `{"entry": ...}`; omitting the
  body still works exactly as before (`entry: None`), so this is fully
  backward compatible with any existing caller. Legacy sessions migrated
  from `projects/*.json` also come back with no `entry` key at all
  (`_migrate_legacy_projects_dir()` untouched) -- frontend routing (Phase
  4) must treat missing/`None` the same as `"reciter"` (today's only flow)
  as the safe default.
  - Verified with `python3 -m py_compile app.py`.

**Backend work (Phases 1-3) complete.** Remaining phases are all
`static/*.html` frontend work.

- Phase 4 (`static/index.html`): added a second CTA `#customAudioBtn`
  ("Create with custom audio") right under the existing `#newVideoBtn`,
  styled via a new `.cta-secondary` (outlined, reuses existing `--surface-2`/
  `--line` tokens) so it reads as the alternate path, not equal-weight.
  Both buttons now `POST /api/sessions` with `{entry: 'reciter'}` /
  `{entry: 'custom'}` respectively before navigating (falls back to the
  bare page URL on a failed POST, same defensive pattern as before).
  - Incomplete-session row click routing: `s.entry === 'custom'` ->
    `custom_audio.html?sessionId=...`, else (covers `'reciter'`, `null`,
    and legacy-migrated sessions with no `entry` key at all) ->
    `new_video.html?sessionId=...`, matching today's only behavior as the
    safe default.
  - Deleted the entire `#genOverlay` markup block, its CSS
    (`.gen-overlay`/`.gen-title`/`.gen-track`/`.gen-fill`/`.gen-label`/
    `.gen-log`/`.gen-error`/`.gen-btn`), and its JS (`genFill`/`genLabel`/
    `genLog`/`genError`/`genBackBtn`/`showGenError`/the inline
    `setInterval` poller). The "regenerate a completed session with a new
    theme" sheet's `#themeSheetSelect` handler now just `POST
    /api/generate` and, on success, navigates to
    `/generating.html?jobId=...` instead of polling inline -- this is the
    plan's "consolidate onto the new shared screens" cleanup, and gives
    this action cancel support for free once Phase 9 lands.
  - Left the separate top-of-page "Styles" row (`#styleCards`/
    `#styleAddBtn`, the standalone saved-styles library management UI)
    untouched -- it's unrelated to the create-video flow.
  - Verified: `grep` confirms zero remaining `gen-overlay`/`genOverlay`/
    `genFill`/etc. references anywhere in the file; extracted the inline
    `<script>` and ran `node -c` against it (syntax only). `generating.html`
    doesn't exist yet (Phase 9) -- the new navigation target is a dangling
    link until then, same "build the whole chain before it's live"
    approach as prior refactors in this repo.

- Phase 5 (`static/new_video.html`): removed the entire "Style" section
  (markup + `.theme-card`/`.theme-add`/`.opt-row` CSS, since `.opt-row` was
  only ever used by this row), the `#manageCustomAudioBtn` button (markup +
  its `.sheet-manage-btn` CSS + click handler) from the reciter sheet's
  footer, and the whole theme JS module (`themeEntries()`, `swatchStyle()`/
  `glyphColor()`, `renderThemeCards()`, `loadSavedThemes()`,
  `selectTheme()`, `updateOrientationHint()`, `updateThemeAddTile()`, the
  `#themeAddBtn` -> `frame_editor.html` roundtrip, and
  `consumeCustomThemeIfJustSaved()`/its `pageshow` listener).
  - `state.themeKey`/`themeLabel`/`themeObject` dropped from `state`; the
    `theme`/`themeName` fields dropped from `saveSessionState()`'s PUT
    payload -- this page no longer touches those two session fields at
    all, leaving them to `select_style.html` (Phase 8).
  - `updateContinueBtn()`/the Continue click handler now gate on
    `state.reciter` only (theme requirement removed); the hint text
    changed from a 3-way (no reciter / no theme / ready) to a 2-way (no
    reciter / ready) message.
  - `init()`'s session-restore no longer awaits `loadSavedThemes()` or
    tries to match `session.themeName` back to a theme entry -- that
    session field just isn't read on this page anymore.
  - `ensureSession()`'s fallback `POST /api/sessions` (reached only when
    the page is opened with no `?sessionId=` at all) now sends
    `{entry: 'reciter'}`, matching Phase 3/4's routing marker.
  - Also removed the now-fully-unused `showConfirm()` helper (its only
    caller was the deleted theme-delete confirmation); `showAlert()` and
    the `#confirmBackdrop`/`#confirmModal` markup they share stay, since
    `showAlert()` is still used elsewhere on this page (gapless-reciter
    warnings).
  - Verified: `grep` confirms zero remaining `theme`/`Theme` references in
    the file other than the unrelated `<meta name="theme-color">` tag, and
    zero remaining `id="theme..."`/`class="theme..."` markup; `node -c` on
    the extracted inline `<script>` passes.

- Phase 6 (`static/custom_audio.html`): this was the biggest single phase.
  Replaced the static `.context-banner` (glyph + read-only surah/range text)
  with a full surah + ayah-range + translation picker ported verbatim (CSS
  and markup structure) from `new_video.html`'s equivalent section --
  `.select-btn`/`.select-glyph`/`.select-text`, `.range-card`/`.stepper*`,
  and the generic bottom-sheet CSS (`.sheet*`), none of which existed in
  this file before. Added the surah and translation `<div class="sheet">`
  markup blocks (with search inputs) near the end of the body, alongside
  the existing trim/crop dialog.
  - Deliberate deviation from the user's literal step-by-step scenario
    order ("paste URL, then pick surah/range/translation"): the picker is
    now the first section on the page (same position `new_video.html` uses
    it), ahead of "Recording". Functionally this doesn't block anything --
    nothing stops pasting a URL first and picking surah after, since the
    "Recording"/"Ayah marks" sections were already independently gated on
    `rangeAudioInfo` existing, not on surah being picked -- but it does
    mean the picker is visually first. Reusing `new_video.html`'s exact,
    already-tested layout was judged lower-risk than inventing a new
    "paste-first" layout; flagged to the user as a call worth revisiting
    if it reads wrong in practice.
  - `init()` no longer hard-redirects to `new_video.html` when
    `session.surah`/`ayahStart`/`ayahEnd` are unset. Instead it seeds the
    same defaults `new_video.html` uses for a brand new draft (first surah
    in `window.SURAHS`, ayahs 1-7 clamped to the surah's length,
    `en.sahih` translation), immediately `PUT`s them via a new
    `saveSessionState()` (this file had zero PUT calls before), then
    proceeds -- so a fresh custom-audio session always has real,
    non-null surah/range/translation from the first render, same
    invariant `new_video.html` already relied on.
  - Surah/range/translation JS ported from `new_video.html`
    (`updateSurahDisplay`/`updateRangeDisplay`/`updateTranslationDisplay`,
    `renderSurahSheetList`/`renderTranslationSheetList`, the stepper
    handlers, `LANG_FLAGS`/`langToFlag`, `openSheet`/`closeSheet`) but
    adapted to mutate the existing `session` object directly (this file
    has no separate `state` object the way `new_video.html` does) and to
    call this file's own existing `loadPreviewText()`/`updatePreviewStatus()`
    (NOT new_video.html's `loadPreview()`, which doesn't exist here) after
    a change, since this page's ayah-marking preview card was already
    built around those.
  - New behavior with no `new_video.html` equivalent: switching surahs via
    the picker now warns (native `confirm()`, no custom dialog component
    existed in this file to reuse) and clears `marks` if any exist, since
    marks are keyed by ayah *number* and switching surahs makes existing
    numbers refer to different content. Range steppers do NOT clear marks
    -- confirmed by reading `findMarkIndexForAyah`/`surahAyahCount()` that
    marks are absolute ayah numbers bounded by the *surah's* length, not
    by `ayahStart`/`ayahEnd`, so narrowing/widening the range doesn't
    invalidate any existing mark data.
  - `#backBtn` (top-left) now goes to `/` instead of
    `new_video.html?sessionId=...` -- this page is a standalone entry
    point now, not a sub-step of `new_video.html`. Same for the missing-
    `sessionId` guard at the top of the script (was
    `location.href = '/new_video.html'`, now `'/'`).
  - Verified: zero remaining `ctxGlyph`/`ctxTitle`/`ctxSub`/`context-banner`
    references; zero duplicate `id="..."` attributes anywhere in the file
    (`grep`+`sort`+`uniq -c`); `<div>`/`</div>` counts balanced (120/120);
    `node -c` on the extracted inline `<script>` passes. Not yet verified:
    an actual browser click-through (deferred to Phase 11, once
    `select_style.html`/`generating.html`/`video_ready.html` exist so the
    whole chain can be exercised together, same approach as
    `sessions-refactor-progress.md`).

- Phase 7 (`static/timing.html`): renamed the final-ayah button label from
  "Generate video" to "Done" (the `nextBtn.innerHTML` branch keyed on
  `ayahIndex === ayahs.length - 1`). `goNext()`'s last-ayah branch now
  calls a new `finishTiming()` instead of the old `generateVideo()`.
  - Deleted the entire `#genOverlay` markup + `.gen-*` CSS (moves to
    `generating.html`, Phase 9) and the old `generateVideo()`/
    `showGenError()`/`genBackBtn` click handler.
  - `finishTiming()` branches on `editOnlyMode` (this page's existing
    "correct word timing on an already-complete/locked session" mode,
    reached via `index.html`'s regenerate sheet -> "Edit word timing
    instead" -> `timing.html?sessionId=...&editTiming=1`): in that mode
    there's no style to (re-)pick -- the session already has one and is
    locked -- so it `POST /api/generate {sessionId, timing}` directly
    (matching the payload the old inline path sent for this case) and
    goes straight to `/generating.html?jobId=...`, skipping
    `select_style.html` entirely.
  - For a normal (non-`editOnlyMode`, not-yet-complete) session,
    `finishTiming()` builds `manifest`/`customAudioManifest` exactly as
    before, then **awaits** `PUT /api/sessions/<id> {timing, customAudio}`
    (not fire-and-forget, unlike `new_video.html`'s `saveSessionState()`)
    before navigating to `/select_style.html?sessionId=...` -- deliberately
    blocking, and surfacing an alert + refusing to navigate on failure,
    because `select_style.html`'s later `/api/generate` call reads
    `timing`/`customAudio` back off the *stored* session rather than
    having them passed through the URL, so a lost PUT would otherwise
    silently generate with stale/missing timing.
  - `stopPlayback()` (previously called right before opening the overlay)
    is now called at the top of `finishTiming()`, same effective timing.
  - Verified: `grep` confirms zero remaining `genOverlay`/`genFill`/
    `genLabel`/`genLog`/`genError`/`genBackBtn`/`gen-overlay`/etc.
    references anywhere in the file; `node -c` on the extracted inline
    `<script>` passes. `select_style.html`/`generating.html` don't exist
    yet (Phases 8-9) -- both new navigation targets are dangling links
    until then.

- Phase 8 (new `static/select_style.html`): standalone page, same
  head/`:root`/topbar/body boilerplate as every other screen in this app.
  Reads `?sessionId=...`, `GET`s the session, shows a small read-only
  context banner (surah + range, reusing the exact CSS `custom_audio.html`
  used to have before Phase 6 replaced it with the picker there) and the
  theme grid ported from the `new_video.html` code deleted in Phase 5
  (`themeEntries`/`swatchStyle`/`glyphColor`/`renderThemeCards`/
  `loadSavedThemes`/`selectTheme`/the `frame_editor.html` add/edit
  roundtrip incl. `consumeCustomThemeIfJustSaved`), adapted from
  `new_video.html`'s `.opt-row` horizontal-scroll strip to a `flex-wrap`
  grid (this page has no other content competing for horizontal space, so
  wrapping reads better than scrolling) and from `state.theme*` fields to
  local `themeKey`/`themeLabel`/`themeObject` variables (no shared `state`
  object here).
  - Simplified the delete-confirmation from `new_video.html`'s custom
    modal (`showConfirm`, deleted in Phase 5) to plain `confirm()` --
    same call this session already made for `custom_audio.html`'s
    surah-switch warning in Phase 6, avoiding porting a whole dialog
    component for a same-page-only affordance.
  - Two footer actions: `#saveLaterBtn` ("Save & finish later") just goes
    to `/` -- nothing to persist here since Phase 7 already `PUT` the
    timing/customAudio before handing off to this page, so the session is
    already in a resumable state regardless of whether a theme gets
    picked. `#generateBtn` (disabled until `themeKey` is set) does
    `POST /api/generate {sessionId, theme, themeName}` and on success
    navigates to `/generating.html?jobId=...`.
  - This same page also serves `index.html`'s "regenerate a completed
    session with a new theme" entry point from Phase 4 -- verified no
    special-casing is needed for a `status: "complete"` (locked) session:
    `/api/generate`'s existing `field()`/`override_field()` logic in
    `app.py` already resolves every other field from the locked session
    regardless of what this page sends, and `theme`/`themeName` are always
    overridable even when locked (that's `override_field`'s whole
    purpose, predating this refactor).
  - `#backBtn` returns to `timing.html?sessionId=...` (in case the user
    wants to adjust marking before picking a style).
  - Verified: `node -c` on the extracted inline `<script>` passes;
    `<div>`/`</div>` counts balanced (23/23).

- Also (small follow-up while wiring Phase 9): all three existing
  `location.href = '/generating.html?jobId=...'` call sites --
  `select_style.html` (Phase 8), `timing.html`'s `editOnlyMode` branch
  (Phase 7), and `index.html`'s regenerate-with-theme handler (Phase 4)
  -- now also append `&sessionId=...`, so `generating.html`'s "Back to
  style" button has something to link to regardless of which of the three
  flows started the job.

- Phase 9 (new `static/generating.html`): standalone page (centered,
  no topbar -- unlike the other new pages there's nothing to navigate to
  mid-generation except cancel). Progress bar/log UI is the same
  `.gen-title`/`.gen-track`/`.gen-fill`/`.gen-label`/`.gen-log`/`.gen-error`
  markup+CSS deleted from `timing.html`/`index.html` in Phases 4 and 7,
  now living in exactly one place. Polls `/api/status/<jobId>` on the same
  1200ms interval and `since`-offset log-tailing pattern the old inline
  implementations used.
  - New `#cancelBtn` -> `POST /api/jobs/<jobId>/cancel` (Phase 1's new
    endpoint). Deliberately does NOT stop the poll loop or change UI state
    itself on click (just disables the button and relabels it
    "Cancelling…") -- the poll loop is left as the single source of truth
    for job status, so whichever of "the cancel POST returns" or "the next
    poll tick sees `status: 'cancelled'`" happens first, the UI still ends
    up correct. Backend Phase 1 already blocks the cancel POST up to ~3s
    (terminate + grace period before kill), so in practice the two usually
    resolve close together.
  - Three end states, each stopping the poll and swapping in
    `#backToStyleBtn`/`#homeBtn` (via a shared `showEndState()`):
    `status: 'done'` -> immediately navigates to
    `/video_ready.html?jobId=...` (no terminal screen of its own here,
    unlike error/cancelled); `status: 'error'` -> shows `json.error` text;
    `status: 'cancelled'` -> shows a plain "Generation was cancelled."
    message. `#backToStyleBtn` only renders (`showEndState`'s `showBack`
    param) when a `sessionId` was present in the URL, and links to
    `/select_style.html?sessionId=...`.
  - Verified: `node -c` on the extracted inline `<script>` passes;
    `<div>`/`</div>` counts balanced (7/7).

- Phase 10 (new `static/video_ready.html`): standalone page, `?jobId=...`
  only (no `sessionId` needed -- nothing here writes back to a session).
  `GET /api/status/<jobId>` on load: if the job isn't known, isn't
  `status: 'done'`, or has no `downloadUrl` (e.g. the server restarted
  since the job ran -- `JOBS` is in-memory only, per `app.py`'s own
  comment on `_write_video_sidecar()`), shows a plain "isn't available
  anymore" state with just a Home button, per the plan's graceful-fallback
  note. Otherwise renders a `<video>` preview and two actions: a Download
  link (`<a download>` to the same `downloadUrl`, i.e.
  `/api/download/<jobId>`) and Home.
  - Used `/api/download/<jobId>` directly as both the `<video src>` and
    the download link, rather than adding a separate non-attachment
    "preview" URL -- `send_file(..., as_attachment=True)` still serves
    Range requests (Flask's default `conditional=True`), so `<video>`
    playback works despite the `Content-Disposition: attachment` header;
    only a direct link click/download is affected by that header, which
    is exactly what's wanted for the Download button. No backend change
    needed for this page.
  - Verified: `node -c` on the extracted inline `<script>` passes;
    `<div>`/`</div>` counts balanced (9/9, including the template-literal
    markup built at runtime).

- Phase 11 (verification): ran, in order:
  1. `python3 -m py_compile app.py quran_lib/audio.py` and a `node -c`
     syntax pass over every touched/new page's extracted inline
     `<script>` (`index.html`, `new_video.html`, `custom_audio.html`,
     `timing.html`, `select_style.html`, `generating.html`,
     `video_ready.html`) -- all clean.
  2. A scripted `test_client` pass (`verify_phase11.py`, deleted after
     running -- was a scratchpad file, not committed) covering the three
     backend-only pieces in isolation: session `entry` marker (`custom`/
     `reciter`/omitted/an invalid value, confirming invalid values store as
     `None` rather than erroring); cancelling a **real** running job (a
     genuine `subprocess.Popen(["sleep", "5"])` driven through the actual
     `_run_job`, cancelled mid-flight via the HTTP endpoint, confirmed to
     reach `status: "cancelled"`) plus cancel being a no-op/404 on an
     already-finished or unknown job; the URL audio cache (mocked
     `download_audio_from_url`, called twice with the identical URL into
     two different session upload dirs -- second call hit the cache and
     never invoked the mock again, a genuinely different URL did invoke
     it).
  3. A live HTTP-level end-to-end smoke test (`verify_phase11_e2e.py`,
     also scratchpad-only) against the real `app.py`/Flask `test_client`
     (not a separate server process this time, since no live browser
     click-through was possible either way in this environment -- same
     constraint the original `sessions-refactor-progress.md` Phase 5
     ran into for its browser-only gaps), with `_run_job` swapped for a
     fake that succeeds instantly (writes a small fake `.mp4`, marks the
     job done, calls the real `_mark_session_complete()`) so this runs
     offline with no real `quran_video.py`/ffmpeg/yt-dlp dependency --
     mirrors the same monkeypatching approach the sessions-refactor
     verification used. Drove the **actual** call sequence each page now
     makes for both scenarios end-to-end: scenario 2 (reciter) --
     create session with `entry:'reciter'` -> `PUT` surah/range/reciter/
     translation -> `PUT` timing (the new `timing.html` "Done" behavior)
     -> `POST /api/generate` -> poll to `done` -> session flips to
     `complete` -> downloaded bytes match -> a further `PUT` on the now-
     locked session correctly 409s. Scenario 1 (custom audio) -- create
     session with `entry:'custom'` -> confirmed `surah` starts `None`
     (i.e. `custom_audio.html`'s own `init()` is really responsible for
     defaulting it, not the backend) -> `PUT` surah/range/translation ->
     `PUT` timing+customAudio -> `POST /api/generate` with a style ->
     poll to `done`.
  - **Found and fixed a real bug during this pass**: scenario 1's
    `/api/generate` call 400'd with `"Unknown reciter."` even though
    nothing in the new flow ever touches reciter. Root cause: `app.py`'s
    `field(key, default)` helper only falls back to `default` when the
    session dict is missing the key entirely -- but every session (via
    `_new_session()`) always has an explicit `"reciter": None` key, so
    `field("reciter", "yasser_al_dossary")` returned `None` (a present
    value) instead of the default whenever no reciter had ever been
    picked, which is *always* true for a custom-audio session now that
    `custom_audio.html` no longer requires going through `new_video.html`
    first. This bug already existed before this refactor (reachable if
    someone opened `new_video.html`'s "Manage custom audio" without
    picking a reciter first) but this refactor makes it hit on every
    single scenario-1 generate. Fixed with a targeted one-line change --
    `reciter = field("reciter") or "yasser_al_dossary"` -- rather than
    changing the shared `field()` helper itself, since `None` is the
    *correct* pass-through value for other fields that use it
    (`ayahStart`/`ayahEnd`/`customAudio`). Re-ran both scripted passes
    afterward; both green.
  - **Not verified**: an actual browser click-through of any page (no
    headless browser available in this environment, same limitation
    every prior `*-progress.md` in this repo has hit) -- in particular,
    the picker UI ported into `custom_audio.html` in Phase 6 and the
    three brand-new pages (Phases 8-10) have only been exercised at the
    HTTP/JS-syntax level, not by actually clicking through them in a
    browser. Recommend the user do one real click-through of both
    scenarios before relying on this.

**All 11 phases complete.**
