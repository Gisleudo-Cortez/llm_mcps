import os
import sys
import ollama
from ascii_art.config import (
    PRIMARY_MODEL,
    FALLBACK_MODEL,
    OLLAMA_OPTIONS,
    OLLAMA_HOST,
    STREAM,
    STYLES,
    PLANNING_SYSTEM,
)


class ASCIIArtGenerator:
    """
    ASCII art generator supporting two backends:

    - "image"  (default when OPENROUTER_API_KEY is set):
        text → OpenRouter FLUX image → PIL brightness mapping → ASCII
        Fast (~5s), deterministic, accurate proportions.

    - "llm"  (fallback, local Ollama):
        text → plan → gemma3:12b draw → ASCII
        Slower (~3min), approximate, no API key needed.
    """

    def __init__(self, model: str = PRIMARY_MODEL, host: str | None = None):
        self._requested_model = model
        self._client = ollama.Client(host=host or OLLAMA_HOST)
        self._model: str | None = None

    # ── Public API (MCP-facing) ───────────────────────────────────────────────

    def generate(
        self,
        subject: str,
        width: int = 80,
        style: str | None = None,
        stream: bool = STREAM,
        extra_options: dict | None = None,
        auto_refine: bool = False,
        backend: str = "auto",
    ) -> str:
        """
        Generate ASCII art for `subject`.

        backend: "auto" | "image" | "llm"
          auto  → image if OPENROUTER_API_KEY is set, else llm
          image → OpenRouter FLUX + PIL conversion (fast, accurate)
          llm   → local Ollama two-phase plan+draw (slow, approximate)
        """
        resolved = self._resolve_backend(backend)

        if resolved == "image":
            from ascii_art.image_pipeline import ImagePipeline
            return ImagePipeline().generate(subject, width=width, style=style)

        # ── LLM path ─────────────────────────────────────────────────────────
        self._ensure_model()
        options = {**OLLAMA_OPTIONS, **(extra_options or {})}

        _status("Planning...")
        plan = self._plan(subject, width, style, options)

        _status("Drawing...")
        raw = self._draw(subject, width, style, plan, options, stream)
        art = self._postprocess(raw, width)

        if auto_refine:
            _status("Refining...")
            raw2 = self._auto_refine_pass(subject, width, style, art, options, stream)
            art = self._postprocess(raw2, width)

        return art

    def refine(
        self,
        art: str,
        feedback: str,
        width: int = 80,
        stream: bool = STREAM,
        extra_options: dict | None = None,
    ) -> str:
        """Improve existing art based on explicit feedback string."""
        self._ensure_model()
        options = {**OLLAMA_OPTIONS, **(extra_options or {})}
        prompt = (
            f"Improve this ASCII art based on the feedback below.\n\n"
            f"Current art:\n{art}\n\n"
            f"Feedback: {feedback}\n\n"
            f"Redraw the art at exactly {width} characters wide, fixing the issues. "
            f"Output ONLY the improved ASCII art, nothing else."
        )
        raw = self._chat([{"role": "user", "content": prompt}], options, stream)
        return self._postprocess(raw, width)

    def list_styles(self) -> dict[str, str]:
        """Return available style presets with descriptions."""
        return dict(STYLES)

    # ── Backend resolution ────────────────────────────────────────────────────

    @staticmethod
    def _resolve_backend(backend: str) -> str:
        if backend == "auto":
            return "image" if os.environ.get("OPENROUTER_API_KEY") else "llm"
        return backend

    def check_model_available(self, model_name: str) -> bool:
        try:
            for m in self._client.list().models:
                if m.model == model_name or m.model == f"{model_name}:latest":
                    return True
            return False
        except Exception:
            return False

    def resolve_model(self) -> str:
        if self.check_model_available(self._requested_model):
            return self._requested_model
        print(
            f"WARNING: '{self._requested_model}' not found, falling back to '{FALLBACK_MODEL}'",
            file=sys.stderr,
        )
        if self.check_model_available(FALLBACK_MODEL):
            return FALLBACK_MODEL
        raise RuntimeError(
            f"Neither '{self._requested_model}' nor '{FALLBACK_MODEL}' is available. "
            f"Run: ollama pull {FALLBACK_MODEL}"
        )

    # ── Internal phases ───────────────────────────────────────────────────────

    def _ensure_model(self) -> None:
        if self._model is None:
            self._model = self.resolve_model()

    def _plan(self, subject: str, width: int, style: str | None, options: dict) -> str:
        """Phase 1: get a concise spatial layout plan (non-streamed, fast)."""
        messages = [
            {"role": "system", "content": PLANNING_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Plan ASCII art of: {subject}. "
                    f"Canvas: {width} chars wide. "
                    f"Style: {style or 'detailed'}."
                ),
            },
        ]
        resp = self._client.chat(model=self._model, messages=messages, options=options, stream=False)
        plan = resp.message.content.strip()
        _status(f"Plan: {plan}")
        return plan

    def _draw(
        self,
        subject: str,
        width: int,
        style: str | None,
        plan: str,
        options: dict,
        stream: bool,
    ) -> str:
        """Phase 2: draw the art, guided by the plan injected as system context."""
        from ascii_art.config import DRAW_SYSTEM
        draw_system = f"{DRAW_SYSTEM}\n\nSPATIAL PLAN — follow this exactly:\n{plan}"
        messages = [
            {"role": "system", "content": draw_system},
            {
                "role": "user",
                "content": (
                    f"Draw ASCII art of: {subject}. "
                    f"Width: {width} characters. "
                    f"Style: {style or 'detailed'}."
                ),
            },
        ]
        return self._chat(messages, options, stream)

    def _auto_refine_pass(
        self,
        subject: str,
        width: int,
        style: str | None,
        art: str,
        options: dict,
        stream: bool,
    ) -> str:
        """Phase 3: critique the art, then redraw with fixes."""
        critique_messages = [
            {
                "role": "system",
                "content": (
                    "You are an ASCII art critic. Identify exactly 2-3 specific issues "
                    "with the proportions or recognisability of the art. Be brief and concrete. "
                    "No suggestions to 'add more detail' — only spatial/structural issues."
                ),
            },
            {
                "role": "user",
                "content": f"Critique this ASCII art of '{subject}':\n\n{art}",
            },
        ]
        critique = self._client.chat(
            model=self._model, messages=critique_messages, options=options, stream=False
        )
        critique_text = critique.message.content.strip()

        redraw_prompt = (
            f"Draw improved ASCII art of: {subject}. "
            f"Width: {width} characters. "
            f"Style: {style or 'detailed'}.\n"
            f"Fix these specific issues from the previous attempt:\n{critique_text}"
        )
        return self._chat([{"role": "user", "content": redraw_prompt}], options, stream)

    # ── Shared helpers ────────────────────────────────────────────────────────

    def _chat(self, messages: list, options: dict, stream: bool) -> str:
        if stream:
            chunks: list[str] = []
            for chunk in self._client.chat(
                model=self._model, messages=messages, options=options, stream=True
            ):
                token = chunk.message.content
                if token:
                    print(token, end="", flush=True)
                    chunks.append(token)
            print()
            return "".join(chunks)
        else:
            resp = self._client.chat(
                model=self._model, messages=messages, options=options, stream=False
            )
            return resp.message.content

    def _postprocess(self, raw: str, width: int) -> str:
        lines = raw.splitlines()

        # Extract content from inside the first markdown fence pair when present
        fence_indices = [i for i, l in enumerate(lines) if l.strip().startswith("```")]
        if len(fence_indices) >= 2:
            lines = lines[fence_indices[0] + 1 : fence_indices[1]]
        elif len(fence_indices) == 1:
            lines = lines[fence_indices[0] + 1 :]

        # Strip leading/trailing blank lines
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()

        return "\n".join(line.ljust(width) for line in lines)


def _status(msg: str) -> None:
    """Print a status line to stderr so stdout stays clean (copy-pasteable art)."""
    print(f"  {msg}", file=sys.stderr, flush=True)
