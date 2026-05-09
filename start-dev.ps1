# EE AI Fund Management - Windows local dev launcher
# Recommended: double click start-dev.cmd
# Or: powershell -NoProfile -ExecutionPolicy Bypass -File .\start-dev.ps1

$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ROOT

try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
try { $OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

function Pause-OnError {
    param([string]$msg = "Error")
    Write-Host ""
    Write-Host "[ERROR] $msg" -ForegroundColor Red
    Write-Host "Press ENTER to close..." -ForegroundColor Yellow
    [void][Console]::ReadLine()
    exit 1
}

# ---- Refresh PATH so newly installed tools are visible ----
try {
    $machinePath = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath    = [System.Environment]::GetEnvironmentVariable("Path", "User")
    $env:PATH = ($machinePath, $userPath, $env:PATH) -join ";"
} catch {}

# ---- Locate a real Python (skip WindowsApps stub) ----
function Find-RealPython {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        $tmpOut = & $py.Source -3 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $tmpOut) {
            $p = $tmpOut.Trim()
            if ($p -and (Test-Path $p)) { return $p }
        }
    }
    $cands = @()
    $cands += Get-Command python  -All -ErrorAction SilentlyContinue
    $cands += Get-Command python3 -All -ErrorAction SilentlyContinue
    foreach ($c in $cands) {
        $p = $c.Source
        if (-not $p) { continue }
        if ($p -match "\\WindowsApps\\") { continue }
        $tmpOut = & $p -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $tmpOut) {
            $real = $tmpOut.Trim()
            if ($real -and (Test-Path $real)) { return $real }
        }
    }
    $guess = @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
        "C:\Python312\python.exe",
        "C:\Python311\python.exe",
        "C:\Python310\python.exe"
    )
    foreach ($g in $guess) { if (Test-Path $g) { return $g } }
    return $null
}

# ---- Locate npm.cmd (avoid npm.ps1 ExecutionPolicy issues) ----
function Find-NpmCmd {
    $all = @(Get-Command npm -All -ErrorAction SilentlyContinue)
    foreach ($c in $all) {
        if ($c.Source -and $c.Source -match "\.(cmd|bat)$") { return $c.Source }
    }
    foreach ($c in $all) {
        if ($c.Source) {
            $dir = Split-Path $c.Source -Parent
            $cmd = Join-Path $dir "npm.cmd"
            if (Test-Path $cmd) { return $cmd }
        }
    }
    return $null
}

Write-Host "==> Checking environment..." -ForegroundColor Cyan
$pythonExe = Find-RealPython
if (-not $pythonExe) {
    Write-Host "[X] No usable Python 3.x found." -ForegroundColor Red
    Write-Host "    Suggested: winget install -e --id Python.Python.3.11 --scope user" -ForegroundColor Yellow
    Pause-OnError "Python missing"
}
Write-Host ("    Python -> " + $pythonExe)
& $pythonExe --version

$npmCmd = Find-NpmCmd
if (-not $npmCmd) {
    Write-Host "[X] Node.js / npm not found." -ForegroundColor Red
    Write-Host "    Suggested: winget install -e --id OpenJS.NodeJS.LTS --scope user" -ForegroundColor Yellow
    Pause-OnError "Node.js missing"
}
$nodeExe = (Get-Command node -ErrorAction SilentlyContinue).Source
Write-Host ("    Node -> " + $nodeExe)
Write-Host ("    npm  -> " + $npmCmd)
& $nodeExe --version

# ---- Backend venv ----
Write-Host ""
Write-Host "==> Preparing backend virtualenv..." -ForegroundColor Cyan
$venv = Join-Path $ROOT "backend\.venv"
if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
    & $pythonExe -m venv $venv
    if ($LASTEXITCODE -ne 0) { Pause-OnError "create venv failed" }
}
$venvPython = Join-Path $venv "Scripts\python.exe"
Write-Host "    Upgrading pip..."
& $venvPython -m pip install --upgrade pip --quiet
Write-Host "    Installing requirements (first run is slower)..."
& $venvPython -m pip install -r (Join-Path $ROOT "backend\requirements.txt")
if ($LASTEXITCODE -ne 0) { Pause-OnError "pip install failed" }

# ---- Frontend deps ----
Write-Host ""
Write-Host "==> Preparing frontend deps..." -ForegroundColor Cyan
$frontend = Join-Path $ROOT "frontend"
if (-not (Test-Path (Join-Path $frontend "node_modules"))) {
    Push-Location $frontend
    try {
        & $npmCmd install --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw ("npm install failed, exit " + $LASTEXITCODE) }
    } catch {
        Pop-Location
        Pause-OnError $_.Exception.Message
    }
    Pop-Location
}

# ---- Run ----
$dataDir  = Join-Path $ROOT "backend\data"
$skillDir = Join-Path $ROOT "backend\skills_installed"
New-Item -ItemType Directory -Path $dataDir  -Force | Out-Null
New-Item -ItemType Directory -Path $skillDir -Force | Out-Null

# ---- Load .env into current process (so child PowerShell inherits) ----
$envFile = Join-Path $ROOT ".env"
if (Test-Path $envFile) {
    Write-Host "    Loading .env ..."
    Get-Content $envFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line -match "^([^=]+)=(.*)$") {
            $k = $Matches[1].Trim()
            $v = $Matches[2].Trim().Trim('"').Trim("'")
            [System.Environment]::SetEnvironmentVariable($k, $v, "Process")
        }
    }
    if ($env:CF_ACCESS_CLIENT_ID) {
        $idPrefix = $env:CF_ACCESS_CLIENT_ID.Substring(0, [Math]::Min(8, $env:CF_ACCESS_CLIENT_ID.Length))
        Write-Host ("    CF_ACCESS_CLIENT_ID = " + $idPrefix + "...") -ForegroundColor DarkGreen
    } else {
        Write-Host "    [WARN] CF_ACCESS_CLIENT_ID not set in .env" -ForegroundColor Yellow
    }
} else {
    Write-Host "    [INFO] No .env file found at project root (CF Access headers will not be injected)." -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "==> Cleaning up old backend / frontend processes..." -ForegroundColor Cyan
# Windows 上 uvicorn --reload 是「父进程 watcher + 子进程 worker」两个 Python；
# 用户重复双击启动脚本会留一堆僵尸进程，全部都在抢 8000 端口（端口实际只能被
# 最早成功 bind 的那个进程持有，新进程 bind 失败但 PowerShell 子窗口仍显示
# "启动成功"，看起来在跑实际是旧代码）。这里启动前先全清。
$killed = 0
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -like '*uvicorn*app.main*' -or
    ($_.CommandLine -like '*npm*' -and $_.CommandLine -like '*run*dev*') -or
    ($_.CommandLine -like '*vite*' -and $_.Name -like 'node*')
} | ForEach-Object {
    try {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop
        $killed++
    } catch {}
}
if ($killed -gt 0) {
    Write-Host ("    Killed " + $killed + " stale process(es)") -ForegroundColor DarkGray
    Start-Sleep -Seconds 1
} else {
    Write-Host "    No stale process found." -ForegroundColor DarkGray
}

# ============================================================
# 端口选择：自动避开僵尸 LISTEN 占用的端口
# ------------------------------------------------------------
# Windows TCP 栈在 uvicorn --reload 异常退出时，会留下僵尸 LISTEN 套接字
# （PID 在 netstat 里仍显示但实际进程已死），新进程 bind 同端口会被
# 内核分发到僵尸 socket 上，外部访问似乎"通"，但响应来自任何旧实例。
# 现象就是 "代码改了重启也没生效"。
#
# 解决：每次启动时探测目标端口；如果有任何 LISTEN（不管 PID 死活），
# 自动换下一个。前后端配套写到环境变量 + vite proxy 里。
# ============================================================
function Find-FreePort {
    param([int]$start, [int]$count = 20)
    for ($p = $start; $p -lt $start + $count; $p++) {
        $listeners = @(netstat -ano | Select-String (':' + $p + '\s'))
        if ($listeners.Count -eq 0) { return $p }
    }
    return 0
}
$BACKEND_PORT = Find-FreePort -start 8000 -count 20
if ($BACKEND_PORT -eq 0) {
    Pause-OnError "Cannot find a free port between 8000-8019; please reboot to clear stale sockets."
}
$FRONTEND_PORT = Find-FreePort -start 5173 -count 20
if ($FRONTEND_PORT -eq 0) {
    Pause-OnError "Cannot find a free frontend port between 5173-5192."
}
Write-Host ("    Backend  port -> " + $BACKEND_PORT)
Write-Host ("    Frontend port -> " + $FRONTEND_PORT)

# 写一份临时配置给 vite proxy 用 —— 通过环境变量传，避免每次改 vite.config.ts
# vite 会读 VITE_BACKEND_URL 当 proxy target；vite.config.ts 已支持。
$env:VITE_BACKEND_URL = "http://localhost:$BACKEND_PORT"

Write-Host ""
Write-Host ("==> Starting backend at http://localhost:" + $BACKEND_PORT + " ...") -ForegroundColor Green
# 子 PowerShell 必须自己再切一次 UTF-8：Start-Process 启动新进程时不继承父
# 进程的 [Console]::OutputEncoding，必须在子进程内 chcp 65001 + 重设
# OutputEncoding，否则 uvicorn / Python print 中文会全部乱码。
# PYTHONIOENCODING 也加上，作为 sys.stdout.reconfigure 的双保险（兼容旧 Python）。
$utf8Prelude = "chcp 65001 > `$null; [Console]::OutputEncoding = [System.Text.Encoding]::UTF8; `$OutputEncoding = [System.Text.Encoding]::UTF8; `$env:PYTHONIOENCODING='utf-8'; `$env:PYTHONUTF8='1'; "
$backendCmd = $utf8Prelude + "`$env:DATA_DIR='$dataDir'; `$env:SKILLS_DIR='$skillDir'; `$env:CF_ACCESS_CLIENT_ID='$($env:CF_ACCESS_CLIENT_ID)'; `$env:CF_ACCESS_CLIENT_SECRET='$($env:CF_ACCESS_CLIENT_SECRET)'; `$env:CF_ACCESS_HOSTS='$($env:CF_ACCESS_HOSTS)'; Set-Location '$ROOT\backend'; & '$venvPython' -m uvicorn app.main:app --host 0.0.0.0 --port $BACKEND_PORT --reload"
$backArgs = @('-NoExit','-NoProfile','-ExecutionPolicy','Bypass','-Command', $backendCmd)
Start-Process powershell -ArgumentList $backArgs | Out-Null

Start-Sleep -Seconds 2

Write-Host ("==> Starting frontend at http://localhost:" + $FRONTEND_PORT + " ...") -ForegroundColor Green
$frontendCmd = $utf8Prelude + "`$env:VITE_BACKEND_URL='http://localhost:$BACKEND_PORT'; Set-Location '$frontend'; & '$npmCmd' run dev -- --port $FRONTEND_PORT --host 0.0.0.0"
$frontArgs = @('-NoExit','-NoProfile','-ExecutionPolicy','Bypass','-Command', $frontendCmd)
Start-Process powershell -ArgumentList $frontArgs | Out-Null

Start-Sleep -Seconds 3
try { Start-Process ("http://localhost:" + $FRONTEND_PORT) } catch {}

Write-Host ""
Write-Host "[OK] Services launched:" -ForegroundColor Cyan
Write-Host ("     Backend  -> http://localhost:" + $BACKEND_PORT + "  (API docs: /docs)")
Write-Host ("     Frontend -> http://localhost:" + $FRONTEND_PORT)
Write-Host ""
Write-Host "Close the two new PowerShell windows to stop services." -ForegroundColor DarkGray
Write-Host "Press ENTER to close this window..." -ForegroundColor Yellow
[void][Console]::ReadLine()
