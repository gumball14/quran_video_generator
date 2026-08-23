"""
Video assembly: turns fetched verses + downloaded audio into the final
.mp4, including the optional per-word highlight/pointer animation.
"""
import os
import sys

import numpy as np

try:
    from moviepy import (
        ImageClip,
        AudioFileClip,
        CompositeVideoClip,
        CompositeAudioClip,
        concatenate_videoclips,
        vfx,
    )
except ImportError:
    print("Missing dependency. Run: pip install moviepy --break-system-packages")
    sys.exit(1)

from .constants import OUTPUT_DIR, FPS
from . import theme as theme_mod
from .quran_api import Verse
from .audio import (
    download_ayah_audio,
    download_surah_audio,
    get_audio_duration,
    get_custom_ayah_audio,
    get_surah_audio_for_subclipping,
    get_surah_ayah_boundaries,
    get_trimmed_ayah_audio,
)
from .text_render import render_verse_frame, build_ayah_layout, draw_dynamic_layer, render_background
from .timing import get_ayah_frames
from .audio_sources import get_ayah_audio_source


def _pointer_glide_steps(seg_dur):
    """How many sub-frames to split a pointer glide of this length into --
    roughly one per actual video frame (at the export FPS), so the motion
    reads as smooth continuous movement instead of a handful of visibly
    discrete jumps."""
    return max(2, round(seg_dur * FPS))


# Fraction of a word's segment the pointer spends resting on it (matching
# the highlight, which sits on that word for the whole segment) before it
# starts moving on to the next word. Gliding across the FULL segment instead
# (starting to move the instant the word becomes highlighted) made the
# pointer visibly drift ahead of the still-highlighted word for most of its
# duration -- it kept arriving "on time" at the boundary, but spent the
# whole segment looking like it was racing ahead of the highlight rather
# than moving at the same pace as it. Resting first and only gliding near
# the end keeps the pointer sitting with the current word (like the
# highlight does) and confines the motion to a short, still-smooth window
# that still lands exactly on the next word as the segment ends.
_POINTER_HOLD_FRAC = 0.6

# Deliberate silence inserted between two ayahs' own (fully-trimmed) audio.
# Cutting the gap to exactly 0s removed the risk of a long stacked pause, but
# also removed the ear's only cue that a new verse has started -- Quranic
# ayat often begin with a short, common word (e.g. several oath verses in a
# row starting with "wa" -- "and"), and with zero separation that word lands
# right on the previous ayah's tail and reads as a stutter/repeat rather than
# a new verse beginning. This restores just enough of a pause for that
# without reintroducing the original too-long-gap problem.
_MIN_INTER_AYAH_GAP = 0.15


def _glide_positions(cur_pos, next_pos, seg_dur):
    """Yields (pointer_pos, step_dur) pairs: the pointer rests on cur_pos for
    the first _POINTER_HOLD_FRAC of seg_dur (matching the highlight, which
    holds on the current word for the same span), then linearly glides to
    next_pos over the rest, landing there exactly as seg_dur ends -- in sync
    with, not ahead of, the highlight moving on to that word. The glide
    itself is split into ~1 sub-frame per actual video frame (FPS-based) so
    it still reads as smooth continuous movement, not a jump. Linear (not
    eased) on purpose: an eased curve decelerates to zero speed at both
    endpoints, which reads as an extra little stop/start on top of the
    resting hold. Speed is distance / glide_dur, so a short word-to-word gap
    glides fast and a long one glides slowly -- "speed based on the time gap
    between words"."""
    hold_dur = seg_dur * _POINTER_HOLD_FRAC
    if hold_dur > 0:
        yield cur_pos, hold_dur

    glide_dur = seg_dur - hold_dur
    if glide_dur <= 0:
        return
    steps = _pointer_glide_steps(glide_dur)
    step_dur = glide_dur / steps
    for step in range(1, steps + 1):
        frac = step / steps
        pos = {
            "x": cur_pos["x"] + (next_pos["x"] - cur_pos["x"]) * frac,
            "top": cur_pos["top"] + (next_pos["top"] - cur_pos["top"]) * frac,
            "font_size": cur_pos["font_size"] + (next_pos["font_size"] - cur_pos["font_size"]) * frac,
        }
        yield pos, step_dur


def _add_word_highlighted_scene(render_verse, surah_name_arabic, surah_name_text, size, show_translation, duration):
    """Renders one scene as a sequence of short ImageClips, switching the
    highlighted word (and, if enabled, gliding the pointer to it) evenly
    across the scene's REAL audio duration -- not the editor's simulated
    preview pace, which only exists because the browser has no audio to
    time against.

    Returns (sub_clips, duration): sub_clips is an ordered list of clips
    with no absolute position set (just durations) that sum to exactly
    `duration` seconds -- the caller (build_video) concatenates them into
    one per-ayah clip and positions/crossfades that as a whole, instead of
    every word-level clip being placed individually into the top-level
    composite."""
    THEME = theme_mod.THEME
    words = [wd for wd in render_verse.arabic.split(" ") if wd]
    n = max(1, len(words))

    # respect the theme's words/sec pace, but never overrun the ayah's actual audio length
    wps = max(0.4, THEME["highlight_fallback_wps"])
    word_dur = min(1.0 / wps, duration / n)

    base_img, layout = build_ayah_layout(render_verse, surah_name_arabic, size, show_translation,
                                          surah_name_text=surah_name_text)
    word_boxes = layout["word_boxes"]

    pointer_on = THEME["highlight_pointer_enabled"]

    sub_clips = []

    def emit(highlight_idx, pointer_pos, dur):
        if dur <= 0:
            return
        frame_img = draw_dynamic_layer(base_img, layout, highlight_idx, pointer_pos)
        sub_clips.append(ImageClip(np.array(frame_img)).with_duration(dur))

    t = 0.0
    for idx in range(n):
        is_last = idx == n - 1
        seg_dur = (duration - t) if is_last else word_dur
        if seg_dur <= 0:
            continue
        cur_box = word_boxes[idx]
        next_box = word_boxes[idx + 1] if idx + 1 < n else None

        if not pointer_on:
            emit(idx, None, seg_dur)
            t += seg_dur
            continue

        cur_pos = {"x": cur_box["cx"], "top": cur_box["top"], "font_size": cur_box["font_size"]}
        if next_box is None:  # last word -- nowhere left to glide to
            emit(idx, cur_pos, seg_dur)
            t += seg_dur
            continue

        next_pos = {"x": next_box["cx"], "top": next_box["top"], "font_size": next_box["font_size"]}
        for pointer_pos, step_dur in _glide_positions(cur_pos, next_pos, seg_dur):
            emit(idx, pointer_pos, step_dur)
            t += step_dur

    return sub_clips, duration


def _add_manual_frame_scene(frames, surah_name_arabic, surah_name_text, size, show_translation,
                             fallback_translation, verse_number_for_filenames):
    """Renders one scene from an explicit list of timing-manifest frames
    (see quran_lib/timing.py) instead of the automatic wps pacing. Each
    frame gets its own text and, optionally, its own per-word highlight
    sub-timings. Frames don't have to start at the audio's local time 0 or
    run to its end (e.g. you might only mark the middle of a long ayah) --
    the caller is responsible for trimming the *audio* clip to match
    (frames[0]["start"] .. frames[-1]["end"]), since this function only
    ever produces the video side of the scene.

    Returns (sub_clips, duration), exactly like _add_word_highlighted_scene()
    -- duration is frames[-1]["end"] - frames[0]["start"]."""
    THEME = theme_mod.THEME
    pointer_on = THEME["highlight_pointer_enabled"]
    sub_clips = []

    def box_pos(layout, idx):
        box = layout["word_boxes"][idx]
        return {"x": box["cx"], "top": box["top"], "font_size": box["font_size"]}

    def emit(base_img, layout, highlight_idx, pointer_pos, dur):
        if dur <= 0:
            return
        frame_img = draw_dynamic_layer(base_img, layout, highlight_idx, pointer_pos)
        sub_clips.append(ImageClip(np.array(frame_img)).with_duration(dur))

    def emit_word(base_img, layout, idx, seg_dur, next_pos):
        """Emits one highlighted word's segment. If the pointer is on and a
        next position is known (the next highlighted word, whether in this
        frame or the next one), continuously glides from the current word to
        it across the WHOLE segment (see _glide_positions() -- no static
        hold, speed is purely distance/seg_dur). Otherwise just holds the
        pointer on the current word for the whole segment (nothing to glide
        toward, e.g. the very last highlighted word in the scene)."""
        if not pointer_on:
            emit(base_img, layout, idx, None, seg_dur)
            return
        cur_pos = box_pos(layout, idx)
        if next_pos is None:
            emit(base_img, layout, idx, cur_pos, seg_dur)
            return

        for pos, step_dur in _glide_positions(cur_pos, next_pos, seg_dur):
            emit(base_img, layout, idx, pos, step_dur)

    scene_start = frames[0]["start"]
    t = 0.0
    frame_layouts = []  # (base_img, layout, word_timings) per frame, so a word's
                         # glide target can look ahead into the NEXT frame too
    for frame in frames:
        render_verse = Verse(
            number=verse_number_for_filenames,
            arabic=frame["text"],
            translation=frame.get("translation", fallback_translation),
        )
        base_img, layout = build_ayah_layout(render_verse, surah_name_arabic, size, show_translation,
                                              surah_name_text=surah_name_text)
        word_timings = sorted(frame.get("highlight_words", []), key=lambda w: w["start"])
        frame_layouts.append((base_img, layout, word_timings))

    for fi, frame in enumerate(frames):
        base_img, layout, word_timings = frame_layouts[fi]
        frame_start_t = frame["start"] - scene_start
        frame_end_t = frame["end"] - scene_start

        if not word_timings:
            emit(base_img, layout, -1, None, frame_end_t - frame_start_t)
            t = frame_end_t
            continue

        # gap before the first highlighted word (if any) shows with no highlight
        if word_timings[0]["start"] > frame["start"]:
            gap_end_t = frame_start_t + (word_timings[0]["start"] - frame["start"])
            emit(base_img, layout, -1, None, gap_end_t - frame_start_t)
            t = gap_end_t
        else:
            t = frame_start_t

        for wi, wt in enumerate(word_timings):
            seg_start_t = wt["start"] - scene_start
            seg_end_t = wt["end"] - scene_start
            if seg_start_t > t:  # gap between two highlighted words -- show unhighlighted
                emit(base_img, layout, -1, None, seg_start_t - t)
                t = seg_start_t

            # the pointer's glide target for this word: the next highlighted
            # word, which may be later in this same frame or -- if this is
            # the last highlighted word here -- the first highlighted word
            # of the next frame that actually has one (its own layout, since
            # each frame can show different text).
            if wi + 1 < len(word_timings):
                next_pos = box_pos(layout, word_timings[wi + 1]["index"])
            else:
                next_pos = None
                for _, next_layout, next_word_timings in frame_layouts[fi + 1:]:
                    if next_word_timings:
                        next_pos = box_pos(next_layout, next_word_timings[0]["index"])
                        break

            emit_word(base_img, layout, wt["index"], seg_end_t - t, next_pos)
            t = seg_end_t

        if t < frame_end_t:  # trailing gap after the last highlighted word
            emit(base_img, layout, -1, None, frame_end_t - t)
            t = frame_end_t

    return sub_clips, t


def build_video(verses, surah_name_arabic, surah_number, reciter_key, size, output_path,
                 fade_duration=None, show_translation=True, timing_manifest=None, surah_name_text=None,
                 outro_enabled=None, audio_source_manifest=None, custom_audio_dir=None):
    THEME = theme_mod.THEME
    if fade_duration is None:
        fade_duration = THEME["fade_duration"]
    clips = []
    clip_is_chained = []  # parallel to `clips` -- see the vfx.Freeze comment below
    audio_clips = []
    cursor = 0.0

    # For reciters/surahs listed in RANGE_AUDIO_SOURCES, one genuinely
    # continuous per-surah recording (never split into per-ayah files) plus
    # its per-ayah boundary timestamps is available -- see
    # download_surah_audio()/get_surah_ayah_boundaries(). Fetched once, up
    # front, for the whole surah; a miss on either (unsupported reciter,
    # network failure, or a timing/audio mismatch caught by the duration
    # cross-check) just leaves both None and every ayah falls back to
    # today's per-ayah download further down -- this must never block a
    # generate.
    range_audio_path = download_surah_audio(surah_number, reciter_key)
    range_boundaries = get_surah_ayah_boundaries(surah_number, reciter_key) if range_audio_path else None
    range_audio_wav_path = get_surah_audio_for_subclipping(surah_number, reciter_key) if range_boundaries else None
    if range_audio_wav_path and range_boundaries:
        print(f"  Using one continuous recording for surah {surah_number} ({reciter_key}) "
              f"instead of downloading audio per ayah…")
    else:
        range_audio_wav_path = None
        range_boundaries = None

    for idx, verse in enumerate(verses):
        # scenes is a list of (Verse-to-render, ...) pairs -- always just one.
        # Ayah 1 of every surah except Al-Fatihah has the Bismillah stripped out of
        # its script text already (see split_basmala_text), and automatic (no-manifest)
        # pacing never shows it as its own scene -- only Al-Fatihah's ayah 1, whose text
        # is left untouched, shows it (as part of that ayah itself). A timing manifest
        # can still mark an explicit Bismillah scene via ayah "0" (see the merge below),
        # rendered from the same undivided ayah-1 audio, not a separate file.
        scenes = [verse]

        for render_verse in scenes:
            label = f"Ayah {render_verse.number}"
            manual_frames = get_ayah_frames(timing_manifest, render_verse.number)

            # Ayah "0" in the manifest is the Bismillah -- a separate set of
            # frames timed by the editor, but against the SAME undivided
            # ayah-1 audio file as the ayah's own frames (see
            # detect_basmala_split() / timing.html), not a physically split
            # clip of its own. Merge it in as a prefix so the two render as
            # one continuous scene over one audio subclip, rather than
            # trying to play a second audio source. Only merge when the ayah
            # itself also has manual frames -- a lone Bismillah entry with no
            # ayah frames would otherwise cut the scene off after the
            # Bismillah and never play the actual ayah.
            if render_verse.number == 1 and manual_frames:
                basmala_frames = get_ayah_frames(timing_manifest, 0)
                if basmala_frames:
                    manual_frames = sorted(basmala_frames + manual_frames, key=lambda f: f["start"])

            scene_origin = cursor  # where THIS scene's audio needs to start playing
            used_range_audio = False

            # Phase 3: a per-ayah custom-audio override (user upload + crop,
            # see quran_lib/audio_sources.py) takes priority over BOTH the
            # range-audio hybrid and the plain per-ayah download -- resolved
            # once here so both the manual-frames and automatic-pacing
            # branches below can use it. render_verse.number is never 0 here
            # (the Bismillah is merged into ayah 1's manual_frames above, not
            # rendered as its own scene), so a manifest's "0" entry -- which
            # by convention shares ayah 1's uploaded file -- never needs a
            # separate lookup: looking up ayah 1 already covers it.
            custom_source = get_ayah_audio_source(audio_source_manifest, render_verse.number)
            custom_audio_path = None
            if custom_source and custom_audio_dir:
                raw_path = custom_audio_dir / custom_source["filename"]
                custom_audio_path = get_custom_ayah_audio(
                    raw_path, custom_source["crop_start"], custom_source["crop_end"]
                )

            if manual_frames:
                # Manually-timed frames (from the timing-editor manifest) are
                # timed against this ayah's own audio -- the custom upload's
                # (already-cropped) window if one was set for this ayah, per
                # the coordinate-system rule in audio_sources.py (frame times
                # are relative to the CROPPED window), otherwise the usual
                # downloaded file. Never the shared per-surah recording.
                if custom_audio_path is not None:
                    scene_audio_path = custom_audio_path
                    print(f"  {label}: using uploaded audio…")
                else:
                    print(f"  {label}: downloading audio…")
                    scene_audio_path = download_ayah_audio(
                        surah=surah_number, ayah=render_verse.number, reciter_key=reciter_key
                    )
                duration = get_audio_duration(scene_audio_path)
                manual_start, manual_end = manual_frames[0]["start"], manual_frames[-1]["end"]
                print(f"  {label}: rendering {len(manual_frames)} manually-timed frame(s) "
                      f"(using {manual_start:.1f}s-{manual_end:.1f}s of {duration:.1f}s audio)…")
                sub_clips, scene_duration = _add_manual_frame_scene(
                    manual_frames, surah_name_arabic, surah_name_text, size, show_translation,
                    render_verse.translation, verse.number,
                )
                # only the marked window of audio plays -- anything before the first frame
                # or after the last one (e.g. unmarked lead-in/trailing silence) is trimmed
                raw_audio_clip = AudioFileClip(str(scene_audio_path))
                a_clip = (
                    raw_audio_clip
                    .subclipped(manual_start, min(manual_end, raw_audio_clip.duration))
                    .with_start(scene_origin)
                )
            elif custom_audio_path is not None:
                # Custom audio with no manual word-timing for this ayah --
                # automatic per-word highlight pacing (same rendering as the
                # plain per-ayah fallback below), just sourced from the
                # already-cropped upload instead of a download. No extra
                # silence-trim is applied on top: the crop IS the user's
                # intended exact window, unlike a raw everyayah.com download.
                # Uses the synthetic _MIN_INTER_AYAH_GAP afterward (see the
                # gap calculation below) -- a one-off upload has no
                # guaranteed-contiguous neighboring ayah the way range-audio
                # slices do, so it's treated like the per-ayah fallback path,
                # not like range audio.
                duration = get_audio_duration(custom_audio_path)
                print(f"  {label}: rendering frame ({duration:.1f}s, from uploaded audio)…")
                if THEME["highlight_enabled"]:
                    sub_clips, scene_duration = _add_word_highlighted_scene(
                        render_verse, surah_name_arabic, surah_name_text, size, show_translation, duration,
                    )
                else:
                    frame, _ = render_verse_frame(render_verse, surah_name_arabic, size, show_translation,
                                                   surah_name_text=surah_name_text)
                    sub_clips = [ImageClip(np.array(frame)).with_duration(duration)]
                    scene_duration = duration
                a_clip = AudioFileClip(str(custom_audio_path)).with_start(scene_origin)
            elif range_boundaries and render_verse.number in range_boundaries:
                # This ayah's own span within the ONE continuous surah
                # recording fetched above -- subclipped by time, never a
                # separately downloaded or cut file. Unlike the per-ayah
                # fallback below, nothing here is trimmed: consecutive
                # ayahs' windows are exactly contiguous (ayah N's end ==
                # ayah N+1's start), so playing them back to back with NO
                # extra gap reproduces the reciter's own natural pause
                # exactly as spoken -- adding _MIN_INTER_AYAH_GAP on top
                # would just make an already-real pause artificially longer.
                used_range_audio = True
                start, end = range_boundaries[render_verse.number]
                duration = end - start
                print(f"  {label}: rendering frame ({duration:.1f}s, from the continuous surah audio)…")
                if THEME["highlight_enabled"]:
                    sub_clips, scene_duration = _add_word_highlighted_scene(
                        render_verse, surah_name_arabic, surah_name_text, size, show_translation, duration,
                    )
                else:
                    frame, _ = render_verse_frame(render_verse, surah_name_arabic, size, show_translation,
                                                   surah_name_text=surah_name_text)
                    sub_clips = [ImageClip(np.array(frame)).with_duration(duration)]
                    scene_duration = duration
                a_clip = (
                    AudioFileClip(str(range_audio_wav_path))
                    .subclipped(start, end)
                    .with_start(scene_origin)
                )
            else:
                # No manual timing and no continuous-surah audio for this ayah
                # -- fall back to today's per-ayah download, trimmed of its own
                # leading/trailing silence padding before placing it (otherwise
                # one ayah's trailing pause plus the next one's leading pause
                # stack into an audibly long gap between recitations that
                # neither file has on its own). Cut via a real re-encoded file
                # (not an in-memory subclip) so the boundary is sample-accurate
                # -- see get_trimmed_ayah_audio.
                print(f"  {label}: downloading audio…")
                audio_path = download_ayah_audio(
                    surah=surah_number, ayah=render_verse.number, reciter_key=reciter_key
                )
                trimmed_audio_path = get_trimmed_ayah_audio(audio_path)
                duration = get_audio_duration(trimmed_audio_path)
                if THEME["highlight_enabled"]:
                    print(f"  {label}: rendering frame ({duration:.1f}s)…")
                    sub_clips, scene_duration = _add_word_highlighted_scene(
                        render_verse, surah_name_arabic, surah_name_text, size, show_translation, duration,
                    )
                else:
                    print(f"  {label}: rendering frame ({duration:.1f}s)…")
                    frame, _ = render_verse_frame(render_verse, surah_name_arabic, size, show_translation,
                                                   surah_name_text=surah_name_text)
                    sub_clips = [ImageClip(np.array(frame)).with_duration(duration)]
                    scene_duration = duration
                a_clip = AudioFileClip(str(trimmed_audio_path)).with_start(scene_origin)

            # Concatenate this ayah's own sub-clips into ONE clip (cheap --
            # they're already sequential/non-overlapping) instead of handing
            # every word-level clip to the top-level CompositeVideoClip, which
            # otherwise has to check every one of them against every output
            # frame's timestamp across the WHOLE video. Only `len(verses)`
            # clips ever reach the top-level composite now.
            ayah_clip = concatenate_videoclips(sub_clips, method="chain") if len(sub_clips) > 1 else sub_clips[0]
            # This clip starts exactly when its OWN audio starts (scene_origin)
            # -- never earlier. Starting it early (the previous approach) made
            # the next ayah's text finish fading in and just sit there for
            # fade_duration seconds *before* its recitation actually began,
            # which read as the audio "restarting" once it finally kicked in.
            # Instead, the dissolve trails the audio boundary: this clip fades
            # IN starting at its own (audio-synced) start, while the PREVIOUS
            # clip is retroactively extended and faded OUT over the same
            # window so there's still something under it to dissolve against.
            ayah_clip = ayah_clip.with_start(scene_origin)
            if idx > 0:
                ayah_clip = ayah_clip.with_effects([vfx.CrossFadeIn(fade_duration)])
                prev_clip = clips[-1]
                if clip_is_chained[-1]:
                    # prev_clip is a concatenate_videoclips(..., method="chain")
                    # result (any scene with more than one sub-clip -- word
                    # highlighting, manual per-word timing), and "chain"'s
                    # frame_function bisects a `timings` array built ONCE from
                    # the sub-clips' ORIGINAL durations (see moviepy's
                    # CompositeVideoClip.concatenate_videoclips). Extending
                    # duration via plain with_duration() only changes the
                    # outer .duration label -- it adds no real frames, so any
                    # request inside the newly-claimed extra `fade_duration`
                    # window lands past the end of that `timings` array and
                    # raises "IndexError: list index out of range" from deep
                    # inside moviepy's own compositing code. Reproducible with
                    # nothing but two-or-more ayahs of manual multi-word
                    # timing (or highlight_enabled's automatic per-word
                    # pacing) rendered back to back -- no Phase-3 custom audio
                    # required; this was a latent, pre-existing bug.
                    # vfx.Freeze rebuilds the clip as [before] + [frozen last
                    # frame] + [] and re-concatenates, so the new clip's own
                    # `timings` array is computed fresh from parts that
                    # genuinely sum to the requested total -- no phantom zone.
                    # Its t="end" shorthand needs clip.fps, which a
                    # concatenated ImageClip chain doesn't carry, so the
                    # freeze point is computed directly against this module's
                    # known FPS instead.
                    freeze_t = max(0.0, prev_clip.duration - 1 / FPS)
                    prev_clip = prev_clip.with_effects(
                        [vfx.Freeze(t=freeze_t, total_duration=prev_clip.duration + fade_duration)]
                    )
                else:
                    # A single (non-chained) ImageClip's frame_function
                    # ignores `t` entirely -- with_duration() is exact and
                    # far cheaper than Freeze's slice-and-reconcatenate, and
                    # is provably safe here since there's no `timings` array
                    # to outrun. This is by far the common case (highlighting
                    # disabled, no manual per-word timing), so keeping it
                    # fast matters.
                    prev_clip = prev_clip.with_duration(prev_clip.duration + fade_duration)
                clips[-1] = prev_clip.with_effects([vfx.CrossFadeOut(fade_duration)])
            clips.append(ayah_clip)
            clip_is_chained.append(len(sub_clips) > 1)

            # Manual timing-manifest frames are hand-tuned by whoever built the
            # manifest, including where each frame ends -- respect that exactly.
            # Range-audio ayahs are contiguous slices of one real, unedited
            # recording, so their natural inter-ayah pause is already intact
            # -- no gap needed. Only the per-ayah fallback trims each file's
            # own silence to zero and needs a synthetic pause added back.
            gap = _MIN_INTER_AYAH_GAP if not (manual_frames or used_range_audio) else 0.0
            cursor = scene_origin + scene_duration + gap
            audio_clips.append(a_clip)

    # outro_enabled=None (the default) defers to the theme's toggle, so the
    # frame editor's "Outro screen" switch controls it by default; passing
    # an explicit True/False (e.g. quran_video.py's --no-outro flag) still
    # overrides the theme either way.
    effective_outro = THEME["outro_enabled"] if outro_enabled is None else outro_enabled
    if effective_outro and THEME["outro_duration"] > 0:
        # plain background, no text -- crossfades in over the last ayah's tail
        # exactly like one ayah crossfades into the next (see `overlap` above)
        print(f"  Outro: rendering closing screen ({THEME['outro_duration']:.1f}s)…")
        outro_img = render_background(size)
        outro_clip = ImageClip(np.array(outro_img)).with_duration(THEME["outro_duration"] + fade_duration)
        outro_clip = outro_clip.with_start(cursor - fade_duration)
        outro_clip = outro_clip.with_effects([vfx.CrossFadeIn(fade_duration)])
        clips.append(outro_clip)
        cursor = cursor + THEME["outro_duration"]

    video = CompositeVideoClip(clips, size=size).with_duration(cursor)
    audio = CompositeAudioClip(audio_clips).with_duration(cursor)
    video = video.with_audio(audio)

    OUTPUT_DIR.mkdir(exist_ok=True)
    video.write_videofile(
        str(output_path), fps=FPS, codec="libx264", audio_codec="aac",
        preset="faster", threads=os.cpu_count() or 4,
    )