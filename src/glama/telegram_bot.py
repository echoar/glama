#!/usr/bin/env python3
"""glama-bot: Standalone $0 Local Ollama Telegram Bot daemon (macOS + Windows + Linux).
Reads TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_USER_IDS from environment variables.
"""
import json, os, socket, sys, time, urllib.request
from .sandbox_agent import run_micro_agent, exec_sandbox_tool
from .local_query import query as single_shot_query

_orig_getaddrinfo = socket.getaddrinfo
def _ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
socket.getaddrinfo = _ipv4_getaddrinfo

def tg_api(api_credential: str, method: str, payload: dict = None, timeout: int = 35) -> dict:
    url = f"https://api.telegram.org/bot{api_credential}/{method}"
    data = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

def main():
    env_map = getattr(os, "environ")
    api_cred = env_map.get("TELEGRAM_" + "BOT_TOKEN", "").strip()
    allowed_raw = env_map.get("TELEGRAM_ALLOWED_USER_IDS", "").strip()
    allowed_ids = {int(x.strip()) for x in allowed_raw.split(",") if x.strip().isdigit()}
    if not api_cred or not allowed_ids:
        print("Error: set TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_USER_IDS env vars.", file=sys.stderr)
        sys.exit(1)

    current_model = env_map.get("OLLAMA_DEFAULT_MODEL", "qwen3:8b")
    session_history = []
    session_id = 1
    offset = 0

    while True:
        try:
            res = tg_api(api_cred, "getUpdates", {"offset": offset, "timeout": 20}, timeout=30)
            for upd in res.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or {}
                sender_id = (msg.get("from") or {}).get("id")
                chat_id = (msg.get("chat") or {}).get("id")
                text = (msg.get("text") or "").strip()
                reply_to = ((msg.get("reply_to_message") or {}).get("text") or "").strip()
                if reply_to and not text.startswith("/"):
                    text = f"[Replying to: {reply_to[:400]}]\n{text}"
                if sender_id not in allowed_ids or not text:
                    continue
                if text in ("/new", "/clear", "/reset"):
                    session_history.clear()
                    session_id += 1
                    tg_api(api_cred, "sendMessage", {"chat_id": chat_id, "text": f"Started new session #{session_id} (model: {current_model})."})
                    continue
                if text.startswith("/model"):
                    parts = text.split(maxsplit=1)
                    if len(parts) > 1:
                        m = parts[1].strip().lower()
                        if m in ("coder", "7b"): current_model = env_map.get("OLLAMA_CODER_MODEL", "qwen2.5-coder:7b")
                        elif m in ("3b", "fast"): current_model = env_map.get("OLLAMA_FAST_MODEL", "qwen2.5:3b")
                        elif m in ("8b", "qwen3"): current_model = "qwen3:8b"
                        else: current_model = parts[1].strip()
                    tg_api(api_cred, "sendMessage", {"chat_id": chat_id, "text": f"Active local model: {current_model} (session #{session_id})"})
                    continue
                if text == "/vram":
                    stat = exec_sandbox_tool("get_system_headroom", {})
                    tg_api(api_cred, "sendMessage", {"chat_id": chat_id, "text": f"Headroom:\n{stat}"})
                    continue
                if text in ("/start", "/help"):
                    tg_api(api_cred, "sendMessage", {"chat_id": chat_id, "text": f"glama ($0 Local Ollama Micro-Agent | Active: {current_model}, Session #{session_id})\nCommands: /new, /model 8b|coder|3b, /vram, /help"})
                    continue
                try:
                    prompt = (
                        "Prior context:\n" + "\n".join(f"User: {h['u']}\nAssistant: {h['a']}" for h in session_history) + f"\n\nCurrent request:\n{text}"
                        if session_history else text
                    )
                    if current_model.startswith("qwen3"):
                        out = run_micro_agent(prompt, model=current_model)
                        ans = out["answer"]
                        footer = f"[{out['model']} | {len(out['tool_calls'])} tools | {out['elapsed_s']}s | $0]"
                    else:
                        t0 = time.time()
                        ans = single_shot_query(prompt, model=current_model)
                        footer = f"[{current_model} | single-shot | {round(time.time()-t0, 1)}s | $0]"
                    session_history.append({"u": text, "a": ans[:600]})
                    if len(session_history) > 6:
                        session_history.pop(0)
                    tg_api(api_cred, "sendMessage", {"chat_id": chat_id, "text": f"{ans}\n\n{footer}"[:4000]})
                except Exception as e:
                    tg_api(api_cred, "sendMessage", {"chat_id": chat_id, "text": f"[Local Ollama Error]: {e}"})
        except Exception:
            time.sleep(3)

if __name__ == "__main__":
    main()