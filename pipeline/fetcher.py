import asyncio
import os
import random

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

from utils.cache import get_cached, set_cache
from utils.logger import get_logger

logger = get_logger(__name__)
MAX_RETRIES = 3

# ── stealth User-Agent pool ───────────────────────────────────────────────────
USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
]


def _pick_headers(site_headers: dict) -> dict:
    """Merge site headers with a random User-Agent if none provided."""
    headers = dict(site_headers)
    if "User-Agent" not in headers:
        headers["User-Agent"] = random.choice(USER_AGENTS)
    return headers


def _browser_config(headers: dict) -> BrowserConfig:
    return BrowserConfig(
        headless=True,
        verbose=False,
        headers=headers,
    )


def _run_config(wait_selector: str = None) -> CrawlerRunConfig:
    return CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        wait_for=wait_selector,
        word_count_threshold=10,
        remove_overlay_elements=True,
        exclude_external_links=True,
    )


async def _crawl(url: str, browser_cfg: BrowserConfig, run_cfg: CrawlerRunConfig, label: str) -> str:
    """Core fetch with retry + exponential backoff."""
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with AsyncWebCrawler(config=browser_cfg) as crawler:
                result = await crawler.arun(url=url, config=run_cfg)

            if not result.success:
                raise RuntimeError(f"crawl4ai error: {result.error_message}")

            markdown = result.markdown.fit_markdown or result.markdown.raw_markdown

            if not markdown or len(markdown.strip()) < 50:
                raise RuntimeError("content too short — page may not have loaded")

            logger.info(f"[fetch] {label} — {len(markdown)} chars")
            return markdown

        except Exception as e:
            last_error = e
            logger.warning(f"[fetch] attempt {attempt}/{MAX_RETRIES} failed for {label}: {e}")
            if attempt < MAX_RETRIES:
                # exponential backoff + random jitter for stealth
                backoff = (2 ** attempt) + random.uniform(0.5, 2.0)
                await asyncio.sleep(backoff)

    raise RuntimeError(f"fetch failed after {MAX_RETRIES} attempts: {last_error}")


async def fetch(site: dict) -> str:
    """Stage 1 — fetch search results page. Returns clean markdown."""
    url       = site["url"]
    wait_for  = site.get("wait_for", "networkidle")
    cache_on  = os.getenv("SCRAPER_CACHE_ENABLED", "true").lower() == "true"
    ttl       = int(os.getenv("SCRAPER_CACHE_TTL_MINUTES", "60"))
    headers   = _pick_headers(site.get("headers", {}))

    if cache_on:
        cached = get_cached(url, ttl)
        if cached:
            logger.info(f"[cache hit] {site['name']}")
            return cached

    wait_selector = None if wait_for == "networkidle" else wait_for
    markdown = await _crawl(url, _browser_config(headers), _run_config(wait_selector), site["name"])

    if cache_on:
        set_cache(url, markdown)

    return markdown


async def fetch_product_page(product_url: str, site: dict) -> str:
    """Stage 2 — fetch individual product page for review extraction."""
    cache_on = os.getenv("SCRAPER_CACHE_ENABLED", "true").lower() == "true"
    ttl      = int(os.getenv("SCRAPER_CACHE_TTL_MINUTES", "60"))
    headers  = _pick_headers(site.get("headers", {}))

    if cache_on:
        cached = get_cached(product_url, ttl)
        if cached:
            logger.info(f"[cache hit] {product_url[:60]}")
            return cached

    markdown = await _crawl(product_url, _browser_config(headers), _run_config(None), product_url[:60])

    if cache_on:
        set_cache(product_url, markdown)

    return markdown
