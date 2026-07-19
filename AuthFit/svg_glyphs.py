"""
Monoline (single-stroke) uppercase glyph set for the animated ghost-text
footer heading. Each entry is SVG path `d` data drawn on a 0-100 tall grid,
with a `width` used for horizontal layout / kerning.

These are NOT traced from Space Grotesk — they're a simple geometric
plotter-style alphabet designed to look intentional as a hand-drawn
outline animation. If pixel-accurate brand letterforms are needed later,
swap this out for glyphs traced from the actual font file via opentype.js
or fontTools, keeping the same `d` / `width` shape.
"""

GLYPHS = {
    "A": {"d": "M0,100 L27,0 L54,100 M10,65 L44,65", "width": 54},
    "B": {"d": "M6,0 L6,100 M6,0 L32,0 C48,0 48,22 32,25 L6,25 M6,25 L34,25 C52,25 52,50 34,50 L6,50", "width": 52},
    "C": {"d": "M58,15 C48,2 34,0 24,0 C6,0 0,22 0,50 C0,78 6,100 24,100 C34,100 48,98 58,85", "width": 58},
    "D": {"d": "M6,0 L6,100 L26,100 C50,100 62,80 62,50 C62,20 50,0 26,0 Z", "width": 62},
    "E": {"d": "M56,0 L8,0 L8,100 L56,100 M8,50 L44,50", "width": 56},
    "F": {"d": "M8,100 L8,0 L54,0 M8,50 L42,50", "width": 54},
    "G": {"d": "M54,20 C54,8 42,0 30,0 C10,0 0,20 0,50 C0,80 10,100 30,100 C46,100 54,90 54,74 L54,60 L34,60", "width": 54},
    "H": {"d": "M8,0 L8,100 M56,0 L56,100 M8,50 L56,50", "width": 56},
    "I": {"d": "M12,0 L12,100", "width": 24},
    "J": {"d": "M40,0 L40,74 C40,92 28,100 16,100 C4,100 0,90 0,80", "width": 40},
    "K": {"d": "M8,0 L8,100 M52,0 L8,52 M20,60 L54,100", "width": 54},
    "L": {"d": "M8,0 L8,100 L56,100", "width": 56},
    "M": {"d": "M0,100 L0,0 L36,60 L72,0 L72,100", "width": 72},
    "N": {"d": "M8,100 L8,0 L56,100 L56,0", "width": 56},
    "O": {"d": "M32,0 C10,0 0,22 0,50 C0,78 10,100 32,100 C54,100 64,78 64,50 C64,22 54,0 32,0 Z", "width": 64},
    "P": {"d": "M6,100 L6,0 L32,0 C50,0 50,26 32,26 L6,26", "width": 50},
    "Q": {"d": "M32,0 C10,0 0,22 0,50 C0,78 10,100 32,100 C54,100 64,78 64,50 C64,22 54,0 32,0 Z M40,72 L64,100", "width": 64},
    "R": {"d": "M6,100 L6,0 L32,0 C50,0 50,26 32,26 L6,26 M28,26 L56,100", "width": 56},
    "S": {"d": "M56,18 C50,4 36,0 24,0 C10,0 2,8 2,20 C2,44 56,40 56,74 C56,92 42,100 28,100 C14,100 2,94 0,80", "width": 56},
    "T": {"d": "M0,0 L60,0 M30,0 L30,100", "width": 60},
    "U": {"d": "M4,0 L4,66 C4,90 20,100 32,100 C44,100 60,90 60,66 L60,0", "width": 64},
    "V": {"d": "M0,0 L30,100 L60,0", "width": 60},
    "W": {"d": "M0,0 L18,100 L38,30 L58,100 L76,0", "width": 76},
    "X": {"d": "M0,0 L56,100 M56,0 L0,100", "width": 56},
    "Y": {"d": "M0,0 L30,50 L60,0 M30,50 L30,100", "width": 60},
    "Z": {"d": "M0,0 L58,0 L0,100 L58,100", "width": 58},
    "0": {"d": "M32,0 C10,0 0,22 0,50 C0,78 10,100 32,100 C54,100 64,78 64,50 C64,22 54,0 32,0 Z", "width": 64},
    "1": {"d": "M12,20 L30,0 L30,100", "width": 42},
}

DEFAULT_KERNING = 18  # gap between letters
SPACE_WIDTH = 30
FALLBACK_GLYPH = {"d": "M0,100 L0,0", "width": 30}  # thin bar for unmapped chars