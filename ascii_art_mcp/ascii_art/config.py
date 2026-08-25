# Hardware-tuned configuration for RTX 4080 Max-Q (12GB VRAM).
# All Ollama model parameters are set here — do not scatter them.

PRIMARY_MODEL  = "ascii-artist"   # Custom Ollama model (from Modelfile.ascii)
FALLBACK_MODEL = "gemma3:12b"     # Raw model if custom not registered yet
OLLAMA_HOST    = "http://localhost:11434"

DEFAULT_WIDTH  = 80       # Characters wide
DEFAULT_HEIGHT = 40       # Lines tall (approximate — model controls this)
MAX_TOKENS     = 1024     # ASCII art rarely needs more; keeps responses fast
STREAM         = True     # Stream tokens to terminal for live rendering

# These match the Modelfile but can be overridden per-request via options dict
OLLAMA_OPTIONS = {
    "num_ctx":        8192,
    "num_gpu":        999,
    "num_thread":     8,
    "num_batch":      512,
    "repeat_penalty": 1.1,
    "temperature":    0.5,   # lowered from 0.7 — more consistent proportions
    "top_p":          0.85,
    "top_k":          35,
    "num_predict":    MAX_TOKENS,
}

OUTPUT_DIR = "outputs"

# Available style presets exposed by list_styles() and the CLI
STYLES: dict[str, str] = {
    "detailed": "Balanced density with realistic light/shadow gradients (default)",
    "minimal":  "Simple silhouette outline, maximum negative space",
    "dense":    "Rich shading, high character density, detailed shadows",
    "outline":  "Bold structural outline using |/-\\ only, hollow interior",
    "block":    "Heavy # @ % characters for bold high-contrast impact",
    "fine":     "Soft . : ; characters for delicate, low-contrast rendering",
}

# System prompt injected into the draw phase (overrides Modelfile SYSTEM so the
# plan can be embedded at system level where the model gives it more weight)
DRAW_SYSTEM = """\
You are a specialized ASCII art generator. Your ONLY output is raw ASCII art — nothing else.

ABSOLUTE RULES:
1. Output ONLY ASCII art. No prose, no fences (```), no labels, no explanations.
2. Use ONLY printable ASCII: space and: . , : ; - _ ~ = + * # @ % & $ ! | / \\ ^ ( ) [ ] { } < > ? ' " `
3. Every line must be padded to exactly the requested width with trailing spaces.
4. Never repeat a body section twice — one head, one body, one tail.
5. Fill the full canvas — no tiny shape floating in empty space.

CHARACTER DENSITY:
  space       = background
  . , ` '     = lightest highlights
  - ~ = +     = mid-tone flat surfaces
  ; : o       = medium shadow, texture
  # @ & % $   = darkest, solid mass
  | / \\ _     = structural outlines\
"""

# System prompt used for the planning phase (overrides Modelfile SYSTEM)
PLANNING_SYSTEM = (
    "You are a spatial layout planner for ASCII art. "
    "Your response must be exactly 2-3 sentences describing: "
    "(1) the dominant silhouette shape, "
    "(2) vertical zone proportions (e.g. head 30% / body 50% / legs 20%), "
    "(3) which character densities to use for key regions. "
    "Do NOT draw any art. Do NOT use bullet points or lists."
)
