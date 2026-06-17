# talkdock-webscrapper

An AI-powered web scraper that extracts structured product data from multiple e-commerce and quick-commerce sites using ChatGPT for intelligent extraction — no fragile CSS selectors, survives DOM changes automatically.

## Features

- **Multi-site** — Flipkart, Amazon, Zepto, Blinkit (easily extensible)
- **AI extraction** — ChatGPT reads clean markdown and extracts structured data based on your schema
- **Two-stage pipeline** — stage 1 extracts product listings, stage 2 visits each product page for SKU + reviews
- **Smart not-found detection** — detects when a product isn't listed on a site instead of returning irrelevant results
- **Stealth fetching** — Crawl4AI (Chromium) with automatic Camoufox (Firefox) fallback for bot-protected sites
- **User-Agent rotation** — randomised UA pool + jitter to avoid detection
- **Configurable schema** — define what to extract in plain English YAML, no code changes needed
- **Multiple output formats** — JSON, CSV, SQLite
- **Review extraction** — fetches and extracts customer reviews per product
- **Async + concurrent** — scrapes multiple sites in parallel

---

## Project structure

```
talkdock-webscrapper/
├── scraper.py                  ← entry point
├── scraper_config.yaml         ← sites, schemas, settings
├── requirements.txt
├── .env                        ← API keys (never commit)
├── .env.example                ← template
├── .gitignore
├── pipeline/
│   ├── config_loader.py        ← YAML/JSON config parser + validation
│   ├── fetcher.py              ← Crawl4AI + Camoufox fallback
│   ├── extractor.py            ← ChatGPT schema-aware extraction
│   └── storage.py              ← JSON / CSV / SQLite output
├── utils/
│   ├── logger.py               ← structured logging (console + file)
│   └── cache.py                ← URL-based markdown cache with TTL
├── output/                     ← generated output files (gitignored)
└── logs/                       ← run logs (gitignored)
```

---

## Setup

### Prerequisites

- Python 3.12+
- OpenAI API key

### Install

```bash
# clone the repo
git clone https://github.com/your-org/talkdock-webscrapper.git
cd talkdock-webscrapper

# create virtual environment
python3.12 -m venv .venv
source .venv/bin/activate        # macOS / Linux
# .venv\Scripts\activate         # Windows

# install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# install browser binaries
crawl4ai-setup                   # downloads Chromium for Crawl4AI
python -m camoufox fetch         # downloads Firefox for Camoufox fallback

# verify Crawl4AI is working
crawl4ai-doctor
```

### Configure API key

```bash
cp .env.example .env
# edit .env and add your OpenAI API key
```

`.env` file:
```
OPENAI_API_KEY=sk-proj-...
OPENAI_MODEL=gpt-4o-mini
```

---

## Usage

### Basic run

```bash
python scraper.py --config scraper_config.yaml --query "iphone 15"
```

### Search a specific product across all sites

```bash
python scraper.py --config scraper_config.yaml --query "samsung galaxy s24"
python scraper.py --config scraper_config.yaml --query "rice bran oil"
python scraper.py --config scraper_config.yaml --query "apple juice"
```

### Use a different config file

```bash
python scraper.py --config my_custom_config.yaml --query "laptop"
```

---

## Output

Results are saved to `./output/` as one file per site:

```
output/
├── flipkart-iphone-15.json
├── amazon-iphone-15.json
├── zepto-iphone-15.json
└── blinkit-iphone-15.json
```

### Sample output (with reviews)

```json
{
  "site": "flipkart-iphone-15",
  "url": "https://www.flipkart.com/search?q=iphone+15",
  "scraped_at": "2025-06-16T10:32:11Z",
  "total": 8,
  "data": [
    {
      "product_name": "Apple iPhone 15 (128GB, Black)",
      "product_url": "https://www.flipkart.com/apple-iphone-15/p/itm...",
      "mrp": 79900.0,
      "selling_price": 69999.0,
      "discount_percent": 12,
      "rating": 4.6,
      "reviews_count": 18234,
      "in_stock": true,
      "sku": "MOBH4FGGZYFVHNDK",
      "brand": "Apple",
      "specifications": ["6.1 inch display", "48MP camera", "A16 Bionic chip"],
      "reviews": [
        {
          "reviewer": "Rahul M.",
          "rating": 5,
          "date": "2025-05-10",
          "title": "Best phone I have used",
          "comment": "Camera quality is outstanding. Battery lasts all day."
        }
      ]
    }
  ]
}
```

### Not found detection

When a product is not listed on a site (e.g. Motorola g57 on Blinkit):

```
┌──────────────────────┬───────────┬─────────┐
│ site                 │ status    │ records │
├──────────────────────┼───────────┼─────────┤
│ flipkart-Motorola-g57│ ok        │ 8       │
│ amazon-Motorola-g57  │ ok        │ 2       │
│ zepto-Motorola-g57   │ not found │ 0       │
│ blinkit-Motorola-g57 │ not found │ 0       │
└──────────────────────┴───────────┴─────────┘
```

---

## Configuration

All scraping behaviour is controlled via `scraper_config.yaml`.

### Global settings

```yaml
global:
  concurrency: 2          # parallel sites at once
  delay_seconds: 3        # delay between requests
  output_dir: ./output
  output_format: json     # json | csv | sqlite
  cache_enabled: false
  model: gpt-4o-mini      # gpt-4o | gpt-4o-mini | gpt-4.1
```

### Adding a new site

```yaml
sites:
  - name: mynewsite
    url: https://www.mynewsite.com/search
    query_param: q                  # URL param for search query
    js_enabled: true                # true = Playwright browser
    wait_for: networkidle           # or a CSS selector e.g. ".results"
    reviews:
      enabled: true
      max_reviews: 5
      min_reviews: 10
    schema:
      product_name:
        description: full product name
        type: string
      price:
        description: selling price, number only
        type: float
```

### Schema types supported

| type | example |
|---|---|
| `string` | `"Apple iPhone 15"` |
| `float` | `69999.0` |
| `integer` | `18234` |
| `boolean` | `true` |
| `list[string]` | `["6GB RAM", "128GB"]` |

---

## How it works

```
URL + query
    ↓
config_loader   — parse + validate scraper_config.yaml
    ↓
fetcher         — Crawl4AI (Chromium) → clean markdown
                  if blocked → Camoufox (stealth Firefox) fallback
    ↓
extractor       — ChatGPT reads markdown + schema → structured JSON
                  if no relevant products found → not_found signal
    ↓
  [per product]
fetcher         — visit product page URL
extractor       — extract SKU, specs, reviews
    ↓
storage         — write JSON / CSV / SQLite to output/
```

---

## Supported sites

| Site | Type | Reviews | SKU | Notes |
|---|---|---|---|---|
| Flipkart | E-commerce | ✓ | ✓ | Full product + review extraction |
| Amazon | E-commerce | ✓ | ✓ | Anti-bot recovery via retry |
| Zepto | Quick-commerce | ✗ | ✓ | Grocery/essentials only |
| Blinkit | Quick-commerce | ✗ | ✓ | Grocery/essentials only |

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | required | Your OpenAI API key |
| `OPENAI_MODEL` | `gpt-4o-mini` | Model to use for extraction |
| `SCRAPER_CACHE_ENABLED` | `true` | Enable markdown cache |
| `SCRAPER_CACHE_TTL_MINUTES` | `60` | Cache expiry in minutes |
| `LOG_LEVEL` | `INFO` | `DEBUG / INFO / WARNING / ERROR` |
| `LOG_FILE` | `./logs/scraper.log` | Log file path |

---

## License

MIT
