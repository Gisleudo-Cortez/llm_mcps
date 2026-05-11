"""
MCP server for Miku voice synthesis via the RVC pipeline.

Wraps the existing ~/.hermes/scripts/miku_rvc/miku_rvc.py pipeline
as an MCP tool, exposing text-to-Miku-voice generation through a
clean FastMCP interface.

Architecture:
    Text → edge-tts (neutral female) → RVC Miku model → WAV/OGG

The pipeline runs as a subprocess using the existing uv-managed venv
at ~/.hermes/scripts/miku_rvc/.venv/.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

mcp = FastMCP("miku_voice_mcp")

# -------------------------------------------------------------------
# Paths — anchor to the existing pipeline installation
# -------------------------------------------------------------------

PIPELINE_DIR = Path.home() / ".hermes" / "scripts" / "miku_rvc"
VENV_PY = PIPELINE_DIR / ".venv" / "bin" / "python"
PIPELINE_SCRIPT = PIPELINE_DIR / "miku_rvc.py"
OUTPUT_DIR = PIPELINE_DIR / "output"
TELEGRAM_WAV_DIR = Path.home() / ".hermes" / "audio_cache"


# -------------------------------------------------------------------
# Pydantic input models
# -------------------------------------------------------------------


class SynthesizeVoiceInput(BaseModel):
    """Input for generating a Miku voice audio file from text."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    text: str = Field(
        description="Text to synthesize in Miku's voice. Maximum 500 characters.",
        min_length=1,
        max_length=500,
    )
    pitch_shift: int = Field(
        default=4,
        description="Pitch shift in semitones. 4 is the Miku sweet spot. Range 0-12.",
        ge=0,
        le=12,
    )
    index_rate: float = Field(
        default=0.82,
        description="RVC retrieval blending. Higher = more Miku character. Range 0.0-1.0.",
        ge=0.0,
        le=1.0,
    )
    skip_rvc: bool = Field(
        default=False,
        description="If true, output neutral TTS only (no Miku conversion). Useful for comparison.",
    )
    play_locally: bool = Field(
        default=True,
        description="If true (default), auto-play the generated audio on the default speaker using ffplay. Set to false if you only want the file path (e.g. for manual sending or scripting).",
    )
    output_format: Literal["wav", "ogg"] = Field(
        default="ogg",
        description="Output audio format. 'ogg' (Opus) for Telegram voice messages, 'wav' for general use. Only used when play_locally is false.",
    )


class PipelineStatusInput(BaseModel):
    """Input for checking the health of the Miku RVC pipeline."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------


def _pipeline_ready() -> tuple[bool, list[str]]:
    """Check if the pipeline is installed and ready.

    Returns (ready, issues) where issues is a list of human-readable
    problems found.
    """
    issues: list[str] = []

    if not PIPELINE_SCRIPT.exists():
        issues.append(f"Pipeline script not found: {PIPELINE_SCRIPT}")
    if not VENV_PY.exists():
        issues.append(f"Virtual environment not found: {VENV_PY}")

    models_dir = PIPELINE_DIR / "models" / "binant_miku"
    required_models = ["model.pth", "model.index", "config.json"]
    for name in required_models:
        if not (models_dir / name).exists():
            issues.append(f"RVC model file missing: models/binant_miku/{name}")

    hubert = PIPELINE_DIR / "rvc_repo" / "assets" / "hubert" / "hubert_base.pt"
    rmvpe = PIPELINE_DIR / "rvc_repo" / "assets" / "rmvpe" / "rmvpe.pt"
    if not hubert.exists():
        issues.append(f"HuBERT encoder model not found: {hubert}")
    if not rmvpe.exists():
        issues.append(f"RMVPE pitch model not found: {rmvpe}")

    return len(issues) == 0, issues


def _create_temp_output_name(text: str, suffix: str = ".wav") -> Path:
    """Generate a unique output filename from text content."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in " _-" else "_" for c in text)[:50]
    ts = int(time.time())
    return OUTPUT_DIR / f"miku_{safe}_{ts}{suffix}"


# -------------------------------------------------------------------
# Tools
# -------------------------------------------------------------------


@mcp.tool(
    name="miku_voice_synthesize",
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
def synthesize_voice(params: SynthesizeVoiceInput) -> str:
    """Convert text to Miku-sounding voice audio.

    **TRIGGER CONDITION:** Use when the user wants to hear text spoken in
    a Miku / Vocaloid voice. This includes: persona voice messages,
    generating voice lines for a character, or testing the Miku pipeline.

    **SEQUENCE GUIDANCE:** Call `miku_voice_check_pipeline` first to verify
    the pipeline is installed, then call this tool with the text.
    By default, the audio auto-plays on the default speaker via ffplay.
    To skip playback (e.g. for sending via `send_message` with MEDIA: prefix
    or manual scripting), set `play_locally` to false.
    Output file path is in the result.

    **CONSTRAINT WARNING:** Max 500 characters per call. Audio generation
    takes 10-20 seconds on first call (model loading) and 3-5 seconds on
    subsequent calls. The tool is destructive — it writes audio files to
    disk. Pipeline requires ~3-4GB VRAM on an NVIDIA GPU.

    **OUTPUT EXPECTATION:** Returns JSON with:
      - path: absolute path to generated audio file
      - format: "wav" or "ogg"
      - duration_seconds: approximate generation time
      - success: true/false
      - text_preview: first 80 chars of input text
    On failure, returns error details and suggests `miku_voice_check_pipeline`.

    **ERROR RECOVERY:** If pipeline fails, call `miku_voice_check_pipeline`
    to diagnose missing models or dependencies. If VRAM is exhausted, try
    closing other GPU applications and retry.
    """
    start = time.time()
    output_wav = _create_temp_output_name(params.text)

    ready, issues = _pipeline_ready()
    if not ready:
        return json.dumps({
            "success": False,
            "error": "Pipeline not ready",
            "issues": issues,
            "suggestion": "Run: cd ~/.hermes/scripts/miku_rvc && ./setup.sh",
        })

    # Build the command — patch the script's f0_up_key and index_rate
    # via arguments. The existing miku_rvc.py doesn't expose these as
    # CLI flags, so we write a thin runner inline.
    cmd = [
        str(VENV_PY),
        str(PIPELINE_SCRIPT),
        params.text,
        "-o", str(output_wav),
    ]
    if params.skip_rvc:
        cmd.append("--skip-rvc")

    # Set env vars so the script can override its defaults
    env = os.environ.copy()
    env["MIKU_F0_UP_KEY"] = str(params.pitch_shift)
    env["MIKU_INDEX_RATE"] = str(params.index_rate)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(PIPELINE_DIR),
            env=env,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return json.dumps({
            "success": False,
            "error": "Pipeline timed out after 120 seconds",
            "suggestion": "VRAM may be exhausted. Close other GPU apps and retry.",
        })
    except FileNotFoundError as e:
        return json.dumps({
            "success": False,
            "error": f"Pipeline command not found: {e}",
            "suggestion": "Run setup: cd ~/.hermes/scripts/miku_rvc && ./setup.sh",
        })

    # The script generates .wav and auto-converts to .ogg if ffmpeg succeeds.
    # Determine which file was produced.
    generated = output_wav
    ogg_path = output_wav.with_suffix(".ogg")
    if ogg_path.exists():
        generated = ogg_path

    if result.returncode != 0 and not generated.exists():
        stderr_tail = result.stderr[-500:] if result.stderr else ""
        return json.dumps({
            "success": False,
            "error": f"Pipeline exited code {result.returncode}",
            "stderr": stderr_tail,
            "suggestion": "Run verification: miku_voice_check_pipeline tool",
        })

    if not generated.exists():
        return json.dumps({
            "success": False,
            "error": "Pipeline completed but output file not found",
            "suggestion": "Check disk space and permissions on output directory",
        })

    # Copy to telegram cache if format requested is ogg
    final_path = generated
    if params.output_format == "ogg" and generated.suffix == ".wav":
        TELEGRAM_WAV_DIR.mkdir(parents=True, exist_ok=True)
        ogg_dest = TELEGRAM_WAV_DIR / f"miku_voice_{int(time.time())}.ogg"
        ffmpeg_cmd = [
            "ffmpeg", "-y", "-i", str(generated),
            "-c:a", "libopus", "-b:a", "24k", "-ar", "24000",
            str(ogg_dest),
        ]
        ff = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, timeout=30)
        if ff.returncode == 0 and ogg_dest.exists():
            final_path = ogg_dest
        else:
            # fallback — return WAV, the caller can convert
            pass
    elif params.output_format == "wav" and generated.suffix == ".ogg":
        # Generated OGG but caller wanted WAV — convert back
        wav_dest = TELEGRAM_WAV_DIR / f"miku_voice_{int(time.time())}.wav"
        ffmpeg_cmd = [
            "ffmpeg", "-y", "-i", str(generated),
            str(wav_dest),
        ]
        ff = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, timeout=30)
        if ff.returncode == 0 and wav_dest.exists():
            final_path = wav_dest

    elapsed = round(time.time() - start, 1)

    playback_info = None
    if params.play_locally:
        # --- Auto-play on default speaker via ffplay ---
        ffplay_cmd = [
            "ffplay", "-nodisp", "-autoexit", "-loglevel", "error",
            str(final_path),
        ]
        try:
            playback = subprocess.run(
                ffplay_cmd, capture_output=True, text=True, timeout=60
            )
            if playback.returncode == 0:
                playback_info = {
                    "played": True,
                    "player": "ffplay",
                    "device": "default",
                }
            else:
                playback_info = {
                    "played": False,
                    "player": "ffplay",
                    "error": f"ffplay exited {playback.returncode}: {playback.stderr.strip()[:200]}",
                }
        except FileNotFoundError:
            playback_info = {
                "played": False,
                "player": "ffplay",
                "error": "ffplay not found in PATH. Install ffmpeg.",
            }
        except subprocess.TimeoutExpired:
            playback_info = {
                "played": False,
                "player": "ffplay",
                "error": "Playback timed out after 60s.",
            }

    result_dict = {
        "success": True,
        "path": str(final_path.resolve()),
        "format": final_path.suffix.lstrip("."),
        "duration_seconds": elapsed,
        "text_preview": params.text[:80],
        "size_bytes": final_path.stat().st_size,
    }
    if playback_info:
        result_dict["playback"] = playback_info

    return json.dumps(result_dict)


@mcp.tool(
    name="miku_voice_check_pipeline",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def check_pipeline(params: PipelineStatusInput) -> str:
    """Verify the Miku RVC pipeline installation and dependencies.

    **TRIGGER CONDITION:** Use whenever `miku_voice_synthesize` returns an
    error, or to confirm the pipeline is ready before attempting synthesis.
    Also use when setting up for the first time.

    **SEQUENCE GUIDANCE:** Call before the first synthesis in a session,
    or after any setup changes. No prerequisites.

    **OUTPUT EXPECTATION:** Returns JSON with:
      - ready: true/false
      - issues: list of problems found (empty if ready)
      - pipeline_dir: path to pipeline
      - venv_python: path to venv python
      - has_gpu: true if CUDA torch is available
      - gpu_name: GPU name if available
      - model_files: list of present/absent model files
    """
    ready, issues = _pipeline_ready()

    # Check RVC repo
    rvc_repo = PIPELINE_DIR / "rvc_repo"
    rvc_ready = rvc_repo.exists() and any(rvc_repo.iterdir())

    # Check GPU
    has_gpu = False
    gpu_name = "N/A"
    try:
        gpu_check = subprocess.run(
            [str(VENV_PY), "-c", "import torch; print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')"],
            capture_output=True, text=True, timeout=10,
        )
        if gpu_check.returncode == 0:
            lines = gpu_check.stdout.strip().splitlines()
            has_gpu = lines[0] == "True" if lines else False
            gpu_name = lines[1] if len(lines) > 1 and lines[1] else "N/A"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Check individual model files
    models_dir = PIPELINE_DIR / "models" / "binant_miku"
    model_files = {}
    for name in ["config.json", "metadata.json", "model.index", "model.pth"]:
        fpath = models_dir / name
        if fpath.exists():
            model_files[name] = "present"
        else:
            model_files[name] = "missing"
            issues.append(f"Model file missing: {name}")

    info = {
        "ready": ready,
        "issues": issues if issues else None,
        "pipeline_dir": str(PIPELINE_DIR),
        "venv_python": str(VENV_PY),
        "script": str(PIPELINE_SCRIPT),
        "rvc_repo_ready": rvc_ready,
        "gpu_available": has_gpu,
        "gpu_name": gpu_name,
        "model_files": model_files,
    }

    # If issues, add a fix suggestion
    if issues:
        info["setup_command"] = "cd ~/.hermes/scripts/miku_rvc && ./setup.sh"

    return json.dumps(info, indent=2)


if __name__ == "__main__":
    mcp.run(transport="stdio")
