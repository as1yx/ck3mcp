$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
py -3.12 -m venv .venv
if ($LASTEXITCODE -ne 0) { throw '创建虚拟环境失败；请安装 Python 3.12' }
& '.\.venv\Scripts\python.exe' -m pip install -e .
if ($LASTEXITCODE -ne 0) { throw '依赖安装失败' }
if (-not (Test-Path -LiteralPath 'config.json')) {
    Copy-Item -LiteralPath 'config.example.json' -Destination 'config.json'
}
Write-Host '安装完成。请编辑 config.json 并设置 CK3_API_KEY。此脚本不启动游戏或 Agent。'
