# LUXAR Web 网关启动(兼容入口,实际逻辑在 start.ps1)
# 用法: powershell -ExecutionPolicy Bypass -File scripts\run-web.ps1 [-Port 8000] [-SerialPort COM4] [-Target esp32]

param(
    [string]$Port,
    [string]$SerialPort,
    [string]$Target
)

$root = Split-Path -Parent $PSScriptRoot
& powershell -ExecutionPolicy Bypass -File (Join-Path $root 'start.ps1') @PSBoundParameters
exit $LASTEXITCODE
