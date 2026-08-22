# LUXAR 一键环境准备(Windows PowerShell,零 Python 环境也可运行)
# 用法: powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 [-Force] [-NoPathEdit]
# 环境已就绪时直接退出;加 -Force 强制重装依赖;-NoPathEdit 不修改用户 PATH。

param([switch]$Force, [switch]$NoPathEdit)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

Write-Host '== LUXAR 环境准备 ==' -ForegroundColor Cyan

$venvPython = Join-Path $root '.venv\Scripts\python.exe'
$venvCfg = Join-Path $root '.venv\pyvenv.cfg'

# 0. 就绪检查:完整 .venv + 方案 2 的全部运行依赖可导入 = 无需补装
$ready = $false
if (-not $Force -and (Test-Path $venvPython) -and (Test-Path $venvCfg)) {
    & $venvPython -c "import luxar; from langgraph.checkpoint.sqlite import SqliteSaver; import lancedb" 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { $ready = $true }
}

if (-not $ready) {
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
} else {
    Write-Host '环境已就绪,跳过补装(需要强制重装请加 -Force)。' -ForegroundColor Green
}

# 4. 确保 .env 存在(密钥不入仓库)
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
# 可选: SQLite + LanceDB 本地持久化目录
# LUXAR_STORAGE_DIRECTORY=.luxar-data
# 可选: 独立 embedding 服务（启用 LanceDB 知识库/RAG）
# LUXAR_EMBEDDING_API_KEY=
"@ | Set-Content $envFile -Encoding UTF8
    Write-Host '[4/4] 已生成 .env'
} else {
    Write-Host '[4/4] .env 已存在,跳过'
}

# 5. 把 .venv\Scripts 写入用户 PATH(自动执行;加 -NoPathEdit 可跳过)
#    这是"拉取仓库 → 跑一次 start.ps1 → 以后新终端直接输入 luxar"的关键一步。
if (-not $NoPathEdit) {
    $venvScripts = Join-Path $root '.venv\Scripts'
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if ($userPath -notlike "*$venvScripts*") {
        [Environment]::SetEnvironmentVariable(
            'Path',
            "$($userPath.TrimEnd(';'));$venvScripts",
            'User'
        )
        $env:PATH = "$env:PATH;$venvScripts"
        Write-Host '[5/6] 已把 .venv\Scripts 写入用户 PATH(新终端可直接输入 luxar)。'
    } else {
        Write-Host '[5/6] 用户 PATH 已包含 .venv\Scripts,跳过。'
    }
}

# 6. 用与 Web 网关相同的探测器检查 ESP-IDF，不依赖固定盘符或版本。
$toolchainConfig = Join-Path $root 'projects\.luxar\toolchain.json'
$toolchainProbe = @'
import json
import sys
from pathlib import Path
from luxar.toolchain import EspIdfToolchainManager

manager = EspIdfToolchainManager(config_path=Path(sys.argv[1]))
status = manager.status
print(json.dumps({
    "available": status.available,
    "version": status.version,
    "idf_path": status.idf_path,
}, ensure_ascii=True))
'@
$toolchainJson = & $venvPython -c $toolchainProbe $toolchainConfig

if ($LASTEXITCODE -eq 0 -and $toolchainJson) {
    $toolchain = $toolchainJson | ConvertFrom-Json
    if ($toolchain.available) {
        Write-Host "[6/6] 检测到 ESP-IDF: $($toolchain.version)  $($toolchain.idf_path)" -ForegroundColor Green
    } else {
        Write-Host '[6/6] 未检测到可用的 ESP-IDF;启动后可在仪表盘选择工具链位置。' -ForegroundColor Yellow
    }
} else {
    Write-Host '[6/6] ESP-IDF 检测未完成;启动后可在仪表盘重新检测。' -ForegroundColor Yellow
}

Write-Host ''
Write-Host '启动网关:' -ForegroundColor Green
Write-Host '  新终端直接输入 luxar,或'
Write-Host "  powershell -ExecutionPolicy Bypass -File start.ps1"
