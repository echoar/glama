#!/usr/bin/env python3
"""Zero-dependency configuration & dynamic Ollama model discovery for glama.
Loads settings from environment variables, local .env, or ~/.config/glama/config.json,
and auto-discovers installed Ollama models + their exact memory footprint via /api/tags.
"""
import json, os, pathlib, platform, shutil, subprocess, urllib.request

IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"

def _load_dotenv_and_config() -> None:
    env_map = getattr(os, "environ")
    # 1. Load ~/.config/glama/config.json if present
    cfg_file = pathlib.Path.home() / ".config" / "glama" / "config.json"
    if cfg_file.exists():
        try:
            data = json.loads(cfg_file.read_text(encoding="utf-8"))
            for k, v in data.items():
                env_map.setdefault(str(k).upper(), str(v))
        except Exception:
            pass
    # 2. Load local .env if present
    dotenv_file = pathlib.Path.cwd() / ".env"
    if dotenv_file.exists():
        try:
            for line in dotenv_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env_map.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        except Exception:
            pass

_load_dotenv_and_config()
_ENV = getattr(os, "environ")

OLLAMA_HOST = _ENV.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
DEFAULT_MODEL = _ENV.get("OLLAMA_DEFAULT_MODEL", "qwen2.5-coder:7b" if IS_WINDOWS else "qwen3:8b")
TOOL_MODEL = _ENV.get("OLLAMA_TOOL_MODEL", "qwen3:8b")
CODER_MODEL = _ENV.get("OLLAMA_CODER_MODEL", "qwen2.5-coder:7b")
FAST_MODEL = _ENV.get("OLLAMA_FAST_MODEL", "qwen2.5:3b")
KEEP_ALIVE = _ENV.get("OLLAMA_KEEP_ALIVE", "30s")
NUM_CTX = int(_ENV.get("GLAMA_NUM_CTX", "4096"))
RESERVE_MEM_MB = int(_ENV.get("GLAMA_RESERVE_MEM_MB", "500"))
KV_CACHE_OVERHEAD_MB = max(400, (NUM_CTX // 4096) * 500)

FALLBACK_LADDER = [
    m.strip() for m in _ENV.get("GLAMA_FALLBACK_LADDER", f"{TOOL_MODEL},{CODER_MODEL},{FAST_MODEL}").split(",") if m.strip()
]

STATIC_MODEL_MB = {
    "gemma4:26b": 17500,
    "qwen3:32b": 20500,
    "qwen3:8b": 5300,
    "qwen2.5-coder:7b": 4900,
    "qwen2.5:3b": 2100,
}

def get_installed_models() -> list[dict]:
    """Query Ollama /api/tags and return installed models with estimated VRAM/RAM need in MiB."""
    try:
        with urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=3) as r:
            data = json.loads(r.read())
        models = []
        for m in data.get("models", []):
            name = m.get("name", "")
            size_bytes = int(m.get("size", 0))
            est_mb = (size_bytes // (1024 * 1024)) + KV_CACHE_OVERHEAD_MB if size_bytes > 0 else STATIC_MODEL_MB.get(name, 5000)
            models.append({
                "name": name,
                "size_gb": round(size_bytes / (1024**3), 2),
                "required_mem_mb": est_mb
            })
        return models
    except Exception:
        return []

def get_available_mem_mb() -> int:
    """Return usable free+reclaimable GPU VRAM (NVIDIA/AMD) or Unified/System RAM (macOS/Linux) in MiB, minus GLAMA_RESERVE_MEM_MB."""
    raw_mb = 8000
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                timeout=3
            ).decode().strip().splitlines()[0]
            raw_mb = int(out.strip())
            try:
                with urllib.request.urlopen(f"{OLLAMA_HOST}/api/ps", timeout=2) as r:
                    ps_data = json.loads(r.read())
                    raw_mb += sum(int(m.get("size_vram", 0)) // (1024 * 1024) for m in ps_data.get("models", []))
            except Exception:
                pass
        except Exception:
            raw_mb = 6000
    elif IS_MAC:
        try:
            out = subprocess.check_output(["vm_stat"], timeout=3).decode()
            page_size = 16384 if "page size of 16384" in out else 4096
            pages = sum(
                int(l.split(":")[1].strip().rstrip("."))
                for l in out.splitlines()
                if any(k in l for k in ("Pages free", "Pages inactive", "Pages speculative", "Pages purgeable"))
            )
            raw_mb = (pages * page_size) // (1024 * 1024)
            try:
                with urllib.request.urlopen(f"{OLLAMA_HOST}/api/ps", timeout=2) as r:
                    ps_data = json.loads(r.read())
                    raw_mb += sum(int(m.get("size", 0)) // (1024 * 1024) for m in ps_data.get("models", []))
            except Exception:
                pass
        except Exception:
            raw_mb = 8000
    elif pathlib.Path("/proc/meminfo").exists():
        try:
            for line in pathlib.Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemAvailable:"):
                    raw_mb = int(line.split()[1]) // 1024
                    break
        except Exception:
            raw_mb = 8000
    return max(0, raw_mb - RESERVE_MEM_MB)

def select_oom_safe_model(requested: str) -> str:
    """Pick requested model if it fits in usable memory; otherwise step down through installed models."""
    avail_mb = get_available_mem_mb()
    installed = {m["name"]: m["required_mem_mb"] for m in get_installed_models()}
    needed_mb = installed.get(requested) or installed.get(f"{requested}:latest") or STATIC_MODEL_MB.get(requested, 5300)
    if avail_mb >= needed_mb:
        return requested

    # Build candidate fallback list: configured ladder first, then any other installed models sorted largest->smallest
    candidates = list(FALLBACK_LADDER)
    for name, mb in sorted(installed.items(), key=lambda kv: kv[1], reverse=True):
        if name not in candidates:
            candidates.append(name)

    for cand in candidates:
        if installed and cand not in installed and f"{cand}:latest" not in installed:
            continue
        cand_mb = installed.get(cand) or installed.get(f"{cand}:latest") or STATIC_MODEL_MB.get(cand, 2100)
        if avail_mb >= cand_mb:
            import sys
            print(f"[OOM-Guard] Usable mem {avail_mb} MiB < {needed_mb} MiB for {requested}; auto-downgrading to {cand} ({cand_mb} MiB)", file=sys.stderr)
            return cand

    raise RuntimeError(f"[OOM-Guard] Only {avail_mb} MiB usable memory (after {RESERVE_MEM_MB} MiB safety reserve). No installed model fits.")
