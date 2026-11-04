#!/usr/bin/env python3
"""OOM-safe single-shot local Ollama query wrapper (Windows/Linux GPU VRAM + macOS Unified RAM)."""
import argparse, json, os, pathlib, sys, tempfile, time, urllib.request
from .config import (
    OLLAMA_HOST, DEFAULT_MODEL, KEEP_ALIVE, NUM_CTX,
    get_available_mem_mb, get_installed_models, select_oom_safe_model
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

LOCK_PATH = pathlib.Path(tempfile.gettempdir()) / "ollama_micro_agent.lock"

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

def query(prompt: str, system: str = None, max_tokens: int = 2048, model: str = None, keep_alive: str = KEEP_ALIVE, think: bool = False, num_ctx: int = NUM_CTX) -> str:
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
    p = argparse.ArgumentParser(description="glama-query: OOM-safe single-shot Ollama query runner")
    p.add_argument("prompt", nargs="?", default=None)
    p.add_argument("--system", "-s", default=None)
    p.add_argument("--model", "-m", default=DEFAULT_MODEL)
    p.add_argument("--max-tokens", "-n", type=int, default=2048)
    p.add_argument("--keep-alive", "-k", default=KEEP_ALIVE)
    p.add_argument("--num-ctx", "-c", type=int, default=NUM_CTX)
    p.add_argument("--think", action="store_true")
    p.add_argument("--list-models", action="store_true", help="List auto-discovered local Ollama models and memory footprint")
    args = p.parse_args()
    if args.list_models:
        print(json.dumps({"usable_mem_mb": get_available_mem_mb(), "installed_models": get_installed_models()}, indent=2))
        return
    prompt = args.prompt or sys.stdin.read().strip()
    if not prompt:
        print("Error: no prompt provided", file=sys.stderr)
        sys.exit(1)
    query(prompt, system=args.system, max_tokens=args.max_tokens, model=args.model, keep_alive=args.keep_alive, think=args.think, num_ctx=args.num_ctx)

if __name__ == "__main__":
    main()
