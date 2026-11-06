#!/usr/bin/env python3
"""Interactive hardware-aware onboarding wizard (glama --setup / glama-setup)."""
import json, pathlib, platform, shutil, subprocess, sys
from .config import get_available_mem_mb, get_installed_models

def run_setup_wizard() -> None:
    print("🦙 glama Interactive Hardware & Setup Wizard")
    print("=" * 46)
    os_name = platform.system()
    avail_mb = get_available_mem_mb()
    installed = get_installed_models()
    print(f"• OS Detected:        {os_name}")
    print(f"• Usable VRAM/RAM:    ~{avail_mb} MiB")
    print(f"• Installed Models:   {', '.join(m['name'] for m in installed) or 'None detected (is ollama running?)'}")

    if avail_mb >= 5500:
        rec_default = "qwen3:8b"
        rec_coder = "qwen2.5-coder:7b"
    else:
        rec_default = "qwen2.5:3b"
        rec_coder = "qwen2.5:3b"
    print(f"• Recommended Tier:   {rec_default} (tool/chat), {rec_coder} (SQL/code)")

    if shutil.which("ollama") and not any(m["name"].startswith(rec_default) for m in installed):
        ans = input(f"\nPull recommended model '{rec_default}' via Ollama now? [Y/n]: ").strip().lower()
        if ans in ("", "y", "yes"):
            subprocess.run(["ollama", "pull", rec_default])

    cfg_path = pathlib.Path.home() / ".config" / "glama" / "config.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if cfg_path.exists():
        try:
            existing = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    existing.setdefault("OLLAMA_DEFAULT_MODEL", rec_default)
    existing.setdefault("OLLAMA_CODER_MODEL", rec_coder)
    existing.setdefault("OLLAMA_FAST_MODEL", "qwen2.5:3b")
    existing.setdefault("GLAMA_PERMISSION_MODE", "readonly")

    cfg_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    print(f"\n✅ Saved configuration to {cfg_path}")
    print("Try running:")
    print('  glama "Check system headroom and 24h weather forecast"')
    print("  glama-query --list-models")
