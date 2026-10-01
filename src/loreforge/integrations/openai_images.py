"""OpenAI image generation via the Responses API (ChatGPT's internal image pipeline).

Ported from ``generate_thumbnail.py``: gpt-4o receives a natural-language request and
internally calls the image_generation tool — the same flow as typing into ChatGPT.
"""
from __future__ import annotations

import base64
from pathlib import Path

from loreforge.config.settings import get_settings

# 1536x1024 = landscape 16:9 (YouTube); 1024x1536 = portrait 9:16 (Shorts/TikTok)
SIZE_LANDSCAPE = "1536x1024"
SIZE_PORTRAIT = "1024x1536"

_MIME_BY_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".webp": "image/webp"}


def _to_data_url(path: Path) -> str:
    """Read an image file and return it as a base64 ``data:`` URL."""
    mime = _MIME_BY_EXT.get(path.suffix.lower(), "image/png")
    b64 = base64.b64encode(path.read_bytes()).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def generate_image(
    request: str,
    out_path: Path,
    *,
    portrait: bool = False,
    quality: str = "high",
    reference_images: list[Path] | None = None,
    on_log=None,
) -> Path:
    """Generate an image from a natural-language request and write it to ``out_path``.

    If ``reference_images`` is given, they are passed to the model as ``input_image``
    blocks (style/composition guides) alongside the text prompt.
    """
    from openai import OpenAI

    settings = get_settings()
    client = OpenAI(api_key=settings.require("openai_api_key"))
    size = SIZE_PORTRAIT if portrait else SIZE_LANDSCAPE

    if reference_images:
        model_input = [{
            "role": "user",
            "content": [
                {"type": "input_text", "text": request},
                *({"type": "input_image", "image_url": _to_data_url(p), "detail": "auto"}
                  for p in reference_images),
            ],
        }]
    else:
        model_input = request

    if on_log:
        refs_note = f" + {len(reference_images)} ref(s)" if reference_images else ""
        on_log(f"Requesting image ({size}){refs_note}: {request[:100]}...")

    # Reference images are passed as input_image blocks (see above). Note: gpt-image-2
    # already uses high input fidelity automatically and REJECTS an explicit
    # input_fidelity param, so we don't set it here.
    response = client.responses.create(
        model="gpt-4o",
        input=model_input,
        tools=[{"type": "image_generation", "quality": quality, "size": size}],
    )
    image_data = next(
        (item.result for item in response.output
         if item.type == "image_generation_call"),
        None,
    )
    if not image_data:
        # Usually the model answered in text instead (a refusal or a question): surface it.
        said = (getattr(response, "output_text", "") or "").strip()
        calls = [f"{item.type}:{getattr(item, 'status', '?')}" for item in response.output]
        raise RuntimeError("No image returned by the Responses API"
                           + (f". The model said: {said[:500]}" if said else "")
                           + f" (output: {calls})")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(base64.b64decode(image_data))
    if on_log:
        on_log(f"Image saved: {out_path.name}")
    return out_path
