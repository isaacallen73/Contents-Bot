import json
import re
import sys
from pathlib import Path

import anthropic

_LOG = Path(__file__).parent.parent / 'price_search_debug.log'

def _log(msg: str):
    """Write debug info to a log file AND stdout (with flush)."""
    line = msg + '\n'
    try:
        with open(_LOG, 'a', encoding='utf-8') as f:
            f.write(line)
    except Exception:
        pass
    print(msg, flush=True)
    sys.stdout.flush()

_SYSTEM_WEB = (
    "You are an insurance replacement-cost specialist helping find retail replacement costs "
    "for household items. Use web search to find current prices."
)

_SYSTEM_FALLBACK = (
    "You are an insurance replacement-cost specialist. Estimate current retail replacement "
    "costs for household items based on your training knowledge."
)

_INITIAL_PROMPT = """\
Find the current retail replacement cost for this item: {item_desc}

Search Amazon, Walmart, Best Buy, Target, Home Depot, or similar major retailers.

Respond with ONLY a raw JSON array — no prose, no markdown, no code fences:
[{{"price": 99.99, "source": "amazon.com", "title": "Exact Product Name", "url": "https://...", "confidence": "high"}}]

Include up to 3 results. confidence = "high" / "medium" / "low" based on match quality.

If the description is too vague, respond with ONLY:
{{"question": "One clarifying question"}}"""

_FOLLOWUP_PROMPT = """\
Based on that answer, search and return prices now.

Respond with ONLY a raw JSON array — no prose, no markdown:
[{{"price": 99.99, "source": "amazon.com", "title": "Product Name", "url": "https://...", "confidence": "high"}}]

Or if still unclear: {{"question": "Next clarifying question"}}"""

_FALLBACK_PROMPT = """\
Estimate the current retail replacement cost for: {item_desc}

Respond with ONLY a raw JSON array — no prose, no markdown, no code fences:
[{{"price": 99.99, "source": "training estimate", "title": "{item_desc}", "url": "", "confidence": "low"}}]

Include 1-3 estimates if there are meaningfully different price tiers."""


def _extract_text(response) -> str:
    """Extract the final text block from a response, handling pause_turn by continuing."""
    # Collect all text blocks (there may be multiple after tool use)
    parts = [b.text for b in response.content
             if hasattr(b, 'text') and b.type == 'text']
    return '\n'.join(parts).strip()


def _parse_json(text: str) -> list | dict | None:
    """
    Try to extract a JSON value (array or object) from text.
    Handles markdown code fences and surrounding prose.
    """
    if not text:
        return None

    # Strip markdown fences
    cleaned = re.sub(r'```(?:json|JSON)?\s*', '', text)
    cleaned = cleaned.replace('```', '').strip()

    # Try greedy array match first (gets the largest [...] block)
    arr_match = re.search(r'\[.*\]', cleaned, re.DOTALL)
    if arr_match:
        try:
            return json.loads(arr_match.group())
        except (json.JSONDecodeError, ValueError):
            pass

    # Try greedy object match
    obj_match = re.search(r'\{.*\}', cleaned, re.DOTALL)
    if obj_match:
        try:
            return json.loads(obj_match.group())
        except (json.JSONDecodeError, ValueError):
            pass

    return None


def _parse_response(text: str, messages: list) -> dict | None:
    """
    Parse Claude's text into a result dict, or return None if unparseable.
    """
    data = _parse_json(text)
    if data is None:
        return None

    # It's a price array
    if isinstance(data, list):
        suggestions = []
        for s in data[:3]:
            try:
                price = float(s.get('price', 0))
                if 0.50 < price < 50000:
                    suggestions.append({
                        'price': price,
                        'source': str(s.get('source', ''))[:50],
                        'title': str(s.get('title', ''))[:80],
                        'url': str(s.get('url', '')),
                        'confidence': s.get('confidence', 'medium'),
                    })
            except (ValueError, TypeError):
                continue
        if suggestions:
            return {'suggestions': suggestions}

    # It's an object — check for question
    if isinstance(data, dict) and 'question' in data:
        new_messages = messages + [{'role': 'assistant', 'content': text}]
        return {'question': data['question'], 'messages': new_messages}

    return None


def search_price(manufacturer: str, item: str, model_serial: str,
                 api_key: str, model: str = None,
                 messages: list = None) -> dict:
    """
    Search for replacement cost using Claude with web search when available.

    messages: prior conversation turns for multi-turn Q&A (None = first call).

    Returns one of:
      {'suggestions': [...]}
      {'question': '...', 'messages': [...]}
      {'error': '...'}
    """
    _log(f'[price_search] called: mfr={manufacturer!r} item={item!r} model={model_serial!r}')
    parts = [p for p in (manufacturer, item, model_serial) if p]
    if not parts:
        _log('[price_search] no parts — returning empty')
        return {'suggestions': []}

    item_desc = ' '.join(parts)
    use_model = model or 'claude-haiku-4-5-20251001'
    client = anthropic.Anthropic(api_key=api_key)

    if messages is None:
        conv = [{'role': 'user', 'content': _INITIAL_PROMPT.format(item_desc=item_desc)}]
    else:
        conv = messages + [{'role': 'user', 'content': _FOLLOWUP_PROMPT}]

    # ── Attempt 1: with web search tool ───────────────────────────────────────
    web_error = None
    try:
        response = client.messages.create(
            model=use_model,
            max_tokens=1024,
            system=_SYSTEM_WEB,
            tools=[{'type': 'web_search_20250305', 'name': 'web_search'}],
            messages=conv,
        )

        # Handle pause_turn: continue the server-side loop if needed
        continuations = 0
        while response.stop_reason == 'pause_turn' and continuations < 3:
            continuations += 1
            cont_messages = conv + [{'role': 'assistant', 'content': response.content}]
            response = client.messages.create(
                model=use_model,
                max_tokens=1024,
                system=_SYSTEM_WEB,
                tools=[{'type': 'web_search_20250305', 'name': 'web_search'}],
                messages=cont_messages,
            )

        text = _extract_text(response)
        _log(f'[price_search] web search response ({response.stop_reason}): {text[:300]!r}')

        result = _parse_response(text, conv)
        if result is not None:
            return result

    except anthropic.BadRequestError as e:
        web_error = str(e)
        _log(f'[price_search] web search BadRequestError: {web_error}')
    except anthropic.APIError as e:
        web_error = str(e)
        _log(f'[price_search] web search APIError: {web_error}')
    except Exception as e:
        web_error = str(e)
        _log(f'[price_search] web search exception: {web_error}')

    # ── Attempt 2: fallback — no web search ───────────────────────────────────
    if messages is None:  # Only on first call, not mid-conversation
        try:
            fallback_response = client.messages.create(
                model=use_model,
                max_tokens=512,
                system=_SYSTEM_FALLBACK,
                messages=[{
                    'role': 'user',
                    'content': _FALLBACK_PROMPT.format(item_desc=item_desc),
                }],
            )
            text = _extract_text(fallback_response)
            _log(f'[price_search] fallback response: {text[:300]!r}')

            result = _parse_response(text, conv)
            if result is not None and 'suggestions' in result:
                return result
        except Exception as e:
            _log(f'[price_search] fallback exception: {e}')

    if web_error:
        return {'error': web_error}
    return {'suggestions': []}
