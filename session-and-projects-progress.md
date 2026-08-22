# Session persistence + regenerable projects — implementation progress

**Status: Phases 1-3 done.** Phase 4 (richer project-aware UI) intentionally
deferred, not requested.

Goal (from user request):
1. Client-side "session" state so navigating between `new_video.html` ->
   `timing.html` -> back doesn't forget the current surah/ayah range/reciter/
   theme/orientation/translation selections.
2. Persist the *full* generation config (not just the small display sidecar)
   so a finished video can be "regenerated with a different theme" without
   re-entering everything, ideally as a reusable "project" concept: one saved
   config -> can produce multiple videos (one per theme).

## Plan

- [x] Phase 1 — Shared session object across screens
  - Replace the one-way `afs_draft` localStorage handoff with a single shared
    session key that every page (`new_video.html`, `timing.html`,
    `frame_editor.html` where relevant) reads on load and writes on every
    relevant change, so back/forward navigation preserves state.
  - Keep it scoped to in-progress-draft only (not finished videos).
- [x] Phase 2 — Persist full generation config as a "project"
  - When `/api/generate` runs, write the full config (surah, ayah range,
    reciter, translation, orientation, flags, theme dict, timing manifest) to
    a permanent `projects/<project_id>.json`, not just job-scratch.
  - Link each generated video's sidecar to its `projectId`.
- [x] Phase 3 — "Regenerate with different theme" flow
  - Library UI: action on a video to relaunch generation reusing the stored
    project config but with a different theme (existing preset/saved theme).
  - Backend: extended `/api/generate` to accept `projectId` + override
    `theme`/`themeName`; other fields fall back to the stored project config.
- [ ] Phase 4 (deferred, not requested yet) — richer project-aware library UI
  (e.g. grouping multiple videos under one project, picking a *new* custom
  theme via the frame editor from the regenerate flow instead of only
  existing presets/saved themes).

## Notes / decisions log

- Phase 1: kept the existing `afs_draft` localStorage key (no migration
  needed) but changed it from a one-shot write-on-Continue into a
  continuously-updated draft: `new_video.html` now calls `saveDraftState()`
  after every surah/range/reciter/theme change, and reads it back via
  `loadDraftState()` in `init()` to restore the surah, ayah range, reciter,
  and theme selection. `timing.html` is unchanged (it already read the same
  key once on load and clears it on successful generation).
  - Theme restore: `selectTheme(draft.themeKey)` runs after
    `loadSavedThemes()`; if the saved/preset theme no longer exists (deleted
    since), `themeEntries().find(...)` returns undefined and selection just
    clears back to "no theme picked" rather than erroring — acceptable edge
    case, user just re-picks a theme.
  - Restoring the ayah range validates `from`/`to` against the restored
    surah's ayah count before trusting it, in case the surah list or ayah
    counts ever change.
  - `consumeCustomThemeIfJustSaved()` (existing flow: returning from
    `frame_editor.html` after saving a new/edited custom theme) runs after
    the new draft-restore logic and still wins if both apply, since it calls
    `selectTheme()` again afterward.
  - Verified with a Node syntax check of the extracted inline `<script>`
    block (no headless browser available in this environment); did not do a
    live click-through in a real browser.

- Phase 2/3 (`app.py`): added `PROJECTS_DIR = HERE / "projects"` and
  `_load_project(project_id)`. `/api/generate` now accepts an optional
  `projectId`; a `field(key, default)` helper resolves each config value as
  request-value-if-explicitly-given, else stored-project-value, else
  default, so a regenerate call only needs to send
  `{projectId, theme, themeName}` and everything else (surah, ayah range,
  reciter, translation, orientation, flags, timing manifest) comes from the
  saved project. The merged/effective config is written back to
  `projects/<project_id>.json` on every generate call (fresh or regenerate),
  and `project_id` flows into the job's `meta` dict -> the video sidecar
  (`_write_video_sidecar` already copies `meta` wholesale) -> `/api/videos`
  response as `projectId`.
  - New project IDs are generated separately from job IDs (`uuid4().hex[:10]`,
    same shape) since one project config is meant to produce many videos
    over time (one per regenerate-with-different-theme call).
  - Deleting a video (`DELETE /api/videos/<id>`) intentionally does **not**
    touch `projects/`, so a project stays regenerate-able even after its
    original video is deleted. Nothing currently deletes project files —
    acceptable for now (small JSON files), revisit if `projects/` growth
    becomes a real concern.
  - Verified with a scripted Flask `test_client` run (`threading.Thread`
    monkey-patched to a no-op so no real `quran_video.py` subprocess/network
    calls happen): a fresh generate call creates `projects/<id>.json` with
    the full config; a follow-up call passing only `{projectId, theme,
    themeName}` correctly reused the stored surah/ayah range/reciter and
    only changed the theme; `/api/videos` correctly surfaces `projectId`
    from a sidecar.
  - `index.html`: added a "regenerate" icon button on each video row (only
    shown when `v.projectId` is present — older videos generated before this
    change won't have one and simply don't get the button), which opens a
    theme-picker bottom sheet (reusing the same preset+saved-theme listing
    and swatch styling as `new_video.html`'s theme cards, just as
    `sheet-row`s instead of a card grid) and, on selection, POSTs to
    `/api/generate` with `{projectId, theme, themeName}` and polls
    `/api/status/<jobId>` with the same overlay pattern already used in
    `timing.html`. On completion it refreshes the video list in place rather
    than redirecting.
  - Scope decision: regenerate only lets you pick from existing presets/saved
    themes (not create a brand new custom theme via the frame editor
    mid-flow) — matches what the user asked for ("regenerate using different
    theme") without scope-creeping into a cross-page editor handoff. Noted
    as Phase 4 if wanted later.
  - Verified with a Node syntax check of `index.html`'s inline script only
    (no live browser click-through in this environment).
  - **Additionally verified against a real running server** (a second
    instance on `PORT=5099`, separate from the user's already-running
    dev server on 5050, so as not to disturb it): a real
    `POST /api/generate` for Al-Ikhlas (surah 112) wrote
    `projects/<id>.json`, finished encoding, and showed up via
    `/api/videos` with the correct `projectId`; a follow-up
    `POST /api/generate` with only `{projectId, theme, themeName}`
    correctly reused the stored surah/range/reciter and produced a second
    video with the new theme. All test-generated files (`output/*`,
    `jobs/*`, `projects/*`) and the temporary server process were cleaned
    up afterward. Still no live browser click-through of the
    `index.html`/`new_video.html` UI itself in this environment.
  - Added `projects/` to `.gitignore` (matches the existing `jobs/`,
    `output/`, `themes/` pattern — these are runtime-generated, not
    checked-in project files).
