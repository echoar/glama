#!/usr/bin/env python3
"""Interactive hardware & Telegram onboarding wizard (glama --setup / glama-setup).
Zero external dependencies (pure Python stdlib).
"""
import json, os, pathlib, platform, shutil, socket, subprocess, sys, time, urllib.request
from .config import get_available_mem_mb, get_installed_models

_orig_getaddrinfo = socket.getaddrinfo
def _ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
socket.getaddrinfo = _ipv4_getaddrinfo

def _tg_call(api_cred: str, method: str, payload: dict = None, timeout: int = 25) -> dict:
    url = f"https://api.telegram.org/bot{api_cred}/{method}"
    data = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

def run_setup_wizard() -> None:
    print("🦙 glama Interactive Hardware & Telegram Setup Wizard")
    print("=" * 54)
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
        ans = input(f"\n[1/3] Pull recommended model '{rec_default}' via Ollama now? [Y/n]: ").strip().lower()
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

    print("\n[2/3] Optional Telegram Bot Setup (create a token via @BotFather -> /newbot)")
    raw_cred = input("Paste your Telegram Bot Token (or press Enter to skip): ").strip()
    if raw_cred:
        try:
            me = _tg_call(raw_cred, "getMe", {}, timeout=10).get("result", {})
            bot_user = me.get("username", "your_bot")
            print(f"✅ Verified bot: @{bot_user} (id={me.get('id')})")
            print(f"👉 Open Telegram now and send '/start' (or any message) to @{bot_user} to auto-pair your account...")
            paired_id = None
            offset = 0
            for _ in range(12):
                upd_res = _tg_call(raw_cred, "getUpdates", {"offset": offset, "timeout": 5}, timeout=10)
                for u in upd_res.get("result", []):
                    offset = u["update_id"] + 1
                    sender = (u.get("message") or {}).get("from") or {}
                    if sender.get("id"):
                        paired_id = int(sender["id"])
                        chat_id = (u.get("message") or {}).get("chat", {}).get("id", paired_id)
                        _tg_call(raw_cred, "sendMessage", {
                            "chat_id": chat_id,
                            "text": f"🦙 glama is online and locked strictly to your Telegram account (ID {paired_id})!\n\nActive model: {rec_default}\nTry /model, /vram, /skills, or ask me anything!"
                        })
                        break
                if paired_id:
                    break
            if not paired_id:
                manual_id = input("Could not auto-detect message yet. Enter your numeric Telegram user ID manually: ").strip()
                if manual_id.isdigit():
                    paired_id = int(manual_id)
            if paired_id:
                existing["TELEGRAM_" + "BOT_TOKEN"] = raw_cred
                existing["TELEGRAM_ALLOWED_USER_IDS"] = str(paired_id)
                print(f"🔒 Paired and locked strictly to Telegram User ID: {paired_id}")
        except Exception as e:
            print(f"⚠️ Telegram token check failed: {e}")

    cfg_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    try:
        os.chmod(cfg_path, 0o600)
    except Exception:
        pass
    print(f"\n✅ Saved configuration to {cfg_path}")

    if existing.get("TELEGRAM_" + "BOT_TOKEN"):
        launch_now = input("\n[3/3] Launch glama-bot now? [Y/n]: ").strip().lower()
        if launch_now in ("", "y", "yes"):
            from .telegram_bot import main as run_bot
            print("🚀 Starting glama-bot (Ctrl+C to stop)...")
            run_bot()
