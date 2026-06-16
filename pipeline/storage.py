import csv
import json
import os
import re
import aiosqlite
from datetime import datetime, timezone
from pathlib import Path

from utils.logger import get_logger

logger = get_logger(__name__)


def _fix_blinkit_urls(records: list) -> list:
    """
    Blinkit product URLs follow the pattern:
      https://blinkit.com/prn/<slug>/prid/<id>
    If product_url is missing or just the domain, reconstruct it from prid.
    """
    for record in records:
        url  = record.get("product_url") or ""
        prid = record.get("prid")

        # url is missing or just the bare domain
        if prid and (not url or url.rstrip("/") == "https://blinkit.com"):
            slug = re.sub(r"[^a-z0-9]+", "-", (record.get("product_name") or "product").lower()).strip("-")
            record["product_url"] = f"https://blinkit.com/prn/{slug}/prid/{prid}"

    return records


def _postprocess(records: list, site: dict) -> list:
    """Apply site-specific post-processing before saving."""
    site_name = site.get("name", "")
    if "blinkit" in site_name:
        records = _fix_blinkit_urls(records)
    return records


def _envelope(records: list, site: dict) -> dict:
    return {
        "site":       site["name"],
        "url":        site["url"],
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "total":      len(records),
        "data":       records,
    }


async def save(records: list, site: dict, global_cfg: dict):
    fmt        = global_cfg.get("output_format", "json")
    output_dir = Path(global_cfg.get("output_dir", "./output"))
    output_dir.mkdir(parents=True, exist_ok=True)
    name       = site["name"]

    # post-process before saving
    records = _postprocess(records, site)

    if fmt == "json":
        await _save_json(records, site, output_dir)
    elif fmt == "csv":
        await _save_csv(records, site, output_dir)
    elif fmt == "sqlite":
        db_path = output_dir / "scraper.db"
        await _save_sqlite(records, site, db_path)
    else:
        raise ValueError(f"unsupported output_format '{fmt}' — use json, csv, or sqlite")

    logger.info(f"[save] {name} → {fmt}")


async def _save_json(records: list, site: dict, output_dir: Path):
    path = output_dir / f"{site['name']}.json"
    envelope = _envelope(records, site)
    path.write_text(json.dumps(envelope, indent=2, ensure_ascii=False), encoding="utf-8")


async def _save_csv(records: list, site: dict, output_dir: Path):
    if not records:
        return
    path = output_dir / f"{site['name']}.csv"
    keys = list(records[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


async def _save_sqlite(records: list, site: dict, db_path: Path):
    if not records:
        return

    table    = site["name"].replace("-", "_")
    cols     = list(records[0].keys())
    col_defs = ", ".join(f'"{c}" TEXT' for c in cols)

    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            f'CREATE TABLE IF NOT EXISTS "{table}" '
            f'(id INTEGER PRIMARY KEY AUTOINCREMENT, scraped_at TEXT, {col_defs})'
        )
        scraped_at   = datetime.now(timezone.utc).isoformat()
        placeholders = ", ".join("?" for _ in cols)
        col_names    = ", ".join(f'"{c}"' for c in cols)

        for row in records:
            values = [str(row.get(c, "")) for c in cols]
            await db.execute(
                f'INSERT INTO "{table}" (scraped_at, {col_names}) VALUES (?, {placeholders})',
                [scraped_at] + values,
            )
        await db.commit()
