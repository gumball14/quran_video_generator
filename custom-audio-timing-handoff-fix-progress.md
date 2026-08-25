# Custom-audio → timing.html hand-off bug — fix progress

## Root cause (confirmed by reading code, not guessing)

`custom_audio.html`'s "Continue to timing" already correctly navigates to
`timing.html?sessionId=...` and stashes `customAudioByRealAyah`/`rangeAudioInfo`
into `localStorage['afs_timing_session']` right before navigating. The bug is
entirely in **`timing.html`'s `init()`**:

1. `timing.html` line ~2171: `if (!draft.surah || !draft.from || !draft.to || !draft.reciterKey)`
   redirects back to `new_video.html`. A custom-audio-only session
   (`entry:'custom'`) never sets `session.reciter` — `custom_audio.html`'s
   `saveSessionState()` never PUTs a `reciter` field — so `draft.reciterKey`
   is always falsy for this flow, and the user gets bounced to
   `new_video.html` every time. This matches "why do I end up on
   new_video.html instead".

2. Bouncing to `new_video.html` and picking a reciter there, then hitting
   *its* Continue button, navigates to `timing.html` again — but now
   `session.reciter` is non-null. `timing.html`'s `sessionKeyFor(draft)`
   (used by `loadSavedSession()` to decide whether the stashed
   localStorage draft applies) includes `reciterKey` in the key. The draft
   `custom_audio.html` stashed was keyed with `fresh.reciter` = `null` at
   save time; the new load computes the key with the just-picked reciter.
   Mismatch → `loadSavedSession()` returns `null` → `customAudioByRealAyah`
   / `rangeAudioInfo` are silently dropped → every ayah falls back to the
   plain reciter download. This matches "custom audio is gone, now it only
   uses the selected reciter".

Backend (`app.py`) already handles a null/missing reciter gracefully in two
places (`/api/generate`'s `reciter = field("reciter") or "yasser_al_dossary"`,
and `/api/timing/audio`'s `request.args.get("reciter", "yasser_al_dossary")`)
— this was fixed during the prior `flow-restructure-progress.md` refactor.
**`timing.html`'s own guard/key logic was never updated to match**, which is
exactly the gap that doc's Phase 11 flagged as unverified (no real browser
click-through was done).

## Fix plan (small, separated steps — check off as each lands)

- [x] Step 1 — `static/timing.html`: default `draft.reciterKey` to
      `'yasser_al_dossary'` (same fallback constant already used
      server-side) when building `draft` in `init()`, instead of leaving it
      `null`/`undefined` for a custom-audio session.
- [x] Step 2 — `static/timing.html`: drop `!draft.reciterKey` from the
      redirect-to-`new_video.html` guard now that `draft.reciterKey` is
      never falsy.
- [x] Step 3 — `static/custom_audio.html`: make the `continueBtn` handler's
      `sessionKeyFor(...)` call use the same `fresh.reciter || 'yasser_al_dossary'`
      default `timing.html` now uses, so the two key computations always
      agree and the stashed `customAudioByRealAyah`/`rangeAudioInfo` is
      always found by `loadSavedSession()`.
- [x] Step 4 — `static/timing.html`: make the top-left back button
      entry-aware (`session.entry === 'custom'` → `custom_audio.html`,
      else → `new_video.html`, unchanged for `editOnlyMode` → `/`) instead
      of unconditionally targeting `new_video.html` — currently wired
      before the session is even fetched, using only the URL's
      `sessionId`. Cosmetic/consistency fix, not the reported bug, but same
      root gap (this page assuming reciter-flow-only).
- [x] Step 5 — Verify: `node -c` syntax check on both files' extracted
      inline scripts; re-read the final diffs; confirm the two
      `sessionKeyFor`-equivalent computations produce identical strings for
      a custom-audio session.

## Notes / decisions log

- Steps 1-2 (`static/timing.html`, `init()`): `draft.reciterKey` now
  defaults to `'yasser_al_dossary'` when `session.reciter` is null (custom
  entry), and the redirect guard no longer checks `draft.reciterKey` at
  all — only `surah`/`from`/`to`. Since `draft.reciterKey` is now always a
  real, valid `RECITERS` key, every downstream use (`audioUrl()`,
  `fetchBasmalaSplit()`, `sessionKeyFor()`, the "Reciter — X" fallback
  label in `renderAudioSourceRow()`) keeps working unmodified — none of
  them needed a code change.
- Step 3 (`static/custom_audio.html`, `continueBtn` handler): key
  computation now applies the identical `|| 'yasser_al_dossary'` default,
  so it always matches what `timing.html` computes for the same session.
  Verified by inspection: for a custom-audio session `fresh.reciter` is
  always `null` (never PUT by this page), so both sides now compute
  `[surah, from, to, 'yasser_al_dossary', translation].join('|')`
  identically.
- Step 4 (`static/timing.html`, back button): moved the real wiring into
  `init()` after the session is fetched, so it can branch on
  `session.entry`. The pre-fetch wiring at the top of the script is kept
  only as a non-broken default before that resolves (still targets
  `new_video.html`, matching legacy/reciter-flow sessions, the more common
  case if this ever flashes).
- Step 5 (verify): `node -c` on both files' extracted inline `<script>`
  passes. No backend changes were needed — `app.py`'s `/api/generate` and
  `/api/timing/audio` already defaulted a missing reciter to
  `'yasser_al_dossary'` from the prior refactor; this fix only brings
  `timing.html`'s own guard/key logic in line with that existing backend
  contract.
- **Not verified**: an actual browser click-through (no headless browser
  in this environment — same limitation every prior `*-progress.md` in
  this repo notes). Recommend the user does one real run: open
  `custom_audio.html` fresh, fetch/upload audio, mark at least one ayah,
  hit "Continue to timing", and confirm it lands on `timing.html` with the
  custom recording already loaded (not the reciter's).

**All 5 steps complete.**
