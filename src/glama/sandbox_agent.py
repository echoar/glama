#!/usr/bin/env python3
"""Option B: Cross-platform (Windows + macOS) OOM-safe Read-Only Sandboxed Tool Micro-Agent.
Includes headless w3m-style web search (web_search_text) and URL reader (web_fetch_text).
"""
import argparse, html.parser, ipaddress, json, os, pathlib, platform, shutil, socket, subprocess, sys, time, urllib.parse, urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

IS_WINDOWS = platform.system() == "Windows"
DEFAULT_MODEL = "qwen3:8b"
OLLAMA_CHAT_URL = "http://127.0.0.1:11434/api/chat"
OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"
WORKSPACE = pathlib.Path(os.environ.get("OLLAMA_SANDBOX_ROOT", ".")).resolve()

class _W3MStyleTextDumper(html.parser.HTMLParser):
    SKIP_TAGS = {"script", "style", "noscript", "svg", "head", "meta", "link", "iframe"}
    BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "article", "section"}
    def __init__(self):
        super().__init__()
        self._skip_depth = 0
        self.chunks = []
    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t in self.SKIP_TAGS: self._skip_depth += 1
        elif t in self.BLOCK_TAGS and self._skip_depth == 0: self.chunks.append("\n")
    def handle_endtag(self, tag):
        if tag.lower() in self.SKIP_TAGS and self._skip_depth > 0: self._skip_depth -= 1
    def handle_data(self, data):
        if self._skip_depth == 0:
            txt = " ".join(data.split())
            if txt: self.chunks.append(txt + " ")
    def get_text(self) -> str:
        raw = "".join(self.chunks)
        return "\n".join(line.strip() for line in raw.splitlines() if line.strip())

def _assert_safe_public_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise PermissionError("Only public http/https URLs are permitted")
    host = parsed.hostname.lower()
    if host in ("localhost", "0.0.0.0") or host.endswith((".local", ".internal", ".nord", ".lan")):
        raise PermissionError("Local/internal hostnames are blocked by SSRF guard")
    for info in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), socket.AF_INET):
        ip_str = info[4][0]
        ip_obj = ipaddress.ip_address(ip_str)
        if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local or ip_obj.is_multicast or ip_obj.is_reserved or ip_obj in ipaddress.ip_network("100.64.0.0/10"):
            raise PermissionError(f"Non-public IP ({ip_str}) blocked by SSRF guard")
    return url

def dump_url_w3m_style(url: str, max_chars: int = 2500) -> str:
    _assert_safe_public_url(url)
    if shutil.which("w3m"):
        try:
            out = subprocess.check_output(["w3m", "-dump", "-cols", "100", url], timeout=10, stderr=subprocess.DEVNULL).decode("utf-8", errors="replace")
            return out[:max_chars]
        except Exception:
            pass
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; glama-w3m-reader/0.1)"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw_bytes = resp.read(96 * 1024)
        ctype = resp.headers.get("Content-Type", "").lower()
    text = raw_bytes.decode("utf-8", errors="replace")
    if "json" in ctype or "plain" in ctype:
        return text[:max_chars]
    dumper = _W3MStyleTextDumper()
    dumper.feed(text)
    return dumper.get_text()[:max_chars]

def search_web_readonly(query: str, max_results: int = 5) -> list[dict]:
    results = []
    try:
        data = urllib.parse.urlencode({"q": query}).encode("utf-8")
        req = urllib.request.Request("https://lite.duckduckgo.com/lite/", data=data, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=8) as r:
            html_txt = r.read(64 * 1024).decode("utf-8", errors="replace")
        dumper = _W3MStyleTextDumper()
        dumper.feed(html_txt)
        lines = [l for l in dumper.get_text().splitlines() if len(l) > 25 and not l.startswith("DuckDuckGo")]
        for l in lines[:max_results]:
            results.append({"snippet": l[:240]})
    except Exception:
        pass
    if not results:
        try:
            q_enc = urllib.parse.quote(query)
            wiki_url = f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={q_enc}&utf8=&format=json&srlimit={max_results}"
            req = urllib.request.Request(wiki_url, headers={"User-Agent": "glama-w3m-reader/0.1"})
            with urllib.request.urlopen(req, timeout=8) as r:
                wdata = json.loads(r.read())
            for item in wdata.get("query", {}).get("search", []):
                dumper = _W3MStyleTextDumper()
                dumper.feed(item.get("snippet", ""))
                results.append({"title": item.get("title"), "url": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(item.get("title", "").replace(" ", "_")), "snippet": dumper.get_text()})
        except Exception as e:
            results.append({"error": str(e)})
    return results

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search_text",
            "description": "Search the internet/web (Google/DuckDuckGo/Wikipedia) and return plain-text search results.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Search query to look up on the internet"}},
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch_text",
            "description": "Fetch and read any public https:// webpage in headless w3m plain-text mode.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "Public https:// URL to read"}},
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_system_headroom",
            "description": "Read-only check of free disk space (GB) and available GPU VRAM / Unified RAM (MB).",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_workspace_files",
            "description": "List files in a relative workspace directory (read-only sandbox).",
            "parameters": {
                "type": "object",
                "properties": {"subdir": {"type": "string", "description": "Relative directory path (e.g. . or memory or scripts)"}},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_workspace_file",
            "description": "Read up to max_lines from a file inside ~/.openclaw/workspace (read-only sandbox, secrets blocked).",
            "parameters": {
                "type": "object",
                "properties": {
                    "rel_path": {"type": "string", "description": "Relative file path inside workspace, e.g. IDENTITY.md or MEMORY.md"},
                    "max_lines": {"type": "integer", "description": "Max lines to read (default 40, max 120)"}
                },
                "required": ["rel_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_workspace_text",
            "description": "Case-insensitive substring search across markdown/text files in workspace (read-only).",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Keyword or phrase to search for"}},
                "required": ["query"]
            }
        }
    }
]

def get_available_mem_mb() -> int:
    if IS_WINDOWS and shutil.which("nvidia-smi"):
        try:
            out = subprocess.check_output(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"], timeout=3).decode().strip().splitlines()[0]
            free_vram = int(out.strip())
            try:
                with urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=2) as r:
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
            pages = sum(int(l.split(":")[1].strip().rstrip(".")) for l in out.splitlines() if any(k in l for k in ("Pages free", "Pages inactive", "Pages speculative", "Pages purgeable")))
            free_mb = (pages * page_size) // (1024 * 1024)
            try:
                with urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=2) as r:
                    ps_data = json.loads(r.read())
                    free_mb += sum(int(m.get("size", 0)) // (1024 * 1024) for m in ps_data.get("models", []))
            except Exception:
                pass
            return free_mb
        except Exception:
            return 8000
    return 8000

def safe_resolve(rel_path: str) -> pathlib.Path:
    p = (WORKSPACE / rel_path).resolve()
    if not str(p).startswith(str(WORKSPACE)):
        raise PermissionError("Path outside workspace sandbox")
    low = p.name.lower()
    if any(k in low for k in (".env", "secret", "token", "password", "credential", "openclaw.json", "pg_service")):
        raise PermissionError("Sensitive file blocked by sandbox")
    return p

def exec_sandbox_tool(name: str, args: dict) -> str:
    try:
        if name == "web_search_text":
            return json.dumps({"query": args.get("query", ""), "results": search_web_readonly(str(args.get("query", "")))})
        elif name == "web_fetch_text":
            u = str(args.get("url", "")).strip()
            return json.dumps({"url": u, "text": dump_url_w3m_style(u)})
        elif name == "get_system_headroom":
            drives = {}
            for d in (["C:\\", "D:\\"] if IS_WINDOWS else [str(pathlib.Path.home())]):
                if os.path.exists(d):
                    total, used, free = shutil.disk_usage(d)
                    drives[d] = {"free_gb": round(free / (1024**3), 2), "total_gb": round(total / (1024**3), 1)}
            return json.dumps({"available_vram_or_ram_mb": get_available_mem_mb(), "drives": drives})
        elif name == "list_workspace_files":
            sub = safe_resolve(args.get("subdir", "."))
            items = [f.name + ("/" if f.is_dir() else "") for f in sorted(sub.iterdir())[:50] if not f.name.startswith(".git")]
            return json.dumps({"dir": str(sub.relative_to(WORKSPACE)), "entries": items})
        elif name == "read_workspace_file":
            target = safe_resolve(args.get("rel_path", "IDENTITY.md"))
            max_l = min(max(int(args.get("max_lines", 40)), 1), 120)
            lines = target.read_text(encoding="utf-8", errors="replace").splitlines()[:max_l]
            return json.dumps({"file": str(target.relative_to(WORKSPACE)), "content": "\n".join(lines)})
        elif name == "search_workspace_text":
            q = str(args.get("query", "")).strip().lower()
            if not q:
                return json.dumps({"matches": []})
            hits = []
            for f in WORKSPACE.rglob("*.md"):
                if ".git" in f.parts:
                    continue
                try:
                    for idx, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                        if q in line.lower():
                            hits.append(f"{f.relative_to(WORKSPACE)}:{idx}: {line[:140]}")
                            if len(hits) >= 20:
                                break
                except Exception:
                    pass
                if len(hits) >= 20:
                    break
            return json.dumps({"query": q, "matches": hits})
        return json.dumps({"error": f"Unknown tool: {name}"})
    except Exception as e:
        return json.dumps({"error": str(e)})

def ensure_ollama():
    try:
        with urllib.request.urlopen(OLLAMA_TAGS_URL, timeout=2) as r:
            if r.status == 200:
                return
    except Exception:
        pass
    if IS_WINDOWS:
        subprocess.run(["schtasks", "/Run", "/TN", "OpenClaw-Ollama-Serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
    for _ in range(15):
        time.sleep(0.5)
        try:
            with urllib.request.urlopen(OLLAMA_TAGS_URL, timeout=2) as r:
                if r.status == 200:
                    return
        except Exception:
            pass

SYSTEM_PROMPT = (
    "You are glama, an OOM-safe read-only sandboxed micro-agent with LIVE headless internet search and URL reading tools! "
    "Your tools are: "
    "1) web_search_text(query) -> searches the internet (DuckDuckGo Lite / Wikipedia) and returns plain-text results. "
    "2) web_fetch_text(url) -> fetches any public https:// webpage in w3m plain-text mode. "
    "3) get_system_headroom() -> checks GPU VRAM and disk space. "
    "4) list_workspace_files, read_workspace_file, search_workspace_text -> read-only workspace access. "
    "Commands: /new (reset session), /models, /model 8b|coder|3b, /vram, /help. "
    "IMPORTANT: You CAN search the internet using web_search_text and read webpages using web_fetch_text! Reply tersely."
)

def run_micro_agent(prompt: str, model: str = DEFAULT_MODEL, max_turns: int = 4) -> dict:
    ensure_ollama()
    avail_mb = get_available_mem_mb()
    if avail_mb < 5000 and model in ("qwen3:8b", "gemma4:26b", "qwen3:32b"):
        raise RuntimeError(f"[OOM-Guard] Free VRAM/RAM ({avail_mb} MB) < 5000 MB needed for tool-calling model {model}.")
    t0 = time.time()
    user_prompt = f"/no_think\n{prompt}" if model.startswith("qwen3") else prompt
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt}
    ]
    tool_calls_made = []
    for turn in range(max_turns):
        payload = json.dumps({
            "model": model,
            "messages": messages,
            "tools": TOOLS,
            "stream": False,
            "think": False,
            "keep_alive": "60s",
            "options": {"num_ctx": 4096, "temperature": 0.1}
        }).encode()
        req = urllib.request.Request(OLLAMA_CHAT_URL, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            r = json.loads(resp.read())
        msg = r.get("message", {})
        messages.append(msg)
        tcalls = msg.get("tool_calls") or []
        if not tcalls:
            return {
                "model": model,
                "turns": turn + 1,
                "elapsed_s": round(time.time() - t0, 2),
                "tool_calls": tool_calls_made,
                "answer": msg.get("content", "").strip()
            }
        for tc in tcalls:
            fn = tc.get("function", {})
            fname = fn.get("name", "")
            fargs = fn.get("arguments") or {}
            if isinstance(fargs, str):
                fargs = json.loads(fargs)
            res_str = exec_sandbox_tool(fname, fargs)
            tool_calls_made.append({"tool": fname, "args": fargs, "result": json.loads(res_str)})
            messages.append({"role": "tool", "content": res_str})
    return {"model": model, "turns": max_turns, "elapsed_s": round(time.time() - t0, 2), "tool_calls": tool_calls_made, "answer": messages[-1].get("content", "")}

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Run OOM-safe read-only sandboxed Ollama tool micro-agent")
    p.add_argument("prompt", nargs="?", default="Check system headroom and read the first 10 lines of IDENTITY.md.")
    p.add_argument("-m", "--model", default=DEFAULT_MODEL)
    p.add_argument("--json", action="store_true", help="Emit full JSON trace")
    args = p.parse_args()
    out = run_micro_agent(args.prompt, model=args.model)
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        print(out["answer"])
        print("[{}] {} turns | {} tools | {}s".format(out["model"], out["turns"], len(out["tool_calls"]), out["elapsed_s"]), file=sys.stderr)