param([string]$OutputDirectory)
$ErrorActionPreference = 'Stop'
$releaseRoot = Split-Path -Parent $PSScriptRoot
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $releaseRoot 'releases' }
Push-Location -LiteralPath $releaseRoot
try {
    $gitRoot = & git rev-parse --show-toplevel
    if ($LASTEXITCODE -ne 0 -or [IO.Path]::GetFullPath($gitRoot) -ne [IO.Path]::GetFullPath($releaseRoot)) { throw 'プロジェクトルートのGitリポジトリが必要です。' }
    $changes = & git status --porcelain --untracked-files=normal
    if ($LASTEXITCODE -ne 0 -or $changes) { throw '配布前に今回の変更をコミットしてください。未コミットファイルはZIPに含めません。' }
    $releaseVersion = (& git show HEAD:VERSION).Trim()
    if ($LASTEXITCODE -ne 0 -or $releaseVersion -notmatch '^\d+\.\d+\.\d+$') { throw 'コミット済みVERSIONが無効です。' }
    $manifest = & git show HEAD:scripts/release-files.txt
    if ($LASTEXITCODE -ne 0) { throw '配布対象一覧を読めません。' }
    $manifest = @($manifest | Where-Object { $_ -and -not $_.StartsWith('#') })
    if ($manifest | Where-Object { $_ -match '(^/|\.\.|:|\\)' }) { throw '配布対象は安全なリポジトリ相対パスに限ります。' }
    [System.IO.Directory]::CreateDirectory([IO.Path]::GetFullPath($OutputDirectory)) | Out-Null
    $zipPath = Join-Path ([IO.Path]::GetFullPath($OutputDirectory)) ('Antigravity-Local-WebUI-v' + $releaseVersion + '.zip')
    $prefix = 'Antigravity-Local-WebUI-v' + $releaseVersion + '/'
    & git archive --format=zip ('--prefix=' + $prefix) ('--output=' + $zipPath) HEAD -- @manifest
    if ($LASTEXITCODE -ne 0) { throw '配布ZIPの生成に失敗しました。' }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead($zipPath)
    try {
        $members = @($archive.Entries | Where-Object { -not $_.FullName.EndsWith('/') } | ForEach-Object { $_.FullName.Substring($prefix.Length) })
        if ($members.Count -lt 50 -or ($members | Where-Object { $_ -match '(^|/)(\.git|\.venv|data|validation-results|exports|releases|__pycache__)(/|$)|(^|/)webui\.local\.json$|\.(log|sqlite3|pyc)$' })) { throw '配布ZIPに禁止ファイルが含まれるか、必須構造が不足しています。' }
        foreach ($required in @('README.md','LICENSE','VERSION','Setup.bat','launch.py','static/setup.js','tests/mock_cli.py','tests/mock_usage.py','tests/fixtures/quota-argon-1.3.0.txt','static/vendor/marked-LICENSE.txt','static/vendor/dompurify-LICENSE.txt')) {
            if ($required -notin $members) { throw ('必須ファイルがありません: ' + $required) }
        }
        $reader = [IO.StreamReader]::new($archive.GetEntry($prefix + 'static/vendor/manifest.json').Open())
        try { $vendors = $reader.ReadToEnd() | ConvertFrom-Json -AsHashtable } finally { $reader.Dispose() }
        foreach ($component in @(@('marked','marked.js'),@('dompurify','purify.js'))) {
            $stream = $archive.GetEntry($prefix + 'static/vendor/' + $component[1]).Open()
            try { $actual = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($stream)).ToLowerInvariant() } finally { $stream.Dispose() }
            if ($actual -ne $vendors[$component[0]].sha256) { throw ('同梱資産のハッシュが一致しません: ' + $component[1]) }
        }
    } finally { $archive.Dispose() }
    $hash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
    ($hash + '  ' + [IO.Path]::GetFileName($zipPath)) | Set-Content -LiteralPath ($zipPath + '.sha256') -Encoding utf8NoBOM
    $commit = & git rev-parse HEAD
    if ($LASTEXITCODE -ne 0) { throw 'コミットIDを取得できません。' }
    @{version=$releaseVersion;commit=$commit;sha256=$hash;files=$members} | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath ($zipPath + '.manifest.json') -Encoding utf8NoBOM
    Get-Item -LiteralPath $zipPath | Select-Object FullName,Length
    Write-Output ('SHA-256: ' + $hash)
} finally { Pop-Location }
