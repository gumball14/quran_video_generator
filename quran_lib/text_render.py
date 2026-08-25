"""
Drawing a single frame: Arabic shaping, line wrapping, background styles,
and the layout math (column width, translation_position, text_align) that
mirrors the HTML editor's canvas preview. render_verse_frame() is the one
entry point everything else (video_build.py) calls.
"""
import functools
import math
import os
import sys

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
except ImportError:
    print("Missing dependency. Run: pip install arabic-reshaper python-bidi --break-system-packages")
    sys.exit(1)

from . import theme as theme_mod
from .quran_api import Verse

# Pillow ships with "raqm" (proper Arabic/complex-script shaping) on most
# platforms nowadays. When it's present, Pillow will shape + reorder Arabic
# text itself if you just pass direction="rtl"/language="ar" to draw.text().
# If we ALSO manually reshape+bidi the string first (the old approach below),
# Pillow shapes it a second time on top of that -> mirrored, broken text.
# So: detect raqm once, and pick ONE approach consistently.
_HAS_RAQM = None


def has_raqm() -> bool:
    global _HAS_RAQM
    if _HAS_RAQM is None:
        try:
            from PIL import features
            _HAS_RAQM = features.check("raqm")
        except Exception:
            _HAS_RAQM = False
    return _HAS_RAQM


def shape_arabic(text: str) -> str:
    """Manual fallback shaping for when Pillow has no raqm support."""
    reshaped = arabic_reshaper.reshape(text)
    return get_display(reshaped)


def arabic_draw_args(text: str):
    """Return (text_to_draw, extra_kwargs_for_draw.text/textbbox) for Arabic text,
    using whichever rendering path is safe for this Pillow install."""
    if has_raqm():
        return text, {"direction": "rtl", "language": "ar"}
    return shape_arabic(text), {}


_ROMAN_TABLE = [
    (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
    (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
    (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
]


def to_roman_numeral(n: int) -> str:
    """Converts a positive integer to an uppercase Roman numeral string.
    Roman numerals have no standard representation past 3999, so numbers
    outside 1-3999 fall back to plain digits rather than rendering blank."""
    if n <= 0 or n > 3999:
        return str(n)
    remainder = n
    parts = []
    for value, symbol in _ROMAN_TABLE:
        count, remainder = divmod(remainder, value)
        parts.append(symbol * count)
    return "".join(parts)


_ARABIC_INDIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"


def to_arabic_indic_numeral(n: int) -> str:
    """Converts a non-negative integer to Eastern Arabic-Indic digits
    (e.g. 12 -> "١٢"), used by the badge's "arabic" numeral style."""
    return "".join(_ARABIC_INDIC_DIGITS[int(d)] for d in str(n))


def format_badge_number(n: int, numeral_style: str) -> str:
    """Formats the ayah/verse number for the number badge according to
    THEME["badge_numeral_style"]: "western" (plain digits, default),
    "arabic" (Eastern Arabic-Indic digits), or "roman" (Roman numerals)."""
    if numeral_style == "arabic":
        return to_arabic_indic_numeral(n)
    if numeral_style == "roman":
        return to_roman_numeral(n)
    return str(n)


def wrap_arabic_lines(text: str, font: ImageFont.FreeTypeFont, max_width: int, draw: ImageDraw.ImageDraw):
    """Wrap Arabic text (in logical/original order) into lines that fit max_width
    once shaped for display."""
    words = text.split(" ")
    lines, current = [], []

    def measured_width(words_list):
        display_text, kwargs = arabic_draw_args(" ".join(words_list))
        bbox = draw.textbbox((0, 0), display_text, font=font, **kwargs)
        return bbox[2] - bbox[0]

    for word in words:
        trial = current + [word]
        if current and measured_width(trial) > max_width:
            lines.append(" ".join(current))
            current = [word]
        else:
            current = trial
    if current:
        lines.append(" ".join(current))
    return lines


def wrap_latin_lines(text: str, font: ImageFont.FreeTypeFont, max_width: int):
    words = text.split(" ")
    lines, current = [], []
    for word in words:
        trial = current + [word]
        line = " ".join(trial)
        bbox = font.getbbox(line)
        if current and (bbox[2] - bbox[0]) > max_width:
            lines.append(" ".join(current))
            current = [word]
        else:
            current = trial
    if current:
        lines.append(" ".join(current))
    return lines


def _shadow_settings(h):
    """Returns (enabled, color, spread_color, blur_px, offset_x_px, offset_y_px)
    for the current THEME, sized off frame height `h`."""
    THEME = theme_mod.THEME
    enabled = THEME.get("text_shadow_enabled", False)
    color = tuple(THEME.get("text_shadow_color", [0, 0, 0]))
    spread_color = tuple(THEME.get("text_shadow_spread_color") or color)
    blur_px = h * THEME.get("text_shadow_blur_frac", 0.006)
    offset_x = h * THEME.get("text_shadow_offset_x_frac", 0.003)
    offset_y = h * THEME.get("text_shadow_offset_y_frac", 0.004)
    return enabled, color, spread_color, blur_px, offset_x, offset_y


def _relative_luminance(rgb):
    """WCAG relative luminance of an (r,g,b) triple, 0 (black) - 1 (white)."""
    def chan(c):
        s = c / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b)


def _is_light_color(rgb):
    return _relative_luminance(rgb) > 0.55


def _shadow_layers(shadow_color, blur_px, offset_x, offset_y, h):
    """Returns a list of (rgba_color, layer_blur_px, layer_offset_x, layer_offset_y)
    describing how to paste a shadow. Mirrors frame_editor.html's
    buildShadowCss(): a light shadow color reads as a dark smear on top of
    light text, so light colors render as a soft centered glow (no
    directional offset, two wider layered blur passes) instead of the
    directional offset/blur drop shadow used for dark colors -- keeping the
    exported frame in sync with what the editor's live preview shows."""
    r, g, b = shadow_color
    if _is_light_color(shadow_color):
        glow_blur = blur_px * 1.8 + h * 0.008
        return [
            ((r, g, b, 242), glow_blur * 0.55, 0, 0),
            ((r, g, b, 140), glow_blur, 0, 0),
        ]
    return [((r, g, b, 255), blur_px, offset_x, offset_y)]


# Uthmani waqf/pause marks -- see quran_api.py's _attach_waqf_marks(), which
# glues one of these onto the end of the previous word (no separating space)
# for any fetched ayah that has one, so raqm's shaper positions it as a
# combining mark stacked above that word instead of floating as its own
# token. Repeated here (not imported from quran_api) to keep text_render.py
# usable standalone/in tests without a network-facing dependency.
_WAQF_MARK_CHARS = "ۖۗۘۙۚۛۜ"

# How much further above the font's own built-in mark-anchor position to
# nudge a trailing waqf mark, as a fraction of the word's font size. Some
# bundled fonts' anchor sits close enough to the base letter to read as
# touching/overlapping it -- see _composite_lifted_text().
WAQF_MARK_LIFT_FRAC = 0.22

# A tall-ish Arabic letter, used only to measure how far above a base
# letter's own top a waqf mark's glyph naturally extends (see
# _waqf_mark_extra_ascent_frac()) -- not part of any real word.
_WAQF_MARK_ASCENT_PROBE_BASE = "ا"


@functools.lru_cache(maxsize=64)
def _waqf_mark_extra_ascent_frac(font_path: str, mark: str) -> float:
    """How far above a base letter's own top this one waqf mark's glyph
    extends, as a fraction of font size -- measured once per (font, mark)
    at a large reference size and cached (bbox height scales ~linearly
    with font size, so the fraction carries over to any size). Combined
    with WAQF_MARK_LIFT_FRAC by arabic_line_height_with_waqf_pad() to make
    sure a lifted mark never has less headroom above it than the line
    above needs. These marks are stylized calligraphic ligatures (not
    plain dots) and can genuinely be quite tall -- this measures the real
    glyph rather than guessing."""
    ref_size = 200
    font = _cached_font(font_path, ref_size)
    img = Image.new("L", (1, 1))
    draw = ImageDraw.Draw(img)
    base_kwargs = arabic_draw_args(_WAQF_MARK_ASCENT_PROBE_BASE)[1]
    base_bbox = draw.textbbox((0, 0), _WAQF_MARK_ASCENT_PROBE_BASE, font=font, **base_kwargs)
    full_text = _WAQF_MARK_ASCENT_PROBE_BASE + mark
    full_kwargs = arabic_draw_args(full_text)[1]
    full_bbox = draw.textbbox((0, 0), full_text, font=font, **full_kwargs)
    return max(0.0, (base_bbox[1] - full_bbox[1]) / ref_size)


def arabic_line_height_with_waqf_pad(arabic_size, base_mult, font_path):
    """Returns the arabic_line_height_mult to actually use for this ayah's
    Arabic block -- `base_mult` (the theme's own setting) bumped by enough
    to guarantee a lifted waqf mark on ANY line never runs into the line
    above it. The bump is computed from the worst case across all possible
    waqf marks (not just the one(s) actually present in `text`), and is
    applied unconditionally -- every ayah gets the same line height whether
    or not it actually has a mark, so spacing never visibly shifts between
    ayahs depending on whether one happens to carry a mark."""
    worst = max(_waqf_mark_extra_ascent_frac(font_path, mark) for mark in _WAQF_MARK_CHARS)
    return base_mult + WAQF_MARK_LIFT_FRAC + worst


def _split_trailing_waqf_mark(word):
    """If `word` (logical order -- i.e. as split on spaces, before any RTL
    reshaping) ends in a waqf mark, returns (word_without_mark, mark_char).
    Otherwise returns (word, None)."""
    if word and word[-1] in _WAQF_MARK_CHARS:
        return word[:-1], word[-1]
    return word, None


def _composite_lifted_text(mode, size, xy, text_full, text_base, font, fill, lift_px, **kwargs):
    """Draws `text_full` at `xy` into a fresh `mode` ("L" or "RGBA") image of
    `size`, same as a plain ImageDraw.text() call, except a trailing waqf
    mark (if `text_base` is `text_full` with one split off -- pass identical
    strings when there's none) is shifted `lift_px` further above the base
    word than the font's own combining-mark anchor places it.

    PIL/raqm expose no per-glyph position hook to nudge just one combining
    mark directly, so this isolates it indirectly: render text_base alone
    and text_full (base+mark) at the same `xy`, diff their alpha to pull out
    just the mark's own pixels (the base glyphs render identically in both,
    since a trailing non-spacing mark doesn't reflow anything before it),
    then re-composite with that isolated mark shifted up."""
    zero = 0 if mode == "L" else (0, 0, 0, 0)
    full = Image.new(mode, size, zero)
    ImageDraw.Draw(full).text(xy, text_full, font=font, fill=fill, **kwargs)
    if text_base == text_full or lift_px <= 0:
        return full
    base = Image.new(mode, size, zero)
    ImageDraw.Draw(base).text(xy, text_base, font=font, fill=fill, **kwargs)
    full_a = full if mode == "L" else full.getchannel("A")
    base_a = base if mode == "L" else base.getchannel("A")
    mark_mask = ImageChops.subtract(full_a, base_a)
    if mark_mask.getbbox() is None:
        return full  # nothing isolated (unexpected) -- fall back to the plain render
    mark_only = Image.new(mode, size, zero)
    mark_only.paste(full, (0, 0), mark_mask)
    mark_lifted = Image.new(mode, size, zero)
    mark_lifted.paste(mark_only, (0, -round(lift_px)))
    lifted = base.copy()
    lifted.paste(mark_lifted, (0, 0), mark_lifted)
    return lifted


def _render_shadow_layer(size, mask_fn, color, blur_px):
    """Renders one shadow layer as a flat `color` masked by a blurred alpha
    shape. mask_fn(size) returns an "L"-mode mask, which is what actually
    gets blurred -- NOT an RGBA image with `color` painted directly, which
    is what every version of this used to do. Blurring RGBA directly mixes
    the flat color into the implicit black RGB of the fully-transparent
    pixels surrounding it (PIL's GaussianBlur has no idea those pixels are
    "invisible" -- it just averages whatever's in the R/G/B channels there,
    which defaults to 0), so a light shadow color's blurred edge comes out
    gray/dark instead of fading to the color itself. Barely visible against
    a dark background (a gray fringe still looks like glow), but glaring
    against a light one (a bright color like white produces a visibly dark
    ring). Blurring a single-channel mask has no such cross-channel
    contamination."""
    mask = mask_fn(size)
    if blur_px > 0:
        mask = mask.filter(ImageFilter.GaussianBlur(blur_px))
    r, g, b, a = color
    if a != 255:
        mask = mask.point(lambda v: v * a // 255)
    layer = Image.new("RGBA", size, (r, g, b, 0))
    layer.putalpha(mask)
    return layer


def _paste_text_shadow(img: Image.Image, text, font, xy, layers, text_base=None, lift_px=0, **kwargs):
    """Blurs and pastes shadow-colored copies of `text` behind wherever the
    real glyph will be drawn -- but does NOT draw the real glyph itself, so
    callers that draw their real text separately (e.g. per-word Arabic
    glyphs, drawn later by draw_dynamic_layer) can bake just the shadow in
    now. `layers` comes from _shadow_layers() -- usually one directional
    layer, or two centered glow layers for light shadow colors. The overlay
    is sized to the text's own bounding box (+ blur padding), not the full
    frame, so this stays cheap even called once per word.

    `text_base`/`lift_px`: when `text` ends in a waqf mark, pass `text_base`
    as `text` with that mark split off (see _split_trailing_waqf_mark) and
    `lift_px` > 0 to nudge the mark's shadow up along with the real glyph
    (see _composite_lifted_text) -- otherwise the shadow would sit at the
    font's own (un-lifted) anchor position while the glyph above it moved,
    visibly detaching the two."""
    if text_base is None:
        text_base = text
    draw = ImageDraw.Draw(img)
    bbox = draw.textbbox(xy, text, font=font, **kwargs)
    max_blur = max(layer[1] for layer in layers)
    pad = int(max_blur * 3) + 4
    region = [int(bbox[0]) - pad, int(bbox[1]) - pad, int(bbox[2]) + pad, int(bbox[3]) + pad]
    rw, rh = region[2] - region[0], region[3] - region[1]
    if rw <= 0 or rh <= 0:
        return
    local_xy = (xy[0] - region[0], xy[1] - region[1])
    overlay = Image.new("RGBA", (rw, rh), (0, 0, 0, 0))
    for color, blur_px, offset_x, offset_y in layers:
        def mask_fn(size, offset_x=offset_x, offset_y=offset_y):
            off_xy = (local_xy[0] + offset_x, local_xy[1] + offset_y)
            return _composite_lifted_text("L", size, off_xy, text, text_base, font, 255, lift_px, **kwargs)
        layer_img = _render_shadow_layer((rw, rh), mask_fn, color, blur_px)
        overlay = Image.alpha_composite(overlay, layer_img)
    img.paste(overlay, (region[0], region[1]), overlay)


def draw_text_with_shadow(img: Image.Image, draw: ImageDraw.ImageDraw, text, font, xy, fill,
                           shadow_enabled, layers, **kwargs):
    """Draws `text` at xy with `fill`, pasting a blurred shadow behind
    it first when shadow_enabled -- used for elements drawn once per ayah
    (header, badge number, translation lines), where drawing the shadow and
    the real glyph together in one call is simplest."""
    if shadow_enabled:
        _paste_text_shadow(img, text, font, xy, layers, **kwargs)
        draw = ImageDraw.Draw(img)  # paste() mutates img in place; re-grab a draw handle to be safe across Pillow versions
    draw.text(xy, text, font=font, fill=fill, **kwargs)
    return draw


@functools.lru_cache(maxsize=64)
def _cached_font(path_str: str, size: int) -> ImageFont.FreeTypeFont:
    """ImageFont.truetype() re-parses the font file from disk on every call;
    (path, size) pairs repeat constantly across frames/ayahs, so cache them."""
    return ImageFont.truetype(path_str, size)


@functools.lru_cache(maxsize=8)
def _cached_background_image(spec: str):
    """Wraps theme_mod.load_background_image() so the same background_image
    spec doesn't get decoded from disk/base64 on every ayah."""
    return theme_mod.load_background_image(spec)


@functools.lru_cache(maxsize=8)
def _cached_gradient(size, top_color, bottom_color, style="linear") -> Image.Image:
    """Renders the theme's background gradient -- cache the result per
    (size, colors, style) since it's the same for every ayah that shares a
    theme. Callers must .copy() the result before drawing on it."""
    if style == "radial":
        return radial_gradient(size, top_color, bottom_color).convert("RGB")
    if style == "diagonal":
        return diagonal_gradient(size, top_color, bottom_color).convert("RGB")
    return vertical_gradient(size, top_color, bottom_color).convert("RGB")


def theme_gradient(size) -> Image.Image:
    """render_background()/render_outro_frame()'s single entry point for the
    theme's gradient -- reads bg_top/bg_bottom/bg_gradient_style off THEME so
    every caller doesn't have to."""
    THEME = theme_mod.THEME
    return _cached_gradient(size, tuple(THEME["bg_top"]), tuple(THEME["bg_bottom"]),
                             THEME.get("bg_gradient_style", "linear"))


def vertical_gradient(size, top_color, bottom_color):
    w, h = size
    base = Image.new("RGB", (1, h), color=0)
    draw = ImageDraw.Draw(base)
    for y in range(h):
        t = y / max(h - 1, 1)
        r = int(top_color[0] + (bottom_color[0] - top_color[0]) * t)
        g = int(top_color[1] + (bottom_color[1] - top_color[1]) * t)
        b = int(top_color[2] + (bottom_color[2] - top_color[2]) * t)
        draw.point((0, y), fill=(r, g, b))
    return base.resize((w, h))


# radial_gradient/diagonal_gradient render at a small fixed resolution (a
# per-pixel Python loop, unlike vertical_gradient's cheap 1-column trick)
# then upscale to the real frame size -- smooth enough for a soft color
# gradient, and cheap since it only runs once per (size, colors, style)
# thanks to _cached_gradient's lru_cache.
_GRADIENT_BASE_N = 128


def radial_gradient(size, top_color, bottom_color):
    n = _GRADIENT_BASE_N
    base = Image.new("RGB", (n, n))
    px = base.load()
    cx = cy = (n - 1) / 2
    max_r = math.hypot(cx, cy)
    for y in range(n):
        for x in range(n):
            t = min(math.hypot(x - cx, y - cy) / max_r, 1.0)
            px[x, y] = (
                int(top_color[0] + (bottom_color[0] - top_color[0]) * t),
                int(top_color[1] + (bottom_color[1] - top_color[1]) * t),
                int(top_color[2] + (bottom_color[2] - top_color[2]) * t),
            )
    return base.resize(size, Image.LANCZOS)


def diagonal_gradient(size, top_color, bottom_color):
    n = _GRADIENT_BASE_N
    base = Image.new("RGB", (n, n))
    px = base.load()
    denom = 2 * (n - 1)
    for y in range(n):
        for x in range(n):
            t = (x + y) / denom
            px[x, y] = (
                int(top_color[0] + (bottom_color[0] - top_color[0]) * t),
                int(top_color[1] + (bottom_color[1] - top_color[1]) * t),
                int(top_color[2] + (bottom_color[2] - top_color[2]) * t),
            )
    return base.resize(size, Image.LANCZOS)


# Common install locations for a color-emoji font. None of these ship with
# the project (the bundled fonts/ directory only has the Arabic + Latin
# faces used for verse text), so the "hand" pointer only gets a real 👆
# glyph when the host system happens to have one of these installed --
# otherwise draw_pointer() falls back to the vector marker it always used.
_EMOJI_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/truetype/noto-emoji/NotoColorEmoji.ttf",
    "/usr/local/share/fonts/NotoColorEmoji.ttf",
    "/System/Library/Fonts/Apple Color Emoji.ttc",
    "C:\\Windows\\Fonts\\seguiemj.ttf",
]

# Color-emoji fonts (Noto Color Emoji, Apple Color Emoji, Segoe UI Emoji) are
# bitmap fonts (CBDT/sbix) that embed only ONE fixed-size strike -- e.g. Noto
# Color Emoji ships just a 109px strike. Pillow does NOT snap an arbitrary
# requested size to that strike the way it does for outline fonts: asking
# for any other size raises `OSError: invalid pixel size`, so a naive
# `ImageFont.truetype(path, font_size)` silently fails for every size except
# that one exact value and _emoji_font() used to swallow the error and fall
# back to the vector marker every time. Instead, load the font at whichever
# of these common native strike sizes actually works, then raster the glyph
# once at that native size and resize the *bitmap* to whatever size
# draw_pointer needs.
_EMOJI_STRIKE_SIZES = [109, 136, 128, 96, 72, 64, 48, 160]

_emoji_font_cache = {}


def _emoji_font():
    """Loads the host's color-emoji font at its native bitmap strike size,
    trying a handful of common install locations and strike sizes, and
    caches the (font, native_size) result -- including a None on failure --
    so every frame doesn't re-hit the filesystem. Returns None if no usable
    emoji font is installed on the host."""
    if "result" in _emoji_font_cache:
        return _emoji_font_cache["result"]
    result = None
    for path in _EMOJI_FONT_CANDIDATES:
        if not os.path.exists(path):
            continue
        for size in _EMOJI_STRIKE_SIZES:
            try:
                result = (ImageFont.truetype(path, size), size)
                break
            except Exception:
                continue
        if result is not None:
            break
    _emoji_font_cache["result"] = result
    return result


def draw_pointer(draw: ImageDraw.ImageDraw, x: float, top_y: float, font_size: float, bottom_y: float = None,
                  img: Image.Image = None):
    """Pointer marker for the active word, placed above it (top_y) or below
    it (bottom_y), per highlight_pointer_position.

    'hand' draws a real hand emoji (👆 above the word / 👇 below it) when a
    color-emoji font is installed on the host (see _emoji_font()) AND `img`
    is passed (needed to paste the resized glyph bitmap -- see below);
    'arrow' always renders as a vector triangle, and 'dot' as a vector
    circle -- those two have no natural emoji equivalent that reads as a
    pointer at a glance, and a plain arrow/dot in the theme's highlight
    color matches the rest of the highlight styling better than a
    fixed-color emoji glyph would. If no emoji font/img is available, 'hand'
    falls back to the same triangle-plus-stem vector marker it always used.

    Sized up from the original marker (which read as a faint speck at
    typical frame sizes) and outlined in black so it stays visible against
    highlight-colored or light-colored words too, not just dark backgrounds."""
    THEME = theme_mod.THEME
    style = THEME["highlight_pointer_style"]
    position = THEME.get("highlight_pointer_position", "top")
    gap = THEME.get("highlight_pointer_gap_mult", 1.0)
    size_mult = THEME.get("highlight_pointer_size_mult", 1.0)
    r, g, b = THEME["highlight_color"]
    outline = (0, 0, 0)
    outline_width = max(1, round(font_size * 0.035 * size_mult))
    if bottom_y is None:
        bottom_y = top_y + font_size * 1.12  # matches the highlight pill's bottom edge

    if style == "hand":
        loaded = _emoji_font()
        if loaded is not None and img is not None:
            font, native_size = loaded
            # The glyph must point AT the word, not away from it: when the
            # pointer sits above the word (position == "top") it needs the
            # downward-pointing hand (👇), and vice versa for "bottom".
            glyph = "\U0001F447" if position == "top" else "\U0001F446"  # 👇 / 👆
            bbox = draw.textbbox((0, 0), glyph, font=font, embedded_color=True)
            gw, gh = bbox[2] - bbox[0], bbox[3] - bbox[1]
            if gw > 0 and gh > 0:
                # Raster the glyph at the font's native (fixed) strike size,
                # then resize the bitmap itself to the size we actually want
                # -- Pillow can't render this color-bitmap font at an
                # arbitrary point size directly (see _emoji_font()).
                glyph_img = Image.new("RGBA", (gw, gh), (0, 0, 0, 0))
                ImageDraw.Draw(glyph_img).text((-bbox[0], -bbox[1]), glyph, font=font, embedded_color=True)
                target_h = round(font_size * 1.1 * size_mult)
                scale = target_h / native_size
                new_w, new_h = max(1, round(gw * scale)), max(1, round(gh * scale))
                glyph_img = glyph_img.resize((new_w, new_h), Image.LANCZOS)

                gx = round(x - new_w / 2)
                if position == "top":
                    gy = round(top_y - new_h * gap)
                else:
                    gy = round(bottom_y + font_size * 0.15 * gap)
                img.paste(glyph_img, (gx, gy), glyph_img)
                return
        # no emoji font/img available -- fall through to the vector marker below

    if style in ("hand", "arrow"):
        s = font_size * 0.46 * size_mult
        stem_h = font_size * 0.42 * gap * size_mult
        stem_w = s * 0.6
        if position == "top":
            tip_y = top_y - font_size * 0.22 * gap
            pts = [(x, tip_y), (x - s, tip_y - s), (x + s, tip_y - s)]
            stem_box = [x - stem_w / 2, tip_y - s - stem_h, x + stem_w / 2, tip_y - s]
        else:
            tip_y = bottom_y + font_size * 0.22 * gap
            pts = [(x, tip_y), (x - s, tip_y + s), (x + s, tip_y + s)]
            stem_box = [x - stem_w / 2, tip_y + s, x + stem_w / 2, tip_y + s + stem_h]
        draw.polygon(pts, fill=(r, g, b), outline=outline, width=outline_width)
        if style == "hand":
            draw.rectangle(stem_box, fill=(r, g, b), outline=outline, width=outline_width)
    elif style == "dot":
        rad = font_size * 0.15 * size_mult
        cy = (top_y - font_size * 0.25 * gap) if position == "top" else (bottom_y + font_size * 0.25 * gap)
        draw.ellipse([x - rad, cy - rad, x + rad, cy + rad], fill=(r, g, b), outline=outline, width=outline_width)


def _badge_shape(style, cx, cy, half):
    """Returns (kind, data) describing the badge outline for `style`, centered
    at (cx, cy) with half-width/height `half`. Mirrors the CSS shapes in
    frame_editor.html (.fc-badge.style-*) so the exported frame matches the
    editor's live preview.
    kind is one of: "none", "ellipse", "ring", "rounded_rect", "polygon"."""
    if style == "none":
        return "none", None
    if style == "circle":
        return "ellipse", [cx - half, cy - half, cx + half, cy + half]
    if style == "ring":
        return "ring", [cx - half, cy - half, cx + half, cy + half]
    if style == "square":
        return "rounded_rect", ([cx - half, cy - half, cx + half, cy + half], half * 0.36)
    if style == "diamond":
        return "polygon", [(cx, cy - half), (cx + half, cy), (cx, cy + half), (cx - half, cy)]
    if style == "hexagon":
        pts_pct = [(0.5, 0), (1, 0.25), (1, 0.75), (0.5, 1), (0, 0.75), (0, 0.25)]
        return "polygon", [(cx - half + px * half * 2, cy - half + py * half * 2) for px, py in pts_pct]
    if style == "flower":
        pts_pct = [
            (0.5, 0), (0.61, 0.18), (0.82, 0.10), (0.79, 0.33), (1.0, 0.5),
            (0.79, 0.67), (0.82, 0.90), (0.61, 0.82), (0.5, 1.0), (0.39, 0.82),
            (0.18, 0.90), (0.21, 0.67), (0.0, 0.5), (0.21, 0.33), (0.18, 0.10), (0.39, 0.18),
        ]
        return "polygon", [(cx - half + px * half * 2, cy - half + py * half * 2) for px, py in pts_pct]
    if style == "star8":
        # Eight-pointed star (the "Rub el Hizb" motif traditionally used to
        # mark Quranic divisions) -- 16 vertices alternating outer/inner radius.
        return "polygon", _star_polygon(cx, cy, half, spikes=8, inner_ratio=0.44)
    if style == "scallop":
        # Shallow scalloped medallion edge -- a calligraphic-manuscript-style
        # frame, distinct from "flower"'s deeper 16-point petals.
        return "polygon", _star_polygon(cx, cy, half, spikes=14, inner_ratio=0.88)
    # "ornament" (default) and any unrecognized style: soft rounded rect
    return "rounded_rect", ([cx - half, cy - half, cx + half, cy + half], half * 0.6)


def _star_polygon(cx, cy, half, spikes, inner_ratio, rotation_deg=-90):
    """Returns the vertex list for a `spikes`-pointed star (or shallow
    scalloped edge, for a high inner_ratio) centered at (cx, cy), alternating
    between the outer radius `half` and `half * inner_ratio`. Mirrors the
    clip-path polygons used for the matching .style-star8/.style-scallop
    swatches in frame_editor.html so the exported frame matches the editor."""
    points = []
    n = spikes * 2
    for i in range(n):
        r = half if i % 2 == 0 else half * inner_ratio
        angle = math.radians(rotation_deg + i * (360 / n))
        points.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    return points


def _draw_line_cap(target, x, y, direction, cap, size, color):
    """Draws one end marker for a "line_*" badge style at (x, y) -- the
    outer tip of the line, `size` past the circle's border. `direction` is
    -1 for the left-hand end (marker points further left/outward) or +1
    for the right-hand end (points right/outward). `cap` is "dot",
    "arrow", or "diamond"."""
    if cap == "dot":
        target.ellipse([x - size, y - size, x + size, y + size], fill=color)
    elif cap == "diamond":
        target.polygon([(x, y - size), (x + size, y), (x, y + size), (x - size, y)], fill=color)
    elif cap == "arrow":
        tip_x = x + direction * size * 1.4
        target.polygon([(tip_x, y), (x, y - size), (x, y + size)], fill=color)


def _draw_line_cap(target, x, y, direction, cap, size, color):
    """Draws one end marker for the badge's line accent at (x, y) -- the
    outer tip of the line, `size` past the badge's border. `direction` is
    -1 for the left-hand end (marker points further left/outward) or +1
    for the right-hand end (points right/outward). `cap` is "dot",
    "arrow", or "diamond"."""
    if cap == "dot":
        target.ellipse([x - size, y - size, x + size, y + size], fill=color)
    elif cap == "diamond":
        target.polygon([(x, y - size), (x + size, y), (x, y + size), (x - size, y)], fill=color)
    elif cap == "arrow":
        tip_x = x + direction * size * 1.4
        target.polygon([(tip_x, y), (x, y - size), (x, y + size)], fill=color)


def _paste_badge_shape_shadow(img: Image.Image, kind, data, border_width_px, layers, cx, cy, half):
    """Blurs and pastes the badge shape's OUTLINE (not its fill) behind
    where the real border will be drawn, once per layer in `layers` (see
    _shadow_layers) -- gated by text_shadow_badge_border, independent of
    whether the digit/other text elements have shadows on. `kind`/`data`
    come from _badge_shape(); "ring" needs its own outer-radius math to
    match _draw_badge_shape()'s own ring rendering."""
    if kind not in ("ellipse", "ring", "rounded_rect", "polygon"):
        return
    max_blur = max(layer[1] for layer in layers)
    pad = int(border_width_px + max_blur * 3) + 6
    region = [int(cx - half - pad), int(cy - half - pad), int(cx + half + pad), int(cy + half + pad)]
    rw, rh = region[2] - region[0], region[3] - region[1]
    if rw <= 0 or rh <= 0:
        return

    overlay = Image.new("RGBA", (rw, rh), (0, 0, 0, 0))
    for color, blur_px, offset_x, offset_y in layers:
        def shift(pt, ox=offset_x, oy=offset_y):
            return (pt[0] - region[0] + ox, pt[1] - region[1] + oy)

        def mask_fn(size, shift=shift):
            mask = Image.new("L", size, 0)
            odraw = ImageDraw.Draw(mask)
            if kind == "ellipse":
                odraw.ellipse([*shift((data[0], data[1])), *shift((data[2], data[3]))], outline=255, width=border_width_px)
            elif kind == "ring":
                ring_offset = border_width_px * 2
                outer = [data[0] - ring_offset, data[1] - ring_offset, data[2] + ring_offset, data[3] + ring_offset]
                odraw.ellipse([*shift((outer[0], outer[1])), *shift((outer[2], outer[3]))], outline=255, width=border_width_px)
            elif kind == "rounded_rect":
                rect, radius = data
                odraw.rounded_rectangle([*shift((rect[0], rect[1])), *shift((rect[2], rect[3]))], radius=radius,
                                         outline=255, width=border_width_px)
            elif kind == "polygon":
                odraw.polygon([shift(p) for p in data], outline=255, width=border_width_px)
            return mask

        layer_img = _render_shadow_layer((rw, rh), mask_fn, color, blur_px)
        overlay = Image.alpha_composite(overlay, layer_img)
    img.paste(overlay, (region[0], region[1]), overlay)


def _paste_badge_line_shadow(img: Image.Image, cx, cy, line_half_len, line_width_px, line_cap_style,
                              line_cap_size_px, layers):
    """Blurs and pastes the badge's line accent (+ end caps) behind where
    the real one will be drawn, once per layer in `layers` (see
    _shadow_layers) -- gated by text_shadow_line, independent of the
    badge's other shadow targets."""
    max_blur = max(layer[1] for layer in layers)
    pad = int(line_width_px + line_cap_size_px + max_blur * 3) + 6
    region = [int(cx - line_half_len - pad), int(cy - pad), int(cx + line_half_len + pad), int(cy + pad)]
    rw, rh = region[2] - region[0], region[3] - region[1]
    if rw <= 0 or rh <= 0:
        return
    overlay = Image.new("RGBA", (rw, rh), (0, 0, 0, 0))
    for color, blur_px, offset_x, offset_y in layers:
        def mask_fn(size, offset_x=offset_x, offset_y=offset_y):
            mask = Image.new("L", size, 0)
            odraw = ImageDraw.Draw(mask)
            lcx, lcy = cx - region[0] + offset_x, cy - region[1] + offset_y
            odraw.line([(lcx - line_half_len, lcy), (lcx + line_half_len, lcy)], fill=255, width=line_width_px)
            _draw_line_cap(odraw, lcx - line_half_len, lcy, -1, line_cap_style, line_cap_size_px, 255)
            _draw_line_cap(odraw, lcx + line_half_len, lcy, 1, line_cap_style, line_cap_size_px, 255)
            return mask

        layer_img = _render_shadow_layer((rw, rh), mask_fn, color, blur_px)
        overlay = Image.alpha_composite(overlay, layer_img)
    img.paste(overlay, (region[0], region[1]), overlay)


def _draw_badge_shape(img, draw, style, cx, cy, half, fill_rgba, border_rgb, border_width_px,
                       line_width_px=0, line_cap_style="dot", line_cap_size_px=0, line_color=None,
                       line_half_len=None):
    """Draws the badge outline/fill onto `img`, returning the (possibly new)
    (img, draw) pair to keep using -- a new Image is only created when alpha
    blending (a translucent fill) requires compositing.

    line_width_px > 0 additionally draws the optional "line accent": a
    horizontal line (+ end markers) through the badge, drawn UNDERNEATH the
    badge_style shape so the shape's own fill covers the line's middle,
    leaving it visible only where it pokes out past the border on each
    side. This works with every badge_style, including "none" (a bare line
    with no shape around it). line_half_len is the line's horizontal reach
    on EACH side of center, in px -- pass it explicitly (see badge_line_length
    in render_verse_frame) so the accent's length matches the editor's
    "Line width" slider; falls back to a fixed proportion of the badge for
    any caller that doesn't supply one."""
    kind, data = _badge_shape(style, cx, cy, half)
    if kind == "none" and line_width_px <= 0:
        return img, draw
    if line_half_len is None:
        line_half_len = half * 2.6

    if kind == "none":
        # No shape at all -- just the line accent, drawn straight onto the
        # base draw context (no translucent fill to composite here).
        lc = line_color if line_color is not None else (border_rgb if border_rgb is not None else (255, 255, 255))
        draw.line([(cx - line_half_len, cy), (cx + line_half_len, cy)], fill=lc, width=line_width_px)
        _draw_line_cap(draw, cx - line_half_len, cy, -1, line_cap_style, line_cap_size_px, lc)
        _draw_line_cap(draw, cx + line_half_len, cy, 1, line_cap_style, line_cap_size_px, lc)
        return img, draw

    needs_overlay = fill_rgba is not None and fill_rgba[3] < 255
    if needs_overlay:
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        target = ImageDraw.Draw(overlay)
    else:
        target = draw
    fill_solid = fill_rgba[:3] if (fill_rgba is not None and not needs_overlay) else None

    if line_width_px > 0:
        lc = line_color if line_color is not None else (border_rgb if border_rgb is not None else (255, 255, 255))
        target.line([(cx - line_half_len, cy), (cx + line_half_len, cy)], fill=lc, width=line_width_px)
        _draw_line_cap(target, cx - line_half_len, cy, -1, line_cap_style, line_cap_size_px, lc)
        _draw_line_cap(target, cx + line_half_len, cy, 1, line_cap_style, line_cap_size_px, lc)

    if kind == "ellipse":
        target.ellipse(data, fill=(fill_rgba if needs_overlay else fill_solid),
                        outline=border_rgb, width=border_width_px)
    elif kind == "ring":
        offset = border_width_px * 2
        outer = [data[0] - offset, data[1] - offset, data[2] + offset, data[3] + offset]
        if fill_rgba is not None:
            target.ellipse(data, fill=(fill_rgba if needs_overlay else fill_solid))
        target.ellipse(outer, outline=border_rgb, width=border_width_px)
    elif kind == "rounded_rect":
        rect, radius = data
        target.rounded_rectangle(rect, radius=radius, fill=(fill_rgba if needs_overlay else fill_solid),
                                  outline=border_rgb, width=border_width_px)
    elif kind == "polygon":
        target.polygon(data, fill=(fill_rgba if needs_overlay else fill_solid),
                        outline=border_rgb, width=border_width_px)
    elif kind == "line":
        # Draw the line (+ end caps) FIRST, then the circle on top -- so the
        # circle's fill visually "swallows" the middle of the line, leaving
        # it only visible poking out past the border on each side, capped
        # with a dot/arrowhead/diamond marker.
        line_color = border_rgb if border_rgb is not None else (255, 255, 255)
        line_width = border_width_px if border_width_px > 0 else max(1, round(half * 0.12))
        cap_size = max(line_width * 1.7, half * 0.16)
        target.line([(cx - line_half_len, cy), (cx + line_half_len, cy)], fill=line_color, width=line_width)
        _draw_line_cap(target, cx - line_half_len, cy, -1, data["cap"], cap_size, line_color)
        _draw_line_cap(target, cx + line_half_len, cy, 1, data["cap"], cap_size, line_color)
        target.ellipse(data["ellipse"], fill=(fill_rgba if needs_overlay else fill_solid),
                        outline=border_rgb, width=border_width_px)

    if needs_overlay:
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(img)
    return img, draw


def render_background(size) -> Image.Image:
    """Renders just the theme's background (solid/gradient/image + overlay,
    per THEME["background_style"]) with no text or other elements on top.
    Shared by build_ayah_layout() (as the base every frame draws onto) and
    by the outro scene (a plain background frame with nothing else on it)."""
    THEME = theme_mod.THEME
    w, h = size
    if THEME["background_style"] == "solid":
        img = Image.new("RGB", size, tuple(THEME["bg_solid"]))
    elif THEME["background_style"] == "image" and THEME.get("background_image"):
        bg = _cached_background_image(THEME["background_image"])
        if bg is not None:
            # cover-fit, centered -- matches the editor preview's canvas math
            scale = max(w / bg.width, h / bg.height)
            iw, ih = int(bg.width * scale), int(bg.height * scale)
            bg = bg.resize((iw, ih), Image.LANCZOS)
            img = Image.new("RGB", size)
            img.paste(bg, ((w - iw) // 2, (h - ih) // 2))
            overlay = Image.new("RGBA", size, (0, 0, 0, int(255 * THEME["background_overlay_opacity"])))
            img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
        else:
            img = theme_gradient(size).copy()
    else:
        img = theme_gradient(size).copy()
    return img


def build_ayah_layout(verse: Verse, surah_name_arabic: str, size, show_translation=True, surah_name_text=None):
    """Runs once per ayah: background, header, Arabic font-fitting/wrapping/
    word measurement, badge, and translation. Returns (base_image, layout)
    where base_image is a finished PIL.Image with everything EXCEPT the
    Arabic word glyphs, highlight pill, and pointer (those depend on
    highlight_index/pointer_pos, which change every frame), and layout is
    {"arabic_font", "arabic_size", "word_boxes"} for draw_dynamic_layer().

    word_boxes is a list of {left, right, cx, top, font_size, display, kwargs}
    dicts, one per Arabic word, in reading order -- used both by
    draw_dynamic_layer() (display/kwargs let it draw each word without
    re-measuring) and by video_build.build_video() (left/right/cx/top/
    font_size time the highlight/pointer against each word's position).

    surah_name_text is the surah's English/Latin name (e.g. "Al-Fatiha").
    Which of it or surah_name_arabic gets drawn as the header is controlled
    by THEME["header_script"] ("arabic" | "text")."""
    THEME = theme_mod.THEME
    w, h = size

    # --- background ---
    img = render_background(size)
    draw = ImageDraw.Draw(img)
    shadow_on, shadow_color, shadow_spread_color, shadow_blur, shadow_dx, shadow_dy = _shadow_settings(h)
    text_shadow_layers = _shadow_layers(shadow_color, shadow_blur, shadow_dx, shadow_dy, h)
    spread_shadow_layers = _shadow_layers(shadow_spread_color, shadow_blur, shadow_dx, shadow_dy, h)
    header_shadow_on = shadow_on and THEME.get("text_shadow_header", True)
    arabic_shadow_on = shadow_on and THEME.get("text_shadow_arabic", True)
    badge_num_shadow_on = shadow_on and THEME.get("text_shadow_badge_number", True)
    badge_border_shadow_on = shadow_on and THEME.get("text_shadow_badge_border", False)
    badge_line_shadow_on = shadow_on and THEME.get("text_shadow_line", False)
    translation_shadow_on = shadow_on and THEME.get("text_shadow_translation", True)

    # --- layout columns: side-by-side translation splits the width, text_align shifts within a column ---
    margin = int(w * THEME["margin_frac"])
    max_text_width = w - 2 * margin
    side_mode = THEME["translation_position"] == "side" and show_translation and THEME["show_translation"]
    arabic_col_width = max_text_width * 0.55 if side_mode else max_text_width
    trans_col_width = max_text_width * 0.4 if side_mode else max_text_width
    text_align = THEME["text_align"]  # "left" | "center" | "right"
    arabic_col_left = margin
    if side_mode:
        gap = max_text_width * 0.05
        arabic_col_left = margin + trans_col_width + gap if text_align == "right" else margin

    # Header: surah name (small, top). header_script picks which string/font/
    # shaping path to use -- "text" draws surah_name_text in the Latin font
    # left-to-right (like the translation), "arabic" (default) draws
    # surah_name_arabic in the Arabic font with RTL shaping, as before.
    use_text_header = THEME.get("header_script") == "text"
    header_text = surah_name_text if use_text_header else surah_name_arabic
    if THEME["show_header"] and header_text:
        if use_text_header:
            header_font = _cached_font(str(theme_mod.LATIN_FONT_REGULAR), int(h * THEME["header_size_frac"]))
            hb = draw.textbbox((0, 0), header_text, font=header_font)
            draw = draw_text_with_shadow(img, draw, header_text, header_font,
                       ((w - (hb[2] - hb[0])) / 2, h * THEME["header_y_frac"]), tuple(THEME["header_color"]),
                       header_shadow_on, text_shadow_layers)
        else:
            header_font = _cached_font(str(theme_mod.ARABIC_FONT_REGULAR), int(h * THEME["header_size_frac"]))
            header_display, header_kwargs = arabic_draw_args(header_text)
            hb = draw.textbbox((0, 0), header_display, font=header_font, **header_kwargs)
            draw = draw_text_with_shadow(img, draw, header_display, header_font,
                       ((w - (hb[2] - hb[0])) / 2, h * THEME["header_y_frac"]), tuple(THEME["header_color"]),
                       header_shadow_on, text_shadow_layers, **header_kwargs)

    # Arabic verse text, sized down if long
    arabic_size = int(h * THEME["arabic_size_max_frac"])
    min_size = int(h * THEME["arabic_size_min_frac"])
    # Bumped uniformly for every ayah (see arabic_line_height_with_waqf_pad())
    # so a lifted waqf mark never runs into the line above, regardless of
    # which line it lands on or whether this particular ayah has one.
    arabic_line_height_mult = arabic_line_height_with_waqf_pad(
        arabic_size, THEME["arabic_line_height_mult"], str(theme_mod.ARABIC_FONT_BOLD))
    while arabic_size > min_size:
        arabic_font = _cached_font(str(theme_mod.ARABIC_FONT_BOLD), arabic_size)
        lines = wrap_arabic_lines(verse.arabic, arabic_font, arabic_col_width, draw)
        line_height = int(arabic_size * arabic_line_height_mult)
        block_height = line_height * len(lines)
        if block_height < h * 0.42:
            break
        arabic_size -= 2

    total_arabic_h = line_height * len(lines)
    start_y = h * THEME["arabic_center_y_frac"] - total_arabic_h / 2

    space_display, space_kwargs = arabic_draw_args(" ")
    space_bbox = draw.textbbox((0, 0), space_display, font=arabic_font, **space_kwargs)
    space_width = space_bbox[2] - space_bbox[0]

    # --- pass 1: measure every word's box (also gives us the pointer's target position) ---
    word_boxes = []
    line_layout = []  # (words, right_edge, y) per line, reused in pass 2
    for i, line in enumerate(lines):
        y = start_y + i * line_height
        words = [wd for wd in line.split(" ") if wd]
        full_display, full_kwargs = arabic_draw_args(line)
        full_bbox = draw.textbbox((0, 0), full_display, font=arabic_font, **full_kwargs)
        line_width = full_bbox[2] - full_bbox[0]
        if text_align == "left":
            right_edge = arabic_col_left + line_width
        elif text_align == "right":
            right_edge = arabic_col_left + arabic_col_width
        else:
            right_edge = arabic_col_left + arabic_col_width / 2 + line_width / 2
        line_layout.append((words, right_edge, y))
        cursor = right_edge
        for word in words:
            wd_base_word, wd_mark = _split_trailing_waqf_mark(word)
            wd_display, wd_kwargs = arabic_draw_args(word)
            wd_lift_px = arabic_size * WAQF_MARK_LIFT_FRAC if wd_mark else 0
            if wd_mark:
                wd_base_display, wd_base_kwargs = arabic_draw_args(wd_base_word)
            else:
                wd_base_display, wd_base_kwargs = wd_display, wd_kwargs
            wd_bbox = draw.textbbox((0, 0), wd_display, font=arabic_font, **wd_kwargs)
            wd_width = wd_bbox[2] - wd_bbox[0]
            left = cursor - wd_width
            word_boxes.append({"left": left, "right": cursor, "cx": left + wd_width / 2,
                                "top": y, "font_size": arabic_size,
                                "display": wd_display, "kwargs": wd_kwargs,
                                "base_display": wd_base_display, "lift_px": wd_lift_px})
            if arabic_shadow_on:
                # Only the shadow is baked in here -- the real glyph is drawn
                # per-frame by draw_dynamic_layer() (word highlighting changes
                # 6x/word), but the shadow itself never changes frame to
                # frame, so baking it once into the cached base image avoids
                # redoing the blur on every frame.
                _paste_text_shadow(img, wd_display, arabic_font, (left, y), text_shadow_layers,
                                    text_base=wd_base_display, lift_px=wd_lift_px, **wd_kwargs)
            cursor -= wd_width + space_width

    # Word glyphs, highlight pill, and pointer are all per-frame (highlight_index/
    # pointer_pos change 6x/word) -- drawn later by draw_dynamic_layer(), not here.

    # Verse number badge (skip for the Basmala scene, number 0). Where it sits
    # is controlled by badge_position: "below_header" (right under the surah
    # name), "below_arabic" (default -- just under the Arabic block, as
    # before), or "bottom" (pinned near the bottom of the frame regardless of
    # verse length). badge_gap_frac adds extra breathing room on top of that
    # anchor (below_arabic keeps its small built-in cushion either way, so
    # gap=0 still looks the way this used to before the option existed).
    # Centered within the Arabic column (matches the editor: full-width when
    # not in side mode).
    arabic_bottom_y = start_y + total_arabic_h + h * 0.015
    badge_gap_px = h * THEME.get("badge_gap_frac", 0.03)
    badge_position = THEME.get("badge_position", "below_arabic")
    if badge_position == "below_header":
        badge_top_y = h * THEME["header_y_frac"] + h * THEME["header_size_frac"] * 1.6 + badge_gap_px
    else:
        badge_top_y = arabic_bottom_y + badge_gap_px  # also used as the "bottom" fallback below

    badge_cx = arabic_col_left + arabic_col_width / 2
    if THEME["show_badge"] and verse.number > 0:
        badge_font_size = int(h * THEME["badge_size_frac"])
        numeral_style = THEME.get("badge_numeral_style", "western")
        badge_display = format_badge_number(verse.number, numeral_style)
        # Roman numerals are Latin letters -- draw them in the Latin font;
        # plain and Arabic-Indic digits both read fine in the Arabic font
        # (as plain digits already did before this option existed).
        badge_font_path = theme_mod.LATIN_FONT_REGULAR if numeral_style == "roman" else theme_mod.ARABIC_FONT_REGULAR
        badge_font = _cached_font(str(badge_font_path), badge_font_size)
        bb = draw.textbbox((0, 0), badge_display, font=badge_font)
        text_w = bb[2] - bb[0]
        text_h = bb[3] - bb[1]

        # Badge box: big enough for the digits (with padding) or a sane minimum,
        # whichever is larger -- so 2-3 digit ayah numbers still fit inside
        # circle/diamond/etc shapes instead of overflowing them.
        pad = badge_font_size * 0.7
        box_size = max(badge_font_size * 1.9, text_w + pad, text_h + pad)
        half = box_size / 2
        if badge_position == "bottom":
            badge_cy = h - h * 0.06 - half
        else:
            badge_cy = badge_top_y + badge_font_size * 0.62

        style = THEME.get("badge_style", "ornament")
        border_width_val = THEME.get("badge_border_width", 0.15)
        border_width_px = max(1, round(box_size * 0.14 * border_width_val)) if border_width_val > 0 else 0
        border_color = tuple(THEME.get("badge_border_color", THEME["badge_color"])) if border_width_px > 0 else None
        fill_rgba = None
        if THEME.get("badge_fill_enabled", False):
            fr, fg, fb = THEME.get("badge_fill_color", THEME["badge_color"])
            alpha = int(255 * THEME.get("badge_fill_opacity", 0.14))
            fill_rgba = (fr, fg, fb, alpha)

        # Optional line accent -- a horizontal line through the badge, poking
        # out past whichever badge_style border is active. Always uses
        # badge_border_color (no separate line color), and works on top of
        # any border style, including "none".
        line_width_px = 0
        line_cap_style = THEME.get("badge_line_style", "dot")
        line_cap_size_px = 0
        line_half_len_px = None
        line_color = tuple(THEME.get("badge_border_color", THEME["badge_color"]))
        if THEME.get("badge_line_enabled", False):
            line_width_val = THEME.get("badge_line_width", 0.15)
            line_width_px = max(1, round(box_size * 0.14 * line_width_val))
            cap_size_val = THEME.get("badge_line_cap_size", 0.30)
            line_cap_size_px = max(line_width_px * 1.3, half * 0.5 * cap_size_val)
            # badge_line_length is the TOTAL span of both pokes, as a % of the
            # frame width `w` (matches the editor's "Line width" slider, where
            # 100 stretches the line edge to edge) -- line_half_len is each
            # side's reach, so divide by 200 rather than 100.
            line_length_val = THEME.get("badge_line_length", 10)
            line_half_len_px = w * (line_length_val / 200.0)

        if badge_border_shadow_on and style != "none" and border_width_px > 0:
            kind, shape_data = _badge_shape(style, badge_cx, badge_cy, half)
            _paste_badge_shape_shadow(img, kind, shape_data, border_width_px, spread_shadow_layers,
                                       badge_cx, badge_cy, half)
        if badge_line_shadow_on and line_width_px > 0:
            _paste_badge_line_shadow(img, badge_cx, badge_cy, line_half_len_px if line_half_len_px is not None else half * 2.6,
                                      line_width_px, line_cap_style, line_cap_size_px, spread_shadow_layers)

        img, draw = _draw_badge_shape(img, draw, style, badge_cx, badge_cy, half,
                                       fill_rgba, border_color, border_width_px,
                                       line_width_px, line_cap_style, line_cap_size_px, line_color,
                                       line_half_len_px)

        # Plain digits are drawn left-to-right with no direction/language kwargs
        # (unlike the Arabic verse/header text above) so the bbox-based centering
        # below lands the number dead-center in the badge instead of skewed by
        # RTL shaping.
        draw = draw_text_with_shadow(img, draw, badge_display, badge_font,
                   (badge_cx - text_w / 2 - bb[0], badge_cy - text_h / 2 - bb[1]), tuple(THEME["badge_color"]),
                   badge_num_shadow_on, text_shadow_layers)
        badge_bottom = badge_cy + half
    else:
        badge_bottom = badge_top_y

    # Translation ("below" mode) flows right under the badge only when the
    # badge actually sits directly beneath the Arabic verse (the original,
    # default arrangement); for "below_header"/"bottom" the badge is
    # elsewhere on the frame, so translation flows under the verse instead.
    translation_ref_bottom = badge_bottom if badge_position == "below_arabic" else arabic_bottom_y

    # Translation -- position/column/alignment follow translation_position + text_align
    if show_translation and THEME["show_translation"]:
        trans_size = int(h * THEME["translation_size_frac"])
        trans_font = _cached_font(str(theme_mod.LATIN_FONT_REGULAR), trans_size)
        trans_col_width_eff = trans_col_width if side_mode else max_text_width
        trans_lines = wrap_latin_lines(verse.translation, trans_font, trans_col_width_eff)
        t_line_height = int(trans_size * 1.5)

        if side_mode:
            gap = max_text_width * 0.05
            trans_col_left = margin if text_align == "right" else margin + arabic_col_width + gap
            total_t_h = t_line_height * len(trans_lines)
            t_start_y = h * THEME["arabic_center_y_frac"] - total_t_h / 2
        elif THEME["translation_position"] == "above":
            trans_col_left = margin
            total_t_h = t_line_height * len(trans_lines)
            t_start_y = start_y - h * THEME["translation_gap_frac"] - total_t_h
        else:  # "below" (default)
            trans_col_left = margin
            t_start_y = translation_ref_bottom + h * THEME["translation_gap_frac"]

        for i, line in enumerate(trans_lines):
            bbox = draw.textbbox((0, 0), line, font=trans_font)
            line_w = bbox[2] - bbox[0]
            if text_align == "left":
                x = trans_col_left
            elif text_align == "right":
                x = trans_col_left + trans_col_width_eff - line_w
            else:
                x = trans_col_left + (trans_col_width_eff - line_w) / 2
            y = t_start_y + i * t_line_height
            draw = draw_text_with_shadow(img, draw, line, trans_font, (x, y), tuple(THEME["translation_color"]),
                       translation_shadow_on, text_shadow_layers)

    layout = {"arabic_font": arabic_font, "arabic_size": arabic_size, "word_boxes": word_boxes,
              "arabic_shadow_on": arabic_shadow_on, "text_shadow_layers": text_shadow_layers}
    return img, layout


def draw_dynamic_layer(base_image: Image.Image, layout: dict, highlight_index=-1, pointer_pos=None) -> Image.Image:
    """Runs per frame (6x/word): copies the cached base image from
    build_ayah_layout() and draws just the highlight pill + word glyphs +
    pointer on top. No re-measuring, no re-wrapping, no font reload.

    highlight_index is a single word index (-1 = none, the default), or a
    list of indices for a merged group -- two or more words recited fast
    enough to highlight together as one beat instead of animating word to
    word (see quran_lib/timing.py). A group's pill spans the union of its
    words' boxes, drawn once per distinct line the group falls on (almost
    always just one line -- one pill)."""
    THEME = theme_mod.THEME
    arabic_font = layout["arabic_font"]
    arabic_size = layout["arabic_size"]
    word_boxes = layout["word_boxes"]

    img = base_image.copy()

    raw_indices = highlight_index if isinstance(highlight_index, list) else [highlight_index]
    highlight_set = {i for i in raw_indices if 0 <= i < len(word_boxes)}

    # --- the highlight pill needs alpha blending and must sit BEHIND the word text,
    #     so composite it onto the background before drawing any text -- grouped by
    #     line (top) so a merged multi-word highlight draws one pill per line instead
    #     of one per word, reducing to today's single-box pill when there's just one ---
    if THEME["highlight_enabled"] and THEME["highlight_style"] == "pill" and highlight_set:
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        odraw = ImageDraw.Draw(overlay)
        alpha = int(255 * THEME["highlight_bg_opacity"])
        r, g, b = THEME["highlight_color"]
        lines = {}
        for i in highlight_set:
            lines.setdefault(word_boxes[i]["top"], []).append(word_boxes[i])
        for boxes in lines.values():
            left = min(b["left"] for b in boxes)
            right = max(b["right"] for b in boxes)
            ref = boxes[0]
            pad_x = ref["font_size"] * 0.14
            top = ref["top"] - ref["font_size"] * 0.08
            bottom = ref["top"] + ref["font_size"] * 1.12
            odraw.rounded_rectangle(
                [left - pad_x, top, right + pad_x, bottom],
                radius=ref["font_size"] * 0.24, fill=(r, g, b, alpha),
            )
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")

        # The pill just painted over whatever shadow build_ayah_layout() baked
        # in behind these words (the pill has to sit above the base image to
        # read as a solid highlight), so re-paste their shadow on top of the
        # pill, before the glyphs go on -- otherwise every highlighted word's
        # shadow visibly disappears/thins out the instant it's highlighted.
        if layout.get("arabic_shadow_on"):
            layers = layout["text_shadow_layers"]
            for i in highlight_set:
                box = word_boxes[i]
                _paste_text_shadow(img, box["display"], arabic_font, (box["left"], box["top"]), layers, **box["kwargs"])

    draw = ImageDraw.Draw(img)

    # --- draw the actual word glyphs on top, using the display/kwargs cached
    #     by build_ayah_layout() (no re-measuring) ---
    for idx, box in enumerate(word_boxes):
        fill = tuple(THEME["arabic_color"])
        if THEME["highlight_enabled"] and idx in highlight_set:
            if THEME["highlight_style"] == "underline":
                ly = box["top"] + arabic_size * 1.02
                draw.line([(box["left"], ly), (box["right"], ly)],
                           fill=tuple(THEME["highlight_color"]),
                           width=max(1, int(arabic_size * 0.06)))
            elif THEME["highlight_style"] == "color":
                fill = tuple(THEME["highlight_color"])
        lift_px = box.get("lift_px", 0)
        if lift_px:
            # This word ends in a waqf mark -- nudge it above the font's own
            # (sometimes too-tight) anchor position, same as the shadow
            # baked behind it in build_ayah_layout(). See
            # _composite_lifted_text() for how the isolated-mark compositing
            # works.
            xy = (box["left"], box["top"])
            bbox = draw.textbbox(xy, box["display"], font=arabic_font, **box["kwargs"])
            pad = int(lift_px) + 4
            region = [int(bbox[0]) - pad, int(bbox[1]) - pad, int(bbox[2]) + pad, int(bbox[3]) + pad]
            rw, rh = region[2] - region[0], region[3] - region[1]
            if rw > 0 and rh > 0:
                local_xy = (xy[0] - region[0], xy[1] - region[1])
                layer = _composite_lifted_text("RGBA", (rw, rh), local_xy, box["display"], box["base_display"],
                                                arabic_font, (*fill, 255), lift_px, **box["kwargs"])
                img.paste(layer, (region[0], region[1]), layer)
                draw = ImageDraw.Draw(img)  # paste() mutates img in place; re-grab a draw handle to be safe across Pillow versions
                continue
        draw.text((box["left"], box["top"]), box["display"], font=arabic_font, fill=fill, **box["kwargs"])

    if THEME["highlight_enabled"] and THEME["highlight_pointer_enabled"] and pointer_pos is not None:
        draw_pointer(draw, pointer_pos["x"], pointer_pos["top"], pointer_pos["font_size"], img=img)

    return img


def render_verse_frame(verse: Verse, surah_name_arabic: str, size, show_translation=True,
                        highlight_index=-1, pointer_pos=None, surah_name_text=None):
    """Renders one complete frame in a single call -- convenience wrapper
    around build_ayah_layout() + draw_dynamic_layer() for callers (app.py's
    single-frame preview endpoint) that don't need to reuse the layout
    across multiple frames. Returns (PIL.Image, word_boxes); see
    build_ayah_layout() for what word_boxes contains.

    Hot paths rendering many frames per ayah (video_build.py) should call
    build_ayah_layout() once and draw_dynamic_layer() per frame directly,
    instead of this wrapper, to avoid redoing the layout work every frame."""
    base_img, layout = build_ayah_layout(verse, surah_name_arabic, size, show_translation,
                                          surah_name_text=surah_name_text)
    img = draw_dynamic_layer(base_img, layout, highlight_index, pointer_pos)
    return img, layout["word_boxes"]


def render_outro_frame(size, text="Thank you for watching") -> Image.Image:
    """Simple closing card appended after the last ayah: same background
    (gradient/solid/image) as the rest of the video, with short centered
    text in the theme's gold header color. No per-word layout needed --
    this is a single static frame held for a few seconds by video_build.py."""
    THEME = theme_mod.THEME
    w, h = size

    if THEME["background_style"] == "solid":
        img = Image.new("RGB", size, tuple(THEME["bg_solid"]))
    elif THEME["background_style"] == "image" and THEME.get("background_image"):
        bg = _cached_background_image(THEME["background_image"])
        if bg is not None:
            scale = max(w / bg.width, h / bg.height)
            iw, ih = int(bg.width * scale), int(bg.height * scale)
            bg = bg.resize((iw, ih), Image.LANCZOS)
            img = Image.new("RGB", size)
            img.paste(bg, ((w - iw) // 2, (h - ih) // 2))
            overlay = Image.new("RGBA", size, (0, 0, 0, int(255 * THEME["background_overlay_opacity"])))
            img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
        else:
            img = theme_gradient(size).copy()
    else:
        img = theme_gradient(size).copy()

    draw = ImageDraw.Draw(img)
    shadow_on, shadow_color, _shadow_spread_color, shadow_blur, shadow_dx, shadow_dy = _shadow_settings(h)
    text_shadow_layers = _shadow_layers(shadow_color, shadow_blur, shadow_dx, shadow_dy, h)
    header_shadow_on = shadow_on and THEME.get("text_shadow_header", True)
    font_size = int(h * 0.032)
    font = _cached_font(str(theme_mod.LATIN_FONT_REGULAR), font_size)
    lines = wrap_latin_lines(text, font, int(w * (1 - 2 * THEME["margin_frac"])))
    line_height = font_size * 1.35
    total_h = line_height * len(lines)
    y = (h - total_h) / 2
    for line in lines:
        bbox = font.getbbox(line)
        line_w = bbox[2] - bbox[0]
        x = (w - line_w) / 2
        draw = draw_text_with_shadow(img, draw, line, font, (x, y), tuple(THEME["header_color"]),
                   header_shadow_on, text_shadow_layers)
        y += line_height

    return img