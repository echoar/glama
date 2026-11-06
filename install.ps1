# glama 1-line installer for Windows PowerShell
# Usage: irm https://raw.githubusercontent.com/echoar/glama/main/install.ps1 | iex
$ErrorActionPreference = "Stop"
Write-Host "🦙 Installing glama..." -ForegroundColor Cyan

if (Get-Command uv -ErrorAction SilentlyContinue) {
  uv tool install --force git+https://github.com/echoar/glama.git
} elseif (Get-Command pipx -ErrorAction SilentlyContinue) {
  pipx install --force git+https://github.com/echoar/glama.git
} else {
  python -m pip install --user --upgrade git+https://github.com/echoar/glama.git
}

Write-Host "✅ glama installed! Run 'glama --setup' to configure models & optional Telegram bot." -ForegroundColor Green
