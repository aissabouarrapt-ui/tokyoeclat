# CLIPPING_GTA6 - installer (called by setup.bat). Safe to run again at any time.
# Installs only what is missing: Node 20+, ffmpeg (with libass), Python 3.12,
# heistclips (pinned commit), a private Python venv INSIDE this folder, fonts, whisper model.
# Touches nothing outside this folder except installing Node/ffmpeg/Python with winget.

# 'Continue': in Windows PowerShell 5.1 a native tool writing to stderr (pip warnings, winget
# progress) must not abort the whole setup; risky cmdlets use -ErrorAction Stop inside try/catch.
$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$HeistSha = '50bb7610b5715f2a65d471f90e33d8b63ce77b13'
$Failed = @()

function Say($msg, $color = 'Gray') { Write-Host $msg -ForegroundColor $color }
function Ok($msg) { Say "  [OK] $msg" 'Green' }
function Bad($msg) { Say "  [!!] $msg" 'Red'; $script:Failed += $msg }

function Refresh-Path {
    $m = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $u = [Environment]::GetEnvironmentVariable('Path', 'User')
    $links = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links'
    $env:Path = "$m;$u;$links"
}

function Have-Winget { return [bool](Get-Command winget -ErrorAction SilentlyContinue) }

function Winget-Install($id, $label) {
    if (-not (Have-Winget)) {
        Bad "winget not found: install '$label' by hand (see README.md), then run setup.bat again."
        return
    }
    Say "  Installing $label with winget ($id)..." 'Yellow'
    & winget install -e --id $id --accept-source-agreements --accept-package-agreements --silent | Out-Host
    Refresh-Path
}

Say "`n=== CLIPPING_GTA6 setup ===   folder: $Root`n" 'Cyan'
Refresh-Path

# ---------------------------------------------------------------- 1. Node 20+
Say '1) Node.js 20+'
function Node-Major {
    $n = Get-Command node -ErrorAction SilentlyContinue
    if (-not $n) { return 0 }
    $v = (& node --version) -replace '^v', ''
    return [int]($v.Split('.')[0])
}
if ((Node-Major) -lt 20) { Winget-Install 'OpenJS.NodeJS.LTS' 'Node.js LTS' }
if ((Node-Major) -ge 20) { Ok ("Node " + (& node --version)) } else { Bad 'Node.js 20+ missing' }

# ---------------------------------------------------------------- 2. ffmpeg + libass
Say '2) ffmpeg (with libass for subtitles, libx264)'
function Ffmpeg-Good {
    if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) { return $false }
    $f = (& ffmpeg -hide_banner -filters 2>$null) -join "`n"
    $e = (& ffmpeg -hide_banner -encoders 2>$null) -join "`n"
    return ($f -match '\sass\s' -and $e -match 'libx264')
}
if (-not (Ffmpeg-Good)) { Winget-Install 'Gyan.FFmpeg' 'FFmpeg (full build)' }
if (Ffmpeg-Good) { Ok ((& ffmpeg -version | Select-Object -First 1)) }
else { Bad 'ffmpeg with libass+libx264 missing (if an old ffmpeg is first in PATH, remove it or reorder PATH)' }

# ---------------------------------------------------------------- 3. Python 3.12
Say '3) Python 3.12'
function Find-Python {
    $cands = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($v in '3.12', '3.13', '3.11') {
            try { $p = (& py "-$v" -c 'import sys;print(sys.executable)' 2>$null); if ($LASTEXITCODE -eq 0 -and $p) { $cands += $p.Trim() } } catch {}
        }
    }
    foreach ($v in 'Python312', 'Python313', 'Python311') {
        $p = Join-Path $env:LOCALAPPDATA "Programs\Python\$v\python.exe"
        if (Test-Path $p) { $cands += $p }
    }
    return ($cands | Select-Object -First 1)
}
$Py = Find-Python
if (-not $Py) { Winget-Install 'Python.Python.3.12' 'Python 3.12'; $Py = Find-Python }
if ($Py) { Ok "Python: $Py" } else { Bad 'Python 3.11-3.13 missing' }

# ---------------------------------------------------------------- 4. heistclips
Say "4) heistclips (github.com/2016judea/heistclips @ $($HeistSha.Substring(0,7)))"
$Tools = Join-Path $Root 'tools'
$Hc = Join-Path $Tools 'heistclips'
$VerFile = Join-Path $Hc 'VERSION.txt'
$want = "2016judea/heistclips @ $HeistSha"
if (-not ((Test-Path $VerFile) -and ((Get-Content $VerFile -Raw).Trim() -eq $want))) {
    New-Item -ItemType Directory -Force -Path $Tools | Out-Null
    $zip = Join-Path $Tools 'heistclips.zip'
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/2016judea/heistclips/archive/$HeistSha.zip" -OutFile $zip -ErrorAction Stop
        if (Test-Path $Hc) { Remove-Item -Recurse -Force $Hc -ErrorAction Stop }
        Expand-Archive -Path $zip -DestinationPath $Tools -Force -ErrorAction Stop
        Rename-Item -Path (Join-Path $Tools "heistclips-$HeistSha") -NewName 'heistclips' -ErrorAction Stop
        Remove-Item $zip -Force
        Set-Content -Path $VerFile -Value $want -Encoding ASCII
    } catch { Bad "heistclips download failed: $($_.Exception.Message)" }
}
if (Test-Path (Join-Path $Hc 'cli\heistclips.mjs')) { Ok 'heistclips installed' } else { Bad 'heistclips missing' }

# ---------------------------------------------------------------- 5. venv + libraries
Say '5) Private Python environment (.venv) + faster-whisper, OpenCV, requests'
$VPy = Join-Path $Root '.venv\Scripts\python.exe'
if ($Py -and -not (Test-Path $VPy)) { & $Py -m venv (Join-Path $Root '.venv') }
if (Test-Path $VPy) {
    & $VPy -m pip install --upgrade pip --quiet --disable-pip-version-check | Out-Host
    & $VPy -m pip install -r (Join-Path $Root 'app\requirements.txt') --quiet --disable-pip-version-check | Out-Host
    if ($LASTEXITCODE -eq 0) { Ok 'Python libraries installed' } else { Bad 'pip install failed (see messages above)' }
    & $VPy -c "import faster_whisper, cv2" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Say '  faster-whisper/OpenCV cannot load: installing the Microsoft Visual C++ runtime...' 'Yellow'
        Winget-Install 'Microsoft.VCRedist.2015+.x64' 'Visual C++ runtime'
        & $VPy -c "import faster_whisper, cv2" 2>$null
        if ($LASTEXITCODE -eq 0) { Ok 'faster-whisper + OpenCV load' } else { Bad 'faster-whisper/OpenCV still cannot load (run check.bat for the error)' }
    }
    & $VPy (Join-Path $Root 'app\patch_heistclips.py') | Out-Host
} else { Bad '.venv not created' }

# ---------------------------------------------------------------- 6. fonts, .env, files
Say '6) Fonts, .env, folders'
$Fonts = Join-Path $Root 'fonts'
New-Item -ItemType Directory -Force -Path $Fonts | Out-Null
foreach ($f in 'ariblk.ttf', 'arialbd.ttf', 'impact.ttf') {
    $src = Join-Path $env:WINDIR "Fonts\$f"
    if ((Test-Path $src) -and -not (Test-Path (Join-Path $Fonts $f))) { Copy-Item $src $Fonts }
}
if (Get-ChildItem $Fonts -Filter *.ttf) { Ok ('fonts: ' + ((Get-ChildItem $Fonts -Filter *.ttf).Name -join ', ')) } else { Bad 'no subtitle font copied' }
$EnvFile = Join-Path $Root '.env'
if (-not (Test-Path $EnvFile)) { Copy-Item (Join-Path $Root '.env.example') $EnvFile; Ok '.env created (put your Post for Me key in it)' } else { Ok '.env exists (not modified)' }

# ---------------------------------------------------------------- 7. whisper model
if (Test-Path $VPy) {
    Say '7) Downloading the faster-whisper model (first time only, ~500 MB for "small")'
    $model = 'small'
    $line = Select-String -Path (Join-Path $Root 'config.txt') -Pattern '^\s*WHISPER_MODEL\s*=\s*(\S+)' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($line) { $model = $line.Matches[0].Groups[1].Value }
    & $VPy -c "import sys; sys.path.insert(0, r'$Root\app'); import common; common.setup_logging(False); import subs; subs.load_model('$model'); print('model ok')" | Out-Host
    if ($LASTEXITCODE -eq 0) { Ok "whisper model '$model' ready" } else { Bad 'whisper model download failed (it will retry on the first run)' }
}

# ---------------------------------------------------------------- 8. full check
Say "`n8) Final check (doctor)" 'Cyan'
if (Test-Path $VPy) { & $VPy (Join-Path $Root 'app\clipper.py') doctor | Out-Host }

if ($Failed.Count -eq 0) {
    Say "`nSETUP DONE. Next: campaigns.bat, then edit streamers.txt / config.txt / .env, then run.bat (DRY_RUN), then install_task.bat." 'Green'
} else {
    Say "`nSETUP FINISHED WITH PROBLEMS:" 'Red'
    $Failed | ForEach-Object { Say "  - $_" 'Red' }
    Say 'Fix them (see README.md) and run setup.bat again.' 'Red'
    exit 1
}
