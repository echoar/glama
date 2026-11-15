#!/usr/bin/env python3
"""glama-bot: Standalone 0-Token Local Ollama Telegram Bot daemon (macOS + Windows + Linux).
Supports text + 0-token Voice-In (faster-whisper CPU int8) & on-demand Voice-Out (edge-tts + OS TTS fallback).
"""
import json, os, re, socket, sys, tempfile, time, urllib.request
from .sandbox_agent import run_micro_agent, exec_sandbox_tool
from .local_query import query as single_shot_query
from .voice_helper import transcribe as whisper_transcribe, synthesize as tts_synthesize

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

def tg_download_file(api_credential: str, file_id: str, dest_path: str, timeout: int = 45) -> str:
    info = tg_api(api_credential, "getFile", {"file_id": file_id}, timeout=timeout)
    file_path = (info.get("result") or {}).get("file_path")
    if not file_path:
        raise RuntimeError("Telegram getFile returned no file_path")
    url = f"https://api.telegram.org/file/bot{api_credential}/{file_path}"
    with urllib.request.urlopen(url, timeout=timeout) as r, open(dest_path, "wb") as f:
        f.write(r.read())
    return dest_path

def tg_send_voice(api_credential: str, chat_id: int, ogg_path: str, caption: str = "", timeout: int = 60) -> dict:
    boundary = f"----GlamaVoiceBoundary{int(time.time()*1000)}"
    parts = []
    def add_field(name: str, val: str):
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{val}\r\n".encode("utf-8"))
    add_field("chat_id", str(chat_id))
    if caption:
        add_field("caption", caption[:1000])
    with open(ogg_path, "rb") as f:
        audio_bytes = f.read()
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"voice\"; filename=\"voice.ogg\"\r\nContent-Type: audio/ogg\r\n\r\n".encode("utf-8"))
    parts.append(audio_bytes)
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)
    url = f"https://api.telegram.org/bot{api_credential}/sendVoice"
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(len(body)),
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

def main():
    env_map = getattr(os, "environ")
    api_cred = env_map.get("TELEGRAM_" + "BOT_TOKEN", "").strip()
    allowed_raw = env_map.get("TELEGRAM_ALLOWED_USER_IDS", "").strip()
    allowed_ids = {int(x.strip()) for x in allowed_raw.split(",") if x.strip().isdigit()}
    if not api_cred or not allowed_ids:
        print("Error: set TELEGRAM_" + "BOT_TOKEN and TELEGRAM_ALLOWED_USER_IDS env vars.", file=sys.stderr)
        sys.exit(1)

    current_model = env_map.get("OLLAMA_DEFAULT_MODEL", "qwen3:8b")
    session_history = []
    session_id = 1
    voice_mode = "off"  # off | auto | on
    offset = 0

    while True:
        try:
            res = tg_api(api_cred, "getUpdates", {"offset": offset, "timeout": 20}, timeout=30)
            for upd in res.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or {}
                sender_id = (msg.get("from") or {}).get("id")
                chat_id = (msg.get("chat") or {}).get("id")
                if sender_id not in allowed_ids:
                    continue

                text = (msg.get("text") or msg.get("caption") or "").strip()
                is_voice_input = False
                voice_obj = msg.get("voice") or msg.get("audio") or {}
                voice_file_id = voice_obj.get("file_id")
                if voice_file_id:
                    tmp_in = os.path.join(tempfile.gettempdir(), f"glama_in_{int(time.time()*1000)}.ogg")
                    try:
                        tg_download_file(api_cred, voice_file_id, tmp_in)
                        transcript = whisper_transcribe(tmp_in)
                        if not transcript:
                            tg_api(api_cred, "sendMessage", {"chat_id": chat_id, "text": "🎙 Could not detect speech in that voice note."})
                            continue
                        text = f"{text}\n{transcript}".strip() if text else transcript
                        is_voice_input = True
                    except Exception as ve:
                        tg_api(api_cred, "sendMessage", {"chat_id": chat_id, "text": f"[Voice-In Error]: {ve}"})
                        continue
                    finally:
                        try:
                            if os.path.exists(tmp_in): os.remove(tmp_in)
                        except OSError:
                            pass

                if not text:
                    continue

                if text in ("/new", "/clear", "/reset"):
                    session_history.clear()
                    session_id += 1
                    tg_api(api_cred, "sendMessage", {"chat_id": chat_id, "text": f"Started new session #{session_id} (model: {current_model}, voice: {voice_mode})."})
                    continue

                force_voice = False
                if re.match(r"^/(voice|speak|tts)(\s|$)", text, flags=re.I):
                    arg = re.sub(r"^/(voice|speak|tts)\s*", "", text, flags=re.I).strip()
                    if arg.lower() in ("on", "auto", "off"):
                        voice_mode = arg.lower()
                        tg_api(api_cred, "sendMessage", {"chat_id": chat_id, "text": f"🔊 Voice output mode set to: {voice_mode.upper()}"})
                        continue
                    if not arg:
                        reply_txt = ((msg.get("reply_to_message") or {}).get("text") or "").strip()
                        target = reply_txt or (session_history[-1]["a"] if session_history else "")
                        if not target:
                            tg_api(api_cred, "sendMessage", {"chat_id": chat_id, "text": f"🔊 Swipe-reply /voice to any message, or use /voice on|auto|off|<prompt> (current: {voice_mode})."})
                            continue
                        tmp_out = os.path.join(tempfile.gettempdir(), f"glama_out_{int(time.time()*1000)}.ogg")
                        try:
                            eng = tts_synthesize(target, tmp_out)
                            tg_send_voice(api_cred, chat_id, tmp_out, caption=f"[{eng} | 0 USD]")
                        finally:
                            try:
                                if os.path.exists(tmp_out): os.remove(tmp_out)
                            except OSError:
                                pass
                        continue
                    text = arg
                    force_voice = True

                reply_to = ((msg.get("reply_to_message") or {}).get("text") or "").strip()
                if reply_to and not text.startswith("/"):
                    text = f"[Replying to: {reply_to[:400]}]\n{text}"

                if text.startswith("/model"):
                    parts = text.split(maxsplit=1)
                    if len(parts) > 1:
                        m = parts[1].strip().lower()
                        if m in ("coder", "7b"): current_model = env_map.get("OLLAMA_CODER_MODEL", "qwen2.5-coder:7b")
                        elif m in ("3b", "fast"): current_model = env_map.get("OLLAMA_FAST_MODEL", "qwen2.5:3b")
                        elif m in ("8b", "qwen3"): current_model = "qwen3:8b"
                        else: current_model = parts[1].strip()
                    tg_api(api_cred, "sendMessage", {"chat_id": chat_id, "text": f"Active local model: {current_model} (session #{session_id}, voice: {voice_mode})"})
                    continue
                if text == "/vram":
                    stat = exec_sandbox_tool("get_system_headroom", {})
                    tg_api(api_cred, "sendMessage", {"chat_id": chat_id, "text": f"Headroom:\n{stat}"})
                    continue
                if text in ("/start", "/help"):
                    tg_api(api_cred, "sendMessage", {"chat_id": chat_id, "text": f"glama (0-Token Local Ollama Micro-Agent | Active: {current_model}, Session #{session_id}, Voice: {voice_mode})\nCommands: /new, /voice, /model 8b|coder|3b, /vram, /help"})
                    continue
                try:
                    prompt = (
                        "Prior context:\n" + "\n".join(f"User: {h['u']}\nAssistant: {h['a']}" for h in session_history) + f"\n\nCurrent request:\n{text}"
                        if session_history else text
                    )
                    if current_model.startswith("qwen3"):
                        out = run_micro_agent(prompt, model=current_model)
                        ans = out["answer"]
                        footer = f"[{out['model']} | {len(out['tool_calls'])} tools | {out['elapsed_s']}s | 0 USD]"
                    else:
                        t0 = time.time()
                        ans = single_shot_query(prompt, model=current_model)
                        footer = f"[{current_model} | single-shot | {round(time.time()-t0, 1)}s | 0 USD]"
                    session_history.append({"u": text, "a": ans[:600]})
                    if len(session_history) > 6:
                        session_history.pop(0)
                    header = f"🎙 \"{text}\"\n\n" if is_voice_input else ""
                    tg_api(api_cred, "sendMessage", {"chat_id": chat_id, "text": f"{header}{ans}\n\n{footer}"[:4000]})

                    asked_voice = bool(re.search(r"\b(in voice|voice reply|reply with voice|out loud|read (it|that|this) aloud|speak (it|this))\b", text, flags=re.I))
                    if force_voice or voice_mode == "on" or (voice_mode == "auto" and is_voice_input) or asked_voice:
                        tmp_out = os.path.join(tempfile.gettempdir(), f"glama_out_{int(time.time()*1000)}.ogg")
                        try:
                            eng = tts_synthesize(ans, tmp_out)
                            tg_send_voice(api_cred, chat_id, tmp_out, caption=f"[{eng} | 0 USD]")
                        finally:
                            try:
                                if os.path.exists(tmp_out): os.remove(tmp_out)
                            except OSError:
                                pass
                except Exception as e:
                    tg_api(api_cred, "sendMessage", {"chat_id": chat_id, "text": f"[Local Ollama Error]: {e}"})
        except Exception:
            time.sleep(3)

if __name__ == "__main__":
    main()
