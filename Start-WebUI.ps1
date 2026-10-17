param([switch]$Mock,[switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$appPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $appPython)) { throw '最初に Setup.ps1 を実行してください。' }
$startupId = [guid]::NewGuid().ToString()
$startArgs = @((Join-Path $PSScriptRoot 'launch.py'),'--startup-id',$startupId)
if ($Mock) { $startArgs += '--mock' }
if ($NoBrowser) { $startArgs += '--no-browser' }
$quotedArgs = $startArgs | ForEach-Object { '"' + $_ + '"' }
$launchData = Join-Path $PSScriptRoot 'data'
[System.IO.Directory]::CreateDirectory($launchData) | Out-Null
$launchOut = Join-Path $launchData ('launch-' + $startupId + '.out.log')
$launchErr = Join-Path $launchData ('launch-' + $startupId + '.err.log')
$startedProcess = Start-Process -FilePath $appPython -ArgumentList $quotedArgs -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput $launchOut -RedirectStandardError $launchErr -PassThru
$runtimePath = Join-Path $PSScriptRoot 'data\runtime.json'
$startDeadline = (Get-Date).AddSeconds(15)
while ((Get-Date) -lt $startDeadline) {
    $startedProcess.Refresh()
    if ($startedProcess.HasExited) { $startedProcess.WaitForExit(); if ($startedProcess.ExitCode -eq 2) { throw 'このWebUIはすでに起動しています。先に Stop-WebUI.ps1 で終了してください。' }; throw "起動に失敗しました。終了コード: $($startedProcess.ExitCode)" }
    if (Test-Path -LiteralPath $runtimePath) {
        $runtimeInfo = Get-Content -LiteralPath $runtimePath -Encoding utf8 -Raw | ConvertFrom-Json
        if ($runtimeInfo.startup_id -eq $startupId) {
            try {
                $probeHeaders = @{Authorization='Bearer ' + $runtimeInfo.token; Origin=$runtimeInfo.origin}
                Invoke-RestMethod -Method Get -Uri ($runtimeInfo.origin + '/api/status') -Headers $probeHeaders -TimeoutSec 2 | Out-Null
                Write-Output "WebUIを起動しました。PID: $($runtimeInfo.pid)"; exit 0
            } catch { }
        }
    }
    Start-Sleep -Milliseconds 100
}
throw '起動確認がタイムアウトしました。'
