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

function Test-LuxarRuntime($candidate) {
    if (-not $candidate) { return $false }
    # luxar 是项目包；后两项分别属于方案 2 的工作流检查点和向量库。
    & $candidate -c "import luxar; from langgraph.checkpoint.sqlite import SqliteSaver; import lancedb" 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}

# 相对存储路径不能依赖终端启动目录；未显式配置时固定到仓库目录。
if (-not [Environment]::GetEnvironmentVariable('LUXAR_STORAGE_DIRECTORY', 'Process')) {
    $env:LUXAR_STORAGE_DIRECTORY = Join-Path $root '.luxar-data'
}

# 1. 选 Python 入口。只有 LUXAR + SQLite checkpoint + LanceDB 都可导入，
#    才把该解释器视为可启动环境。
#    顺序:LUXAR_PYTHON(显式覆盖) > 完整 .venv > PATH 上的 python
$venvPython = Join-Path $root '.venv\Scripts\python.exe'
$venvCfg = Join-Path $root '.venv\pyvenv.cfg'
$pythonCmd = $null

if ($env:LUXAR_PYTHON -and (Test-Path $env:LUXAR_PYTHON) -and (Test-LuxarRuntime $env:LUXAR_PYTHON)) {
    $pythonCmd = $env:LUXAR_PYTHON
} elseif ((Test-Path $venvPython) -and (Test-Path $venvCfg) -and (Test-LuxarRuntime $venvPython)) {
    $pythonCmd = $venvPython
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $pathPython = (Get-Command python).Source
    if (Test-LuxarRuntime $pathPython) {
        $pythonCmd = $pathPython
    }
}

if (-not $pythonCmd) {
    Write-Host '运行依赖不完整，正在初始化 LUXAR、SQLite checkpoint 和 LanceDB...' -ForegroundColor Cyan
    & powershell -ExecutionPolicy Bypass -File (Join-Path $root 'scripts\setup.ps1')
    if ($LASTEXITCODE -ne 0) {
        Write-Host "环境准备失败(退出码 $LASTEXITCODE)。" -ForegroundColor Red
        exit $LASTEXITCODE
    }
    # 让当前窗口立即能用 luxar;新窗口由 setup 写入的用户 PATH 保证。
    $venvScripts = Split-Path -Parent $venvPython
    $env:PATH = "$env:PATH;$venvScripts"
    $pythonCmd = $venvPython
    if (-not (Test-LuxarRuntime $pythonCmd)) {
        Write-Host '初始化完成，但 SQLite/LanceDB 运行依赖仍不可用。' -ForegroundColor Red
        exit 1
    }
    Write-Host ''
    Write-Host '初始化完成!以后打开新的终端窗口,直接输入 luxar 即可。' -ForegroundColor Green
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

# 4. ESP-IDF 由 Web 网关统一探测并激活。探测来源包括当前环境、
# Espressif Installation Manager、PATH、标准安装目录和仪表盘保存路径。
# 这里不再写入任何机器或版本专属路径。
Write-Host '启动时将自动检测 ESP-IDF 工具链。' -ForegroundColor DarkGray

# 5. 启动网关
Write-Host '== LUXAR Web 网关启动 ==' -ForegroundColor Cyan
& $pythonCmd -m luxar.cli
exit $LASTEXITCODE
