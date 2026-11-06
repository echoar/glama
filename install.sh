#!/usr/bin/env sh
# glama 1-line installer for macOS & Linux
# Usage: curl -fsSL https://raw.githubusercontent.com/echoar/glama/main/install.sh | sh
set -eu

echo "🦙 Installing glama..."
if command -v uv >/dev/null 2>&1; then
  uv tool install --force git+https://github.com/echoar/glama.git
elif command -v pipx >/dev/null 2>&1; then
  pipx install --force git+https://github.com/echoar/glama.git
else
  python3 -m pip install --user --upgrade git+https://github.com/echoar/glama.git
fi

echo "✅ glama installed! Run 'glama --setup' to configure models & optional Telegram bot."
