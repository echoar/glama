# glama 🦙⚡

**glama** is a zero-dependency, **OOM-safe** local Ollama micro-agent and standalone Telegram daemon built for **Apple Silicon Macs (Unified RAM)** and **Windows/Linux GPUs (8GB+ VRAM)**.

## Why `glama`?

Full agent frameworks inject 10,000–16,000 tokens of system prompt and 50+ tool schemas on every turn. On an 8GB GPU—or a MacBook running Chrome, Slack, and Teams—that overflows memory into swap, causing **10x slowdowns or OOM crashes**.

`glama` keeps local models fast and OOM-free on both **macOS** and **Windows/Linux**:
1. **Cross-Platform Pre-Flight Memory Ladder (`vm_stat` on macOS + `nvidia-smi` on Windows/Linux):**
   - **macOS (Apple Silicon Unified RAM):** Reads reclaimable memory (`free + inactive + speculative + purgeable` via `vm_stat`) before loading large models (`gemma4:26b` / `qwen3:32b`). When browser tabs or apps eat RAM, `glama` automatically steps down to `qwen3:8b` (`~5.3 GB`) or `qwen2.5:3b` (`~2.1 GB`) instead of swapping/OOMing.
   - **Windows/Linux (Discrete GPU VRAM):** Checks `nvidia-smi` free VRAM so 3D games and apps never stutter.
2. **Single-Flight File Lock:** Serializes concurrent subagent calls so two models never stack in RAM/VRAM simultaneously.
3. **Micro-Prompt (`<350 tokens`) + Bounded Context (`num_ctx=4096`):** Sub-second to 2s warm replies on `qwen3:8b` (with `/no_think` auto-applied).
4. **Hard Read-Only Sandbox:** Exposes only whitelisted read-only tools (`get_system_headroom`, `list_workspace_files`, `read_workspace_file`, `search_workspace_text`) with path-traversal and secret-file blocking (`.env`, `.key`, `id_rsa`, `pg_service`, etc.).

## Quickstart (macOS, Windows, Linux)

```bash
git clone https://github.com/echoar/glama.git
cd glama
pip install -e .

# 1. Run the read-only tool micro-agent (qwen3:8b)
glama "Check free VRAM/RAM headroom and list workspace files"

# 2. Run a single-shot OOM-safe query (SQL, code, SPEAR gate)
glama-query "Write a PostgreSQL query joining campaigns and metrics..."

# 3. Launch the standalone $0 Telegram Bot
export TELEGRAM_BOT_TOKEN="<your-botfather-token>"
export TELEGRAM_ALLOWED_USER_IDS="<your-numeric-telegram-user-id>"
glama-bot
```

## License
MIT
