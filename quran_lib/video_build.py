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
from .audio import download_ayah_audio, get_audio_duration, get_trimmed_ayah_audio
from .text_render import render_verse_frame, build_ayah_layout, draw_dynamic_layer, render_background
from .timing import get_ayah_frames


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
                 outro_enabled=None):
    THEME = theme_mod.THEME
    if fade_duration is None:
        fade_duration = THEME["fade_duration"]
    clips = []
    audio_clips = []
    cursor = 0.0

    for idx, verse in enumerate(verses):
        print(f"  Ayah {verse.number}: downloading audio…")
        audio_path = download_ayah_audio(
            surah=surah_number, ayah=verse.number, reciter_key=reciter_key
        )

        # scenes is a list of (Verse-to-render, audio_path) pairs -- always just one.
        # Ayah 1 of every surah except Al-Fatihah has the Bismillah stripped out of
        # its script text already (see split_basmala_text); per-ayah recitation audio
        # is recited directly without a separate spoken Bismillah, so we never show or
        # play a standalone Bismillah scene here -- only Al-Fatihah's ayah 1, whose text
        # is left untouched, shows it (as part of that ayah itself).
        scenes = [(verse, audio_path)]

        for render_verse, scene_audio_path in scenes:
            label = f"Ayah {render_verse.number}"
            manual_frames = get_ayah_frames(timing_manifest, render_verse.number)

            scene_origin = cursor  # where THIS scene's audio needs to start playing

            if manual_frames:
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
            else:
                # Trim each ayah's own leading/trailing silence padding before
                # placing it -- otherwise one ayah's trailing pause plus the
                # next one's leading pause stack into an audibly long gap
                # between recitations that neither file has on its own. Cut
                # via a real re-encoded file (not an in-memory subclip) so
                # the boundary is sample-accurate -- see get_trimmed_ayah_audio.
                trimmed_audio_path = get_trimmed_ayah_audio(scene_audio_path)
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
                clips[-1] = (
                    prev_clip
                    .with_duration(prev_clip.duration + fade_duration)
                    .with_effects([vfx.CrossFadeOut(fade_duration)])
                )
            clips.append(ayah_clip)

            # Manual timing-manifest frames are hand-tuned by whoever built the
            # manifest, including where each frame ends -- respect that exactly.
            # Automatic scenes had their own silence trimmed to zero, so add
            # back a small deliberate pause before the next ayah starts.
            gap = 0.0 if manual_frames else _MIN_INTER_AYAH_GAP
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