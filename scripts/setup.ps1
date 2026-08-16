# LUXAR 一键环境准备(Windows PowerShell,零 Python 环境也可运行)
# 用法: powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
# 步骤: 定位/安装 Python 3.12 → 创建 .venv → 安装依赖 → 生成 .env → 检测 ESP-IDF

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

Write-Host '== LUXAR 环境准备 ==' -ForegroundColor Cyan

# 1. 定位 Python 3.12
function Find-Python {
    $fromPyLauncher = & py -3.12 -c "import sys;print(sys.executable)" 2>$null
    if ($fromPyLauncher) { return $fromPyLauncher.Trim() }
    $fromPython = & python -c "import sys;print(sys.version_info.major, sys.version_info.minor, sys.executable)" 2>$null
    if ($fromPython) {
        $parts = $fromPython.Trim() -split '\s+'
        if ($parts.Count -ge 3 -and $parts[0] -eq '3' -and $parts[1] -eq '12') {
            return ($parts[2..($parts.Count - 1)] -join ' ')
        }
    }
    return $null
}

$python = Find-Python
if (-not $python) {
    Write-Host '未检测到 Python 3.12。' -ForegroundColor Yellow
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        $answer = Read-Host '是否用 winget 自动安装 Python 3.12?(y/N)'
        if ($answer -match '^[yY]') {
            winget install --id Python.Python.3.12 -e --silent --accept-package-agreements --accept-source-agreements
            $env:PATH = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
            $python = Find-Python
        }
    }
}
if (-not $python) {
    Write-Error '未找到 Python 3.12。请从 https://www.python.org/downloads/ 安装后重试。'
    exit 1
}
Write-Host "[1/4] 使用 Python: $python"

# 2. 创建项目内虚拟环境
$venvPython = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    & $python -m venv (Join-Path $root '.venv')
    Write-Host '[2/4] 已创建 .venv'
} else {
    Write-Host '[2/4] .venv 已存在,跳过'
}

# 3. 安装依赖(尊重本机 HTTP_PROXY/HTTPS_PROXY)
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -e "${root}[dev]"
Write-Host '[3/4] 依赖安装完成'

# 4. 生成 .env(密钥不入仓库)
$envFile = Join-Path $root '.env'
if (-not (Test-Path $envFile)) {
    $key = Read-Host 'DEEPSEEK_API_KEY(直接回车可稍后再填)'
    $projects = Join-Path $root 'projects'
    @"
# LUXAR 本地配置(gitignore,不会提交)
DEEPSEEK_API_KEY=$key
LUXAR_PROJECTS_ROOT=$projects
# 可选: 开发板串口与芯片
# LUXAR_SERIAL_PORT=COM4
# LUXAR_TARGET_CHIP=esp32
# 可选: Web 端口
# LUXAR_WEB_PORT=8000
"@ | Set-Content $envFile -Encoding UTF8
    Write-Host '[4/4] 已生成 .env'
} else {
    Write-Host '[4/4] .env 已存在,跳过'
}

# 5. 把 .venv\Scripts 写入用户 PATH,让新终端可以直接输入 luxar
$venvScripts = Join-Path $root '.venv\Scripts'
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if ($userPath -notlike "*$venvScripts*") {
    $answer = Read-Host '把 .venv\Scripts 加入用户 PATH,以后新终端可直接输入 luxar?(y/N)'
    if ($answer -match '^[yY]') {
        [Environment]::SetEnvironmentVariable('Path', "$userPath;$venvScripts", 'User')
        $env:PATH = "$env:PATH;$venvScripts"
        Write-Host '已写入用户 PATH(当前窗口已生效)。'
    }
}

# 6. 检测 ESP-IDF
$idfPath = $env:IDF_PATH
if (-not $idfPath -and (Test-Path 'F:\esp\v6.0.2\esp-idf')) {
    $idfPath = 'F:\esp\v6.0.2\esp-idf'
}
if ($idfPath -and (Test-Path (Join-Path $idfPath 'tools\idf.py'))) {
    Write-Host "检测到 ESP-IDF: $idfPath" -ForegroundColor Green
} else {
    Write-Host '未检测到 ESP-IDF:构建/烧录任务会报"环境不可用"。' -ForegroundColor Yellow
    Write-Host '安装指引: https://docs.espressif.com/projects/esp-idf/zh_CN/latest/esp32/get-started/windows-setup.html'
}

Write-Host ''
Write-Host '准备完成!启动网关:' -ForegroundColor Green
Write-Host '  新终端直接输入 luxar,或'
Write-Host "  powershell -ExecutionPolicy Bypass -File scripts\run-web.ps1"
