"""Anthropic Messages API wrapper: streaming, adaptive thinking, prompt caching, costs.

Every call streams (long outputs would otherwise hit HTTP timeouts) and returns the text
plus usage. ``system`` accepts a list of blocks: pass the big, STABLE context first
(style guide, lore bible, outline) and mark it cacheable with ``cached(...)``; the ~15
section calls of one script then read that prefix from cache at ~0.1x cost.

Opus 5 requests opt into server-side refusal fallbacks (``fallbacks="default"``): if a
lore passage about violence or horror trips a safety classifier, the API re-runs the
request on a fallback model inside the same call instead of returning nothing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from loreforge.config.settings import get_settings

# Pin the official API: the SDK otherwise reads ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN
# from the environment, which a Claude Code shell sets for its own proxy.
_API_BASE_URL = "https://api.anthropic.com"
_FALLBACK_BETA = "server-side-fallback-2026-07-01"
_FALLBACK_MODELS = ("claude-opus-5",)


@dataclass
class Completion:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    stop_reason: str = ""

    def cost_deltas(self) -> dict[str, int]:
        """Keyword arguments for ``VideoProject.add_costs``."""
        return {"anthropic_input_tokens": self.input_tokens,
                "anthropic_output_tokens": self.output_tokens,
                "anthropic_cache_read_tokens": self.cache_read_tokens,
                "anthropic_cache_write_tokens": self.cache_write_tokens}


def cached(text: str) -> dict:
    """A system block marked as a cache breakpoint (everything up to it is cached)."""
    return {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}


def plain(text: str) -> dict:
    return {"type": "text", "text": text}


def image_block(data: bytes, media_type: str = "image/png") -> dict:
    """A user content block carrying an image (base64)."""
    import base64

    return {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                        "data": base64.b64encode(data).decode("ascii")}}


def _client():
    import anthropic

    return anthropic.Anthropic(api_key=get_settings().require("anthropic_api_key"),
                               base_url=_API_BASE_URL, max_retries=4)


def complete(system: str | list[dict], user: str | list[dict], *, model: str | None = None,
             max_tokens: int = 32000, effort: str = "high", schema: dict | None = None) -> Completion:
    """Stream one request with adaptive thinking; return the final text and usage.

    ``user`` is a string, or a list of content blocks (e.g. ``image_block`` + text).

    ``schema`` turns on structured outputs: the API guarantees the text is JSON matching
    it (every object needs ``additionalProperties: false``; no min/max constraints).
    """
    settings = get_settings()
    model = model or settings.anthropic_model_script
    output_config: dict = {"effort": effort}
    if schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    kwargs: dict = dict(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        thinking={"type": "adaptive"},
        output_config=output_config,
    )
    client = _client()
    if model in _FALLBACK_MODELS:
        stream = client.beta.messages.stream(betas=[_FALLBACK_BETA], fallbacks="default", **kwargs)
    else:
        stream = client.messages.stream(**kwargs)
    with stream as s:
        msg = s.get_final_message()

    if msg.stop_reason == "refusal":
        details = getattr(msg, "stop_details", None)
        raise RuntimeError(f"Claude declined the request ({getattr(details, 'category', None)}): "
                           f"{getattr(details, 'explanation', '')}")
    text = "".join(b.text for b in msg.content if b.type == "text").strip()
    if msg.stop_reason == "max_tokens":
        where = (f"after {len(text)} chars of answer" if text
                 else "while still thinking, before any answer; lower the effort or split the input")
        raise RuntimeError(f"Output hit max_tokens={max_tokens} {where}.")
    u = msg.usage
    return Completion(
        text=text,
        input_tokens=u.input_tokens or 0,
        output_tokens=u.output_tokens or 0,
        cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
        stop_reason=msg.stop_reason or "",
    )


# --- JSON helpers (tolerant parsing, carried over from cut-forge) ----------------------

def _fix_control_chars(s: str) -> str:
    """Escape raw control characters that appear literally inside JSON string values."""
    result, in_string, escaped = [], False, False
    replacements = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}
    for ch in s:
        if escaped:
            result.append(ch)
            escaped = False
        elif in_string and ch == "\\":
            result.append(ch)
            escaped = True
        elif ch == '"':
            result.append(ch)
            in_string = not in_string
        elif in_string and ch in replacements:
            result.append(replacements[ch])
        elif in_string and ord(ch) < 0x20:
            result.append(f"\\u{ord(ch):04x}")
        else:
            result.append(ch)
    return "".join(result)


def _strip_fences(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
        if raw.endswith("```"):
            raw = raw.rsplit("```", 1)[0]
    return raw.strip()


def parse_json(text: str):
    """Parse a JSON object/array from model output, tolerating fences and stray text."""
    cleaned = _fix_control_chars(_strip_fences(text))
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        starts = [i for i in (cleaned.find("{"), cleaned.find("[")) if i >= 0]
        if starts:
            start = min(starts)
            end = max(cleaned.rfind("}"), cleaned.rfind("]"))
            try:
                return json.loads(cleaned[start:end + 1])
            except json.JSONDecodeError:
                pass
        raise ValueError(f"Claude did not return valid JSON:\n{text[:1000]}")


def complete_json(system: str | list[dict], user: str, **kwargs) -> tuple[object, Completion]:
    """``complete`` + ``parse_json``. Pass ``schema=`` to guarantee the shape."""
    c = complete(system, user, **kwargs)
    return parse_json(c.text), c


# --- Schema helpers ------------------------------------------------------------------

STR = {"type": "string"}
STR_LIST = {"type": "array", "items": STR}
NULLABLE_INT = {"anyOf": [{"type": "integer"}, {"type": "null"}]}


def obj(properties: dict, required: list[str] | None = None) -> dict:
    """A structured-outputs object: all properties required unless told otherwise."""
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required,
            "additionalProperties": False}


def array(items: dict) -> dict:
    return {"type": "array", "items": items}
