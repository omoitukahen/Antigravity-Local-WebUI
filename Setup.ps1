param([string]$PythonPath, [string]$CliPath, [switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
if ($PSVersionTable.PSVersion.Major -lt 7) { throw 'PowerShell 7 が必要です: https://aka.ms/powershell-release?tag=stable' }
if (-not $IsWindows -or -not [Environment]::Is64BitOperatingSystem) { throw 'Windows 11 x64 を対象としています。' }
$pythonProbe = 'import sys,struct,platform; raise SystemExit(0 if sys.platform == "win32" and sys.version_info[:2] == (3,14) and struct.calcsize("P")==8 and platform.machine().lower() in ("amd64","x86_64") else 1)'
if ($PythonPath) {
    if (-not [IO.Path]::IsPathFullyQualified($PythonPath) -or -not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) { throw '-PythonPath には既存のPython 3.14 x64の絶対パスを指定してください。' }
    $basePython = $PythonPath
} else {
    $basePython = $null
    $launcher = Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue
    if ($launcher) {
        $found = & $launcher.Source -3.14 -c 'import sys; print(sys.executable)' 2>$null
        if ($LASTEXITCODE -eq 0 -and $found) { $basePython = ([string]($found | Select-Object -Last 1)).Trim() }
    }
    if (-not $basePython) {
        $candidate = Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue
        if ($candidate -and $candidate.Source -notlike '*\Microsoft\WindowsApps\*') { $basePython = $candidate.Source }
    }
    if (-not $basePython) { throw 'Python 3.14 x64 が見つかりません。https://www.python.org/downloads/windows/ から導入するか -PythonPath で指定してください。' }
}
& $basePython -c $pythonProbe
if ($LASTEXITCODE -ne 0) { throw 'Windows用Python 3.14 x64が必要です。-PythonPath で対象を指定してください。' }
if ($CliPath -and (-not [IO.Path]::IsPathFullyQualified($CliPath) -or -not (Test-Path -LiteralPath $CliPath -PathType Leaf) -or [IO.Path]::GetExtension($CliPath) -ne '.exe')) {
    throw '-CliPath には既存の agy.exe の絶対パスを指定してください。別のCLIへ自動切替はしません。'
}
if ($CheckOnly) { Write-Output 'Prerequisites OK (Python 3.14 x64). No installation or CLI invocation.'; exit 0 }
$envDir = Join-Path $PSScriptRoot '.venv'
$appPython = Join-Path $envDir 'Scripts\python.exe'
if (Test-Path -LiteralPath $envDir) {
    if (-not (Test-Path -LiteralPath $appPython -PathType Leaf)) { throw '既存の .venv が不完全です。内容を確認して退避または削除してから再実行してください。自動削除は行いません。' }
} else {
    & $basePython -m venv $envDir
    if ($LASTEXITCODE -ne 0) { throw '仮想環境の作成に失敗しました。配置先の書込権限を確認してください。' }
}
& $appPython -c $pythonProbe
if ($LASTEXITCODE -ne 0) { throw '既存の .venv はWindows用Python 3.14 x64ではありません。内容を確認して退避し、再構築してください。' }
& $appPython -m pip install --disable-pip-version-check --retries 0 -r (Join-Path $PSScriptRoot 'requirements.lock.txt')
if ($LASTEXITCODE -ne 0) { throw '依存関係の準備に失敗しました。ネットワーク・プロキシと直前のエラーを確認してください。' }
& $appPython -c 'import fastapi, uvicorn, win32job, pyte, wcwidth; print("Dependencies OK")'
if ($LASTEXITCODE -ne 0) { throw '依存関係を読込できません。' }
if ($CliPath) {
    $localConfig = Join-Path $PSScriptRoot 'webui.local.json'
    $temporaryConfig = $localConfig + '.tmp'
    @{cli_path=(Get-Item -LiteralPath $CliPath).FullName} | ConvertTo-Json | Set-Content -LiteralPath $temporaryConfig -Encoding utf8NoBOM
    Move-Item -LiteralPath $temporaryConfig -Destination $localConfig -Force
}
Push-Location -LiteralPath $PSScriptRoot
try {
    & $appPython -c 'from agweb.config import Config,APP_ROOT; from agweb.onboarding import preflight; c=Config(APP_ROOT,"setup",0); issues=preflight(c); print("\n".join(issues) if issues else "Preflight OK"); raise SystemExit(1 if any("Hook" in i for i in issues) else 0)'
    if ($LASTEXITCODE -ne 0) { throw 'Hookまたはデータ保存先の事前確認に失敗しました。表示された配置条件を確認してください。' }
} finally { Pop-Location }
Write-Output 'セットアップ完了。Start-WebUI.bat を開き、初回設定で構成確認と利用への同意を行ってください。CLIの診断・推論は実行していません。'
