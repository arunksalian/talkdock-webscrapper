import json
import os

from openai import AsyncOpenAI

from utils.logger import get_logger

logger = get_logger(__name__)
client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def _schema_to_lines(schema: dict) -> str:
    lines = []
    for key, val in schema.items():
        if isinstance(val, dict):
            desc     = val.get("description", key)
            typ      = val.get("type", "string")
            example  = val.get("example")
            fmt      = val.get("format")
            nullable = val.get("nullable", False)
            line = f"- {key}: {desc} (type: {typ}"
            if fmt:
                line += f", format: {fmt}"
            if example is not None:
                line += f", example: {json.dumps(example)}"
            if nullable:
                line += ", nullable: return null if not found"
            line += ")"
        else:
            line = f"- {key}: {val}"
        lines.append(line)
    return "\n".join(lines)


def _build_search_prompt(schema: dict, markdown: str) -> str:
    return f"""You are a data extraction assistant. Extract structured data from the webpage content below.

Return a JSON array of objects. Each object must have exactly these fields:
{_schema_to_lines(schema)}

Rules:
- Return ONLY a valid JSON array, no markdown, no explanation
- Each item on the page = one object in the array
- If a field is not found, return null
- Strip currency symbols and commas from numeric fields
- Dates must be ISO 8601 format

Webpage content:
{markdown[:12000]}
"""


def _build_review_prompt(review_schema: dict, max_reviews: int, markdown: str) -> str:
    return f"""You are a review extraction assistant. Extract customer reviews from the product page below.

Return a JSON array of up to {max_reviews} reviews. Each review object must have exactly these fields:
{_schema_to_lines(review_schema)}

Rules:
- Return ONLY a valid JSON array, no markdown, no explanation
- Extract at most {max_reviews} reviews
- Prefer the most recent or most helpful reviews
- If a field is not found, return null
- Dates must be ISO 8601 format

Product page content:
{markdown[:12000]}
"""


async def _call_llm(prompt: str, label: str) -> list[dict]:
    """Call ChatGPT and return a parsed list of dicts."""
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    for attempt in range(1, 3):
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "You are a precise data extraction assistant. Always respond with valid JSON only."},
                    {"role": "user",   "content": prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0,
            )

            raw = response.choices[0].message.content.strip()
            parsed = json.loads(raw)

            if isinstance(parsed, dict):
                records = next(
                    (v for v in parsed.values() if isinstance(v, list)),
                    [parsed],
                )
            elif isinstance(parsed, list):
                records = parsed
            else:
                raise ValueError(f"unexpected response shape: {type(parsed)}")

            logger.info(f"[extract] {label} — {len(records)} records via {model}")
            return records

        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"[extract] attempt {attempt} failed for {label}: {e}")
            if attempt == 2:
                raise RuntimeError(f"extraction failed after 2 attempts: {e}")

    return []


async def extract(markdown: str, schema: dict, site_name: str) -> list[dict]:
    """Stage 1 — extract product listing records from search page markdown."""
    prompt = _build_search_prompt(schema, markdown)
    return await _call_llm(prompt, site_name)


async def extract_reviews(markdown: str, review_schema: dict, max_reviews: int, product_name: str) -> list[dict]:
    """Stage 2 — extract review comments from product page markdown."""
    prompt = _build_review_prompt(review_schema, max_reviews, markdown)
    return await _call_llm(prompt, f"reviews:{product_name[:40]}")


def _build_product_prompt(product_schema: dict, markdown: str) -> str:
    return f"""You are a product detail extraction assistant. Extract product details from the page below.

Return a single JSON object with exactly these fields:
{_schema_to_lines(product_schema)}

Rules:
- Return ONLY a valid JSON object, no markdown, no explanation
- If a field is not found, return null
- For specifications, return a list of short strings like ["6GB RAM", "128GB Storage"]

Product page content:
{markdown[:12000]}
"""


async def extract_product_details(markdown: str, product_schema: dict, product_name: str) -> dict:
    """Extract SKU and other product details from product page markdown."""
    prompt = _build_product_prompt(product_schema, markdown)
    model  = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a precise data extraction assistant. Always respond with valid JSON only."},
                {"role": "user",   "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        raw = response.choices[0].message.content.strip()
        result = json.loads(raw)
        logger.info(f"[product_details] {product_name[:40]} — sku: {result.get('sku')}")
        return result if isinstance(result, dict) else {}
    except Exception as e:
        logger.warning(f"[product_details] failed for {product_name}: {e}")
        return {}
