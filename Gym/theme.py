# Gym/theme.py
THEME_PRESETS = {
    'default': '#ff5a00',
    'blue':    '#2b7de9',
    'black':   '#2a2a2a',
    'pink':    '#ec4899',
    'green':   '#2ecc71',
    'red':     '#e53935',
}

def resolve_theme_color(gym):
    """theme_color is the source of truth for CSS; presets just set it."""
    if gym.theme == 'custom':
        return gym.theme_color or '#ff5a00'
    return THEME_PRESETS.get(gym.theme, gym.theme_color or '#ff5a00')