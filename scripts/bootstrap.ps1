param(
    [string]$CoreDir = (Split-Path $PSScriptRoot -Parent),
    [ValidateSet('auto', 'cpu', 'cuda')][string]$Backend = 'auto',
    [switch]$Library,
    [switch]$SetupOnly,
    [switch]$CharacterAssets
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ProgressPreference = 'SilentlyContinue'

function Invoke-Checked {
    param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed (exit $LASTEXITCODE): $Exe" }
}

function Get-VerifiedFile {
    param([string]$Url, [string]$Target, [string]$Sha256)
    if ((Test-Path -LiteralPath $Target) -and (Get-FileHash -LiteralPath $Target).Hash -ieq $Sha256) { return }
    New-Item -ItemType Directory -Force -Path (Split-Path $Target -Parent) | Out-Null
    $partial = "$Target.part"
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        try {
            Write-Host "Downloading $([IO.Path]::GetFileName($Target))..."
            Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $partial -TimeoutSec 120
            if ((Get-FileHash -LiteralPath $partial).Hash -ine $Sha256) { throw 'SHA256 mismatch' }
            Move-Item -LiteralPath $partial -Destination $Target -Force
            return
        } catch {
            if ($attempt -eq 3) { throw }
            Start-Sleep -Seconds $attempt
        }
    }
}

function Ensure-Python312 {
    param([string]$CacheDir)
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'EdgeAgent\Python312\python.exe')
    )
    $command = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($command) { $candidates += $command.Source }
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        $discovered = & $launcher.Source -3.12 -c 'import sys; print(sys.executable)' 2>$null
        if ($LASTEXITCODE -eq 0) { $candidates += [string]$discovered }
    }
    foreach ($candidate in $candidates) {
        if (-not (Test-Path -LiteralPath $candidate)) { continue }
        try {
            & $candidate -c 'import sys,struct,tkinter,venv; raise SystemExit(sys.version_info[:2] != (3,12) or struct.calcsize(chr(80)) != 8)' *> $null
            if ($LASTEXITCODE -eq 0) { return $candidate }
        } catch { continue }
    }
    $installer = Join-Path $CacheDir 'python-3.12.10-amd64.exe'
    Get-VerifiedFile 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe' $installer '67b5635e80ea51072b87941312d00ec8927c4db9ba18938f7ad2d27b328b95fb'
    $destination = Join-Path $env:LOCALAPPDATA 'EdgeAgent\Python312'
    $process = Start-Process -FilePath $installer -ArgumentList @('/quiet', 'InstallAllUsers=0', "TargetDir=`"$destination`"", 'Include_launcher=0', 'Include_test=0', 'Include_pip=1', 'Include_tcltk=1', 'PrependPath=0') -Wait -PassThru -WindowStyle Hidden
    if ($process.ExitCode -notin @(0, 3010)) { throw "Python installation failed: $($process.ExitCode)" }
    $python = Join-Path $destination 'python.exe'
    Invoke-Checked $python @('-c', 'import sys,tkinter,venv; assert sys.version_info[:2] == (3,12)')
    return $python
}

function Ensure-GitHub {
    param([string]$CacheDir)
    $command = Get-Command gh.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $directory = Join-Path $env:LOCALAPPDATA 'EdgeAgent\GitHubCLI\2.102.0'
    $executable = Join-Path $directory 'bin\gh.exe'
    if (-not (Test-Path -LiteralPath $executable)) {
        $archive = Join-Path $CacheDir 'gh_2.102.0_windows_amd64.zip'
        Get-VerifiedFile 'https://github.com/cli/cli/releases/download/v2.102.0/gh_2.102.0_windows_amd64.zip' $archive 'ae64e556ecc240b200f7eba60d550e4bb60d78e860e69dd88c449405b86067f4'
        Expand-Archive -LiteralPath $archive -DestinationPath $directory -Force
    }
    if (-not (Test-Path -LiteralPath $executable)) { throw 'GitHub CLI archive did not contain gh.exe' }
    $env:PATH = "$(Split-Path $executable -Parent);$env:PATH"
    return $executable
}

function Ensure-GitHubLogin {
    param([string]$Gh)
    & $Gh auth status *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'Sign in once to download the private character adapter. No token is stored by this installer.'
        Invoke-Checked $Gh @('auth', 'login', '--hostname', 'github.com', '--git-protocol', 'https', '--web')
    }
}

function Ensure-Git {
    param([string]$Root, [string]$CacheDir)
    $existing = Get-Command git.exe -ErrorAction SilentlyContinue
    if ($existing) {
        Invoke-Checked $existing.Source @('--version')
        return
    }
    $directory = Join-Path $Root '.runtime\git'
    $executable = Join-Path $directory 'cmd\git.exe'
    if (-not (Test-Path -LiteralPath $executable)) {
        $archive = Join-Path $CacheDir 'MinGit-2.56.0-64-bit.zip'
        Get-VerifiedFile 'https://github.com/git-for-windows/git/releases/download/v2.56.0.windows.1/MinGit-2.56.0-64-bit.zip' $archive '064b440ff870ed5198527e8f3a92cdf5bd2fd0fedf5e718af95e3fdaddeff718'
        Expand-Archive -LiteralPath $archive -DestinationPath $directory -Force
    }
    Invoke-Checked $executable @('--version')
    $env:PATH = "$(Split-Path $executable -Parent);$env:PATH"
}

function Install-EdgeAgent {
    param([string]$Root, [string]$SelectedBackend)
    $Root = [IO.Path]::GetFullPath($Root)
    $cache = Join-Path $Root '.runtime\downloads'
    New-Item -ItemType Directory -Force -Path $cache | Out-Null
    $python = Ensure-Python312 $cache
    Ensure-Git $Root $cache
    $venv = Join-Path $Root '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venv)) { Invoke-Checked $python @('-m', 'venv', (Join-Path $Root '.venv')) }
    $projectHash = (Get-FileHash -LiteralPath (Join-Path $Root 'pyproject.toml')).Hash
    $receipt = Join-Path $Root '.runtime\python-dependencies.sha256'
    $dependenciesReady = (Test-Path -LiteralPath $receipt) -and (Get-Content -LiteralPath $receipt -Raw).Trim() -eq $projectHash
    $importsReady = $false
    try { & $venv -c 'import pypdf,edge_agent' *> $null; $importsReady = $LASTEXITCODE -eq 0 } catch { }
    if (-not $dependenciesReady -or -not $importsReady) {
        Invoke-Checked $venv @('-m', 'pip', 'install', '--disable-pip-version-check', '-e', $Root)
        Set-Content -LiteralPath $receipt -Value $projectHash -Encoding ASCII
    }
    if ($CharacterAssets -and -not (Test-Path -LiteralPath (Join-Path $Root 'models\character-e4b-lora-v3.q8_0.gguf'))) {
        $gh = Ensure-GitHub $cache
        Ensure-GitHubLogin $gh
    }
    Push-Location $Root
    try {
        $deployArgs = @('-m', 'edge_agent.deploy', '--backend', $SelectedBackend)
        if ($CharacterAssets) { $deployArgs += '--character' }
        Invoke-Checked $venv $deployArgs
        Invoke-Checked $venv @('-m', 'edge_agent.cli', 'install')
    } finally { Pop-Location }
    $env:PATH = "$(Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links');$env:PATH"
    Write-Host '[OK] EdgeAgent installed. SilentTalk accesses only the localhost lease API.'
}

if ($Library) { return }
if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { throw 'This distribution requires Windows x64.' }
$mutex = New-Object Threading.Mutex($false, 'Local\EdgeAgent.Install')
$held = $false
try {
    try { $held = $mutex.WaitOne([TimeSpan]::FromMinutes(30)) }
    catch [Threading.AbandonedMutexException] { $held = $true }
    if (-not $held) { throw 'Another EdgeAgent installation is still running. Retry after it finishes.' }
    Install-EdgeAgent $CoreDir $Backend
} finally {
    if ($held) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
if (-not $SetupOnly) {
    Invoke-Checked (Join-Path $CoreDir '.venv\Scripts\python.exe') @('-m', 'edge_agent.cli')
}
