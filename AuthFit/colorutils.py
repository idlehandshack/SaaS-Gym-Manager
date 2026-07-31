# colorutils.py — unchanged from before
import re

def _clean_hex(hex_color):
    hex_color = (hex_color or '#ff5a00').lstrip('#')
    if not re.fullmatch(r'[0-9a-fA-F]{6}', hex_color):
        hex_color = 'ff5a00'
    return hex_color

def hex_to_rgba(hex_color, alpha):
    h = _clean_hex(hex_color)
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f'rgba({r},{g},{b},{alpha})'

def lighten(hex_color, amount=0.15):
    h = _clean_hex(hex_color)
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    r = min(255, int(r + (255 - r) * amount))
    g = min(255, int(g + (255 - g) * amount))
    b = min(255, int(b + (255 - b) * amount))
    return f'#{r:02x}{g:02x}{b:02x}'