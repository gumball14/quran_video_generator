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

Next up: Phase 3 (custom audio upload + crop), or Phase 4 (frame dividers)
if you'd rather have multi-frame ayahs before custom audio.

## Phase 3 — Custom audio: upload + crop
- [ ] Upload UI (per ayah): file picker, sent to a new backend endpoint
- [ ] Backend: store upload in the job dir, validate it's audio ffmpeg can read
- [ ] Crop handles on the same waveform view (in/out points)
- [ ] Backend crop endpoint (ffmpeg trim) — or just store the offsets and
      let `quran_video.py` trim at render time, avoiding an extra file

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
