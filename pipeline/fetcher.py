import asyncio
import os
import random

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
from camoufox.async_api import AsyncCamoufox

from utils.cache import get_cached, set_cache
from utils.logger import get_logger

logger = get_logger(__name__)

MAX_RETRIES = 3
MIN_CONTENT_LENGTH = 50

# ── stealth User-Agent pool ───────────────────────────────────────────────────
USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
]


def _pick_headers(site_headers: dict) -> dict:
    headers = dict(site_headers)
    if "User-Agent" not in headers:
        headers["User-Agent"] = random.choice(USER_AGENTS)
    return headers


def _browser_config(headers: dict) -> BrowserConfig:
    return BrowserConfig(headless=True, verbose=False, headers=headers)


def _run_config(wait_selector: str = None) -> CrawlerRunConfig:
    return CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        wait_for=wait_selector,
        word_count_threshold=10,
        remove_overlay_elements=True,
        exclude_external_links=True,
    )


# ── Crawl4AI fetch ────────────────────────────────────────────────────────────

async def _crawl4ai_fetch(url: str, headers: dict, wait_selector: str, label: str) -> str:
    """Fetch using Crawl4AI (Chromium). Returns markdown or raises."""
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with AsyncWebCrawler(config=_browser_config(headers)) as crawler:
                result = await crawler.arun(url=url, config=_run_config(wait_selector))

            if not result.success:
                raise RuntimeError(f"crawl4ai error: {result.error_message}")

            markdown = result.markdown.fit_markdown or result.markdown.raw_markdown

            if not markdown or len(markdown.strip()) < MIN_CONTENT_LENGTH:
                raise RuntimeError("content too short — page may not have loaded")

            logger.info(f"[crawl4ai] {label} — {len(markdown)} chars")
            return markdown

        except Exception as e:
            last_error = e
            logger.warning(f"[crawl4ai] attempt {attempt}/{MAX_RETRIES} failed for {label}: {e}")
            if attempt < MAX_RETRIES:
                backoff = (2 ** attempt) + random.uniform(0.5, 2.0)
                await asyncio.sleep(backoff)

    raise RuntimeError(str(last_error))


# ── Camoufox fallback fetch ───────────────────────────────────────────────────

async def _camoufox_fetch(url: str, label: str) -> str:
    """
    Fallback fetch using Camoufox (stealth Firefox).
    Used when Crawl4AI is blocked by anti-bot protection.
    """
    logger.info(f"[camoufox] attempting stealth fetch for {label}")

    try:
        async with AsyncCamoufox(headless=True, geoip=True) as browser:
            page = await browser.new_page()

            # human-like: random viewport
            await page.set_viewport_size({
                "width":  random.choice([1280, 1366, 1440, 1920]),
                "height": random.choice([720, 768, 800, 900]),
            })

            await page.goto(url, wait_until="networkidle", timeout=60000)

            # small random pause to mimic human reading
            await asyncio.sleep(random.uniform(1.5, 3.0))

            content = await page.content()
            await page.close()

        if not content or len(content.strip()) < MIN_CONTENT_LENGTH:
            raise RuntimeError("camoufox: content too short")

        # strip HTML tags to get readable text for LLM
        from crawl4ai import AsyncWebCrawler
        # use crawl4ai's markdown converter on the raw HTML
        from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
        generator = DefaultMarkdownGenerator()
        markdown = generator.generate_markdown(
            cleaned_html=content,
            base_url=url,
        ).raw_markdown

        if not markdown or len(markdown.strip()) < MIN_CONTENT_LENGTH:
            raise RuntimeError("camoufox: markdown conversion produced empty output")

        logger.info(f"[camoufox] {label} — {len(markdown)} chars (stealth success)")
        return markdown

    except Exception as e:
        raise RuntimeError(f"camoufox fetch failed for {label}: {e}")


# ── main fetch (crawl4ai → camoufox fallback) ─────────────────────────────────

async def _fetch_with_fallback(url: str, site: dict, label: str) -> str:
    """
    Try Crawl4AI first. If it fails or returns thin content,
    automatically fall back to Camoufox.
    """
    headers       = _pick_headers(site.get("headers", {}))
    wait_for      = site.get("wait_for", "networkidle")
    wait_selector = None if wait_for == "networkidle" else wait_for

    try:
        return await _crawl4ai_fetch(url, headers, wait_selector, label)
    except RuntimeError as e:
        logger.warning(f"[fetch] crawl4ai failed for {label} — trying camoufox fallback: {e}")
        return await _camoufox_fetch(url, label)


# ── public API ────────────────────────────────────────────────────────────────

async def fetch(site: dict) -> str:
    """Stage 1 — fetch search results page."""
    url      = site["url"]
    label    = site["name"]
    cache_on = os.getenv("SCRAPER_CACHE_ENABLED", "true").lower() == "true"
    ttl      = int(os.getenv("SCRAPER_CACHE_TTL_MINUTES", "60"))

    if cache_on:
        cached = get_cached(url, ttl)
        if cached:
            logger.info(f"[cache hit] {label}")
            return cached

    markdown = await _fetch_with_fallback(url, site, label)

    if cache_on:
        set_cache(url, markdown)

    return markdown


async def fetch_product_page(product_url: str, site: dict) -> str:
    """Stage 2 — fetch individual product page for review extraction."""
    cache_on = os.getenv("SCRAPER_CACHE_ENABLED", "true").lower() == "true"
    ttl      = int(os.getenv("SCRAPER_CACHE_TTL_MINUTES", "60"))
    label    = product_url[:60]

    if cache_on:
        cached = get_cached(product_url, ttl)
        if cached:
            logger.info(f"[cache hit] {label}")
            return cached

    markdown = await _fetch_with_fallback(product_url, site, label)

    if cache_on:
        set_cache(product_url, markdown)

    return markdown
