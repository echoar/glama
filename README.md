# glama 🦙⚡

**glama** is a zero-dependency, **OOM-safe** local Ollama micro-agent and standalone Telegram daemon built for **Apple Silicon Macs (Unified RAM)** and **Windows/Linux GPUs (8GB+ VRAM)**.

## Why `glama`?

Full agent frameworks inject 10,000–16,000 tokens of system prompt and 50+ tool schemas on every turn. On an 8GB GPU—or a MacBook running Chrome, Slack, and Teams—that overflows memory into swap, causing **10x slowdowns or OOM crashes**.

`glama` keeps local models fast and OOM-free across **macOS**, **Windows**, and **Linux**:
1. **Dynamic Model Discovery & Pre-Flight Memory Ladder (`src/glama/config.py`):**
   - Queries local Ollama (`/api/tags`) to auto-discover your installed models and their exact memory footprint + KV-cache overhead.
   - Checks usable memory before every call via **`vm_stat` (macOS Unified RAM)**, **`nvidia-smi` (Windows/Linux GPU VRAM)**, or **`/proc/meminfo` (Linux)** minus your safety reserve (`GLAMA_RESERVE_MEM_MB`).
   - Automatically steps down model tiers (e.g. `26b/32b -> 8b -> 7b -> 3b`) when games or browser tabs eat memory instead of swapping/OOMing.
2. **Single-Flight Mutex Lock:** Serializes concurrent subagent calls so two models never stack in RAM/VRAM simultaneously.
3. **Micro-Prompt (`<350 tokens`) + Bounded Context (`GLAMA_NUM_CTX=4096`):** Sub-second to 2s warm replies on `qwen3:8b` (with `/no_think` auto-applied).
4. **Zero External Dependencies:** Pure Python standard library (`>=3.10`)—minimizing supply-chain and dependency attack surface.

---

## Built-In Sandboxed Tools & On-Demand Skills

### 1. Read-Only Tools (Always Active)
- **`web_search_text(query)`:** Headless web & news search (Google News RSS + Wikipedia OpenSearch, VPN-safe, `$0`, no API keys).
- **`web_fetch_text(url)`:** Headless `w3m -dump` style plain-text webpage reader (uses `w3m` if installed on macOS/Linux, or pure-stdlib `html.parser` text dumper on Windows) with **strict SSRF protection** (blocks `localhost`, private LAN, `100.64.0.0/10` Meshnet/Tailscale, and cloud metadata IPs).
- **`get_weather_forecast(location)`:** Live 24-hour hourly + 7-day daily forecast via Open-Meteo (`$0`, no API key).
- **`use_skill(skill_name)`:** Loads step-by-step markdown playbooks from `skills/<name>.md` on demand (`<20 tokens` prompt overhead until called).
- **`get_system_headroom()`:** Live GPU VRAM / Unified RAM and disk free space readout.
- **`list_workspace_files` / `read_workspace_file` / `search_workspace_text`:** Confined strictly to `OLLAMA_SANDBOX_ROOT` with secret/config file blocking.

### 2. On-Demand Skills (`skills/*.md`)
Drop any `.md` procedure into `skills/` (included examples: `skills/sql-spear.md` and `skills/web-brief.md`). `glama` advertises only the skill names in the system prompt and loads the full markdown instructions via `use_skill` only when needed.

---

## Security & Permission Modes (`GLAMA_PERMISSION_MODE`)

Permission levels are controlled exclusively by the host OS environment/`.env` (and config/script files are hard-blocked inside `safe_resolve`, so the model or a prompt injection can never escalate its own permissions):

- **`GLAMA_PERMISSION_MODE=readonly` (Default):** Write tools are not registered in the tool schema and raise `PermissionError` at the Python handler layer.
- **`GLAMA_PERMISSION_MODE=workspace-write` (Opt-in):** Registers `write_workspace_file` confined strictly inside `OLLAMA_SANDBOX_ROOT` and restricted to non-executable data/note extensions (`.md`, `.txt`, `.json`, `.csv`)—executable/script extensions (`.py`, `.sh`, `.ps1`, `.vbs`, `.env`) remain hard-blocked.

---

## Quickstart (macOS, Windows, Linux)

```bash
git clone https://github.com/echoar/glama.git
cd glama
pip install -e .

# 1. Inspect usable VRAM/RAM and auto-discovered Ollama models
glama-query --list-models

# 2. Run the read-only tool micro-agent (qwen3:8b)
glama "Check free VRAM/RAM headroom and 24h weather forecast"

# 3. Run a single-shot OOM-safe query (SQL, code, SPEAR gate)
glama-query "Write a PostgreSQL query joining campaigns and metrics..."

# 4. Launch the standalone $0 Telegram Bot
export TELEGRAM_BOT_TOKEN="your-bot-token-from-botfather"
export TELEGRAM_ALLOWED_USER_IDS="123456789"
glama-bot
```

### Telegram Bot Commands (`glama-bot`)
- `/new` (or `/clear`): Reset rolling session history and start a fresh session
- `/models`: List all locally installed Ollama models and disk sizes
- `/model <8b|coder|3b|name>`: Switch active local model on the fly
- `/skills` (or `/tools`): List active sandboxed tools and `skills/*.md` playbooks
- `/vram`: Instant GPU VRAM / Unified RAM & disk headroom check
- Native Telegram **Swipe-to-Reply** context supported automatically

---

## Configuration (`.env` or `~/.config/glama/config.json`)

| Variable | Default | Description |
| :--- | :--- | :--- |
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | Local or LAN Ollama server URL |
| `OLLAMA_DEFAULT_MODEL` | `qwen3:8b` | Default model for tool calling & chat |
| `OLLAMA_CODER_MODEL` | `qwen2.5-coder:7b` | Model alias for `/model coder` |
| `OLLAMA_FAST_MODEL` | `qwen2.5:3b` | Model alias for `/model 3b` |
| `GLAMA_FALLBACK_LADDER` | `qwen3:8b,qwen2.5-coder:7b,qwen2.5:3b` | Priority order when stepping down under RAM/VRAM pressure |
| `GLAMA_RESERVE_MEM_MB` | `500` | Untouchable VRAM/RAM safety buffer in MiB (e.g. `1500` for gaming rigs) |
| `GLAMA_NUM_CTX` | `4096` | Context window cap (auto-scales KV-cache OOM math) |
| `OLLAMA_KEEP_ALIVE` | `30s` | Unload delay after query completion to free VRAM |
| `GLAMA_PERMISSION_MODE` | `readonly` | `readonly` (default) or `workspace-write` |
| `GLAMA_ENABLE_WEB` | `1` | Set to `0` for air-gapped offline mode |

## License
MIT
