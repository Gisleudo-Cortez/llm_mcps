#!/usr/bin/env python3
"""Email classifier performance benchmark — zero external deps.

Tests approaches across local + cloud models using curl subprocesses.
1. Baseline: sequential subprocess (current implementation)
2. ThreadPool: concurrent subprocess via ThreadPoolExecutor
3. Async curl: concurrent HTTP via asyncio subprocess + curl

Models tested:
- Local Ollama: qwen3:4b, granite4.1:3b, granite4.1:8b, gemma4:latest
- OpenRouter: gemma-4-31b-it:free, deepseek-v4-flash (if key available)

Report: latency per email, throughput (emails/min), concurrent scaling.
"""

import asyncio
import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


OLLAMA_URL = "http://localhost:11434/api/generate"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")

SAMPLE_PROMPT = """You are an email classifier. Analyze this email and output ONLY a JSON object with these fields:
- label: one of [receipt, financial, work, personal, newsletter, spam, urgent, unsorted]
- confidence: a number from 0.0 to 1.0
- needs_review: true if confidence < 0.7, otherwise false

EMAIL:
From: orders@amazon.com
To: user@gmail.com
Subject: Your Amazon order #112-1234567 has shipped
Body preview: Your order of Wireless Mouse has shipped and will arrive Friday.

JSON:"""

LOCAL_MODELS = [
    ("qwen3:4b", 2.5),
    ("granite4.1:3b", 2.1),
    ("granite4.1:8b", 5.3),
    ("gemma4:latest", 9.6),
]

CLOUD_MODELS = [
    ("google/gemma-4-31b-it:free", "free"),
    ("deepseek/deepseek-v4-flash", "paid"),
]


@dataclass
class BenchmarkResult:
    approach: str
    model: str
    concurrency: int
    emails_tested: int
    total_seconds: float
    latency_mean_ms: float
    latency_median_ms: float
    latency_p95_ms: float
    throughput_per_min: float
    model_loaded_before: bool
    errors: int
    notes: str = ""


RESULTS: list[BenchmarkResult] = []


# ── Helpers ─────────────────────────────────────────────────────────

def _stats(latencies: list[float]) -> tuple[float, float, float]:
    if not latencies:
        return 0.0, 0.0, 0.0
    sorted_l = sorted(latencies)
    mean = sum(latencies) / len(latencies)
    median = sorted_l[len(sorted_l) // 2]
    p95_idx = int(len(sorted_l) * 0.95)
    p95 = sorted_l[min(p95_idx, len(sorted_l) - 1)]
    return mean, median, p95


def _check_model_loaded(model: str) -> bool:
    try:
        result = subprocess.run(
            ["curl", "-s", "http://localhost:11434/api/ps"],
            capture_output=True, text=True, timeout=5,
        )
        data = json.loads(result.stdout)
        for m in data.get("models", []):
            if m.get("name", "").startswith(model):
                return True
        return False
    except Exception:
        return False


def _record(approach, model, concurrency, emails_tested, latencies, errors, loaded_before, notes=""):
    mean, median, p95 = _stats(latencies)
    total = sum(latencies) / 1000.0 if latencies else 0.0
    throughput = (emails_tested / total * 60.0) if total > 0 else 0.0
    RESULTS.append(BenchmarkResult(
        approach=approach, model=model, concurrency=concurrency,
        emails_tested=emails_tested, total_seconds=round(total, 2),
        latency_mean_ms=round(mean, 1), latency_median_ms=round(median, 1),
        latency_p95_ms=round(p95, 1), throughput_per_min=round(throughput, 1),
        model_loaded_before=loaded_before, errors=errors, notes=notes,
    ))


# ── Approach 1: Baseline sequential subprocess ──────────────────────

def baseline_subprocess(model: str, n: int = 3) -> tuple[list[float], int]:
    latencies = []
    errors = 0
    for _ in range(n):
        start = time.perf_counter()
        try:
            result = subprocess.run(
                ["ollama", "run", model],
                input=SAMPLE_PROMPT, capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                errors += 1
        except Exception as e:
            errors += 1
            print(f"  [baseline error: {e}]")
        latencies.append((time.perf_counter() - start) * 1000)
    return latencies, errors


# ── Approach 2: ThreadPool subprocess ─────────────────────────────

def _single_subprocess(model: str) -> float:
    start = time.perf_counter()
    try:
        subprocess.run(
            ["ollama", "run", model],
            input=SAMPLE_PROMPT, capture_output=True, text=True, timeout=60,
        )
    except Exception:
        pass
    return (time.perf_counter() - start) * 1000


def threadpool_subprocess(model: str, n: int = 4, workers: int = 2) -> tuple[list[float], int]:
    latencies = []
    errors = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_single_subprocess, model) for _ in range(n)]
        for f in futures:
            try:
                latencies.append(f.result(timeout=90))
            except Exception as e:
                errors += 1
                print(f"  [threadpool error: {e}]")
    return latencies, errors


# ── Approach 3: Async curl ──────────────────────────────────────────

async def _async_curl_ollama(model: str) -> float:
    """Async HTTP to Ollama via curl subprocess."""
    payload = json.dumps({
        "model": model,
        "prompt": SAMPLE_PROMPT,
        "stream": False,
        "keep_alive": "30m",
    })
    cmd = [
        "curl", "-s", "-X", "POST", OLLAMA_URL,
        "-H", "Content-Type: application/json",
        "-d", payload,
    ]
    start = time.perf_counter()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        if proc.returncode != 0:
            raise RuntimeError(stderr.decode().strip())
        # Verify valid JSON response
        json.loads(stdout.decode())
    except Exception as e:
        print(f"  [async_ollama error: {e}]")
        pass
    return (time.perf_counter() - start) * 1000


async def async_ollama_http(model: str, n: int = 4, concurrency: int = 2) -> tuple[list[float], int]:
    latencies = []
    errors = 0
    sem = asyncio.Semaphore(concurrency)

    async def _task():
        async with sem:
            return await _async_curl_ollama(model)

    tasks = [asyncio.create_task(_task()) for _ in range(n)]
    for task in asyncio.as_completed(tasks):
        try:
            latencies.append(await task)
        except Exception as e:
            errors += 1
            print(f"  [async gather error: {e}]")

    return latencies, errors


async def _async_curl_openrouter(model: str) -> float:
    """Async HTTP to OpenRouter via curl subprocess."""
    if not OPENROUTER_KEY:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": "You are an email classifier. Output ONLY JSON."},
            {"role": "user", "content": SAMPLE_PROMPT},
        ],
        "temperature": 0.3,
    })
    cmd = [
        "curl", "-s", "-X", "POST", OPENROUTER_URL,
        "-H", "Authorization: Bearer " + OPENROUTER_KEY,
        "-H", "Content-Type: application/json",
        "-H", "HTTP-Referer: https://localhost",
        "-H", "X-Title: Email Classifier Benchmark",
        "-d", payload,
    ]
    start = time.perf_counter()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        if proc.returncode != 0:
            raise RuntimeError(stderr.decode().strip())
        data = json.loads(stdout.decode())
        if "error" in data:
            raise RuntimeError(str(data["error"]))
    except Exception as e:
        print(f"  [async_openrouter error: {e}]")
        pass
    return (time.perf_counter() - start) * 1000


async def async_openrouter_http(model: str, n: int = 4, concurrency: int = 2) -> tuple[list[float], int]:
    latencies = []
    errors = 0
    sem = asyncio.Semaphore(concurrency)

    async def _task():
        async with sem:
            return await _async_curl_openrouter(model)

    tasks = [asyncio.create_task(_task()) for _ in range(n)]
    for task in asyncio.as_completed(tasks):
        try:
            latencies.append(await task)
        except Exception as e:
            errors += 1
            print(f"  [async gather error: {e}]")

    return latencies, errors


# ── Approach 4: Mixed local + cloud ─────────────────────────────────

async def mixed_concurrent(
    local_model: str, cloud_model: str,
    n_each: int = 2, local_concurrency: int = 2, cloud_concurrency: int = 2,
) -> tuple[list[float], int]:
    if not OPENROUTER_KEY:
        raise RuntimeError("OPENROUTER_API_KEY not set")

    latencies = []
    errors = 0
    local_sem = asyncio.Semaphore(local_concurrency)
    cloud_sem = asyncio.Semaphore(cloud_concurrency)

    async def local_task():
        async with local_sem:
            return await _async_curl_ollama(local_model)

    async def cloud_task():
        async with cloud_sem:
            return await _async_curl_openrouter(cloud_model)

    tasks = (
        [asyncio.create_task(local_task()) for _ in range(n_each)] +
        [asyncio.create_task(cloud_task()) for _ in range(n_each)]
    )

    for task in asyncio.as_completed(tasks):
        try:
            latencies.append(await task)
        except Exception as e:
            errors += 1
            print(f"  [mixed error: {e}]")

    return latencies, errors


# ── Main ────────────────────────────────────────────────────────────

async def run_benchmarks():
    print("=" * 70)
    print("EMAIL CLASSIFIER PERFORMANCE BENCHMARK")
    print("=" * 70)
    print(f"OpenRouter key present: {bool(OPENROUTER_KEY)}")
    print(f"Date: 2026-05-12\n")

    # ── Local models ──────────────────────────────────────────────
    print("--- LOCAL MODELS ---")
    for model, size_gb in LOCAL_MODELS:
        print(f"\nModel: {model} ({size_gb} GB)")

        # Pre-warm model
        print("  Pre-loading model...")
        try:
            subprocess.run(
                ["curl", "-s", "-X", "POST", "http://localhost:11434/api/generate",
                 "-d", json.dumps({"model": model, "prompt": "hi", "stream": False, "keep_alive": "30m"})],
                capture_output=True, timeout=30,
            )
        except Exception:
            pass
        time.sleep(2)  # Let Ollama warm up

        loaded_before = _check_model_loaded(model)
        print(f"  Loaded in memory: {loaded_before}")

        # Baseline 1 concurrent
        print("  Baseline (1 concurrent, subprocess)...")
        latencies, errors = baseline_subprocess(model, n=3)
        _record("baseline_subprocess", model, 1, 3, latencies, errors, loaded_before)

        # ThreadPool 2 concurrent
        print("  ThreadPool (2 concurrent, subprocess)...")
        latencies, errors = threadpool_subprocess(model, n=4, workers=2)
        _record("threadpool_subprocess", model, 2, 4, latencies, errors, loaded_before)

        # Async HTTP 1 concurrent
        print("  Async curl (1 concurrent)...")
        latencies, errors = await async_ollama_http(model, n=3, concurrency=1)
        _record("async_ollama_http", model, 1, 3, latencies, errors, loaded_before)

        # Async HTTP 2 concurrent
        print("  Async curl (2 concurrent)...")
        latencies, errors = await async_ollama_http(model, n=4, concurrency=2)
        _record("async_ollama_http", model, 2, 4, latencies, errors, loaded_before)

        # Async HTTP 4 concurrent
        print("  Async curl (4 concurrent)...")
        latencies, errors = await async_ollama_http(model, n=4, concurrency=4)
        _record("async_ollama_http", model, 4, 4, latencies, errors, loaded_before,
                notes="May exceed Ollama parallel limit; observe if latencies degrade")

    # ── Cloud models ──────────────────────────────────────────────
    if OPENROUTER_KEY:
        print("\n--- CLOUD MODELS ---")
        for model, pricing in CLOUD_MODELS:
            print(f"\nModel: {model} ({pricing})")
            for conc in [1, 2]:
                print(f"  Async curl ({conc} concurrent)...")
                n = 3 if conc == 1 else 4
                latencies, errors = await async_openrouter_http(model, n=n, concurrency=conc)
                _record("async_openrouter_http", model, conc, n, latencies, errors, False,
                        notes=f"pricing={pricing}")
    else:
        print("\n--- SKIPPING CLOUD (no OPENROUTER_API_KEY) ---")

    # ── Mixed ─────────────────────────────────────────────────────
    if OPENROUTER_KEY:
        print("\n--- MIXED: local + cloud ---")
        print("  granite4.1:3b + google/gemma-4-31b-it:free")
        latencies, errors = await mixed_concurrent(
            "granite4.1:3b", "google/gemma-4-31b-it:free",
            n_each=2, local_concurrency=2, cloud_concurrency=2,
        )
        _record("mixed_local_cloud", "granite4.1:3b+gemma-4-31b-it-free", 4, 4,
                latencies, errors, False, notes="2 local + 2 cloud parallel")

    # ── Report ────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("RESULTS SUMMARY")
    print("=" * 70)

    approaches = {}
    for r in RESULTS:
        approaches.setdefault(r.approach, []).append(r)

    for approach, results in approaches.items():
        print(f"\n### {approach}")
        print(f"{'Model':<45} {'Conc':>4} {'Mean(ms)':>10} {'P95(ms)':>10} {'e/min':>8} {'Err':>4}")
        print("-" * 85)
        for r in sorted(results, key=lambda x: (x.model, x.concurrency)):
            print(f"{r.model:<45} {r.concurrency:>4} {r.latency_mean_ms:>10.1f} "
                  f"{r.latency_p95_ms:>10.1f} {r.throughput_per_min:>8.1f} {r.errors:>4}")

    # Save reports
    report_dir = Path.home() / "Documents" / "hermes-agent" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)

    json_path = report_dir / "email-classifier-benchmark-2026-05-12.json"
    with open(json_path, "w") as f:
        json.dump([asdict(r) for r in RESULTS], f, indent=2)
    print(f"\nJSON: {json_path}")

    md_path = report_dir / "email-classifier-benchmark-2026-05-12.md"
    with open(md_path, "w") as f:
        f.write("# Email Classifier Performance Benchmark\n\n")
        f.write(f"Date: 2026-05-12\n\n")
        f.write(f"Hardware: NVIDIA RTX 4080 Laptop 12 GB VRAM\n\n")
        f.write("## Results\n\n")
        for approach, results in approaches.items():
            f.write(f"### {approach}\n\n")
            f.write(f"| Model | Conc | Mean(ms) | P95(ms) | Throughput(e/min) | Errors |\n")
            f.write(f"|-------|------|----------|---------|-------------------|--------|\n")
            for r in sorted(results, key=lambda x: (x.model, x.concurrency)):
                f.write(f"| {r.model} | {r.concurrency} | {r.latency_mean_ms:.1f} | "
                        f"{r.latency_p95_ms:.1f} | {r.throughput_per_min:.1f} | {r.errors} |\n")
            f.write("\n")
    print(f"Markdown: {md_path}")


if __name__ == "__main__":
    asyncio.run(run_benchmarks())
