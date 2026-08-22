"""
Drawing a single frame: Arabic shaping, line wrapping, background styles,
and the layout math (column width, translation_position, text_align) that
mirrors the HTML editor's canvas preview. render_verse_frame() is the one
entry point everything else (video_build.py) calls.
"""
import math
import sys

from PIL import Image, ImageDraw, ImageFont

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


def draw_pointer(draw: ImageDraw.ImageDraw, x: float, top_y: float, font_size: float):
    """Vector pointer marker under/over the active word. (Not a real emoji --
    Pillow can't reliably render color emoji without a bundled color-emoji
    font, so 'hand' and 'arrow' both render as a small triangular marker;
    'hand' adds a short stem so it reads more like a pointing finger.)"""
    THEME = theme_mod.THEME
    style = THEME["highlight_pointer_style"]
    gap = THEME.get("highlight_pointer_gap_mult", 1.0)
    r, g, b = THEME["highlight_color"]

    if style in ("hand", "arrow"):
        s = font_size * 0.30
        tip_y = top_y - font_size * 0.15 * gap
        pts = [(x, tip_y), (x - s, tip_y - s), (x + s, tip_y - s)]
        draw.polygon(pts, fill=(r, g, b))
        if style == "hand":
            stem_w = s * 0.55
            draw.rectangle(
                [x - stem_w / 2, tip_y - s - font_size * 0.32 * gap, x + stem_w / 2, tip_y - s],
                fill=(r, g, b),
            )
    elif style == "dot":
        rad = font_size * 0.09
        cy = top_y + font_size * 1.25 * gap
        draw.ellipse([x - rad, cy - rad, x + rad, cy + rad], fill=(r, g, b))


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


def _draw_badge_shape(img, draw, style, cx, cy, half, fill_rgba, border_rgb, border_width_px,
                       line_width_px=0, line_cap_style="dot", line_cap_size_px=0, line_color=None):
    """Draws the badge outline/fill onto `img`, returning the (possibly new)
    (img, draw) pair to keep using -- a new Image is only created when alpha
    blending (a translucent fill) requires compositing.

    line_width_px > 0 additionally draws the optional "line accent": a
    horizontal line (+ end markers) through the badge, drawn UNDERNEATH the
    badge_style shape so the shape's own fill covers the line's middle,
    leaving it visible only where it pokes out past the border on each
    side. This works with every badge_style, including "none" (a bare line
    with no shape around it)."""
    kind, data = _badge_shape(style, cx, cy, half)
    if kind == "none" and line_width_px <= 0:
        return img, draw

    if kind == "none":
        # No shape at all -- just the line accent, drawn straight onto the
        # base draw context (no translucent fill to composite here).
        lc = line_color if line_color is not None else (border_rgb if border_rgb is not None else (255, 255, 255))
        line_half_len = half * 2.6
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
        line_half_len = half * 2.6
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
        line_half_len = half * 2.6
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


def render_verse_frame(verse: Verse, surah_name_arabic: str, size, show_translation=True,
                        highlight_index=-1, pointer_pos=None, surah_name_text=None):
    """Renders one frame. Returns (PIL.Image, word_boxes) where word_boxes is
    a list of {left, right, cx, top, font_size} dicts, one per Arabic word,
    in reading order -- used by video_build.build_video() to time the
    highlight/pointer against each word's on-screen position.

    surah_name_text is the surah's English/Latin name (e.g. "Al-Fatiha").
    Which of it or surah_name_arabic gets drawn as the header is controlled
    by THEME["header_script"] ("arabic" | "text")."""
    THEME = theme_mod.THEME
    w, h = size

    # --- background ---
    if THEME["background_style"] == "solid":
        img = Image.new("RGB", size, tuple(THEME["bg_solid"]))
    elif THEME["background_style"] == "image" and THEME.get("background_image"):
        bg = theme_mod.load_background_image(THEME["background_image"])
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
            img = vertical_gradient(size, tuple(THEME["bg_top"]), tuple(THEME["bg_bottom"])).convert("RGB")
    else:
        img = vertical_gradient(size, tuple(THEME["bg_top"]), tuple(THEME["bg_bottom"])).convert("RGB")
    draw = ImageDraw.Draw(img)

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
            header_font = ImageFont.truetype(str(theme_mod.LATIN_FONT_REGULAR), int(h * THEME["header_size_frac"]))
            hb = draw.textbbox((0, 0), header_text, font=header_font)
            draw.text(((w - (hb[2] - hb[0])) / 2, h * THEME["header_y_frac"]), header_text,
                       font=header_font, fill=tuple(THEME["header_color"]))
        else:
            header_font = ImageFont.truetype(str(theme_mod.ARABIC_FONT_REGULAR), int(h * THEME["header_size_frac"]))
            header_display, header_kwargs = arabic_draw_args(header_text)
            hb = draw.textbbox((0, 0), header_display, font=header_font, **header_kwargs)
            draw.text(((w - (hb[2] - hb[0])) / 2, h * THEME["header_y_frac"]), header_display,
                       font=header_font, fill=tuple(THEME["header_color"]), **header_kwargs)

    # Arabic verse text, sized down if long
    arabic_size = int(h * THEME["arabic_size_max_frac"])
    min_size = int(h * THEME["arabic_size_min_frac"])
    while arabic_size > min_size:
        arabic_font = ImageFont.truetype(str(theme_mod.ARABIC_FONT_BOLD), arabic_size)
        lines = wrap_arabic_lines(verse.arabic, arabic_font, arabic_col_width, draw)
        line_height = int(arabic_size * THEME["arabic_line_height_mult"])
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
            wd_display, wd_kwargs = arabic_draw_args(word)
            wd_bbox = draw.textbbox((0, 0), wd_display, font=arabic_font, **wd_kwargs)
            wd_width = wd_bbox[2] - wd_bbox[0]
            left = cursor - wd_width
            word_boxes.append({"left": left, "right": cursor, "cx": left + wd_width / 2,
                                "top": y, "font_size": arabic_size})
            cursor -= wd_width + space_width

    # --- the highlight pill needs alpha blending and must sit BEHIND the word text,
    #     so composite it onto the background before drawing any text ---
    if THEME["highlight_enabled"] and THEME["highlight_style"] == "pill" and 0 <= highlight_index < len(word_boxes):
        box = word_boxes[highlight_index]
        overlay = Image.new("RGBA", size, (0, 0, 0, 0))
        odraw = ImageDraw.Draw(overlay)
        pad_x = box["font_size"] * 0.14
        top = box["top"] - box["font_size"] * 0.08
        bottom = box["top"] + box["font_size"] * 1.12
        alpha = int(255 * THEME["highlight_bg_opacity"])
        r, g, b = THEME["highlight_color"]
        odraw.rounded_rectangle(
            [box["left"] - pad_x, top, box["right"] + pad_x, bottom],
            radius=box["font_size"] * 0.24, fill=(r, g, b, alpha),
        )
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(img)

    # --- pass 2: draw the actual word glyphs on top ---
    global_idx = 0
    for words, right_edge, y in line_layout:
        cursor = right_edge
        for word in words:
            wd_display, wd_kwargs = arabic_draw_args(word)
            wd_bbox = draw.textbbox((0, 0), wd_display, font=arabic_font, **wd_kwargs)
            wd_width = wd_bbox[2] - wd_bbox[0]
            fill = tuple(THEME["arabic_color"])
            if THEME["highlight_enabled"] and global_idx == highlight_index:
                if THEME["highlight_style"] == "underline":
                    ly = y + arabic_size * 1.02
                    draw.line([(cursor - wd_width, ly), (cursor, ly)],
                               fill=tuple(THEME["highlight_color"]),
                               width=max(1, int(arabic_size * 0.06)))
                elif THEME["highlight_style"] == "color":
                    fill = tuple(THEME["highlight_color"])
            draw.text((cursor - wd_width, y), wd_display, font=arabic_font, fill=fill, **wd_kwargs)
            cursor -= wd_width + space_width
            global_idx += 1

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
        badge_font = ImageFont.truetype(str(badge_font_path), badge_font_size)
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
        line_color = tuple(THEME.get("badge_border_color", THEME["badge_color"]))
        if THEME.get("badge_line_enabled", False):
            line_width_val = THEME.get("badge_line_width", 0.15)
            line_width_px = max(1, round(box_size * 0.14 * line_width_val))
            cap_size_val = THEME.get("badge_line_cap_size", 0.30)
            line_cap_size_px = max(line_width_px * 1.3, half * 0.5 * cap_size_val)

        img, draw = _draw_badge_shape(img, draw, style, badge_cx, badge_cy, half,
                                       fill_rgba, border_color, border_width_px,
                                       line_width_px, line_cap_style, line_cap_size_px, line_color)

        # Plain digits are drawn left-to-right with no direction/language kwargs
        # (unlike the Arabic verse/header text above) so the bbox-based centering
        # below lands the number dead-center in the badge instead of skewed by
        # RTL shaping.
        draw.text((badge_cx - text_w / 2 - bb[0], badge_cy - text_h / 2 - bb[1]), badge_display,
                   font=badge_font, fill=tuple(THEME["badge_color"]))
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
        trans_font = ImageFont.truetype(str(theme_mod.LATIN_FONT_REGULAR), trans_size)
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
            draw.text((x, y), line, font=trans_font, fill=tuple(THEME["translation_color"]))

    if THEME["highlight_enabled"] and THEME["highlight_pointer_enabled"] and pointer_pos is not None:
        draw_pointer(draw, pointer_pos["x"], pointer_pos["top"], pointer_pos["font_size"])

    return img, word_boxes