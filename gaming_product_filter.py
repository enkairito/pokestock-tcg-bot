"""High-confidence quality gate for Gaming products published to the web.

Amazon occasionally places books, films and music in videogame bestseller
nodes.  These rules intentionally prefer false negatives: they only reject
formats that are clearly not a console, videogame or gaming accessory.
"""
import re
import unicodedata


ISBN_ASIN_RE = re.compile(r"^(?:\d{9}[\dXx]|\d{13})$")
PLATFORM_RE = re.compile(
    r"\b(?:ps[345]|playstation|xbox(?:\s+(?:one|series))?|nintendo|switch(?:\s*2)?|wii|3ds)\b",
    re.IGNORECASE,
)
VIDEO_FORMAT_RE = re.compile(r"\b(?:dvd|blu[ -]?ray)\b", re.IGNORECASE)

NON_GAMING_KEYWORDS = (
    "encyclopedia", "enciclopedia", "libro", "guia", "peluche", "figura",
    "poster", "taza", "camiseta", "vinilo", "vinyl", "soundtrack",
    "banda sonora", "audio cd", "cd de audio", "ocarina de cer",
)

# Some music listings omit any format marker from their Amazon title.
KNOWN_NON_GAMING_TITLES = (
    "minecraft volume alpha",
)


def _normalize(value):
    text = unicodedata.normalize("NFD", str(value or "").lower())
    return "".join(char for char in text if unicodedata.category(char) != "Mn")


def is_non_gaming_title(name):
    """Return True only for a title that is clearly outside Gaming."""
    text = _normalize(name)
    if any(keyword in text for keyword in NON_GAMING_KEYWORDS):
        return True
    if any(title in text for title in KNOWN_NON_GAMING_TITLES):
        return True
    # Physical games are sometimes labelled "[Blu-ray]" by Amazon. Keep
    # those when the title also identifies a supported platform.
    if VIDEO_FORMAT_RE.search(text) and not PLATFORM_RE.search(text):
        return True
    return False


def is_non_gaming_product(asin, name):
    """Reject ISBN-backed books and clearly non-gaming product titles."""
    return bool(ISBN_ASIN_RE.fullmatch(str(asin or ""))) or is_non_gaming_title(name)
