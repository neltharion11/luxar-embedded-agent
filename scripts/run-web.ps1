# LUXAR 一键启动 Web 网关
# 用法: powershell -ExecutionPolicy Bypass -File scripts\run-web.ps1
# 可选参数: -Port 8000 -SerialPort COM4 -Target esp32(覆盖 .env)

param(
    [string]$Port,
    [string]$SerialPort,
    [string]$Target
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# 1. 加载 .env
if (Test-Path (Join-Path $root '.env')) {
    Get-Content (Join-Path $root '.env') | ForEach-Object {
        if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
            [Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim(), 'Process')
        }
    }
}

# 2. 选择 Python(优先完整的 .venv,其次 LUXAR_PYTHON,再 PATH 上的 python)
$venvPython = Join-Path $root '.venv\Scripts\python.exe'
$venvCfg = Join-Path $root '.venv\pyvenv.cfg'
if ((Test-Path $venvPython) -and (Test-Path $venvCfg)) {
    $python = $venvPython
} elseif ($env:LUXAR_PYTHON) {
    $python = $env:LUXAR_PYTHON
} else {
    $python = (Get-Command python -ErrorAction Stop).Source
}

# 3. 沙箱/本机补丁目录(存在才加载,其他机器自动跳过)
$siteTools = Join-Path $root '.site-tools'
if (Test-Path $siteTools) {
    $env:PYTHONPATH = if ($env:PYTHONPATH) { "$siteTools;$($env:PYTHONPATH)" } else { $siteTools }
}

# 4. ESP-IDF 环境变量(让 bootstrap 智能解析真实启动器)
$idfPath = $env:IDF_PATH
if (-not $idfPath -and (Test-Path 'F:\esp\v6.0.2\esp-idf')) {
    $idfPath = 'F:\esp\v6.0.2\esp-idf'
}
if ($idfPath) {
    $env:IDF_PATH = $idfPath
    if (-not $env:IDF_TOOLS_PATH -and (Test-Path 'F:\Espressif\tools')) {
        $env:IDF_TOOLS_PATH = 'F:\Espressif\tools'
    }
    if (-not $env:IDF_PYTHON_ENV_PATH -and (Test-Path 'F:\Espressif\tools\python\v6.0.2\venv')) {
        $env:IDF_PYTHON_ENV_PATH = 'F:\Espressif\tools\python\v6.0.2\venv'
    }
    if (-not $env:ESP_IDF_VERSION) {
        $env:ESP_IDF_VERSION = '6.0.2'
    }
    # 工具链 PATH(激活脚本才会加,这里补齐关键目录)
    $toolsRoot = $env:IDF_TOOLS_PATH
    if ($toolsRoot) {
        foreach ($toolDir in (Get-ChildItem $toolsRoot -Directory -ErrorAction SilentlyContinue)) {
            foreach ($versionDir in (Get-ChildItem $toolDir.FullName -Directory -ErrorAction SilentlyContinue)) {
                $bin = Join-Path $versionDir.FullName 'bin'
                if (Test-Path $bin) { $env:PATH = "$bin;$($env:PATH)" }
            }
        }
    }
}

# 5. 密钥检查
if (-not $env:DEEPSEEK_API_KEY) {
    Write-Host '警告: 未设置 DEEPSEEK_API_KEY,提交任务会报"运行配置无效"。' -ForegroundColor Yellow
    Write-Host '请在 .env 中填写,或先运行 scripts\setup.ps1。' -ForegroundColor Yellow
}

# 6. 参数装配(写回环境变量,裸 luxar 命令会读取)
$projectsRoot = if ($env:LUXAR_PROJECTS_ROOT) { $env:LUXAR_PROJECTS_ROOT } else { (Join-Path $root 'projects') }
$webPort = if ($Port) { $Port } elseif ($env:LUXAR_WEB_PORT) { $env:LUXAR_WEB_PORT } else { '8000' }
$serial = if ($SerialPort) { $SerialPort } elseif ($env:LUXAR_SERIAL_PORT) { $env:LUXAR_SERIAL_PORT } else { $null }
$chip = if ($Target) { $Target } elseif ($env:LUXAR_TARGET_CHIP) { $env:LUXAR_TARGET_CHIP } else { $null }

if (-not (Test-Path $projectsRoot)) {
    New-Item -ItemType Directory -Path $projectsRoot | Out-Null
    Write-Host "已创建项目根目录: $projectsRoot(放入 ESP-IDF 工程子目录后即可在界面选择)" -ForegroundColor Yellow
}

$env:LUXAR_PROJECTS_ROOT = $projectsRoot
$env:LUXAR_WEB_PORT = $webPort
if ($serial) { $env:LUXAR_SERIAL_PORT = $serial }
if ($chip) { $env:LUXAR_TARGET_CHIP = $chip }

Write-Host '== LUXAR Web 网关启动 ==' -ForegroundColor Cyan
Write-Host "Python   : $python"
Write-Host "项目目录 : $projectsRoot"
if ($serial) { Write-Host "串口     : $serial" } else { Write-Host '串口     : (未配置,烧录/监控任务会报错)' }
if ($chip) { Write-Host "芯片     : $chip" } else { Write-Host '芯片     : (未配置,创建任务建议提供)' }
Write-Host "地址     : http://127.0.0.1:$webPort"
Write-Host ''

& $python -m luxar.cli
