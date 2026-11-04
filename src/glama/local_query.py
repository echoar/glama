#!/usr/bin/env python3
"""OOM-safe single-shot local Ollama query wrapper (Windows NVIDIA VRAM + macOS Unified RAM).
Enforces single-flight locking, pre-flight memory auto-downgrade, bounded num_ctx,
and automatic keep_alive eviction so games or heavy browser tabs never OOM.
"""
import argparse, datetime, json, os, pathlib, platform, shutil, subprocess, sys, tempfile, time, urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

IS_WINDOWS = platform.system() == "Windows"
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
DEFAULT_MODEL = os.environ.get("OLLAMA_DEFAULT_MODEL", "qwen2.5-coder:7b" if IS_WINDOWS else "qwen3:8b")
LOCK_PATH = pathlib.Path(tempfile.gettempdir()) / "ollama_micro_agent.lock"

MODEL_VRAM_MB = {
    "gemma4:26b": 17500,
    "qwen3:32b": 20500,
    "qwen3:8b": 5300,
    "qwen2.5-coder:7b": 4900,
    "qwen2.5:3b": 2100,
}

def get_available_mem_mb() -> int:
    """Return free+reclaimable GPU VRAM (Windows/Linux NVIDIA) or Unified RAM (macOS) in MiB."""
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                timeout=3
            ).decode().strip().splitlines()[0]
            free_vram = int(out.strip())
            try:
                with urllib.request.urlopen(f"{OLLAMA_HOST}/api/ps", timeout=2) as r:
                    ps_data = json.loads(r.read())
                    free_vram += sum(int(m.get("size_vram", 0)) // (1024 * 1024) for m in ps_data.get("models", []))
            except Exception:
                pass
            return free_vram
        except Exception:
            return 6000
    elif platform.system() == "Darwin":
        try:
            out = subprocess.check_output(["vm_stat"], timeout=3).decode()
            page_size = 16384 if "page size of 16384" in out else 4096
            pages = sum(
                int(l.split(":")[1].strip().rstrip("."))
                for l in out.splitlines()
                if any(k in l for k in ("Pages free", "Pages inactive", "Pages speculative", "Pages purgeable"))
            )
            free_mb = (pages * page_size) // (1024 * 1024)
            try:
                with urllib.request.urlopen(f"{OLLAMA_HOST}/api/ps", timeout=2) as r:
                    ps_data = json.loads(r.read())
                    free_mb += sum(int(m.get("size", 0)) // (1024 * 1024) for m in ps_data.get("models", []))
            except Exception:
                pass
            return free_mb
        except Exception:
            return 8000
    return 8000

def select_oom_safe_model(requested: str) -> str:
    avail_mb = get_available_mem_mb()
    needed_mb = MODEL_VRAM_MB.get(requested, 5300)
    if avail_mb >= needed_mb:
        return requested
    ladder = ["qwen2.5-coder:7b", "qwen3:8b", "qwen2.5:3b"]
    for candidate in ladder:
        if avail_mb >= MODEL_VRAM_MB.get(candidate, 2100):
            print(f"[OOM-Guard] Free mem {avail_mb} MiB < {needed_mb} MiB for {requested}; auto-downgrading to {candidate}", file=sys.stderr)
            return candidate
    raise RuntimeError(f"[OOM-Guard] Only {avail_mb} MiB available (< 2100 MiB minimum). Aborting to prevent OOM.")

class SingleFlightLock:
    def __enter__(self):
        for _ in range(120):
            try:
                if LOCK_PATH.exists() and (time.time() - LOCK_PATH.stat().st_mtime > 180):
                    LOCK_PATH.unlink(missing_ok=True)
                fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return self
            except FileExistsError:
                time.sleep(0.5)
        return self
    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            LOCK_PATH.unlink(missing_ok=True)
        except Exception:
            pass

def query(prompt: str, system: str = None, max_tokens: int = 2048, model: str = None, keep_alive: str = "30s", think: bool = False, num_ctx: int = 4096) -> str:
    with SingleFlightLock():
        requested = model or DEFAULT_MODEL
        active_model = select_oom_safe_model(requested)
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        user_content = f"/no_think\n{prompt}" if (not think and active_model.startswith("qwen3")) else prompt
        messages.append({"role": "user", "content": user_content})
        payload = json.dumps({
            "model": active_model,
            "messages": messages,
            "stream": False,
            "think": think,
            "keep_alive": keep_alive,
            "options": {"num_predict": max_tokens, "num_ctx": num_ctx}
        }).encode()
        req = urllib.request.Request(f"{OLLAMA_HOST}/api/chat", data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as resp:
            r = json.loads(resp.read())
        msg = r.get("message", {})
        content = msg.get("content", "")
        dur = r.get("total_duration", 0) / 1e9
        evl = r.get("eval_count", 0)
        eval_dur = r.get("eval_duration", 1) / 1e9
        spd = evl / eval_dur if eval_dur > 0 else 0
        print(content)
        print(f"[{active_model}] {dur:.1f}s | {evl} tokens | {spd:.1f} tok/s ({int(spd*60)} TPM)", file=sys.stderr)
        return content

def main():
    p = argparse.ArgumentParser(description="OOM-safe single-shot Ollama query runner")
    p.add_argument("prompt", nargs="?", default=None)
    p.add_argument("--system", "-s", default=None)
    p.add_argument("--model", "-m", default=DEFAULT_MODEL)
    p.add_argument("--max-tokens", "-n", type=int, default=2048)
    p.add_argument("--keep-alive", "-k", default=os.environ.get("OLLAMA_KEEP_ALIVE", "30s"))
    p.add_argument("--num-ctx", "-c", type=int, default=4096)
    p.add_argument("--think", action="store_true")
    args = p.parse_args()
    prompt = args.prompt or sys.stdin.read().strip()
    if not prompt:
        print("Error: no prompt provided", file=sys.stderr)
        sys.exit(1)
    query(prompt, system=args.system, max_tokens=args.max_tokens, model=args.model, keep_alive=args.keep_alive, think=args.think, num_ctx=args.num_ctx)

if __name__ == "__main__":
    main()
