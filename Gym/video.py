import re

def extract_youtube_id(url: str) -> str | None:
    if not url:
        return None
    patterns = [
        r'(?:youtube\.com/watch\?v=)([\w-]{11})',
        r'(?:youtu\.be/)([\w-]{11})',
        r'(?:youtube\.com/embed/)([\w-]{11})',
        r'(?:youtube\.com/shorts/)([\w-]{11})',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None