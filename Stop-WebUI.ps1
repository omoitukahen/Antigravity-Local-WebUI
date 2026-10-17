$ErrorActionPreference = 'Stop'
$runtimePath = Join-Path $PSScriptRoot 'data\runtime.json'
if (-not (Test-Path -LiteralPath $runtimePath)) { Write-Output 'WebUIは起動していません。'; exit 0 }
$runtimeInfo = Get-Content -LiteralPath $runtimePath -Encoding utf8 -Raw | ConvertFrom-Json
if ($runtimeInfo.origin -notmatch '^http://127\.0\.0\.1:[0-9]{1,5}$' -or ([uri]$runtimeInfo.origin).Port -gt 65535) { throw '起動記録のローカルURLが無効です。' }
$shutdownHeaders = @{ Authorization = 'Bearer ' + $runtimeInfo.token; Origin = $runtimeInfo.origin }
Invoke-RestMethod -Method Post -Uri ($runtimeInfo.origin + '/api/shutdown') -Headers $shutdownHeaders -TimeoutSec 10 | Out-Null
$shutdownDeadline = (Get-Date).AddSeconds(15)
while ((Test-Path -LiteralPath $runtimePath) -and (Get-Date) -lt $shutdownDeadline) { Start-Sleep -Milliseconds 100 }
if (Test-Path -LiteralPath $runtimePath) { throw '終了を確認できません。プロセス名による一括終了は行っていません。' }
Write-Output 'WebUIの終了を確認しました。'
