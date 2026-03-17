import base64
import json
from pathlib import Path

import anthropic

SYSTEM_PROMPT = (
    "You are an insurance inventory assistant. You will be shown 1-3 photos taken at a "
    "residence after an insurance loss event. Your job is to identify the ONE primary item "
    "being photographed and extract its product information.\n\n"
    "CRITICAL RULES — follow these exactly:\n"
    "1. Identify ONLY the single main item the photo is about. Do NOT create separate "
    "entries for background objects, props, or anything secondary.\n"
    "2. NEVER list gloves, hands, fingers, or any body part as an item — workers often "
    "hold items to photograph them; the glove/hand is NEVER the inventory item.\n"
    "3. IGNORE surfaces the item rests on (tables, floors, shelves, walls).\n"
    "4. If multiple IDENTICAL items are shown together (e.g., a set of 4 chairs, a pair "
    "of shoes), return ONE entry with quantity > 1.\n"
    "5. Always return exactly ONE item in the array.\n"
    "Return ONLY a valid JSON object — no explanation, no markdown, no code blocks."
)

USER_PROMPT = """Analyze the photo(s) and identify the single primary item being inventoried.

Return exactly this structure with exactly ONE item in the array:
{
  "items": [
    {
      "category": "...",
      "manufacturer": "...",
      "item": "...",
      "model_serial": "...",
      "quantity": 1,
      "confidence": {
        "overall": 0.0,
        "manufacturer": 0.0,
        "item": 0.0,
        "model_serial": 0.0,
        "quantity": 0.0,
        "flags": []
      }
    }
  ]
}

Field rules:
- category: one of [Electronics, Clothing, Shoes, Bedding, Furniture, Books, Toys, Sports,
  Home Decor, Household, Outdoor, Fitness, Apparel, Lumber, Home Improvement, Decor,
  Accessory, Tools, Kitchen, Jewelry, Art, Musical Instruments, Baby & Kids, Pet Supplies]
- manufacturer: brand name visible on item (empty string if not identifiable)
- item: descriptive name including key attributes such as color, size, material, style
- model_serial: any model or serial numbers visible (empty string if none visible)
- quantity: number of identical items visible (usually 1; use 2+ for matched sets/pairs)
- confidence.overall: float 0.0–1.0, your certainty about the overall identification
- confidence.manufacturer/item/model_serial/quantity: float 0.0–1.0 per-field certainty
- confidence.model_serial: use 1.0 if empty string is correct (no label present)
- confidence.flags: strings describing uncertainty or notable observations

Return ONLY the JSON object. The "items" array must contain exactly ONE entry."""

MEDIA_TYPES = {
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.png': 'image/png',
    '.webp': 'image/webp',
    '.gif': 'image/gif',
    '.heic': 'image/jpeg',
    '.heif': 'image/jpeg',
}


def _load_image(path: str) -> tuple[str, str]:
    """Return (base64_data, media_type). Converts HEIC/HEIF to JPEG first."""
    p = Path(path)
    ext = p.suffix.lower()

    if ext in ('.heic', '.heif'):
        try:
            from pillow_heif import register_heif_opener
            register_heif_opener()
        except ImportError:
            pass
        from PIL import Image
        import io
        img = Image.open(path).convert('RGB')
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=90)
        data = base64.standard_b64encode(buf.getvalue()).decode('utf-8')
        return data, 'image/jpeg'

    with open(path, 'rb') as f:
        data = base64.standard_b64encode(f.read()).decode('utf-8')
    media_type = MEDIA_TYPES.get(ext, 'image/jpeg')
    return data, media_type


DEFAULT_MODEL = 'claude-haiku-4-5-20251001'

AVAILABLE_MODELS = {
    'claude-haiku-4-5-20251001': 'Haiku 4.5 (faster, cheaper — recommended)',
    'claude-sonnet-4-6':         'Sonnet 4.6 (slower, more accurate)',
}


def process_item(photo_paths: list, api_key: str, model: str = DEFAULT_MODEL) -> list:
    """
    Analyze photos and return a list of identified item dicts.
    Usually returns 1 item; may return multiple if distinct items are detected.
    Each dict has: category, manufacturer, item, model_serial, quantity, confidence.
    """
    client = anthropic.Anthropic(api_key=api_key)

    content = []
    for path in photo_paths:
        image_data, media_type = _load_image(path)
        content.append({
            'type': 'image',
            'source': {'type': 'base64', 'media_type': media_type, 'data': image_data},
        })
    content.append({'type': 'text', 'text': USER_PROMPT})

    response = client.messages.create(
        model=model or DEFAULT_MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{'role': 'user', 'content': content}],
    )

    raw = response.content[0].text.strip()

    # Strip accidental markdown fences
    if raw.startswith('```'):
        lines = raw.split('\n')
        raw = '\n'.join(lines[1:-1]) if len(lines) > 2 else raw.replace('```', '')

    result = json.loads(raw)

    # Support both new {"items": [...]} format and legacy flat object format
    if 'items' in result and isinstance(result['items'], list):
        items_data = result['items']
    else:
        items_data = [result]

    # Normalize confidence sub-fields on each item
    normalized = []
    for item_data in items_data:
        conf = item_data.setdefault('confidence', {})
        for field in ('overall', 'manufacturer', 'item', 'model_serial', 'quantity'):
            conf.setdefault(field, 0.5)
        conf.setdefault('flags', [])
        normalized.append(item_data)

    return normalized
