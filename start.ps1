# LUXAR 总入口:环境就绪则直接启动网关;缺环境才自动补装
# 用法(全新机器或日常启动都只需这一条):
#   powershell -ExecutionPolicy Bypass -File start.ps1
# 可选覆盖: -Port 8000 -SerialPort COM4 -Target esp32

param(
    [string]$Port,
    [string]$SerialPort,
    [string]$Target
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

# 0. 加载 .env(不覆盖已有环境变量)
$envFile = Join-Path $root '.env'
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
            $name = $matches[1].Trim()
            if (-not [Environment]::GetEnvironmentVariable($name, 'Process')) {
                [Environment]::SetEnvironmentVariable($name, $matches[2].Trim(), 'Process')
            }
        }
    }
}

function Test-LuxarPython($candidate) {
    if (-not $candidate) { return $false }
    & $candidate -c "import luxar" 2>$null
    return ($LASTEXITCODE -eq 0)
}

# 1. 选 Python 入口:完整 .venv > LUXAR_PYTHON > PATH 上的 python > PATH 上的 luxar
$venvPython = Join-Path $root '.venv\Scripts\python.exe'
$venvCfg = Join-Path $root '.venv\pyvenv.cfg'
$pythonCmd = $null
$useConsole = $false

if ((Test-Path $venvPython) -and (Test-Path $venvCfg) -and (Test-LuxarPython $venvPython)) {
    $pythonCmd = $venvPython
} elseif ($env:LUXAR_PYTHON -and (Test-Path $env:LUXAR_PYTHON) -and (Test-LuxarPython $env:LUXAR_PYTHON)) {
    $pythonCmd = $env:LUXAR_PYTHON
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $pathPython = (Get-Command python).Source
    if (Test-LuxarPython $pathPython) {
        $pythonCmd = $pathPython
    }
}

if (-not $pythonCmd) {
    if (Get-Command luxar -ErrorAction SilentlyContinue) {
        $useConsole = $true
    } else {
        Write-Host '首次运行:正在完成全部初始化(装 Python/依赖、生成 .env、写入 PATH)...' -ForegroundColor Cyan
        & powershell -ExecutionPolicy Bypass -File (Join-Path $root 'scripts\setup.ps1')
        if ($LASTEXITCODE -ne 0) {
            Write-Host "环境准备失败(退出码 $LASTEXITCODE)。" -ForegroundColor Red
            exit $LASTEXITCODE
        }
        # 让当前窗口立即能用 luxar;新窗口由 setup 写入的用户 PATH 保证。
        $venvScripts = Split-Path -Parent $venvPython
        $env:PATH = "$env:PATH;$venvScripts"
        $pythonCmd = $venvPython
        Write-Host ''
        Write-Host '初始化完成!以后打开新的终端窗口,直接输入 luxar 即可。' -ForegroundColor Green
    }
}

# 2. 覆盖参数写回环境变量(裸 luxar 会读取;.env 值保持默认)
if ($Port) { $env:LUXAR_WEB_PORT = $Port }
if ($SerialPort) { $env:LUXAR_SERIAL_PORT = $SerialPort }
if ($Target) { $env:LUXAR_TARGET_CHIP = $Target }

# 3. 本机补丁目录(存在才加载,其他机器自动跳过)
$siteTools = Join-Path $root '.site-tools'
if (Test-Path $siteTools) {
    $env:PYTHONPATH = if ($env:PYTHONPATH) { "$siteTools;$($env:PYTHONPATH)" } else { $siteTools }
}

# 4. ESP-IDF 环境变量与工具链 PATH(有则注入;没有时任务会得到脱敏提示)
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
    if (-not $env:ESP_IDF_VERSION) { $env:ESP_IDF_VERSION = '6.0.2' }

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

# 5. 启动网关
Write-Host '== LUXAR Web 网关启动 ==' -ForegroundColor Cyan
if ($useConsole) { & luxar } else { & $pythonCmd -m luxar.cli }
exit $LASTEXITCODE
