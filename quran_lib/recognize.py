"""
Audio -> (surah, ayah range) detection for a pasted/downloaded recitation of
unknown provenance: transcribe the Arabic speech with faster-whisper, then
fuzzy-match the transcript against the full Quran text to guess which surah
and ayah range was recited. Best-effort only -- see match_transcript_to_quran().
"""
import re
from difflib import SequenceMatcher

import requests

from .constants import TEXT_EDITION

_full_quran_cache = None  # [{"surah": int, "words": [(word, ayah_number), ...]}]
_whisper_model = None

# Arabic diacritics (tashkeel/tanwin/sukun/quranic annotation marks) and the
# tatweel elongation character -- whisper's raw output and the Uthmani script
# mark these completely differently (or not at all), so both sides need them
# stripped before comparison.
_DIACRITICS_RE = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ࣰۭ-ࣿ]")
_TATWEEL_RE = re.compile(r"ـ")
_NON_ARABIC_RE = re.compile(r"[^ء-غف-ي\s]")

MIN_MATCH_CONFIDENCE = 0.25

# align_words_to_audio()'s merge-suggestion cutoff is this fraction of the
# ayah's own median inter-word gap (a word tighter against its neighbor
# than that is flagged as rushed into it), clamped to [FLOOR, CEILING] so a
# recitation with an unusually large or small median doesn't produce a
# nonsensical cutoff.
MERGE_GAP_RATIO = 0.35
MERGE_GAP_FLOOR = 0.05
MERGE_GAP_CEILING = 0.25


def normalize_arabic(text):
    """Strips diacritics/tatweel and unifies common letter-shape variants
    (alef/hamza/yah/taa-marbuta forms) so ASR output and Uthmani script text
    compare on a level footing -- they otherwise almost never match
    character-for-character even when they're saying the same words."""
    text = _DIACRITICS_RE.sub("", text)
    text = _TATWEEL_RE.sub("", text)
    text = (text
            .replace("آ", "ا").replace("أ", "ا")
            .replace("إ", "ا").replace("ٱ", "ا")
            .replace("ة", "ه")   # taa marbuta -> haa
            .replace("ى", "ي"))  # alef maksura -> yaa
    text = _NON_ARABIC_RE.sub(" ", text)
    return text


def _fetch_full_quran_text():
    """Fetches the whole Quran (all 114 surahs) in one request and reduces
    it to, per surah, a flat list of (normalized_word, ayah_number) pairs --
    the shape match_transcript_to_quran() needs to run a word-level diff
    against. Cached in-memory for the life of the process, same pattern as
    fetch_translation_editions() in quran_api.py."""
    global _full_quran_cache
    if _full_quran_cache is not None:
        return _full_quran_cache

    url = f"https://api.alquran.cloud/v1/quran/{TEXT_EDITION}"
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 200:
        raise RuntimeError(f"API error: {data}")

    surahs = []
    for s in data["data"]["surahs"]:
        words = []
        for a in s["ayahs"]:
            for w in normalize_arabic(a["text"]).split():
                words.append((w, a["numberInSurah"]))
        surahs.append({"surah": s["number"], "words": words})
    _full_quran_cache = surahs
    return surahs


def transcribe_audio(path):
    """Runs the given audio file through faster-whisper (Arabic), returning
    the raw transcript text. The model is loaded lazily and kept in memory
    for the life of the process -- model load is by far the slowest part,
    so it should only happen once per server run."""
    segments, _info = _get_whisper_model().transcribe(str(path), language="ar", vad_filter=True)
    return " ".join(seg.text for seg in segments).strip()


def _get_whisper_model():
    """Lazily loads (once per process) and returns the shared faster-whisper
    model instance -- model load is by far the slowest part of any of this
    module's transcription calls."""
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        _whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
    return _whisper_model


def _transcribe_word_timestamps(audio_path):
    """Runs faster-whisper on audio_path with word-level timestamps and
    returns [(normalized_word, start_time, end_time), ...] in playback
    order. Shared by align_words_to_audio() and align_ayah_starts().

    Raises RuntimeError if whisper transcribed no words at all (e.g. silent
    or unreadable audio) -- there's nothing to align against."""
    segments, _info = _get_whisper_model().transcribe(
        str(audio_path), language="ar", vad_filter=True, word_timestamps=True,
    )
    whisper_words = []
    for seg in segments:
        for w in (seg.words or []):
            norm = normalize_arabic(w.word).strip()
            if norm:
                whisper_words.append((norm, w.start, w.end))

    if not whisper_words:
        raise RuntimeError("Couldn't make out any words in that audio.")
    return whisper_words


def _anchor_and_interpolate(target_words, whisper_words):
    """Fuzzy-matches target_words (in order) against whisper_words (see
    align_words_to_audio() for the matching approach) and returns
    (times, anchors):

    - times: one start time per entry in target_words -- a confidently
      matched word takes the whisper word's own start time; anything in
      between two matches (or before the first / after the last) is
      linearly interpolated/extrapolated from its nearest matched
      neighbors.
    - anchors: the [(target_index, start_time, end_time), ...] triples
      that WERE confident ASR matches, sorted by target_index -- for
      callers that also need the raw match points (e.g. gap analysis)."""
    norm_targets = [normalize_arabic(w).strip() for w in target_words]
    matcher = SequenceMatcher(None, norm_targets, [w for w, _s, _e in whisper_words], autojunk=False)

    anchors = []
    for block in matcher.get_matching_blocks():
        for k in range(block.size):
            ti = block.a + k
            wi = block.b + k
            _norm, w_start, w_end = whisper_words[wi]
            anchors.append((ti, w_start, w_end))

    n = len(target_words)
    times = [0.0] * n
    if not anchors:
        # Nothing matched at all -- spread the whisper words' own span
        # evenly across the target words as the least-bad fallback.
        total_span = whisper_words[-1][1]
        for i in range(n):
            times[i] = total_span * i / max(1, n - 1) if n > 1 else 0.0
        return times, anchors

    # Before the first anchor: space evenly back from it to 0.
    first_ti, first_t, _first_end = anchors[0]
    for i in range(first_ti):
        times[i] = first_t * i / first_ti if first_ti else 0.0
    times[first_ti] = first_t

    # Between consecutive anchors: linear interpolation.
    for (ti_a, t_a, _end_a), (ti_b, t_b, _end_b) in zip(anchors, anchors[1:]):
        span = ti_b - ti_a
        for i in range(ti_a + 1, ti_b):
            times[i] = t_a + (t_b - t_a) * (i - ti_a) / span
        times[ti_b] = t_b

    # After the last anchor: carry the same pace forward as the previous gap
    # (or hold flat if there's only ever been one anchor).
    last_ti, last_t, _last_end = anchors[-1]
    if len(anchors) > 1:
        prev_ti, prev_t, _prev_end = anchors[-2]
        prev_gap = (last_t - prev_t) / max(1, last_ti - prev_ti)
    else:
        prev_gap = 0.0
    for i in range(last_ti + 1, n):
        times[i] = last_t + prev_gap * (i - last_ti)

    return times, anchors


def align_words_to_audio(audio_path, target_words):
    """Best-effort per-word start-time alignment: transcribes `audio_path`
    with faster-whisper's word-level timestamps, then fuzzy-matches the
    transcribed words against `target_words` (the ayah's own word list, in
    order) the same way match_transcript_to_quran() matches a transcript
    against the whole Quran -- a word-level SequenceMatcher diff, since ASR
    output is never a clean 1:1 array against the reference text (missed
    words, extra filler, melisma splitting one recited word into several
    whisper tokens).

    Returns {"times": [...], "merges": [...]}:

    - "times": one start time (seconds into audio_path) per entry in
      target_words, always the same length as target_words. Matched words
      take the whisper word's own start time; anything in between two
      matches (or before the first / after the last) is linearly
      interpolated/extrapolated from its nearest matched neighbors, so
      every word gets a plausible timestamp even though only some are ever
      a confident ASR match.

    - "merges": target-word indices i where word i and word i+1 are
      suggested to be merged into one beat, because whisper detected them
      running together with almost no gap relative to this recitation's
      OWN typical pacing -- i.e. the reciter sped up through the word
      badly enough that it blends into the next one. The comparison is
      against this ayah's median inter-word gap rather than a fixed
      number, since reciters differ a lot in overall pace. A merge only
      ever joins exactly two words: if two flagged boundaries are
      themselves adjacent (which would chain a third word into the same
      run), only the first is kept, so a suggested merge never spans more
      than a pair.

    "merges" is only ever reported between two ADJACENT target words that
    both got a confident ASR match (so the gap reflects a real detected
    tightness, not two matches with an unmatched or interpolated word
    already sitting between them).

    Raises RuntimeError if whisper transcribed no words at all (e.g. silent
    or unreadable audio) -- there's nothing to align against."""
    whisper_words = _transcribe_word_timestamps(audio_path)
    times, anchors = _anchor_and_interpolate(target_words, whisper_words)

    # Adjacent anchor pairs' inter-word gaps, for the merge calls below.
    adjacent_gaps = []  # [(target_index, gap_seconds), ...] -- gap is end_a -> start_b
    for (ti_a, _t_a, end_a), (ti_b, t_b, _end_b) in zip(anchors, anchors[1:]):
        if ti_b == ti_a + 1:
            adjacent_gaps.append((ti_a, t_b - end_a))

    # A word is flagged for merging into the next one when its gap is much
    # tighter than this ayah's own typical (median) gap -- relative, not a
    # fixed cutoff, since a naturally fast reciter's normal pace shouldn't
    # get flagged just for being fast throughout. Too few samples to trust
    # a median (a very short ayah) falls back to a small absolute cutoff.
    merges = []
    if adjacent_gaps:
        sorted_gaps = sorted(g for _, g in adjacent_gaps)
        if len(adjacent_gaps) >= 3:
            median_gap = sorted_gaps[len(sorted_gaps) // 2]
            merge_threshold = min(MERGE_GAP_CEILING, max(MERGE_GAP_FLOOR, median_gap * MERGE_GAP_RATIO))
        else:
            merge_threshold = MERGE_GAP_FLOOR
        # Never chain a merge into a run of three or more words: if the
        # previous boundary was just flagged, this one is skipped even if
        # it's also tight, so a merged run is always exactly two words.
        for ti, gap in adjacent_gaps:
            if gap < merge_threshold and (not merges or merges[-1] != ti - 1):
                merges.append(ti)

    return {"times": times, "merges": merges}


def align_ayah_starts(audio_path, ayah_word_lists):
    """Best-effort auto-marking of where each AYAH begins in one longer
    recording that spans several ayahs back to back -- the custom-audio
    flow's whole-range upload (static/custom_audio.html), as opposed to
    align_words_to_audio()'s single ayah's own audio.

    `ayah_word_lists` is a list of word lists, one per ayah, in recitation
    order (each entry the ayah's own words, in on-screen order -- same
    shape as align_words_to_audio()'s `target_words`, just one level
    deeper). All ayahs' words are flattened into a single target-word
    sequence, in order, and fuzzy-matched against the transcription in one
    pass, the same way align_words_to_audio() matches a single ayah -- an
    ayah boundary is just wherever one ayah's word list ends and the next
    one's begins in that flattened sequence, so this reuses the exact same
    alignment rather than trying to detect boundaries some other way.

    Returns {"times": [...]}: one start time (seconds into audio_path) per
    entry in ayah_word_lists, each the detected/interpolated start time of
    that ayah's own first word -- exactly what a hand-placed mark in the
    custom-audio flow records, so callers can drop these straight into the
    mark list.

    Raises RuntimeError if whisper transcribed no words at all (e.g. silent
    or unreadable audio) -- there's nothing to align against."""
    flat_words = []
    ayah_start_index = []
    for words in ayah_word_lists:
        ayah_start_index.append(len(flat_words))
        flat_words.extend(words)

    if not flat_words:
        return {"times": [0.0] * len(ayah_word_lists)}

    whisper_words = _transcribe_word_timestamps(audio_path)
    times, _anchors = _anchor_and_interpolate(flat_words, whisper_words)

    return {"times": [times[i] for i in ayah_start_index]}


def match_transcript_to_quran(transcript):
    """Fuzzy-matches a (not-yet-normalized) Arabic transcript against every
    surah's text and returns the best-guess {surah, ayahStart, ayahEnd,
    confidence} -- or None if nothing scored high enough to be worth acting
    on (MIN_MATCH_CONFIDENCE).

    Approach: for each surah, run difflib's SequenceMatcher over the
    surah's word list vs. the transcript's word list and look at the
    matching blocks it finds. The surah whose matched-word total is the
    largest fraction of the transcript wins; the ayah range is just the
    span from the first to the last ayah touched by any matching block in
    that surah. This is a best-effort guess, not meant to be perfect --
    ASR mistakes and recitation melisma both blur word boundaries -- but is
    normally decisive since only the genuinely recited surah has any long
    matching runs at all."""
    words = normalize_arabic(transcript).split()
    if not words:
        return None

    surahs = _fetch_full_quran_text()
    best = None
    for s in surahs:
        surah_words = [w for w, _ in s["words"]]
        matcher = SequenceMatcher(None, surah_words, words, autojunk=False)
        blocks = [b for b in matcher.get_matching_blocks() if b.size > 0]
        if not blocks:
            continue
        matched = sum(b.size for b in blocks)
        score = matched / len(words)
        if best is None or score > best["score"]:
            first_ayah = s["words"][blocks[0].a][1]
            last_ayah = s["words"][blocks[-1].a + blocks[-1].size - 1][1]
            best = {
                "score": score,
                "surah": s["surah"],
                "ayahStart": min(first_ayah, last_ayah),
                "ayahEnd": max(first_ayah, last_ayah),
            }

    if best is None or best["score"] < MIN_MATCH_CONFIDENCE:
        return None
    return {
        "surah": best["surah"],
        "ayahStart": best["ayahStart"],
        "ayahEnd": best["ayahEnd"],
        "confidence": round(best["score"], 3),
    }
