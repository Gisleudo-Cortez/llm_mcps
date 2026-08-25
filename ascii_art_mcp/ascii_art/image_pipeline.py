"""
Image-based ASCII art pipeline.

Flow: text prompt → OpenRouter image model (chat completions) → PIL → ASCII.
The image data lives in choices[0].message.images[0].image_url.url as a
base64 data-URI — OpenRouter uses a non-standard 'images' field, not 'content'.

Cost: ~$0.04 per call (Imagen 3 flat rate via gemini-2.5-flash-image).
"""

import base64
import io
import os
import sys

import httpx
from PIL import Image

# Character ramp: light (space) → dark ($)
_RAMP = " .`'^\",:;Il!><~+_-?][}{1)(|/tfjrxnuvczXYUJCLQ0OZmwqpdbkhao*#MW&8%B@$"

# Terminal chars are ~2× taller than wide; compensate when computing output height
_TERM_ASPECT = 0.45

OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"

# Best balance of quality, speed and cost on OpenRouter as of 2026-05
DEFAULT_IMAGE_MODEL = "google/gemini-2.5-flash-image"

# Style hints steer image gen toward high-contrast, ASCII-friendly output
_STYLE_HINTS: dict[str, str] = {
    "minimal":  "minimal black line art on white background, simple clean lines",
    "dense":    "detailed black and white ink illustration, white background, high contrast",
    "outline":  "bold clean outline drawing on white background, hollow interior, no fill",
    "block":    "high contrast black silhouette on white background, bold shapes",
    "fine":     "delicate pencil sketch on white background, fine lines, soft shading",
    "detailed": "clear black and white illustration on white background, high contrast",
}


class ImagePipeline:
    """
    Generates ASCII art via:
      1. OpenRouter chat completions → image bytes (base64 data-URI in message.images)
      2. PIL brightness mapping → ASCII string
    """

    def __init__(self, api_key: str | None = None, model: str = DEFAULT_IMAGE_MODEL):
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        if not self.api_key:
            raise EnvironmentError(
                "OPENROUTER_API_KEY is not set.\n"
                "  Add to .env:  OPENROUTER_API_KEY=sk-or-..."
            )
        self.model = model

    # ── Public API ────────────────────────────────────────────────────────────

    def generate(self, subject: str, width: int = 80, style: str | None = None) -> str:
        """Generate ASCII art: OpenRouter image model → PIL → ASCII."""
        prompt = _build_image_prompt(subject, style)
        _status(f"Generating image via {self.model}...")
        image_bytes = self._request_image(prompt)
        _status("Converting image to ASCII...")
        return image_to_ascii(image_bytes, width)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _request_image(self, prompt: str) -> bytes:
        resp = httpx.post(
            OPENROUTER_CHAT_URL,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 2000,
            },
            timeout=90.0,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"OpenRouter returned {resp.status_code}: {resp.text[:300]}"
            )

        data = resp.json()
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError) as e:
            raise RuntimeError(f"Unexpected OpenRouter response shape: {e}\n{data}") from e

        # OpenRouter image models put data in message.images[], not message.content
        images = message.get("images") or []
        if not images:
            raise RuntimeError(
                f"No image in OpenRouter response. message keys: {list(message.keys())}"
            )

        url: str = images[0]["image_url"]["url"]

        if url.startswith("data:"):
            # data:image/png;base64,<payload>
            _, b64_payload = url.split(",", 1)
            return base64.b64decode(b64_payload)
        else:
            # Remote URL — download it
            img_resp = httpx.get(url, timeout=30.0, follow_redirects=True)
            img_resp.raise_for_status()
            return img_resp.content


# ── Standalone helpers (usable without the class) ─────────────────────────────

def image_to_ascii(image_bytes: bytes, width: int = 80) -> str:
    """
    Convert raw image bytes → ASCII string via PIL brightness mapping.

    Auto-detects dark vs. light background so the subject always renders
    as dense characters on a sparse background.
    """
    img = _load_on_white(image_bytes).convert("L")

    aspect = img.height / img.width
    height = max(4, int(width * aspect * _TERM_ASPECT))
    img = img.resize((width, height), Image.LANCZOS)

    pixels = list(img.getdata())
    avg_brightness = sum(pixels) / len(pixels)
    # Dark-background images need inverted ramp so the subject = dense chars
    ramp = _RAMP[::-1] if avg_brightness < 128 else _RAMP

    lines: list[str] = []
    for y in range(height):
        row = ""
        for x in range(width):
            b = img.getpixel((x, y))
            idx = len(ramp) - 1 - int(b / 255 * (len(ramp) - 1))
            row += ramp[idx]
        lines.append(row)

    return "\n".join(lines)


def _load_on_white(image_bytes: bytes) -> Image.Image:
    """Open image and flatten onto a white RGB background (handles transparency)."""
    img = Image.open(io.BytesIO(image_bytes))
    bg = Image.new("RGB", img.size, (255, 255, 255))
    if img.mode in ("RGBA", "LA"):
        bg.paste(img, mask=img.split()[-1])
    else:
        bg.paste(img.convert("RGB"))
    return bg


def _build_image_prompt(subject: str, style: str | None) -> str:
    hint = _STYLE_HINTS.get(style or "", _STYLE_HINTS["detailed"])
    return f"{subject}, {hint}, centered composition, no text, no watermark, no frame"


def _status(msg: str) -> None:
    print(f"  {msg}", file=sys.stderr, flush=True)
