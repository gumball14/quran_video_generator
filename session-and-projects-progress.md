# Session persistence + regenerable projects — implementation progress

**Status: not started.**

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
- [ ] Phase 2 — Persist full generation config as a "project"
  - When `/api/generate` runs, write the full config (surah, ayah range,
    reciter, translation, orientation, flags, theme dict, timing manifest) to
    a permanent `projects/<project_id>.json`, not just job-scratch.
  - Link each generated video's sidecar to its `projectId`.
- [ ] Phase 3 — "Regenerate with different theme" flow
  - Library UI: action on a video/project to relaunch generation reusing the
    stored project config but with a different theme (existing saved theme or
    the frame editor).
  - Backend: `/api/projects/<id>/regenerate` (or extend `/api/generate` to
    accept `projectId` + override `theme`) that loads the stored config and
    reruns `quran_video.py`.
- [ ] Phase 4 — Library UI updates
  - `index.html` video library grouped/aware of projects; show "regenerate"
    action; make sure deleting a video doesn't delete the project config it
    came from (so it can still be reused).

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
