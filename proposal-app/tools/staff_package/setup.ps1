# ARKVOLT Proposals - staff laptop setup.  Started by Setup.bat (double-click).
# Checks the laptop, downloads what is missing (Python 3.12, Microsoft Edge), installs the app for this
# Windows user, puts "ARKVOLT Proposals" on the desktop and opens it. Safe to run again (keeps proposals).
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Src  = $PSScriptRoot
$Dest = if ($env:ARKVOLT_INSTALL_DIR) { $env:ARKVOLT_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA "ARKVOLT\ProposalApp" }  # override only for testing
$Log  = Join-Path $env:TEMP "arkvolt-setup.log"
Start-Transcript -Path $Log -Force | Out-Null

function Step($n, $t) { Write-Host ""; Write-Host "[$n/7] $t" -ForegroundColor Cyan }
function Ok($t)   { Write-Host "      OK  $t" -ForegroundColor Green }
function Info($t) { Write-Host "      ..  $t" }
function Fail($t) {
    Write-Host ""; Write-Host "  SETUP STOPPED: $t" -ForegroundColor Red
    Write-Host "  Send this file to your admin: $Log" -ForegroundColor Yellow
    Stop-Transcript | Out-Null; Read-Host "Press Enter to close"; exit 1
}
function HasWinget { try { $null = Get-Command winget -ErrorAction Stop; return $true } catch { return $false } }

Write-Host "==============================================================" -ForegroundColor Green
Write-Host "   ARKVOLT Proposals - laptop setup" -ForegroundColor Green
Write-Host "==============================================================" -ForegroundColor Green

# ---------------------------------------------------------------- 1. Windows
Step 1 "Checking Windows"
$os = Get-CimInstance Win32_OperatingSystem
if (-not [Environment]::Is64BitOperatingSystem) { Fail "This laptop runs 32-bit Windows. A 64-bit Windows 10 or 11 laptop is needed." }
if ([int]$os.BuildNumber -lt 17763) { Fail "Windows is too old ($($os.Caption)). Update to Windows 10 (1809 or newer) or Windows 11." }
Ok "$($os.Caption) (64-bit)"
$freeGB = [math]::Round((Get-PSDrive -Name ($env:LOCALAPPDATA.Substring(0,1))).Free / 1GB, 1)
if ($freeGB -lt 1.5) { Fail "Only $freeGB GB free on the drive. Free up at least 1.5 GB and run Setup again." }
Ok "$freeGB GB free disk space"

# ---------------------------------------------------------------- 2. Internet
Step 2 "Checking the internet connection"
try { Invoke-WebRequest "https://pypi.org/simple/pip/" -UseBasicParsing -TimeoutSec 20 | Out-Null; Ok "Internet is working" }
catch { Fail "No internet connection. Connect to Wi-Fi and run Setup again." }

# ---------------------------------------------------------------- 3. Microsoft Edge (draws the PDF proposals)
Step 3 "Checking Microsoft Edge"
$edgePaths = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe", "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe", "$env:LOCALAPPDATA\Microsoft\Edge\Application\msedge.exe")
$edge = $edgePaths | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $edge) {
    Info "Edge not found - installing it"
    if (HasWinget) { winget install -e --id Microsoft.Edge --silent --accept-package-agreements --accept-source-agreements | Out-Null }
    $edge = $edgePaths | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if ($edge) { Ok "Microsoft Edge found" } else { Info "Edge could not be installed - a built-in browser will be downloaded in step 5 instead" }

# ---------------------------------------------------------------- 4. Python 3.12 (runs the app)
Step 4 "Checking Python 3.12"
function FindPython {
    $c = @("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe", "$env:ProgramFiles\Python312\python.exe")
    foreach ($p in $c) { if (Test-Path $p) { return $p } }
    try { $p = (& py -3.12 -c "import sys; print(sys.executable)" 2>$null); if ($LASTEXITCODE -eq 0 -and $p -and (Test-Path $p.Trim())) { return $p.Trim() } } catch {}
    return $null
}
$py = FindPython
if (-not $py) {
    Info "Python 3.12 not found - downloading it (about 25 MB)"
    if (HasWinget) {
        try { winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements | Out-Null } catch {}
        $py = FindPython
    }
    if (-not $py) {
        $inst = Join-Path $env:TEMP "python-3.12.10-amd64.exe"
        try { Invoke-WebRequest "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe" -OutFile $inst -UseBasicParsing }
        catch { Fail "Could not download Python from python.org: $($_.Exception.Message)" }
        Info "Installing Python 3.12 (1-2 minutes)"
        $p = Start-Process -FilePath $inst -ArgumentList "/quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_test=0 Include_doc=0 Shortcuts=0" -Wait -PassThru
        Remove-Item $inst -ErrorAction SilentlyContinue
        $py = FindPython
    }
}
if (-not $py) { Fail "Python 3.12 could not be installed." }
Ok "Python 3.12 ready"

# ---------------------------------------------------------------- 5. App files + its own Python packages
Step 5 "Installing the ARKVOLT Proposals app"
$firstInstall = -not (Test-Path (Join-Path $Dest "data\app.db"))
New-Item -ItemType Directory -Force -Path $Dest | Out-Null
# copy everything from the package; the laptop's own proposals (data\app.db, data\pdfs, ...) are never overwritten
robocopy $Src $Dest /E /NFL /NDL /NJH /NJS /NP /XF "Setup.bat" "setup.ps1" "READ ME FIRST.txt" /XD "data\pdfs" "data\uploads" | Out-Null
if ($LASTEXITCODE -ge 8) { Fail "Could not copy the app files to $Dest" }
if ($firstInstall) { New-Item -ItemType Directory -Force -Path (Join-Path $Dest "data") | Out-Null; Set-Content -Path (Join-Path $Dest "data\NEEDS_SETUP") -Value "first run" }
$venvPy = Join-Path $Dest ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { Info "Creating the app's private Python environment"; & $py -m venv (Join-Path $Dest ".venv"); if ($LASTEXITCODE -ne 0) { Fail "Could not create the Python environment." } }
Info "Downloading the app's components (first time: 2-5 minutes)"
& $venvPy -m pip install --upgrade pip --quiet --disable-pip-version-check 2>&1 | Out-Null
& $venvPy -m pip install -r (Join-Path $Dest "requirements.txt") --quiet --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { Fail "Downloading the app's components failed (pip). Check the internet and run Setup again." }
if (-not $edge) { Info "Downloading a built-in browser for PDFs"; & $venvPy -m playwright install chromium; if ($LASTEXITCODE -ne 0) { Fail "Could not download the PDF browser." } }
& $venvPy -c "import fastapi, sqlalchemy, pymupdf, playwright, segno, PIL; print('ok')" | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "The app's components did not install correctly." }
Ok "App installed in $Dest"

# ---------------------------------------------------------------- 6. Desktop + Start-menu shortcuts
if ($env:ARKVOLT_TEST) { Ok "Test run - shortcuts and start skipped"; Stop-Transcript | Out-Null; exit 0 }
Step 6 "Adding the desktop icon"
$ws = New-Object -ComObject WScript.Shell
foreach ($dir in @([Environment]::GetFolderPath("Desktop"), (Join-Path ([Environment]::GetFolderPath("Programs")) "ARKVOLT"))) {
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $lnk = $ws.CreateShortcut((Join-Path $dir "ARKVOLT Proposals.lnk"))
    $lnk.TargetPath = Join-Path $Dest "ARKVOLT Proposals.bat"
    $lnk.WorkingDirectory = $Dest
    $lnk.IconLocation = (Join-Path $Dest "arkvolt.ico")
    $lnk.WindowStyle = 7
    $lnk.Description = "ARKVOLT solar proposals"
    $lnk.Save()
}
Ok "'ARKVOLT Proposals' is on the desktop and in the Start menu"

# ---------------------------------------------------------------- 7. Start
Step 7 "Starting the app"
Start-Process -FilePath (Join-Path $Dest "ARKVOLT Proposals.bat") -WorkingDirectory $Dest -WindowStyle Minimized
Write-Host ""
Write-Host "==============================================================" -ForegroundColor Green
if ($firstInstall) { Write-Host "  DONE. The browser opens the 'Set up this laptop' page - fill it in once." -ForegroundColor Green }
else { Write-Host "  DONE. The app was updated; proposals on this laptop were kept." -ForegroundColor Green }
Write-Host "  From now on, just double-click 'ARKVOLT Proposals' on the desktop." -ForegroundColor Green
Write-Host "==============================================================" -ForegroundColor Green
Stop-Transcript | Out-Null
Start-Sleep -Seconds 8
