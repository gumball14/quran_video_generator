"""
Video assembly: turns fetched verses + downloaded audio into the final
.mp4, including the optional per-word highlight/pointer animation.
"""
import sys

try:
    from moviepy import (
        ImageClip,
        AudioFileClip,
        CompositeVideoClip,
        CompositeAudioClip,
        vfx,
    )
except ImportError:
    print("Missing dependency. Run: pip install moviepy --break-system-packages")
    sys.exit(1)

from .constants import CACHE_DIR, OUTPUT_DIR, FPS
from . import theme as theme_mod
from .quran_api import Verse
from .audio import download_ayah_audio, get_audio_duration
from .text_render import render_verse_frame
from .timing import get_ayah_frames


def _add_word_highlighted_scene(clips, render_verse, surah_name_arabic, surah_name_text, size, show_translation,
                                 duration, cursor, fade_duration, verse_number_for_filenames, overlap=0.0):
    """Renders one scene as a sequence of short ImageClips, switching the
    highlighted word (and, if enabled, gliding the pointer to it) evenly
    across the scene's REAL audio duration -- not the editor's simulated
    preview pace, which only exists because the browser has no audio to
    time against. Appends the clips to `clips` and returns the new cursor.

    `overlap`: seconds the scene's first clip should start EARLY (and run
    longer), so it overlaps the tail of the previous scene's still-visible
    clip instead of fading in over the compositor's black background. Only
    the first clip (k == 0) is widened -- the returned cursor position is
    unaffected, so all later word timings stay exactly where they were."""
    THEME = theme_mod.THEME
    words = [wd for wd in render_verse.arabic.split(" ") if wd]
    n = max(1, len(words))

    # respect the theme's words/sec pace, but never overrun the ayah's actual audio length
    wps = max(0.4, THEME["highlight_fallback_wps"])
    word_dur = min(1.0 / wps, duration / n)

    _, word_boxes = render_verse_frame(render_verse, surah_name_arabic, size, show_translation, highlight_index=-1,
                                        surah_name_text=surah_name_text)

    pointer_on = THEME["highlight_pointer_enabled"]
    glide_steps = 5   # sub-frames used to animate the pointer between two words
    hold_frac = 0.5   # fraction of a word's duration the pointer rests before gliding on

    def emit(highlight_idx, pointer_pos, dur, t, k):
        frame_img, _ = render_verse_frame(render_verse, surah_name_arabic, size, show_translation,
                                            highlight_index=highlight_idx, pointer_pos=pointer_pos,
                                            surah_name_text=surah_name_text)
        frame_path = CACHE_DIR / f"frame_{verse_number_for_filenames:03d}_{render_verse.number:03d}_{k:04d}.png"
        frame_img.save(frame_path)
        start = t - overlap if k == 0 else t
        real_dur = dur + overlap if k == 0 else dur
        clip = ImageClip(str(frame_path)).with_duration(real_dur).with_start(start)
        if k == 0:
            clip = clip.with_effects([vfx.CrossFadeIn(fade_duration)])
        clips.append(clip)
        return t + dur

    t = cursor
    end_time = cursor + duration
    k = 0
    for idx in range(n):
        is_last = idx == n - 1
        seg_dur = (end_time - t) if is_last else word_dur
        if seg_dur <= 0:
            continue
        cur_box = word_boxes[idx]
        next_box = word_boxes[idx + 1] if idx + 1 < n else cur_box

        if not pointer_on:
            t = emit(idx, None, seg_dur, t, k)
            k += 1
            continue

        hold_dur = seg_dur * hold_frac
        t = emit(idx, {"x": cur_box["cx"], "top": cur_box["top"], "font_size": cur_box["font_size"]}, hold_dur, t, k)
        k += 1

        glide_dur = (seg_dur - hold_dur) / glide_steps
        for step in range(1, glide_steps + 1):
            st = step / glide_steps
            ease = st * st * (3 - 2 * st)  # smoothstep
            pointer_pos = {
                "x": cur_box["cx"] + (next_box["cx"] - cur_box["cx"]) * ease,
                "top": cur_box["top"] + (next_box["top"] - cur_box["top"]) * ease,
                "font_size": cur_box["font_size"] + (next_box["font_size"] - cur_box["font_size"]) * ease,
            }
            t = emit(idx, pointer_pos, glide_dur, t, k)
            k += 1

    return end_time


def _add_manual_frame_scene(clips, frames, surah_name_arabic, surah_name_text, size, show_translation,
                             fallback_translation, cursor, fade_duration, verse_number_for_filenames, overlap=0.0):
    """Renders one scene from an explicit list of timing-manifest frames
    (see quran_lib/timing.py) instead of the automatic wps pacing. Each
    frame gets its own text and, optionally, its own per-word highlight
    sub-timings. Appends clips to `clips` and returns the new cursor,
    exactly like _add_word_highlighted_scene(). Frames don't have to
    start at the audio's local time 0 or run to its end (e.g. you might
    only mark the middle of a long ayah) -- the caller is responsible for
    trimming the *audio* clip to match (frames[0]["start"] .. frames[-1]["end"]),
    since this function only ever produces the video side of the scene.

    `overlap`: see _add_word_highlighted_scene() -- same idea, widens only
    the first clip (k == 0) so it crossfades against the previous scene
    instead of the compositor's black background."""
    k = 0

    def emit(render_verse, highlight_idx, dur, t):
        nonlocal k
        frame_img, _ = render_verse_frame(render_verse, surah_name_arabic, size, show_translation,
                                            highlight_index=highlight_idx, surah_name_text=surah_name_text)
        frame_path = CACHE_DIR / f"frame_{verse_number_for_filenames:03d}_manual_{k:04d}.png"
        frame_img.save(frame_path)
        start = t - overlap if k == 0 else t
        real_dur = dur + overlap if k == 0 else dur
        clip = ImageClip(str(frame_path)).with_duration(real_dur).with_start(start)
        if k == 0:
            clip = clip.with_effects([vfx.CrossFadeIn(fade_duration)])
        clips.append(clip)
        k += 1
        return t + dur

    scene_start = frames[0]["start"]
    t = cursor
    for frame in frames:
        render_verse = Verse(
            number=verse_number_for_filenames,
            arabic=frame["text"],
            translation=frame.get("translation", fallback_translation),
        )
        frame_start_t = cursor + (frame["start"] - scene_start)
        frame_end_t = cursor + (frame["end"] - scene_start)
        word_timings = sorted(frame.get("highlight_words", []), key=lambda w: w["start"])

        if not word_timings:
            t = emit(render_verse, -1, frame_end_t - frame_start_t, frame_start_t)
            continue

        # gap before the first highlighted word (if any) shows with no highlight
        if word_timings[0]["start"] > frame["start"]:
            gap = (frame_start_t + (word_timings[0]["start"] - frame["start"])) - frame_start_t
            t = emit(render_verse, -1, gap, frame_start_t)
        else:
            t = frame_start_t

        for i, wt in enumerate(word_timings):
            seg_start_t = cursor + (wt["start"] - scene_start)
            seg_end_t = cursor + (wt["end"] - scene_start)
            if seg_start_t > t:  # gap between two highlighted words -- show unhighlighted
                t = emit(render_verse, -1, seg_start_t - t, t)
            t = emit(render_verse, wt["index"], seg_end_t - t, t)

        if t < frame_end_t:  # trailing gap after the last highlighted word
            t = emit(render_verse, -1, frame_end_t - t, t)

    return t


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
                cursor = _add_manual_frame_scene(
                    clips, manual_frames, surah_name_arabic, surah_name_text, size, show_translation,
                    render_verse.translation, cursor, fade_duration, verse.number, overlap,
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
                cursor = _add_word_highlighted_scene(
                    clips, render_verse, surah_name_arabic, surah_name_text, size, show_translation,
                    duration, cursor, fade_duration, verse.number, overlap,
                )
                a_clip = AudioFileClip(str(scene_audio_path)).with_start(scene_origin)
            else:
                print(f"  {label}: rendering frame ({duration:.1f}s)…")
                frame, _ = render_verse_frame(render_verse, surah_name_arabic, size, show_translation,
                                               surah_name_text=surah_name_text)
                frame_path = CACHE_DIR / f"frame_{verse.number:03d}_{render_verse.number:03d}.png"
                frame.save(frame_path)

                img_clip = (
                    ImageClip(str(frame_path))
                    .with_duration(duration + overlap)
                    .with_start(cursor - overlap)
                    .with_effects([vfx.CrossFadeIn(fade_duration)])
                )
                clips.append(img_clip)
                cursor += duration
                a_clip = AudioFileClip(str(scene_audio_path)).with_start(scene_origin)

            audio_clips.append(a_clip)

    video = CompositeVideoClip(clips, size=size).with_duration(cursor)
    audio = CompositeAudioClip(audio_clips).with_duration(cursor)
    video = video.with_audio(audio)

    OUTPUT_DIR.mkdir(exist_ok=True)
    video.write_videofile(
        str(output_path), fps=FPS, codec="libx264", audio_codec="aac",
        preset="medium", threads=4,
    )