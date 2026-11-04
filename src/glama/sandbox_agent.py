#!/usr/bin/env python3
"""Read-Only Sandboxed Tool-Calling Micro-Agent for Local Ollama (qwen3:8b).
Fits in 8GB VRAM (num_ctx=4096, <350 token system prompt) and strictly enforces
read-only workspace sandboxing with secret-file blocking.
"""
import argparse, json, os, pathlib, platform, shutil, sys, time, urllib.request
from .local_query import get_available_mem_mb, OLLAMA_HOST, SingleFlightLock

IS_WINDOWS = platform.system() == "Windows"
DEFAULT_TOOL_MODEL = os.environ.get("OLLAMA_DEFAULT_MODEL", "qwen3:8b")
SANDBOX_ROOT = pathlib.Path(os.environ.get("OLLAMA_SANDBOX_ROOT", ".")).resolve()

BLOCKED_PATTERNS = (".env", "secret", "token", "password", "credential", "id_rsa", ".pem", ".key", "pg_service")


import html.parser, ipaddress, socket, urllib.parse

class _W3MStyleTextDumper(html.parser.HTMLParser):
    """Pure-stdlib w3m-dump equivalent: strips scripts/styles and extracts readable text."""
    SKIP_TAGS = {"script", "style", "noscript", "svg", "head", "meta", "link", "iframe"}
    BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "article", "section"}

    def __init__(self):
        super().__init__()
        self._skip_depth = 0
        self.chunks = []

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t in self.SKIP_TAGS:
            self._skip_depth += 1
        elif t in self.BLOCK_TAGS and self._skip_depth == 0:
            self.chunks.append("\n")

    def handle_endtag(self, tag):
        if tag.lower() in self.SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            txt = " ".join(data.split())
            if txt:
                self.chunks.append(txt + " ")

    def get_text(self) -> str:
        raw = "".join(self.chunks)
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        return "\n".join(lines)

def _assert_safe_public_url(url: str) -> str:
    """SSRF guard: allow only http/https and block private/loopback/meshnet/link-local IPs."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise PermissionError("Only public http/https URLs are permitted")
    host = parsed.hostname.lower()
    if host in ("localhost", "0.0.0.0") or host.endswith((".local", ".internal", ".nord", ".lan")):
        raise PermissionError("Local/internal hostnames are blocked by SSRF guard")
    for info in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), socket.AF_INET):
        ip_str = info[4][0]
        ip_obj = ipaddress.ip_address(ip_str)
        if (
            ip_obj.is_private
            or ip_obj.is_loopback
            or ip_obj.is_link_local
            or ip_obj.is_multicast
            or ip_obj.is_reserved
            or ip_obj in ipaddress.ip_network("100.64.0.0/10")
        ):
            raise PermissionError(f"Non-public IP ({ip_str}) blocked by SSRF guard")
    return url

def dump_url_w3m_style(url: str, max_chars: int = 2500) -> str:
    """Fetch a public URL using w3m -dump if installed, else pure-stdlib HTMLParser text dumper."""
    _assert_safe_public_url(url)
    if shutil.which("w3m"):
        try:
            out = subprocess.check_output(
                ["w3m", "-dump", "-cols", "100", url],
                timeout=10,
                stderr=subprocess.DEVNULL
            ).decode("utf-8", errors="replace")
            return out[:max_chars]
        except Exception:
            pass
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; glama-w3m-reader/0.1)"}
    )
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
    """Zero-dependency DuckDuckGo Lite + Wikipedia text search ($0, no API keys)."""
    results = []
    try:
        data = urllib.parse.urlencode({"q": query}).encode("utf-8")
        req = urllib.request.Request(
            "https://lite.duckduckgo.com/lite/",
            data=data,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Content-Type": "application/x-www-form-urlencoded"
            }
        )
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
                results.append({
                    "title": item.get("title"),
                    "url": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(item.get("title", "").replace(" ", "_")),
                    "snippet": dumper.get_text()
                })
        except Exception as e:
            results.append({"error": str(e)})
    return results

TOOLS = [
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
            "description": "List files in a relative directory inside the sandbox root (read-only).",
            "parameters": {
                "type": "object",
                "properties": {"subdir": {"type": "string", "description": "Relative directory path (default '.')"}},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_workspace_file",
            "description": "Read up to max_lines from a text/markdown file inside the sandbox root (read-only).",
            "parameters": {
                "type": "object",
                "properties": {
                    "rel_path": {"type": "string", "description": "Relative file path inside sandbox root"},
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
            "description": "Case-insensitive text search across markdown/text files in the sandbox root.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Substring to search for"}},
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search_text",
            "description": "Search the web (DuckDuckGo Lite / Wikipedia) and return plain-text snippets (read-only).",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Search query"}},
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch_text",
            "description": "Fetch a public https:// URL in w3m-dump plain-text mode (SSRF-guarded, read-only).",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "Public https:// URL to read"}},
                "required": ["url"]
            }
        }
    }
]

def safe_resolve(rel_path: str) -> pathlib.Path:
    p = (SANDBOX_ROOT / rel_path).resolve()
    if not str(p).startswith(str(SANDBOX_ROOT)):
        raise PermissionError("Path outside sandbox root")
    low = p.name.lower()
    if any(k in low for k in BLOCKED_PATTERNS):
        raise PermissionError("Sensitive file blocked by sandbox policy")
    return p

def exec_sandbox_tool(name: str, args: dict) -> str:
    try:
        if name == "get_system_headroom":
            total, used, free = shutil.disk_usage(str(SANDBOX_ROOT))
            return json.dumps({
                "available_vram_or_ram_mb": get_available_mem_mb(),
                "sandbox_drive_free_gb": round(free / (1024**3), 2)
            })
        elif name == "list_workspace_files":
            sub = safe_resolve(args.get("subdir", "."))
            items = [f.name + ("/" if f.is_dir() else "") for f in sorted(sub.iterdir())[:50] if not f.name.startswith(".git")]
            return json.dumps({"dir": str(sub.relative_to(SANDBOX_ROOT)), "entries": items})
        elif name == "read_workspace_file":
            target = safe_resolve(args.get("rel_path", "README.md"))
            max_l = min(max(int(args.get("max_lines", 40)), 1), 120)
            lines = target.read_text(encoding="utf-8", errors="replace").splitlines()[:max_l]
            return json.dumps({"file": str(target.relative_to(SANDBOX_ROOT)), "content": "\n".join(lines)})
        elif name == "search_workspace_text":
            q = str(args.get("query", "")).strip().lower()
            if not q:
                return json.dumps({"matches": []})
            hits = []
            for f in SANDBOX_ROOT.rglob("*.md"):
                if ".git" in f.parts:
                    continue
                try:
                    for idx, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                        if q in line.lower():
                            hits.append(f"{f.relative_to(SANDBOX_ROOT)}:{idx}: {line[:140]}")
                            if len(hits) >= 20:
                                break
                except Exception:
                    pass
                if len(hits) >= 20:
                    break
            return json.dumps({"query": q, "matches": hits})
        elif name == "web_search_text":
            if os.environ.get("GLAMA_ENABLE_WEB", "1") == "0":
                return json.dumps({"error": "Web access disabled by GLAMA_ENABLE_WEB=0"})
            return json.dumps({"query": args.get("query", ""), "results": search_web_readonly(str(args.get("query", "")))})
        elif name == "web_fetch_text":
            if os.environ.get("GLAMA_ENABLE_WEB", "1") == "0":
                return json.dumps({"error": "Web access disabled by GLAMA_ENABLE_WEB=0"})
            u = str(args.get("url", "")).strip()
            return json.dumps({"url": u, "text": dump_url_w3m_style(u)})
        return json.dumps({"error": f"Unknown tool: {name}"})
    except Exception as e:
        return json.dumps({"error": str(e)})

def run_micro_agent(prompt: str, model: str = DEFAULT_TOOL_MODEL, max_turns: int = 4) -> dict:
    with SingleFlightLock():
        avail_mb = get_available_mem_mb()
        if avail_mb < 5000 and model in ("qwen3:8b", "gemma4:26b", "qwen3:32b"):
            raise RuntimeError(f"[OOM-Guard] Free VRAM/RAM ({avail_mb} MB) < 5000 MB needed for tool-calling model {model}.")
        t0 = time.time()
        user_prompt = f"/no_think\n{prompt}" if model.startswith("qwen3") else prompt
        messages = [
            {"role": "system", "content": "You are a strictly read-only sandboxed micro-agent. Commands available: /new (reset session), /model 8b|coder|3b, /vram, /help. Use the provided read-only tools when needed and reply tersely."},
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
                "keep_alive": os.environ.get("OLLAMA_KEEP_ALIVE", "30s"),
                "options": {"num_ctx": 4096, "temperature": 0.1}
            }).encode()
            req = urllib.request.Request(f"{OLLAMA_HOST}/api/chat", data=payload, headers={"Content-Type": "application/json"})
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

def main():
    p = argparse.ArgumentParser(description="Run OOM-safe read-only sandboxed Ollama tool micro-agent")
    p.add_argument("prompt", nargs="?", default="Check system headroom and list workspace files.")
    p.add_argument("-m", "--model", default=DEFAULT_TOOL_MODEL)
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    out = run_micro_agent(args.prompt, model=args.model)
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        print(out["answer"])
        print(f"[{out['model']}] {out['turns']} turns | {len(out['tool_calls'])} tools | {out['elapsed_s']}s", file=sys.stderr)

if __name__ == "__main__":
    main()
