# ARKVOLT Proposals - staff laptop setup window (started by Setup.bat).
# Checks the laptop, downloads what is missing (Python 3.12, Microsoft Edge), installs the app for this Windows
# user, adds the desktop icon and opens the app. Safe to run again: proposals on the laptop are kept.
# Test switches: ARKVOLT_INSTALL_DIR (install elsewhere), ARKVOLT_TEST (no shortcuts/launch), ARKVOLT_AUTORUN (start + close by itself)
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$Src = $PSScriptRoot
$Dest = if ($env:ARKVOLT_INSTALL_DIR) { $env:ARKVOLT_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA "ARKVOLT\ProposalApp" }
$LogFile = Join-Path $env:TEMP "arkvolt-setup.log"
Set-Content -Path $LogFile -Value "ARKVOLT setup $(Get-Date)`r`nSource: $Src`r`nInstall to: $Dest" -Encoding UTF8

$STEPS = @("Windows version and disk space", "Internet connection", "Microsoft Edge (draws the PDFs)", "Python 3.12 (runs the app)",
           "ARKVOLT Proposals app and its components", "Desktop and Start-menu icon", "Final check")
$sync = [hashtable]::Synchronized(@{ State = @{}; Detail = @{}; Log = [System.Collections.ArrayList]::Synchronized((New-Object System.Collections.ArrayList));
                                     Done = $false; Error = $null; First = $true; Busy = $false })

# ------------------------------------------------------------------------------------------- the work (background)
$work = {
    param($sync, $Src, $Dest, $LogFile, $IsTest)
    $ErrorActionPreference = "Stop"; $ProgressPreference = "SilentlyContinue"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    function Log($t) { [void]$sync.Log.Add("$t"); Add-Content -Path $LogFile -Value "$t" -Encoding UTF8 }
    function Set-Step($i, $state, $detail) { $sync.State[$i] = $state; if ($detail) { $sync.Detail[$i] = $detail }; Log "[$($i+1)] $state $detail" }
    function Fail($i, $t) { Set-Step $i "fail" $t; throw "STOP: $t" }
    function HasWinget { try { $null = Get-Command winget -ErrorAction Stop; return $true } catch { return $false } }
    function FindPython {
        foreach ($p in @("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe", "$env:ProgramFiles\Python312\python.exe")) { if (Test-Path $p) { return $p } }
        try { $p = (& py -3.12 -c "import sys; print(sys.executable)" 2>$null); if ($LASTEXITCODE -eq 0 -and $p -and (Test-Path $p.Trim())) { return $p.Trim() } } catch {}
        return $null
    }
    function Run($exe, $argList) {
        # native tools write warnings to stderr; with "Stop" PowerShell 5.1 would turn those into fatal errors
        $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
        try { & $exe @argList 2>&1 | ForEach-Object { Log "    $_" }; $code = $LASTEXITCODE } finally { $ErrorActionPreference = $old }
        return $code
    }
    try {
        # 1 -------------------------------------------------------------------------------------------
        Set-Step 0 "run" "checking"
        $os = Get-CimInstance Win32_OperatingSystem
        if (-not [Environment]::Is64BitOperatingSystem) { Fail 0 "32-bit Windows - a 64-bit Windows 10/11 laptop is needed" }
        if ([int]$os.BuildNumber -lt 17763) { Fail 0 "Windows is too old - update to Windows 10 (1809+) or 11" }
        $free = [math]::Round((Get-PSDrive -Name ($env:LOCALAPPDATA.Substring(0, 1))).Free / 1GB, 1)
        if ($free -lt 1.5) { Fail 0 "only $free GB free - free up 1.5 GB" }
        Set-Step 0 "ok" "$(($os.Caption -replace 'Microsoft ', '')), $free GB free"
        # 2 -------------------------------------------------------------------------------------------
        Set-Step 1 "run" "checking"
        try { Invoke-WebRequest "https://pypi.org/simple/pip/" -UseBasicParsing -TimeoutSec 20 | Out-Null } catch { Fail 1 "no internet - connect to Wi-Fi and press Try again" }
        Set-Step 1 "ok" "connected"
        # 3 -------------------------------------------------------------------------------------------
        Set-Step 2 "run" "checking"
        $edgePaths = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe", "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe", "$env:LOCALAPPDATA\Microsoft\Edge\Application\msedge.exe")
        $edge = $edgePaths | Where-Object { Test-Path $_ } | Select-Object -First 1
        if (-not $edge -and (HasWinget)) { Set-Step 2 "run" "installing Microsoft Edge"; [void](Run "winget" @("install", "-e", "--id", "Microsoft.Edge", "--silent", "--accept-package-agreements", "--accept-source-agreements")); $edge = $edgePaths | Where-Object { Test-Path $_ } | Select-Object -First 1 }
        if ($edge) { Set-Step 2 "ok" "found" } else { Set-Step 2 "ok" "not found - a built-in browser will be added in step 5" }
        # 4 -------------------------------------------------------------------------------------------
        Set-Step 3 "run" "checking"
        $py = FindPython
        if (-not $py -and (HasWinget)) { Set-Step 3 "run" "downloading Python 3.12"; [void](Run "winget" @("install", "-e", "--id", "Python.Python.3.12", "--scope", "user", "--silent", "--accept-package-agreements", "--accept-source-agreements")); $py = FindPython }
        if (-not $py) {
            Set-Step 3 "run" "downloading Python 3.12 from python.org (25 MB)"
            $inst = Join-Path $env:TEMP "python-3.12.10-amd64.exe"
            try { Invoke-WebRequest "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe" -OutFile $inst -UseBasicParsing } catch { Fail 3 "could not download Python: $($_.Exception.Message)" }
            Set-Step 3 "run" "installing Python 3.12 (1-2 minutes)"
            Start-Process -FilePath $inst -ArgumentList "/quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_tcltk=1 Include_test=0 Include_doc=0 Shortcuts=0" -Wait | Out-Null
            Remove-Item $inst -ErrorAction SilentlyContinue
            $py = FindPython
        }
        if (-not $py) { Fail 3 "Python 3.12 could not be installed" }
        Set-Step 3 "ok" "ready"
        # 5 -------------------------------------------------------------------------------------------
        Set-Step 4 "run" "copying the app"
        $sync.First = -not (Test-Path (Join-Path $Dest "data\app.db"))
        New-Item -ItemType Directory -Force -Path (Join-Path $Dest "data") | Out-Null
        robocopy $Src $Dest /E /NFL /NDL /NJH /NJS /NP /XF "Setup.bat" "setup.ps1" "READ ME FIRST.txt" | Out-Null
        if ($LASTEXITCODE -ge 8) { Fail 4 "could not copy the app files to $Dest" }
        if ($sync.First) { Set-Content -Path (Join-Path $Dest "data\NEEDS_SETUP") -Value "first run" }
        $venvPy = Join-Path $Dest ".venv\Scripts\python.exe"
        if (-not (Test-Path $venvPy)) { Set-Step 4 "run" "creating the app's own Python environment"; if ((Run $py @("-m", "venv", (Join-Path $Dest ".venv"))) -ne 0) { Fail 4 "could not create the Python environment" } }
        Set-Step 4 "run" "downloading components (first time: 2-5 minutes)"
        [void](Run $venvPy @("-m", "pip", "install", "--upgrade", "pip", "--quiet", "--disable-pip-version-check"))
        if ((Run $venvPy @("-m", "pip", "install", "-r", (Join-Path $Dest "requirements.txt"), "--disable-pip-version-check", "--progress-bar", "off")) -ne 0) { Fail 4 "downloading the components failed - check the internet and press Try again" }
        if (-not $edge) { Set-Step 4 "run" "downloading the PDF browser"; if ((Run $venvPy @("-m", "playwright", "install", "chromium")) -ne 0) { Fail 4 "could not download the PDF browser" } }
        if ((Run $venvPy @("-c", "import fastapi, sqlalchemy, pymupdf, playwright, segno, PIL, tkinter; print('components ok')")) -ne 0) { Fail 4 "the components did not install correctly" }
        Set-Step 4 "ok" "installed"
        # 6 -------------------------------------------------------------------------------------------
        if ($IsTest) { Set-Step 5 "ok" "skipped (test)" } else {
            Set-Step 5 "run" "adding"
            $ws = New-Object -ComObject WScript.Shell
            foreach ($dir in @([Environment]::GetFolderPath("Desktop"), (Join-Path ([Environment]::GetFolderPath("Programs")) "ARKVOLT"))) {
                New-Item -ItemType Directory -Force -Path $dir | Out-Null
                $lnk = $ws.CreateShortcut((Join-Path $dir "ARKVOLT Proposals.lnk"))
                $lnk.TargetPath = Join-Path $Dest ".venv\Scripts\pythonw.exe"
                $lnk.Arguments = '"' + (Join-Path $Dest "launcher.pyw") + '"'
                $lnk.WorkingDirectory = $Dest
                $lnk.IconLocation = (Join-Path $Dest "arkvolt.ico")
                $lnk.Description = "ARKVOLT solar proposals"
                $lnk.Save()
            }
            Set-Step 5 "ok" "on the desktop and in the Start menu"
        }
        # 7 -------------------------------------------------------------------------------------------
        Set-Step 6 "run" "testing the app"
        if ((Run $venvPy @("-c", "import sys; sys.path.insert(0, r'$Dest'); import app.main; print('app loads ok')")) -ne 0) { Fail 6 "the app does not start - send the log to your admin" }
        Set-Step 6 "ok" "ready to use"
        $sync.Done = $true
    } catch {
        if (-not $sync.Error) { $sync.Error = ($_.Exception.Message -replace '^STOP: ', '') }
        Log "ERROR: $($_.Exception.Message)"
    } finally { $sync.Busy = $false }
}

# ------------------------------------------------------------------------------------------- the window
$NAVY = [Drawing.Color]::FromArgb(10, 21, 39); $GREEN = [Drawing.Color]::FromArgb(128, 194, 0); $GD = [Drawing.Color]::FromArgb(95, 152, 0)
$MUTED = [Drawing.Color]::FromArgb(91, 107, 128); $RED = [Drawing.Color]::FromArgb(214, 69, 69); $BLUE = [Drawing.Color]::FromArgb(27, 105, 168)
$F = "Segoe UI"
$form = New-Object Windows.Forms.Form
$form.Text = "ARKVOLT Proposals - Setup"; $form.ClientSize = New-Object Drawing.Size(640, 600); $form.StartPosition = "CenterScreen"
$form.FormBorderStyle = "FixedSingle"; $form.MaximizeBox = $false; $form.BackColor = [Drawing.Color]::White; $form.Font = New-Object Drawing.Font($F, 9.5)
$ico = Join-Path $Src "arkvolt.ico"; if (Test-Path $ico) { $form.Icon = New-Object Drawing.Icon($ico) }

$head = New-Object Windows.Forms.Panel; $head.Dock = "Top"; $head.Height = 92; $head.BackColor = $NAVY; $form.Controls.Add($head)
$logoPath = Join-Path $Src "logo-white.png"
if (Test-Path $logoPath) {
    $logoPanel = New-Object Windows.Forms.Panel; $logoPanel.SetBounds(28, 20, 230, 52); $logoPanel.BackColor = $NAVY
    $logoPanel.BackgroundImage = [Drawing.Image]::FromFile($logoPath); $logoPanel.BackgroundImageLayout = "Zoom"; $head.Controls.Add($logoPanel)
}
$tag = New-Object Windows.Forms.Label; $dot = [string][char]0x00B7; $tag.Text = "SOLAR  $dot  STORAGE  $dot  SMART ENERGY"; $tag.ForeColor = $GREEN; $tag.Font = New-Object Drawing.Font($F, 8, [Drawing.FontStyle]::Bold)
$tag.SetBounds(380, 40, 240, 20); $tag.TextAlign = "MiddleRight"; $head.Controls.Add($tag)
$stripe = New-Object Windows.Forms.Panel; $stripe.SetBounds(0, 92, 640, 4); $stripe.BackColor = $GREEN; $form.Controls.Add($stripe)

$title = New-Object Windows.Forms.Label; $title.Text = "Set up ARKVOLT Proposals on this laptop"; $title.Font = New-Object Drawing.Font($F, 14, [Drawing.FontStyle]::Bold)
$title.ForeColor = $NAVY; $title.SetBounds(28, 112, 590, 30); $form.Controls.Add($title)
$sub = New-Object Windows.Forms.Label; $sub.Text = "We check this laptop and download anything that is missing. No administrator password is needed; first time takes about 5 minutes."
$sub.ForeColor = $MUTED; $sub.SetBounds(28, 144, 590, 38); $form.Controls.Add($sub)

$icons = @(); $texts = @(); $details = @()
for ($i = 0; $i -lt $STEPS.Count; $i++) {
    $y = 190 + $i * 36
    $ic = New-Object Windows.Forms.Label; $ic.Text = [string][char]0x25CB; $ic.Font = New-Object Drawing.Font("Segoe UI Symbol", 13); $ic.ForeColor = [Drawing.Color]::Silver
    $ic.SetBounds(28, $y, 30, 30); $ic.TextAlign = "MiddleCenter"; $form.Controls.Add($ic); $icons += $ic
    $tx = New-Object Windows.Forms.Label; $tx.Text = $STEPS[$i]; $tx.Font = New-Object Drawing.Font($F, 10, [Drawing.FontStyle]::Bold); $tx.ForeColor = $NAVY
    $tx.SetBounds(62, $y + 1, 300, 28); $tx.TextAlign = "MiddleLeft"; $form.Controls.Add($tx); $texts += $tx
    $dt = New-Object Windows.Forms.Label; $dt.Text = ""; $dt.ForeColor = $MUTED; $dt.SetBounds(360, $y + 1, 255, 28); $dt.TextAlign = "MiddleRight"
    $dt.AutoEllipsis = $true; $form.Controls.Add($dt); $details += $dt
}
$bar = New-Object Windows.Forms.ProgressBar; $bar.SetBounds(28, 448, 584, 10); $bar.Maximum = $STEPS.Count; $form.Controls.Add($bar)
$status = New-Object Windows.Forms.Label; $status.SetBounds(28, 464, 584, 44); $status.ForeColor = $NAVY; $status.Font = New-Object Drawing.Font($F, 10)
$status.Text = "Press Install to begin."; $form.Controls.Add($status)
$logBox = New-Object Windows.Forms.TextBox; $logBox.Multiline = $true; $logBox.ReadOnly = $true; $logBox.ScrollBars = "Vertical"; $logBox.Visible = $false
$logBox.Font = New-Object Drawing.Font("Consolas", 8.5); $logBox.SetBounds(28, 190, 584, 250); $logBox.BackColor = [Drawing.Color]::FromArgb(247, 249, 251); $form.Controls.Add($logBox)
$detLink = New-Object Windows.Forms.LinkLabel; $detLink.Text = "Show details"; $detLink.SetBounds(28, 560, 120, 22); $detLink.LinkColor = $BLUE; $form.Controls.Add($detLink)
$detLink.Add_LinkClicked({ $logBox.Visible = -not $logBox.Visible; $logBox.BringToFront(); $detLink.Text = $(if ($logBox.Visible) { "Hide details" } else { "Show details" }) })

function New-Btn($text, $x, $w, $primary) {
    $b = New-Object Windows.Forms.Button; $b.Text = $text; $b.SetBounds($x, 548, $w, 38); $b.FlatStyle = "Flat"; $b.Cursor = "Hand"
    $b.Font = New-Object Drawing.Font($F, 10, [Drawing.FontStyle]::Bold)
    if ($primary) { $b.BackColor = $GREEN; $b.ForeColor = $NAVY; $b.FlatAppearance.BorderSize = 0 } else { $b.BackColor = [Drawing.Color]::White; $b.ForeColor = $NAVY; $b.FlatAppearance.BorderColor = [Drawing.Color]::FromArgb(203, 213, 225) }
    $form.Controls.Add($b); return $b
}
$btnGo = New-Btn "Install" 392 220 $true
$btnClose = New-Btn "Close" 280 100 $false
$btnClose.Add_Click({ $form.Close() })

$ps = $null; $handle = $null; $shown = 0
function Start-Work {
    $sync.State.Clear(); $sync.Detail.Clear(); $sync.Error = $null; $sync.Done = $false; $sync.Busy = $true
    $btnGo.Enabled = $false; $btnGo.Text = "Installing..."; $status.ForeColor = $NAVY; $status.Text = "Working - please keep this window open."
    $rs = [runspacefactory]::CreateRunspace(); $rs.ApartmentState = "STA"; $rs.Open()
    $script:ps = [powershell]::Create(); $script:ps.Runspace = $rs
    [void]$script:ps.AddScript($work).AddArgument($sync).AddArgument($Src).AddArgument($Dest).AddArgument($LogFile).AddArgument([bool]$env:ARKVOLT_TEST)
    $script:handle = $script:ps.BeginInvoke()
    $timer.Start()
}
$btnGo.Add_Click({
    if ($sync.Done) { if (-not $env:ARKVOLT_TEST) { Start-Process -FilePath (Join-Path $Dest ".venv\Scripts\pythonw.exe") -ArgumentList ('"' + (Join-Path $Dest "launcher.pyw") + '"') -WorkingDirectory $Dest }; $form.Close(); return }
    Start-Work
})

$timer = New-Object Windows.Forms.Timer; $timer.Interval = 200
$timer.Add_Tick({
    $okCount = 0
    for ($i = 0; $i -lt $STEPS.Count; $i++) {
        $st = $sync.State[$i]
        switch ($st) {
            "run"  { $icons[$i].Text = [string][char]0x25CF; $icons[$i].ForeColor = $BLUE }
            "ok"   { $icons[$i].Text = [string][char]0x2714; $icons[$i].ForeColor = $GD; $okCount++ }
            "fail" { $icons[$i].Text = [string][char]0x2716; $icons[$i].ForeColor = $RED }
            default { $icons[$i].Text = [string][char]0x25CB; $icons[$i].ForeColor = [Drawing.Color]::Silver }
        }
        if ($sync.Detail[$i]) { $details[$i].Text = $sync.Detail[$i]; $details[$i].ForeColor = $(if ($st -eq "fail") { $RED } else { $MUTED }) }
    }
    $bar.Value = [math]::Min($okCount, $STEPS.Count)
    while ($shown -lt $sync.Log.Count) { $logBox.AppendText($sync.Log[$shown] + "`r`n"); $script:shown++ }
    if (-not $sync.Busy) {
        $timer.Stop()
        if ($sync.Done) {
            $bar.Value = $bar.Maximum
            $status.ForeColor = $GD
            $status.Text = $(if ($sync.First) { "All done! Click 'Open ARKVOLT Proposals' - the browser shows 'Set up this laptop' to fill in once." } else { "All done! The app was updated; proposals on this laptop were kept." })
            $btnGo.Text = "Open ARKVOLT Proposals"; $btnGo.Enabled = $true
            if ($env:ARKVOLT_SNAPSHOT) { $bmp = New-Object Drawing.Bitmap($form.Width, $form.Height); $form.DrawToBitmap($bmp, (New-Object Drawing.Rectangle(0, 0, $form.Width, $form.Height))); $bmp.Save($env:ARKVOLT_SNAPSHOT + "-done.png") }
            if ($env:ARKVOLT_AUTORUN) { $form.Close() }
        } else {
            $status.ForeColor = $RED; $status.Text = "Setup stopped: $($sync.Error)"
            $btnGo.Text = "Try again"; $btnGo.Enabled = $true
            if ($env:ARKVOLT_AUTORUN) { $form.Close() }
        }
    }
})
if ($env:ARKVOLT_AUTORUN) { $form.Add_Shown({
    if ($env:ARKVOLT_SNAPSHOT) { $bmp = New-Object Drawing.Bitmap($form.Width, $form.Height); $form.DrawToBitmap($bmp, (New-Object Drawing.Rectangle(0, 0, $form.Width, $form.Height))); $bmp.Save($env:ARKVOLT_SNAPSHOT + "-start.png") }
    Start-Work }) }
[void]$form.ShowDialog()
if ($env:ARKVOLT_AUTORUN) { if ($sync.Done) { exit 0 } else { Write-Output "FAILED: $($sync.Error)"; exit 1 } }
