import os
import sys
from pathlib import Path

import click
import ollama
from rich.console import Console

from ascii_art.config import FALLBACK_MODEL, OLLAMA_HOST, PRIMARY_MODEL
from ascii_art.generator import ASCIIArtGenerator
from ascii_art.utils import display_art, estimate_vram_usage, save_art

# Load .env from project root if present (sets OPENROUTER_API_KEY etc.)
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

# Status messages (prompts, warnings) go to stderr; art goes to stdout
err = Console(stderr=True)


@click.group()
def cli():
    """ASCII Art Generator — powered by Ollama + Gemma3:12b (RTX 4080 Max-Q)"""
    pass


# ── generate ──────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("subject")
@click.option("-w", "--width", default=80, show_default=True, type=int, help="Canvas width in chars")
@click.option("-s", "--style", default=None, type=str, help="Style preset (run 'ascii-art styles' to list)")
@click.option("-o", "--output", default=None, type=str, help="Save to outputs/<stem>.txt")
@click.option("-m", "--model", default=None, type=str, help="Override Ollama model name (llm backend only)")
@click.option("--backend", default="auto", type=click.Choice(["auto", "image", "llm"]),
              show_default=True, help="image=OpenRouter FLUX+PIL (fast, accurate); llm=local Ollama (slow)")
@click.option("--no-stream", is_flag=True, default=False, help="Collect output silently then print (llm backend only)")
@click.option("--refine", "auto_refine", is_flag=True, default=False, help="Self-critique redraw pass (llm backend only)")
@click.option("--display", is_flag=True, default=False, help="Show art in a Rich panel instead of raw stdout")
@click.option("--vram", "show_vram", is_flag=True, default=False, help="Show VRAM usage before and after")
def generate(subject, width, style, output, model, backend, no_stream, auto_refine, display, show_vram):
    """Generate ASCII art for SUBJECT.

    \b
    Default backend is 'image' when OPENROUTER_API_KEY is set (recommended).
    Falls back to local Ollama LLM when the key is absent.

    \b
    Examples:
      ascii-art generate "a cat"
      ascii-art generate "a skull" --style dense --output skull
      ascii-art generate "a dragon" --backend llm --refine
    """
    if show_vram:
        before = estimate_vram_usage()
        if before:
            err.print(f"[cyan]VRAM before:[/cyan] {before['used_mb']} / {before['total_mb']} MB ({before['pct_used']}%)")

    stream = not (no_stream or display)
    gen = ASCIIArtGenerator(model=model or PRIMARY_MODEL)

    try:
        art = gen.generate(
            subject, width=width, style=style,
            stream=stream, auto_refine=auto_refine, backend=backend,
        )
    except (RuntimeError, EnvironmentError) as e:
        err.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    if display:
        display_art(art, title=subject)
    else:
        print(art)

    if output:
        path = save_art(art, output)
        err.print(f"[green]Saved:[/green] {path}")

    if show_vram:
        after = estimate_vram_usage()
        if after:
            err.print(f"[cyan]VRAM after:[/cyan]  {after['used_mb']} / {after['total_mb']} MB ({after['pct_used']}%)")


# ── convert ───────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("image_path", type=click.Path(exists=True))
@click.option("-w", "--width", default=80, show_default=True, type=int, help="Canvas width in chars")
@click.option("-o", "--output", default=None, type=str, help="Save to outputs/<stem>.txt")
@click.option("--display", is_flag=True, default=False, help="Show in Rich panel")
def convert(image_path, width, output, display):
    """Convert a local IMAGE_PATH directly to ASCII art (no API call, instant)."""
    from ascii_art.image_pipeline import image_to_ascii
    try:
        with open(image_path, "rb") as f:
            art = image_to_ascii(f.read(), width=width)
    except Exception as e:
        err.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    if display:
        display_art(art, title=image_path)
    else:
        print(art)

    if output:
        path = save_art(art, output)
        err.print(f"[green]Saved:[/green] {path}")


# ── refine ────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("feedback")
@click.option("-f", "--file", "art_file", default=None, type=click.Path(exists=True), help="Read art from file instead of stdin")
@click.option("-w", "--width", default=80, show_default=True, type=int, help="Canvas width in chars")
@click.option("-m", "--model", default=None, type=str, help="Override model name")
@click.option("--no-stream", is_flag=True, default=False, help="Collect output silently then print")
@click.option("--display", is_flag=True, default=False, help="Show result in a Rich panel")
@click.option("-o", "--output", default=None, type=str, help="Save refined art to outputs/<stem>.txt")
def refine(feedback, art_file, width, model, no_stream, display, output):
    """Improve existing ASCII art based on FEEDBACK.

    Reads art from FILE or stdin:

    \b
      ascii-art generate "a cat" | ascii-art refine "add whiskers"
      ascii-art refine "enlarge the eyes" -f outputs/cat.txt
    """
    if art_file:
        with open(art_file) as f:
            art = f.read()
    elif not sys.stdin.isatty():
        art = sys.stdin.read()
    else:
        err.print("[red]Error:[/red] provide art via stdin or --file")
        sys.exit(1)

    stream = not (no_stream or display)
    gen = ASCIIArtGenerator(model=model or PRIMARY_MODEL)

    try:
        refined = gen.refine(art.strip(), feedback, width=width, stream=stream)
    except RuntimeError as e:
        err.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    if display:
        display_art(refined, title=f"refined: {feedback[:40]}")
    else:
        print(refined)

    if output:
        path = save_art(refined, output)
        err.print(f"[green]Saved:[/green] {path}")


# ── styles ────────────────────────────────────────────────────────────────────

@cli.command()
def styles():
    """List available style presets."""
    from rich.table import Table
    from rich.console import Console

    console = Console()
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Style", style="bold white")
    table.add_column("Description")

    gen = ASCIIArtGenerator()
    for name, desc in gen.list_styles().items():
        table.add_row(name, desc)

    console.print(table)


# ── models ────────────────────────────────────────────────────────────────────

@cli.command()
def models():
    """List Ollama models available to this tool."""
    from rich.table import Table
    from rich.console import Console

    console = Console()
    client = ollama.Client(host=OLLAMA_HOST)
    try:
        response = client.list()
    except Exception as e:
        err.print(f"[red]Cannot reach Ollama at {OLLAMA_HOST}:[/red] {e}")
        sys.exit(1)

    table = Table(title="Ollama Models", show_header=True, header_style="bold cyan")
    table.add_column("Model", style="white")
    table.add_column("Size", style="dim", justify="right")
    table.add_column("Role", style="green")

    relevant = {PRIMARY_MODEL, FALLBACK_MODEL, "qwen2.5:14b"}

    for m in response.models:
        name = m.model
        size_bytes = getattr(m, "size", None)
        size_label = f"{size_bytes / 1e9:.1f} GB" if size_bytes else "?"
        base = name.removesuffix(":latest")

        if base == PRIMARY_MODEL or name == PRIMARY_MODEL:
            role = "[bold]primary (active)[/bold]"
        elif base == FALLBACK_MODEL or name == FALLBACK_MODEL:
            role = "fallback"
        elif any(name.startswith(p) for p in relevant):
            role = "alternative"
        else:
            continue

        table.add_row(name, size_label, role)

    if table.row_count == 0:
        console.print("[yellow]No relevant models found. Run:[/yellow] bash setup.sh")
    else:
        console.print(table)


# ── vram ──────────────────────────────────────────────────────────────────────

@cli.command()
def vram():
    """Print current VRAM state from nvidia-smi."""
    from rich.console import Console

    console = Console()
    stats = estimate_vram_usage()
    if stats is None:
        console.print("[yellow]nvidia-smi not available or no NVIDIA GPU detected.[/yellow]")
        return

    used, free, total, pct = stats["used_mb"], stats["free_mb"], stats["total_mb"], stats["pct_used"]
    bar_width = 40
    filled = int(bar_width * pct / 100)
    bar = "█" * filled + "░" * (bar_width - filled)
    color = "red" if pct > 85 else "yellow" if pct > 60 else "green"

    console.print("\n[bold]VRAM Usage[/bold]")
    console.print(f"  [{color}]{bar}[/] {pct}%")
    console.print(f"  Used:  [red]{used} MB[/red]")
    console.print(f"  Free:  [green]{free} MB[/green]")
    console.print(f"  Total: [white]{total} MB[/white]")


if __name__ == "__main__":
    cli()
