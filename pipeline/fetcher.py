import asyncio
import os

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

from utils.cache import get_cached, set_cache
from utils.logger import get_logger

logger = get_logger(__name__)
MAX_RETRIES = 3


def _browser_config(headers: dict = {}) -> BrowserConfig:
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
    """Core fetch + retry logic. Returns clean markdown."""
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
                await asyncio.sleep(2 ** attempt)

    raise RuntimeError(f"fetch failed after {MAX_RETRIES} attempts: {last_error}")


async def fetch(site: dict) -> str:
    """
    Stage 1 — fetch search results page.
    Returns clean markdown of the listing page.
    """
    url        = site["url"]
    headers    = site.get("headers", {})
    wait_for   = site.get("wait_for", "networkidle")
    cache_on   = os.getenv("SCRAPER_CACHE_ENABLED", "true").lower() == "true"
    ttl        = int(os.getenv("SCRAPER_CACHE_TTL_MINUTES", "60"))

    # cache check
    if cache_on:
        cached = get_cached(url, ttl)
        if cached:
            logger.info(f"[cache hit] {site['name']}")
            return cached

    wait_selector = None if wait_for == "networkidle" else wait_for
    markdown = await _crawl(
        url,
        _browser_config(headers),
        _run_config(wait_selector),
        site["name"],
    )

    if cache_on:
        set_cache(url, markdown)

    return markdown


async def fetch_product_page(product_url: str, site: dict) -> str:
    """
    Stage 2 — fetch an individual product page for review extraction.
    Returns clean markdown of the product page.
    """
    headers  = site.get("headers", {})
    cache_on = os.getenv("SCRAPER_CACHE_ENABLED", "true").lower() == "true"
    ttl      = int(os.getenv("SCRAPER_CACHE_TTL_MINUTES", "60"))

    if cache_on:
        cached = get_cached(product_url, ttl)
        if cached:
            logger.info(f"[cache hit] {product_url[:60]}")
            return cached

    markdown = await _crawl(
        product_url,
        _browser_config(headers),
        _run_config(None),
        product_url[:60],
    )

    if cache_on:
        set_cache(product_url, markdown)

    return markdown
