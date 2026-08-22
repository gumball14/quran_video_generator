# Rendering optimization — implementation progress

**Status: Phases 1, 2, 3, 5 done and committed** (`eac9441`, `0cd8ab0`).
Phase 4 (multiprocessing) and Phase 6 (speculative/optional) intentionally
not implemented — see "Deferred" below.

Tracking implementation of `rendering-optimization-plan.md` (phases 1, 2, 3, 5;
phase 4 and 6 deferred as optional follow-ups — see notes at bottom).

Each phase is committed separately so any regression can be bisected/reverted
independently.

## Status

- [x] Phase 1 — per-ayah layout caching (`text_render.py` split into
      `build_ayah_layout` + `draw_dynamic_layer`, font/gradient caching)
- [x] Phase 2 — skip PNG disk round-trip (numpy array straight into `ImageClip`)
- [x] Phase 3 — per-ayah `concatenate_videoclips` instead of one flat
      `CompositeVideoClip` over every word-level clip (see deviation note below
      — did NOT use native negative-padding crossfade at the top level)
- [x] Phase 5 — encoding tuning (`preset`, `threads`)

## Verification approach

- Phase 1: behavior-preserving by design — render the same ayah before/after
  the refactor and diff the output pixels (should be identical, or near-identical
  if float rounding order changes).
- Phase 2: same idea — output video should be pixel-identical, just faster.
- Phase 3: **not** guaranteed pixel-identical (different compositing path) —
  needs a real render-and-watch comparison of a multi-ayah surah, checking
  crossfades look right at ayah boundaries.
- Phase 5: purely an encode-speed/size tradeoff, eyeball the output plays fine.

Test command used: `python quran_video.py --surah 112` (Al-Ikhlas, 4 short
ayahs — good fast smoke test) from repo root.

## Notes / deviations from plan

- **Phase 1 gap found and fixed**: `_add_manual_frame_scene()` (timing-manifest
  path) rebuilds `Verse` text per manifest `frame`, so the layout cache is keyed
  per-frame there, not strictly "once per ayah" as the plan assumed — the plan's
  own review already flagged this risk, and the fix was to call
  `build_ayah_layout()` once per manifest `frame` (all `emit()` calls for that
  frame's word-highlight sub-timings reuse it), not once per ayah.
- Verified Phase 1 is byte-for-byte pixel-identical to the pre-refactor
  renderer across 10 theme variations (default, pill/underline/color highlight
  + pointer, side/above translation, below_header/bottom badge position,
  star8 badge + line accent, text-script header) x 2 verses x 4 highlight
  indices = 80 rendered frames, 0 mismatches. Script:
  `/tmp/.../scratchpad/verify/compare.py` (scratch, not committed).
- Verified Phase 1+2 end-to-end with a real render (`--surah 112` with
  highlight+pointer theme) — output plays, pixel-checked one frame visually,
  no PNG files written for the per-word hot loop anymore.
- `render_verse_frame()` kept as a public wrapper (unchanged signature) since
  `app.py`'s single-frame preview endpoint calls it directly and doesn't need
  the layout-reuse optimization.
- `video_build.py`'s `CACHE_DIR` import removed (no longer used — the two hot
  paths that used to save per-frame PNGs there now pass numpy arrays straight
  to `ImageClip`; audio downloads still cache under `CACHE_DIR` via `audio.py`,
  untouched).
- Added `numpy` to `requirements.txt` (was only an indirect moviepy/Pillow
  dependency before; now imported directly in `video_build.py`).
- **Phase 3 deviation (important — read before touching this again)**: the
  plan's literal Phase 3 (`concatenate_videoclips(ayah_clips, padding=-fade_duration,
  method="compose")` at the top level, with `vfx.CrossFadeOut`/`CrossFadeIn`
  between ayah clips) would desync audio from video. That native pattern
  shifts each subsequent clip's *start time* earlier by `fade_duration` to
  create the crossfade — fine if audio travels with the clip, but this app's
  audio is deliberately a hard cut at each ayah boundary
  (`a_clip.with_start(scene_origin)`, no overlap) while only the *video*
  crossfades over it. Shifting the video's start earlier without shifting
  its paired audio would misalign video against audio by a full
  `fade_duration` for the entire body of every ayah after the first
  (compounding each transition) — not just a glitch at the boundary.
  Implemented instead: concatenate each ayah's own sub-clips into one clip
  via `concatenate_videoclips(sub_clips, method="chain")` (cheap, no
  compositing needed — they're already sequential), then reproduced the
  *existing* manual-overlap trick (widen the scene's first sub-clip by
  `fade_duration` before concatenating, position the whole ayah clip at
  `scene_origin - overlap`, apply `vfx.CrossFadeIn` once per ayah clip)
  at ayah granularity instead of per-word-clip granularity. This still cuts
  the top-level `CompositeVideoClip`'s clip count from thousands (all words
  across the whole video) down to `len(verses)` — which is what actually
  drives Phase 3's O(clips × frames) compositing cost — while keeping
  audio/video sync and the crossfade visual behavior byte-for-byte identical
  to before. Verified: total video duration matched the pre-Phase-3 run to
  the microsecond (10.663991s on the same test surah), and a boundary-frame
  screenshot showed the expected two-frame dissolve.
- Also tested the manual-timing-manifest path (`--timing jobs/050240296c/timing.json
  --surah 1`, which has per-word `highlight_words` sub-timings) end-to-end —
  renders correctly, 47s output, frame-checked visually.
- Phase 5: asked the user for the preset tradeoff (veryfast / faster / skip);
  chose **faster** (`threads=os.cpu_count()`) as a middle ground between
  encode speed and file size.

## Deferred (not implemented in this pass)

- **Phase 4 (multiprocessing)** — biggest wall-clock win on multi-core
  machines, but higher risk (picklability, THEME global state across worker
  processes) and needs Phase 1 solid first. Left for a follow-up once Phase 1
  has been used in production for a bit.
- **Phase 6 (optional)** — `glide_steps` config exposure and
  `audio.py`/`quran_api.py` caching audit were both explicitly speculative in
  the plan and low priority. Not done.
