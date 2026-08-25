import subprocess
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

console = Console()


def display_art(art: str, title: str = "") -> None:
    text = Text(art, style="bold white on black", no_wrap=True)
    panel = Panel(text, title=title, style="bold white on black", expand=False)
    console.print(panel)


def save_art(art: str, filename: str, output_dir: str = "outputs") -> str:
    if not art:
        raise ValueError("art is empty — nothing to save")
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    filepath = out / f"{filename}.txt"
    filepath.write_text(art, encoding="utf-8")
    return str(filepath)


def estimate_vram_usage() -> dict | None:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None
        parts = result.stdout.strip().split(",")
        if len(parts) < 2:
            return None
        used = int(parts[0].strip())
        free = int(parts[1].strip())
        total = used + free
        pct = round(used / total * 100, 1) if total > 0 else 0.0
        return {"used_mb": used, "free_mb": free, "total_mb": total, "pct_used": pct}
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        return None
