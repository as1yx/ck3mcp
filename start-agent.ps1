$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\ck3-agent.exe')) { throw '请先运行 install.ps1' }
if (-not $env:CK3_API_KEY) {
    $secret = Read-Host '模型 API Key（隐藏输入）' -AsSecureString
    $env:CK3_API_KEY = [System.Net.NetworkCredential]::new('', $secret).Password
}
& '.\.venv\Scripts\ck3-agent.exe' run --config config.json --start-delay 8
