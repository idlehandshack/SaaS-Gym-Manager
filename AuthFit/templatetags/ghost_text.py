from django import template
from django.utils.safestring import mark_safe

from AuthFit.svg_glyphs import GLYPHS, DEFAULT_KERNING, SPACE_WIDTH, FALLBACK_GLYPH

register = template.Library()


def _build(word):
    if not word:
        return "", 0

    word = word.strip().upper()
    cursor_x = 0
    paths = []

    for ch in word:
        if ch == " ":
            cursor_x += SPACE_WIDTH
            continue

        glyph = GLYPHS.get(ch, FALLBACK_GLYPH)
        paths.append(
            f'<g transform="translate({cursor_x},0)">'
            f'<path class="ghost-letter-path" pathLength="400" d="{glyph["d"]}" />'
            f'</g>'
        )
        cursor_x += glyph["width"] + DEFAULT_KERNING

    total_width = max(cursor_x - DEFAULT_KERNING, 0)
    return "".join(paths), total_width


@register.simple_tag
def ghost_letter_paths(word):
    paths, _ = _build(word)
    return mark_safe(f'<g class="ghost-italic-group" transform="skewX(-12)">{paths}</g>')


@register.simple_tag
def ghost_letter_width(word):
    _, width = _build(word)
    return width