import hashlib
import os
import time
from pathlib import Path

from utils.logger import get_logger

logger = get_logger(__name__)
CACHE_DIR = Path(".cache")


def _cache_path(url: str) -> Path:
    key = hashlib.md5(url.encode()).hexdigest()
    return CACHE_DIR / f"{key}.md"


def get_cached(url: str, ttl_minutes: int = 60) -> str | None:
    """Return cached markdown for url if it exists and is not expired."""
    path = _cache_path(url)
    if not path.exists():
        return None

    age_minutes = (time.time() - path.stat().st_mtime) / 60
    if age_minutes > ttl_minutes:
        logger.debug(f"cache expired for {url}")
        path.unlink(missing_ok=True)
        return None

    logger.debug(f"cache hit for {url}")
    return path.read_text(encoding="utf-8")


def set_cache(url: str, markdown: str):
    """Save markdown to cache."""
    CACHE_DIR.mkdir(exist_ok=True)
    _cache_path(url).write_text(markdown, encoding="utf-8")
    logger.debug(f"cached {url}")
