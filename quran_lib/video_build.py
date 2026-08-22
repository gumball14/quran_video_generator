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
from .audio import download_ayah_audio, get_audio_duration
from .text_render import render_verse_frame, build_ayah_layout, draw_dynamic_layer
from .timing import get_ayah_frames


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
    glide_steps = 5   # sub-frames used to animate the pointer between two words
    hold_frac = 0.5   # fraction of a word's duration the pointer rests before gliding on

    sub_clips = []

    def emit(highlight_idx, pointer_pos, dur):
        frame_img = draw_dynamic_layer(base_img, layout, highlight_idx, pointer_pos)
        sub_clips.append(ImageClip(np.array(frame_img)).with_duration(dur))

    t = 0.0
    for idx in range(n):
        is_last = idx == n - 1
        seg_dur = (duration - t) if is_last else word_dur
        if seg_dur <= 0:
            continue
        cur_box = word_boxes[idx]
        next_box = word_boxes[idx + 1] if idx + 1 < n else cur_box

        if not pointer_on:
            emit(idx, None, seg_dur)
            t += seg_dur
            continue

        hold_dur = seg_dur * hold_frac
        emit(idx, {"x": cur_box["cx"], "top": cur_box["top"], "font_size": cur_box["font_size"]}, hold_dur)
        t += hold_dur

        glide_dur = (seg_dur - hold_dur) / glide_steps
        for step in range(1, glide_steps + 1):
            st = step / glide_steps
            ease = st * st * (3 - 2 * st)  # smoothstep
            pointer_pos = {
                "x": cur_box["cx"] + (next_box["cx"] - cur_box["cx"]) * ease,
                "top": cur_box["top"] + (next_box["top"] - cur_box["top"]) * ease,
                "font_size": cur_box["font_size"] + (next_box["font_size"] - cur_box["font_size"]) * ease,
            }
            emit(idx, pointer_pos, glide_dur)
            t += glide_dur

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
    sub_clips = []

    def emit(base_img, layout, highlight_idx, dur):
        frame_img = draw_dynamic_layer(base_img, layout, highlight_idx, None)
        sub_clips.append(ImageClip(np.array(frame_img)).with_duration(dur))

    scene_start = frames[0]["start"]
    t = 0.0
    for frame in frames:
        render_verse = Verse(
            number=verse_number_for_filenames,
            arabic=frame["text"],
            translation=frame.get("translation", fallback_translation),
        )
        # every emit() call below shares this same render_verse (same text/
        # translation) for this `frame` -- only highlight_idx changes between
        # them, so the layout only needs to be built once per frame, not once
        # per emitted sub-clip.
        base_img, layout = build_ayah_layout(render_verse, surah_name_arabic, size, show_translation,
                                              surah_name_text=surah_name_text)
        frame_start_t = frame["start"] - scene_start
        frame_end_t = frame["end"] - scene_start
        word_timings = sorted(frame.get("highlight_words", []), key=lambda w: w["start"])

        if not word_timings:
            emit(base_img, layout, -1, frame_end_t - frame_start_t)
            t = frame_end_t
            continue

        # gap before the first highlighted word (if any) shows with no highlight
        if word_timings[0]["start"] > frame["start"]:
            gap_end_t = frame_start_t + (word_timings[0]["start"] - frame["start"])
            emit(base_img, layout, -1, gap_end_t - frame_start_t)
            t = gap_end_t
        else:
            t = frame_start_t

        for wt in word_timings:
            seg_start_t = wt["start"] - scene_start
            seg_end_t = wt["end"] - scene_start
            if seg_start_t > t:  # gap between two highlighted words -- show unhighlighted
                emit(base_img, layout, -1, seg_start_t - t)
                t = seg_start_t
            emit(base_img, layout, wt["index"], seg_end_t - t)
            t = seg_end_t

        if t < frame_end_t:  # trailing gap after the last highlighted word
            emit(base_img, layout, -1, frame_end_t - t)
            t = frame_end_t

    return sub_clips, t


def build_video(verses, surah_name_arabic, surah_number, reciter_key, size, output_path,
                 fade_duration=None, show_translation=True, timing_manifest=None, surah_name_text=None):
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
            duration = get_audio_duration(scene_audio_path)
            label = f"Ayah {render_verse.number}"
            manual_frames = get_ayah_frames(timing_manifest, render_verse.number)

            scene_origin = cursor  # where THIS scene's audio needs to start playing
            # Ayah 1 gets no overlap (nothing precedes it to crossfade against);
            # every later ayah's first clip starts fade_duration seconds early so
            # it overlaps -- and crossfades against -- the previous ayah's tail
            # instead of the compositor's black background.
            overlap = fade_duration if idx > 0 else 0.0

            if manual_frames:
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
            elif THEME["highlight_enabled"]:
                print(f"  {label}: rendering frame ({duration:.1f}s)…")
                sub_clips, scene_duration = _add_word_highlighted_scene(
                    render_verse, surah_name_arabic, surah_name_text, size, show_translation, duration,
                )
                a_clip = AudioFileClip(str(scene_audio_path)).with_start(scene_origin)
            else:
                print(f"  {label}: rendering frame ({duration:.1f}s)…")
                frame, _ = render_verse_frame(render_verse, surah_name_arabic, size, show_translation,
                                               surah_name_text=surah_name_text)
                sub_clips = [ImageClip(np.array(frame)).with_duration(duration)]
                scene_duration = duration
                a_clip = AudioFileClip(str(scene_audio_path)).with_start(scene_origin)

            # Concatenate this ayah's own sub-clips into ONE clip (cheap --
            # they're already sequential/non-overlapping) instead of handing
            # every word-level clip to the top-level CompositeVideoClip, which
            # otherwise has to check every one of them against every output
            # frame's timestamp across the WHOLE video. Only `len(verses)`
            # clips ever reach the top-level composite now.
            if overlap > 0:
                # widen just the scene's first sub-clip so it starts `overlap`
                # seconds early and crossfades against the previous ayah's
                # still-visible tail instead of fading in over black -- same
                # trick as before, just applied once per ayah instead of once
                # per word-level clip.
                sub_clips[0] = sub_clips[0].with_duration(sub_clips[0].duration + overlap)
            ayah_clip = concatenate_videoclips(sub_clips, method="chain") if len(sub_clips) > 1 else sub_clips[0]
            ayah_clip = ayah_clip.with_start(scene_origin - overlap)
            if overlap > 0:
                ayah_clip = ayah_clip.with_effects([vfx.CrossFadeIn(fade_duration)])
            clips.append(ayah_clip)

            cursor = scene_origin + scene_duration
            audio_clips.append(a_clip)

    video = CompositeVideoClip(clips, size=size).with_duration(cursor)
    audio = CompositeAudioClip(audio_clips).with_duration(cursor)
    video = video.with_audio(audio)

    OUTPUT_DIR.mkdir(exist_ok=True)
    video.write_videofile(
        str(output_path), fps=FPS, codec="libx264", audio_codec="aac",
        preset="faster", threads=os.cpu_count() or 4,
    )