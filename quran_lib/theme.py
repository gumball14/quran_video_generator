"""
Theme schema + loading.

THEME is the live, mutated-in-place dict every other module reads its
styling from. DEFAULT_THEME is its shape and starting values, and is also
the whitelist of fields load_theme() will accept from a theme.json.

Field names here are meant to exactly mirror the theme.py-shaped object
static/frame_editor.html's stateToTheme() produces, since a theme designed
there is handed to this script as-is via --theme. If you add a field to
one side, add it to the other, or it'll get silently dropped on load
(with a console warning -- see load_theme() below).
"""
import base64
import json
from io import BytesIO
from pathlib import Path

from PIL import Image

from .constants import HERE, FONT_DIR, BG_COLOR_TOP, BG_COLOR_BOTTOM, GOLD, WHITE

ARABIC_FONT_REGULAR = FONT_DIR / "NotoNaskhArabic-Regular.ttf"
ARABIC_FONT_BOLD = FONT_DIR / "NotoNaskhArabic-Bold.ttf"
LATIN_FONT_REGULAR = FONT_DIR / "NotoSans-Regular.ttf"

DEFAULT_THEME = {
    "background_style": "gradient",
    "bg_top": list(BG_COLOR_TOP),
    "bg_bottom": list(BG_COLOR_BOTTOM),
    "bg_solid": list(BG_COLOR_TOP),

    "arabic_color": list(WHITE),
    "translation_color": [200, 205, 210],
    "header_color": list(GOLD),
    "badge_color": list(GOLD),

    "arabic_font_regular": "fonts/NotoNaskhArabic-Regular.ttf",
    "arabic_font_bold": "fonts/NotoNaskhArabic-Bold.ttf",
    "latin_font": "fonts/NotoSans-Regular.ttf",

    "margin_frac": 0.09,
    "header_y_frac": 0.06,
    "header_size_frac": 0.028,
    "arabic_center_y_frac": 0.36,
    "arabic_size_max_frac": 0.052,
    "arabic_size_min_frac": 0.03,
    "arabic_line_height_mult": 1.55,
    "translation_gap_frac": 0.05,
    "translation_size_frac": 0.024,
    "translation_position": "below",  # "below" | "above" | "side"
    "text_align": "center",           # "left" | "center" | "right"

    "show_header": True,
    "header_script": "arabic",        # "arabic" | "text" -- which surah-name string render_verse_frame() draws
    "show_badge": True,
    "show_translation": True,
    "badge_size_frac": 0.022,
    "badge_gap_frac": 0.03,           # 0-0.12, matches the editor's Gap slider -- extra spacing between the badge and its anchor (the header for "below_header", the verse block for "below_arabic")
    "badge_style": "ornament",        # "ornament" | "circle" | "hexagon" | "diamond" | "square" | "ring" | "flower" | "star8" | "scallop" | "none"
    "badge_numeral_style": "western", # "western" (plain digits) | "arabic" (Eastern Arabic-Indic digits) | "roman" (Roman numerals)
    "badge_position": "below_arabic", # "below_header" (right under the surah name) | "below_arabic" (default, under the verse) | "bottom" (pinned near the bottom of the frame)
    "badge_border_color": list(WHITE),
    "badge_border_width": 0.15,       # 0-1, same slider scale as the editor
    "badge_fill_enabled": True,
    "badge_fill_color": list(WHITE),
    "badge_fill_opacity": 0.14,
    "badge_line_enabled": False,      # optional accent: a horizontal line through the badge, poking out past
                                       # whichever badge_style border is active, on both sides. Works with
                                       # every badge_style (including "none") and always uses badge_border_color
                                       # -- there's no separate line color.
    "badge_line_style": "dot",        # "dot" | "arrow" | "diamond" -- shape of the marker at each end of the line
    "badge_line_width": 0.15,         # 0-1, same slider scale as badge_border_width -- this is the line's THICKNESS (stroke weight)
    "badge_line_length": 10,          # 0-100 (%), the line's horizontal LENGTH: total span of both pokes combined, as a
                                       # percentage of the frame width -- 100 stretches the line edge to edge
    "badge_line_cap_size": 0.30,      # 0-1, size of each end marker relative to the badge

    "background_image": None,          # file path OR "data:image/...;base64,..." (from the HTML editor export)
    "background_video": None,          # not yet implemented -- falls back to gradient, see load_theme()
    "background_overlay_opacity": 0.45,
    "ken_burns": False,                # not yet implemented -- accepted but has no effect

    "fade_duration": 0.6,

    "highlight_enabled": False,
    "highlight_style": "pill",
    "highlight_color": [255, 197, 87],
    "highlight_bg_opacity": 0.85,
    "highlight_fallback_wps": 2.2,
    "highlight_pointer_enabled": False,
    "highlight_pointer_style": "hand",
    "highlight_pointer_position": "top",  # "top" (pointer sits above the highlighted word) | "bottom" (pointer sits below it)
    "highlight_pointer_gap_mult": 1.0,

    "outro_enabled": False,           # append a plain background screen (no text) after the last ayah
    "outro_duration": 2.0,            # seconds the outro screen holds, before/into the closing fade
}

# Mutated in place by load_theme() -- render code reads from this dict
# (via `theme.THEME`, not a copied import) so updates are always visible.
THEME = dict(DEFAULT_THEME)


def load_theme(path: Path):
    """Merge a theme.json (as exported by the HTML editor) into THEME.
    Any key not in DEFAULT_THEME is ignored rather than erroring (so an
    editor export from a newer/older frame_editor.html is always safe to
    pass in), but everything the editor currently exposes -- including
    background_image, translation_position and text_align -- is a real,
    supported field; see text_render.render_verse_frame() for how each
    is used. background_video and ken_burns are accepted (won't be
    dropped/warned about) but not yet implemented -- background_style:
    "video" still falls back to the gradient background."""
    global ARABIC_FONT_REGULAR, ARABIC_FONT_BOLD, LATIN_FONT_REGULAR
    with open(path, "r", encoding="utf-8") as f:
        loaded = json.load(f)
    unsupported = [k for k in loaded if k not in DEFAULT_THEME]
    THEME.update({k: v for k, v in loaded.items() if k in DEFAULT_THEME})
    if unsupported:
        print(f"  (theme.json: ignoring fields not supported by this script: {', '.join(sorted(unsupported))})")
    if THEME.get("background_style") == "video":
        print("  (theme.json: background_style 'video' isn't implemented yet -- using the gradient background instead)")
    if "arabic_font_regular" in loaded:
        ARABIC_FONT_REGULAR = HERE / loaded["arabic_font_regular"]
    if "arabic_font_bold" in loaded:
        ARABIC_FONT_BOLD = HERE / loaded["arabic_font_bold"]
    if "latin_font" in loaded:
        LATIN_FONT_REGULAR = HERE / loaded["latin_font"]


def load_background_image(spec: str):
    """Loads a background_image value that's either a data URL (as embedded
    by the HTML editor's export) or a file path (relative to the project
    root). Returns a PIL.Image, or None (with a printed warning) on failure."""
    try:
        if spec.startswith("data:image"):
            header, b64data = spec.split(",", 1)
            raw = base64.b64decode(b64data)
            return Image.open(BytesIO(raw)).convert("RGB")
        path = Path(spec)
        if not path.is_absolute():
            path = HERE / path
        return Image.open(path).convert("RGB")
    except Exception as e:
        print(f"  (warning: couldn't load background_image ({spec[:60]}...): {e})")
        return None