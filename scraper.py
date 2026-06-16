import asyncio
import argparse
import sys
import signal
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlencode, urlparse, urlunparse, parse_qs

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from pipeline.config_loader import load_config
from pipeline.fetcher import fetch, fetch_product_page
from pipeline.extractor import extract, extract_reviews, extract_product_details
from pipeline.storage import save
from utils.logger import get_logger

load_dotenv()
console = Console()
logger = get_logger(__name__)


def _inject_query(sites: list, query: str) -> list:
    for site in sites:
        param = site.get("query_param")
        if not param:
            continue
        parsed = urlparse(site["url"])
        qs = parse_qs(parsed.query)
        qs[param] = [query]
        site["url"] = urlunparse(parsed._replace(query=urlencode(qs, doseq=True)))
        site["name"] = f"{site['name']}-{query.replace(' ', '-')}"
    return sites


async def scrape_site(site: dict, global_cfg: dict, progress, task_id) -> dict:
    name = site["name"]
    result = {"site": name, "status": "success", "records": 0, "error": None}

    reviews_cfg   = site.get("reviews", {})
    reviews_on    = reviews_cfg.get("enabled", False)
    max_reviews   = reviews_cfg.get("max_reviews", 5)
    min_reviews   = reviews_cfg.get("min_reviews", 0)
    review_schema = site.get("review_schema", {})
    product_schema = site.get("product_schema", {})

    try:
        # ── stage 1: fetch + extract search listing ───────────────────────────
        progress.update(task_id, description=f"[cyan]fetching[/]   {name}")
        markdown = await fetch(site)

        progress.update(task_id, description=f"[magenta]extracting[/] {name}")
        records = await extract(markdown, site["schema"], name)

        # filter by min reviews
        if min_reviews:
            before = len(records)
            records = [
                r for r in records
                if (r.get("reviews_count") or 0) >= min_reviews
            ]
            logger.info(f"[filter] {name} — {before} → {len(records)} records (min {min_reviews} reviews)")

        # ── stage 2: visit product page for SKU + reviews ─────────────────────
        if (reviews_on or product_schema) and records:
            progress.update(task_id, description=f"[yellow]enriching[/]  {name}")
            delay = site.get("delay_seconds", global_cfg.get("delay_seconds", 2))

            for i, record in enumerate(records):
                product_url  = record.get("product_url")
                product_name = record.get("product_name", f"product_{i}")

                if not product_url:
                    logger.warning(f"[enrich] skipping '{product_name}' — no product_url")
                    if reviews_on:
                        record["reviews"] = []
                    continue

                try:
                    await asyncio.sleep(delay)
                    product_md = await fetch_product_page(product_url, site)

                    # extract SKU + product details
                    if product_schema:
                        details = await extract_product_details(product_md, product_schema, product_name)
                        record.update(details)   # merge sku, brand, description, specs into record

                    # extract reviews
                    if reviews_on and review_schema:
                        reviews = await extract_reviews(product_md, review_schema, max_reviews, product_name)
                        record["reviews"] = reviews
                        logger.info(f"[reviews] {product_name[:40]} — {len(reviews)} reviews")

                except Exception as e:
                    logger.warning(f"[enrich] failed for '{product_name}': {e}")
                    if reviews_on:
                        record["reviews"] = []

        # ── stage 3: save ─────────────────────────────────────────────────────
        progress.update(task_id, description=f"[green]saving[/]     {name}")
        await save(records, site, global_cfg)

        result["records"] = len(records)
        logger.info(f"{name} → {len(records)} records saved")

    except Exception as e:
        result["status"] = "failed"
        result["error"] = str(e)
        logger.error(f"{name} failed: {e}")

    return result


async def run(config_path: str, query: str = None):
    config = load_config(config_path)
    global_cfg = config["global"]
    sites = config["sites"]

    if query:
        sites = _inject_query(sites, query)

    Path(global_cfg.get("output_dir", "./output")).mkdir(parents=True, exist_ok=True)
    Path("./logs").mkdir(exist_ok=True)

    console.rule("[bold]scraper starting[/bold]")
    console.print(f"  config   : [cyan]{config_path}[/]")
    console.print(f"  query    : [yellow]{query or '(from config URL)'}[/]")
    console.print(f"  sites    : [cyan]{len(sites)}[/]")
    console.print(f"  model    : [cyan]{global_cfg.get('model', 'gpt-4o-mini')}[/]")
    console.print(f"  format   : [cyan]{global_cfg.get('output_format', 'json')}[/]")
    console.print()

    semaphore  = asyncio.Semaphore(global_cfg.get("concurrency", 5))
    start_time = datetime.now(timezone.utc)

    async def bounded_scrape(site, progress, task_id):
        async with semaphore:
            return await scrape_site(site, global_cfg, progress, task_id)

    with Progress(
        SpinnerColumn(),
        TextColumn("{task.description:<45}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        tasks = []
        for site in sites:
            tid = progress.add_task(f"queued     {site['name']}", total=None)
            tasks.append(bounded_scrape(site, progress, tid))

        results = await asyncio.gather(*tasks)

    elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
    table = Table(title="run summary", show_lines=True)
    table.add_column("site",    style="cyan")
    table.add_column("status",  style="bold")
    table.add_column("records", justify="right")
    table.add_column("error",   style="red")

    total_records = 0
    failed = 0
    for r in results:
        status_fmt = "[green]ok[/]" if r["status"] == "success" else "[red]failed[/]"
        table.add_row(r["site"], status_fmt, str(r["records"]), r["error"] or "")
        total_records += r["records"]
        if r["status"] == "failed":
            failed += 1

    console.print()
    console.print(table)
    console.print(f"\n  [bold]total records[/] : {total_records}")
    console.print(f"  [bold]elapsed[/]       : {elapsed:.1f}s")
    console.print(f"  [bold]failed sites[/]  : {failed}/{len(sites)}")
    console.print()

    return 1 if failed else 0


def main():
    parser = argparse.ArgumentParser(description="AI-powered web scraper")
    parser.add_argument("--config", default="scraper_config.yaml")
    parser.add_argument("--query",  default=None, help="product search term")
    args = parser.parse_args()

    def handle_sigint(sig, frame):
        console.print("\n[yellow]interrupted — shutting down...[/]")
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_sigint)
    exit_code = asyncio.run(run(args.config, query=args.query))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
