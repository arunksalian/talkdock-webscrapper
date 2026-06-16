import json
import os
from pathlib import Path

import yaml


# ── defaults applied to every site ───────────────────────────────────────────
SITE_DEFAULTS = {
    "js_enabled": True,
    "wait_for": "networkidle",
    "headers": {},
    "delay_seconds": None,   # falls back to global delay if None
}

# ── defaults for the global block ────────────────────────────────────────────
GLOBAL_DEFAULTS = {
    "concurrency": 5,
    "delay_seconds": 2,
    "output_dir": "./output",
    "output_format": "json",
    "cache_enabled": True,
    "cache_ttl_minutes": 60,
    "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
}


def load_config(path: str) -> dict:
    """
    Load and validate scraper config from a YAML or JSON file.

    Returns a dict with two keys:
        config["global"]  → merged global settings
        config["sites"]   → list of validated site configs
    """
    config_path = Path(path)

    if not config_path.exists():
        raise FileNotFoundError(f"config file not found: {path}")

    with open(config_path) as f:
        if config_path.suffix in (".yaml", ".yml"):
            raw = yaml.safe_load(f)
        elif config_path.suffix == ".json":
            raw = json.load(f)
        else:
            raise ValueError(f"unsupported config format: {config_path.suffix}")

    if not isinstance(raw, dict):
        raise ValueError("config must be a YAML/JSON object at the top level")

    # ── merge global defaults ─────────────────────────────────────────────────
    global_cfg = {**GLOBAL_DEFAULTS, **raw.get("global", {})}

    # ── validate sites ────────────────────────────────────────────────────────
    sites_raw = raw.get("sites", [])
    if not sites_raw:
        raise ValueError("config must contain at least one site under 'sites'")

    sites = []
    seen_names = set()

    for i, site in enumerate(sites_raw):
        # required fields
        for field in ("name", "url", "schema"):
            if field not in site:
                raise ValueError(f"site[{i}] is missing required field '{field}'")

        name = site["name"]
        if name in seen_names:
            raise ValueError(f"duplicate site name '{name}'")
        seen_names.add(name)

        if not site["url"].startswith(("http://", "https://")):
            raise ValueError(f"site '{name}' url must start with http:// or https://")

        if not isinstance(site["schema"], dict) or not site["schema"]:
            raise ValueError(f"site '{name}' schema must be a non-empty object")

        # merge site-level defaults
        merged = {**SITE_DEFAULTS, **site}

        # per-site delay falls back to global
        if merged["delay_seconds"] is None:
            merged["delay_seconds"] = global_cfg["delay_seconds"]

        sites.append(merged)

    return {"global": global_cfg, "sites": sites}
