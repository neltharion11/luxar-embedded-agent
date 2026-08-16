# LUXAR 总入口:缺环境自动准备,然后启动 Web 网关
# 用法(下载仓库后唯一需要记住的命令):
#   powershell -ExecutionPolicy Bypass -File start.ps1

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$venvCfg = Join-Path $root '.venv\pyvenv.cfg'
$setupScript = Join-Path $root 'scripts\setup.ps1'
$runScript = Join-Path $root 'scripts\run-web.ps1'

if (-not (Test-Path $venvCfg)) {
    Write-Host '首次运行:正在准备环境...' -ForegroundColor Cyan
    & powershell -ExecutionPolicy Bypass -File $setupScript
    if ($LASTEXITCODE -ne 0) {
        Write-Host "环境准备失败(退出码 $LASTEXITCODE)。" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

& powershell -ExecutionPolicy Bypass -File $runScript
exit $LASTEXITCODE
