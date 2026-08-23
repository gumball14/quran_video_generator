# Manual timing + generation wizard — plan

Goal: replace the "pick surah, pick reciter, generate" flow with a wizard
that lets you pick surah/ayah range → pick or upload+crop a reciter's audio
→ set real word/frame timing yourself (waveform editor) → pick a theme →
generate. This fixes the word-highlight sync problem (see prior discussion:
the current highlight is evenly-paced by `highlight_fallback_wps`, not
tied to the actual recitation) and adds the ability to define custom
frames (a frame = a time range + whatever Arabic text you want shown
during it, not necessarily one word).

Status legend: [ ] todo · [~] in progress · [x] done

## Phase 1 — Foundation: timing manifest + backend support  [DONE]
- [x] Define the timing manifest JSON schema (frames per ayah: start/end,
      displayed text, optional per-word highlight timings) — documented in
      `quran_lib/timing.py`
- [x] `quran_lib/timing.py` — load/validate a timing manifest
      (`TimingManifestError` with specific, per-field messages)
- [x] `quran_lib/video_build.py` — `_add_manual_frame_scene()` renders from
      explicit timings when a manifest supplies frames for an ayah; audio is
      trimmed to the marked window (`frames[0].start` .. `frames[-1].end`),
      so unmarked lead-in/trailing silence is cut rather than mis-synced.
      No manifest / no entry for an ayah = today's automatic pacing, unchanged.
- [x] `quran_video.py` — new `--timing` CLI flag (path to manifest json)
- [x] `app.py` — accepts `timing` in the `/api/generate` payload, writes it
      to the job dir, passes `--timing` through to the subprocess
- [x] Tested: manifest validation (valid + 5 invalid cases), a full mocked
      build with no manifest (regression, duration unchanged), and a full
      mocked build with manual frames including unmarked leading/trailing
      silence (confirmed trimmed correctly, highlight renders on the right word)

Next up: Phase 2 (waveform + manual word-timing editor UI) — this is what
actually produces a timing manifest interactively instead of hand-writing
JSON.

## Phase 2 — Waveform + manual word-timing editor (UI)  [DONE]
- [x] Waveform renderer: decode ayah audio client-side (Web Audio API) and
      draw amplitude on a `<canvas>` — `static/timing-editor.js`
- [x] Word chip list under the waveform (ayah's Arabic text split on words,
      same `.split(" ")` convention as `render_verse_frame`, RTL-ordered
      visually via CSS `direction:rtl`)
- [x] Click-to-drop a boundary marker on the waveform; drag to adjust
      (drag is clamped between neighboring markers / frame start-end)
- [x] Assign markers to words (auto-advances to the next unmarked word;
      clicking a chip re-arms that word if you want to fix it out of order)
- [x] Playback scrubber (`<audio>` element + a moving playhead synced via
      `timeupdate`) so you can listen while placing markers
- [x] "Add to manifest" writes the Phase 1 manifest format, accumulating
      across ayahs in `localStorage`; download/copy the finished `timing.json`
- [x] New backend endpoints (`app.py`): `/api/timing/text` and
      `/api/timing/audio` — the latter reuses `split_basmala_audio()`
      directly, so an ayah-1 Bismillah split in the editor is *exactly*
      the same split `build_video()` will use at generation time
- [x] Generate page (`index.html`/`app.js`) gets a `timing.json` drop zone
      alongside the existing `theme.json` one, wired into `/api/generate`
- [x] Tested with real Playwright browser automation (not just code
      review): load w/ Bismillah split, sequential + out-of-order marker
      placement, drag-ordering constraints, frame-end clamping, multi-ayah
      manifest accumulation, row delete, `localStorage` persistence across
      reload, clear-manifest, and the generate-page file attach/clear flow.
      The UI-produced manifest was also round-tripped through the real
      `quran_lib/timing.py` validator and passed.

Known limitation to revisit later: each "Add to manifest" click writes
*one* frame spanning the whole loaded audio (with per-word highlight
sub-timings inside it) — splitting one ayah's audio into several frames
with independently-editable text is Phase 4 (frame dividers), not yet built.

### Post-Phase-2 fixes
- [x] **Bug fix**: `build_video()` was showing Bismillah text whenever an
      ayah's *script* has it prepended (a Quran orthography convention,
      true for nearly every surah's ayah 1 regardless of what was actually
      recited), even when the audio-based silence-split failed to find a
      spoken Bismillah at all. A failed split is evidence there's nothing
      to show, not evidence it's just hard to isolate -- fixed the fallback
      in `quran_lib/video_build.py` to show only the ayah in that case.
      Tested both paths (failed split → ayah only; successful split →
      both scenes, unchanged).
- [x] **Editor simplified**: replaced mouse-precision waveform
      clicking/dragging with a button-driven flow -- play/pause, step
      ±0.25s/±1s, restart, and a big "Mark current position" button that
      captures the playhead time for the currently-armed word and
      auto-advances. Clicking the waveform now only seeks/pauses (doesn't
      place a marker); clicking a word chip re-arms it if you need to redo
      one. Added zoom (1×–8×, horizontally scrollable, auto-scrolls to
      follow the playhead during playback). Re-tested the full flow with
      Playwright, including a real flexbox `min-width` bug the zoom feature
      exposed (a scroll container was growing to fit its zoomed content
      instead of clipping it -- fixed with `min-width:0` on the grid/flex
      ancestors).

Next up: Phase 3 is now done (below) -- Phase 4 (frame dividers) is next.

## Phase 3 — Custom audio: upload + crop  [DONE]

Goal: let a specific ayah (or every ayah in the range) use a user-uploaded
recording instead of the everyayah.com download, with an in/out crop, while
changing as little as possible about how the rest of the pipeline works.
Reuses the same shape Phase 1 used for timing: a small, additive, optional
manifest (`quran_lib/audio_sources.py`, mirroring `quran_lib/timing.py`)
that `video_build.py` consults before falling back to today's
`download_ayah_audio()`/range-audio pipeline, unchanged for anyone who
doesn't use it. Crop is stored as (start, end) offsets only — never a
second physical cut — resolved to a real trimmed file lazily at render
time, same lazy-cache pattern as `get_trimmed_ayah_audio()`.

**Coordinate system** (per the Notes section below, already decided):
crop happens logically *before* Phase 2's word-timing markers, so a
manifest's frame `start`/`end` times for a custom-audio ayah are always
relative to the *cropped* window, not the raw upload. Concretely: the
editor must apply crop start/end before it starts marker placement (or, if
the ayah already has markers, re-anchor them if the crop bounds move) —
tracked explicitly below so this isn't discovered as a bug later.

Status legend: [ ] todo · [~] in progress · [x] done

### 3a — Manifest schema + storage  [DONE]
- [x] Design the audio-source manifest schema: `{"ayahs": {"<n>": {"filename": "...", "crop_start": 0.0, "crop_end": 12.4}}}`, keyed by ayah number exactly like the timing manifest (ayah "0" = Basmala scene shares ayah 1's uploaded file, same convention as `timing.py`)
- [x] `quran_lib/audio_sources.py`: `load_audio_source_manifest(path)` + `AudioSourceManifestError`, validation mirroring `timing.py`'s `_validate_ayah_entry` (required fields, `crop_end > crop_start`, filename restricted to a safe single-segment pattern — rejects path traversal / slashes)
- [x] `quran_lib/audio_sources.py`: `get_ayah_audio_source(manifest, ayah_number)` — returns the entry (with `crop_start`/`crop_end` defaulted) or `None`, same contract as `timing.get_ayah_frames()`
- [x] Decide + document the upload directory layout: `uploads/<session_id>/<ayah>.<ext>` — added `UPLOADS_DIR` to `app.py` alongside `JOBS_DIR`/`SESSIONS_DIR`
- [x] Tested: valid manifest + 9 invalid cases (missing "ayahs" key, non-dict "ayahs", non-numeric ayah key, missing filename, path-traversal filename, filename with slash, negative crop_start, crop_end <= crop_start, non-numeric crop_end) — all raise/pass as expected

### 3b — Backend: upload endpoint + validation  [DONE]
- [x] `app.py`: `POST /api/timing/upload-audio` — multipart file + `sessionId`/`ayah`; creates `uploads/<session_id>/` on demand (`surah` isn't actually needed server-side — the file is keyed by ayah number only, same as the manifest schema)
- [x] Validate the upload is audio ffmpeg can actually read before accepting it: runs `ffprobe` via `get_audio_duration()` on the saved file, deletes + 400s on failure rather than leaving a dead file behind
- [x] Cap upload size (`app.config["MAX_CONTENT_LENGTH"] = 25MB`, with a JSON 413 handler instead of Flask's default HTML page) and restrict accepted extensions (`.mp3`/`.wav`/`.m4a`/`.ogg`) before ffprobe even runs
- [x] Response includes the resolved duration (seconds) so the frontend doesn't need a second round-trip to size the waveform
- [x] `app.py`: `DELETE /api/timing/upload-audio` so a user can revert an ayah back to the reciter download
- [x] Tested live against a running server: valid upload (correct duration returned), unknown/invalid sessionId rejected, disallowed extension rejected, ffprobe-invalid content rejected *and cleaned up from disk*, oversized file rejected before being written, delete removes the file

### 3c — Backend: serving uploaded audio to the existing editor flow  [DONE]
- [x] Extend `/api/timing/audio` (and `/api/timing/basmala-split`) via a shared `_resolve_ayah_audio()` helper that checks `uploads/<session_id>/<ayah>.*` first (when a `sessionId` query param is present) and serves/analyzes that instead of calling `download_ayah_audio()` — keeps `timing.html`'s existing `audioUrl()` working unchanged for both sources, it just needs to start passing `sessionId`. No separate crop-storage endpoint needed: `cropStart`/`cropEnd` are just extra query params on the same request, resolved fresh (and cached) on every call via `get_custom_ayah_audio()` — the editor doesn't need the backend to remember a crop between requests, only the final generate-time manifest (3g) does.
- [x] Serve the *cropped* window, not the raw upload, once `cropStart`/`cropEnd` are passed (ffmpeg trim into a cached file next to the upload via `get_custom_ayah_audio()`), so the waveform/marker UI always sees the same timeline `video_build.py` will render from
- [x] Tested live: `/api/timing/audio` with no `sessionId` still downloads from everyayah.com unchanged; with `sessionId` + an upload but no crop, serves the raw upload (correct duration, correct mimetype); with crop params, serves the exactly-trimmed, cached window; `/api/timing/basmala-split` correctly routes through the same resolver

### 3d — `video_build.py` / CLI integration  [DONE]
- [x] `quran_lib/audio.py`: `get_custom_ayah_audio(path, crop_start, crop_end)` — ffmpeg-trim-and-cache, modeled directly on `get_trimmed_ayah_audio()` (WAV output, same "re-encoding to WAV avoids mp3 encoder priming delay" reasoning applies here too). Cache filename encodes the crop bounds so re-cropping during editing doesn't serve a stale cut, and is still named so `app.py`'s upload-delete glob cleans it up. Tested: no-op crop returns the original path unchanged, a real crop trims to the exact requested duration, repeated calls with the same bounds reuse the cached file, and different bounds produce a distinct cache file.
- [x] `video_build.py`: `build_video()` gains `audio_source_manifest`/`custom_audio_dir` params; resolved once per ayah via `get_ayah_audio_source()` before the manual-frames/range-audio/fallback branching. A custom source wins over BOTH the manual-frames branch (subclips the cropped file instead of downloading, per the coordinate-system rule) and a new dedicated branch for "custom audio, no manual timing" (automatic per-word pacing, sourced from the cropped file, no extra silence-trim on top since the crop is the user's exact intended window)
- [x] Confirmed interaction with the range-audio hybrid path (`RANGE_AUDIO_SOURCES`): the custom-source check sits in the `if/elif` chain *before* the range-boundaries check, so a custom-audio ayah skips range-audio subclipping entirely for that ayah, even when the reciter has range audio for the rest of the surah. Tested live on surah 112 with `yasser_al_dossary` (range-audio-capable): ayah 2 given a custom-audio override rendered "from uploaded audio" while ayahs 1/3/4 still rendered "from the continuous surah audio" unaffected
- [x] `quran_video.py`: new `--custom-audio` CLI flag (path to the manifest json), loaded via `load_audio_source_manifest()` the same way `--timing` loads `load_timing_manifest()`; filenames resolve relative to the manifest file's own directory (`custom_audio_dir = Path(args.custom_audio).resolve().parent`)
- [x] Gap/pacing decision made and implemented: a custom-audio ayah with no manual frames does NOT set `used_range_audio = True`, so it falls through to the existing `gap = _MIN_INTER_AYAH_GAP if not (manual_frames or used_range_audio) else 0.0` line unchanged — the synthetic gap applies, same as the per-ayah fallback path, per [[feedback-range-audio-no-synthetic-gap]]'s reasoning (no guaranteed-contiguous neighbor the way range-audio slices have)
- [x] Tested end-to-end with real `quran_video.py` runs (not mocked): a synthetic 440Hz-tone upload cropped to a 2s window rendered correctly in place of ayah 1's real recitation (confirmed via zero-crossing-rate analysis of the exported .mp4's audio track — 1926 crossings in 2.2s vs. 1936 expected for a pure 440Hz tone) while ayahs 2-4 used their normal downloaded audio

### 3e — Frontend: upload UI (`static/timing.html`)  [DONE]
(Note: there is no separate `timing-editor.js` -- all of Phase 2/3's editor JS lives inline in `static/timing.html`; the plan's earlier reference to a separate file was aspirational, not real.)
- [x] Per-ayah "Use my own audio" control in a new `.audio-source-row` above the waveform, showing either "Reciter — <name>" or "Your recording — X.Xs" with Crop/Remove actions; upload goes through a hidden `<input type="file">` to `/api/timing/upload-audio`
- [x] Loading/error states: an inline `.as-status` line shows "Uploading…"/"Removing…" and surfaces the backend's error message (e.g. the ffprobe-rejection 400) in place; Remove asks for confirmation (also clears markers) before calling `DELETE /api/timing/upload-audio`
- [x] Persisted in the same `localStorage` blob Phase 2 already writes (`persistSession()`/`loadSavedSession()`, same `sessionKeyFor()` gating), as `customAudioByRealAyah` keyed by real ayah number (not entry index, since a Bismillah/ayah-1 pair shares one upload)
- [x] Tested with real Playwright automation: upload via a real file-chooser interaction, confirmed the row updates to "Your recording", confirmed state survives a full page reload, confirmed Remove reverts the row back to "Reciter"

### 3f — Frontend: crop UI  [DONE]
(No `<canvas>` waveform exists anywhere in this app -- Phase 2's waveform is CSS flexbox bars (`.wave-bar` divs) with absolutely-positioned overlay markers, not canvas. The crop UI follows the same pattern: a separate modal (`#cropOverlay`) with its own bar-waveform and two draggable `.crop-handle` divs, reusing a generalized `buildBarsFromBuffer()` that now takes a target container/class instead of always writing to the main waveform.)
- [x] Crop modal always redraws from the RAW (uncropped) upload, never a previously-cropped window, so re-cropping isn't lossy; two pointer-draggable handles (clamped to a 0.2s minimum window and to each other) define the new crop window, applied via a "Set crop"-equivalent action inside `cropApplyBtn`'s click handler (no separate backend "set crop" endpoint was needed -- see 3c's decision that crop is just query params on the existing audio-serving endpoints)
- [x] Coordinate-system rule enforced by a different (and arguably more robust) mechanism than a hard "crop before marking" gate: marking is allowed immediately after upload (against the full file, the implicit "no crop yet" window), and applying or changing a crop always invalidates/clears that ayah's markers via `invalidateEntriesForRealAyah()` -- so markers can never end up silently stale against a since-moved crop window
- [x] **Bug found and fixed during testing**: uploading/cropping custom audio for an ayah that had a pre-existing Bismillah/ayah-1 split (detected against the *original* reciter audio) left stale `rangeStart`/`rangeEnd` values once the underlying audio changed -- e.g. showing a 1-2s slice of the new upload instead of the whole thing. Fixed by collapsing the split pair into one "full" entry the first time custom audio replaces that ayah (documented as a one-way editor-UI simplification in `invalidateEntriesForRealAyah()`; has no effect on generation itself unless that ayah is hand-marked, since automatic pacing re-detects the Bismillah split fresh at render time regardless of what the editor shows)
- [x] **Bug found and fixed during testing**: `persistSession()` was being called *before* `loadAyah()` in the upload/remove/crop-apply handlers, so `saveCurrentAyahState()` re-saved the stale (pre-invalidation) in-memory `markerList` right back over the null `ayahStates` entry that was supposed to clear it. Fixed by reordering all three handlers to call `loadAyah()` (which resets `markerList` from `ayahStates`) before `persistSession()`.
- [x] **Bug found and fixed during testing**: `renderAudioSourceRow()`'s first call inside `loadAyah()` ran before `entry.duration` was computed, so the row displayed the raw upload's duration instead of the post-crop duration. Fixed by adding a second call after `entry.duration` is set.
- [x] Tested with real Playwright automation, including dragging the crop handles with actual mouse events (not just calling internal functions): verified the displayed duration reflects the crop exactly, verified markers are cleared on crop-apply, verified persistence across reload, and ran a **full real generate** through the actual UI (upload → crop → mark all words → click Generate → job completes) -- confirmed via the job log that it rendered "using uploaded audio" with manual frame timing exactly matching the cropped window (`0.0s-3.2s of 3.2s audio`) and highlight-word timings all falling inside `[0, 3.2]`, i.e. correctly relative to the cropped window per the coordinate-system rule

### 3g — Session + generate wiring (mirrors how `timing` already flows end to end)  [DONE]
- [x] `app.py`: session dict gains a `customAudio` field (the manifest dict, or `None`), added to `SESSION_FIELDS` — saved/loaded exactly like `theme`/`timing`
- [x] `/api/generate`: accepts `customAudio` in the payload; when present, copies every upload it references from `uploads/<session_id>/` into a fresh `job_dir/custom_audio/` (a self-contained per-job copy, not a live reach-back into the session's upload dir), writes the manifest to `job_dir/custom_audio/manifest.json`, and passes `--custom-audio <path>` to the subprocess — same shape as the existing `timing`/`theme` handling. A referenced file that's gone missing is silently skipped at copy time; `quran_video.py`'s own ffmpeg call fails loudly on it instead, same as any other bad manifest entry.
- [x] Decided: `uploads/<session_id>/` is kept after a successful generate, not cleaned up — re-generating the same session with tweaked timing/theme shouldn't require re-uploading. (No automatic cleanup of old sessions' uploads exists yet at all, same as there's currently none for old sessions/jobs in general — out of scope for Phase 3.)
- [x] Tested end-to-end over real HTTP: created a session, uploaded a synthetic audio file, called `/api/generate` with a `customAudio` manifest referencing it, confirmed the job dir got a self-contained `custom_audio/` copy (upload + manifest + the lazily-generated crop cache), polled the job to completion, and confirmed the log shows "Ayah 1: rendering frame (1.8s, from uploaded audio)…" — exactly the requested crop window (2.0s - 0.2s)

### 3h — Testing
### 3h — Testing  [DONE]
(Each item below was already exercised live while building 3a-3g rather than deferred to the end — cross-referenced here instead of repeated.)
- [x] `quran_lib/audio_sources.py` manifest validation: valid case + 9 invalid cases (missing "ayahs" key, non-dict "ayahs", non-numeric ayah key, missing filename, path-traversal filename, filename with slash, negative crop_start, `crop_end <= crop_start`, non-numeric crop_end) — see 3a
- [x] `get_custom_ayah_audio()`: no-op crop returns the original path, a real crop trims to the exact requested duration, repeated calls reuse the cached file, different bounds produce distinct cache files; non-audio input is rejected by the upload endpoint's ffprobe check before ever reaching this function (verified: bad content with a `.mp3` extension was rejected and the dead file cleaned up) — see 3b/3d
- [x] Full **real** (not mocked) `quran_video.py` run with a custom-audio ayah mixed into a range-audio-capable reciter's surah (`yasser_al_dossary`, surah 112): confirmed via console output that ayah 2's custom override rendered "from uploaded audio" while ayahs 1/3/4 still used "the continuous surah audio" unaffected — see 3d
- [x] Real Playwright pass on the actual editor (not just internal function calls): uploaded a file via a real file-chooser interaction, dragged the crop handles with real mouse events, placed word markers, verified persistence across a full page reload, removed the upload, and — going further than the plan asked — ran a complete real generate through the UI end to end (upload → crop → mark all words → click Generate → job completes), confirming via the job log that the rendered manifest's frame/highlight timings landed exactly inside the cropped window. Two real bugs (marker-clearing order, stale Bismillah-split bounds after upload) were found and fixed this way — see 3f.

## Phase 3.1 — One recording, split into ayahs  [DONE]

Follow-up to Phase 3, requested directly: a user has ONE continuous
recording (e.g. downloaded from elsewhere) covering multiple ayahs, and
wants to define the ayah boundaries themselves rather than uploading a
separate file per ayah. Turned out to need almost no new backend surface:
`audio_sources.py`'s manifest already lets independent ayahs share one
`filename` with different `crop_start`/`crop_end` windows -- nothing in
`video_build.py`/`get_custom_ayah_audio()` assumed one-file-per-ayah, so
the whole feature is really "one upload endpoint for a file not owned by
any single ayah" + "a frontend UI to define N-1 split points" on top of
the Phase 3 machinery already built. The existing per-ayah upload/crop
still works unchanged and takes priority if used on top of a split (see
below).

- [x] `app.py`: `POST`/`DELETE /api/timing/upload-range-audio` -- same
      validation as the per-ayah endpoint (extracted into a shared
      `_save_uploaded_audio()` helper), stores `uploads/<session_id>/range.<ext>`
- [x] `app.py`: `_resolve_ayah_audio()` gained an explicit `customFilename`
      query param that addresses an upload directly by name instead of the
      `<ayah>.<ext>` glob convention -- this is what lets several ayahs'
      `/api/timing/audio` requests all resolve to the SAME shared file
      (falls back to the glob when omitted, so the original per-ayah flow
      is unaffected)
- [x] `static/timing.html`: a banner above the waveform offers "Upload &
      split" when the range has 2+ ayahs; uploading opens a split modal
      (reusing the crop modal's visual style) with N-1 draggable boundary
      handles over the full waveform, labeled per ayah, defaulting to even
      spacing. Applying writes one `customAudioByRealAyah` entry per ayah
      in the range (same shape a per-ayah crop already produces, just
      sharing one `filename`), then the existing per-ayah word-marking flow
      proceeds completely unchanged
- [x] Per-ayah "Crop"/"Remove" on an individual ayah's row still work on a
      range-derived slice (fine-tune or detach one ayah without redoing the
      whole split); the audio-source label distinguishes "From full
      recording" vs. "Your recording" so it's clear which is which
- [x] Tested with real Playwright automation: uploaded a 10s synthetic
      recording across a 4-ayah range, confirmed default even splits (2.5s
      each, matching 3 draggable handles for 4 ayahs), dragged a boundary
      and confirmed the affected ayah's duration updated exactly, confirmed
      persistence across reload, and ran a **complete real generate**
      through the UI (upload → apply default splits → mark every word
      across all 4 ayahs → Generate → job completes)
- [x] **Major pre-existing bug found and fixed** (unrelated to Phase 3/3.1
      specifically -- see the dedicated note in "Notes / decisions" below):
      any video with two or more ayahs using per-word timing (manual OR
      automatic `highlight_enabled`) crashed moviepy with
      `IndexError: list index out of range` during final render. This is
      exactly the scenario Phase 3.1's own workflow produces (multiple
      manually-marked ayahs back to back), so it had to be fixed here to
      deliver a working feature -- fixed in `quran_lib/video_build.py`.

## Phase 4 — Frame dividers with custom text
- [ ] "Split here" on the timing editor to add a divider (new frame boundary)
- [ ] Per-frame editable text box — defaults to the word span it covers,
      but can be freely overwritten (e.g. combine words, show partial text)
- [ ] Multi-word-per-frame grouping (a frame isn't forced to be one word)

## Phase 5 — Wizard flow
- [ ] Step 1: surah + ayah range (reuse existing controls from index.html)
- [ ] Step 2: reciter dropdown or upload
- [ ] Step 3: timing/frame editor (Phases 2–4)
- [ ] Step 4: theme picker (reuse theme_editor.html as-is)
- [ ] Step 5: review + generate

## Notes / decisions
- Manifest is additive, not a replacement — a job with no manifest (or an
  ayah missing from it) generates exactly as it does today.
- Frame boundary times are stored in seconds relative to the ayah's audio
  (post-crop if cropped), so Phase 3's crop and Phase 2's markers share one
  coordinate system with no extra translation needed.
- **Pre-existing cross-fade bug (found + fixed during Phase 3.1), affects
  Phase 2 too, not just custom audio**: `build_video()`'s ayah-to-ayah
  cross-fade used to extend the previous ayah's clip via plain
  `prev_clip.with_duration(prev_clip.duration + fade_duration)`. That's only
  safe when `prev_clip` is a single `ImageClip` (its `frame_function` ignores
  `t`, so "faking" extra duration costs nothing). But whenever an ayah's
  scene has more than one sub-clip -- `highlight_enabled`'s automatic
  per-word pacing, OR manual per-word timing from a timing manifest -- the
  scene is `concatenate_videoclips(sub_clips, method="chain")`, and chain's
  `frame_function` bisects a `timings` array computed ONCE from the
  sub-clips' real durations. `with_duration()` only relabels the outer
  `.duration`; it adds no actual frames, so any request in the newly-claimed
  `fade_duration` window falls past the end of that array and moviepy raises
  `IndexError: list index out of range` deep in its own compositing code.
  Reproduced with nothing but two-or-more ayahs of manual (or automatic)
  per-word timing rendered back to back -- no custom audio needed at all;
  this could have hit plain Phase 2 usage (mark 2+ ayahs, generate) the whole
  time. **Fix**: track whether each appended clip came from
  `concatenate_videoclips` (`clip_is_chained`, parallel to `clips`); when
  extending a chained clip for the cross-fade, use `vfx.Freeze(t=..., total_duration=...)`
  instead, which rebuilds the clip as real `[before] + [frozen last frame]`
  parts and re-concatenates (so its own `timings` array is computed fresh
  and genuinely covers the full requested duration) -- `vfx.Freeze`'s
  `t="end"` shorthand needs `clip.fps`, which a concatenated `ImageClip`
  chain doesn't carry, so the freeze point is computed directly against the
  module's `FPS` constant instead. A plain (non-chained) `ImageClip` scene
  keeps the cheap `with_duration()` path (it's provably safe, and by far the
  common case when highlighting is off) -- don't switch it to `Freeze`
  unconditionally, that regressed render time in testing (extra
  slice/reconcatenate work) for zero correctness benefit on that path.
