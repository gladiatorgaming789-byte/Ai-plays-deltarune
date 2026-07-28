[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$DeltaModRoot = (
        Join-Path $env:LOCALAPPDATA "deltamod\win-unpacked\resources\app"
    ),
    [string]$ReleaseArchivePath,
    [switch]$Restore
)

$ErrorActionPreference = "Stop"

$RequiredVersion = [Version]"1.2.5"
$ReleaseUrl = (
    "https://github.com/y114git/G3MTool/releases/download/1.2.5/" +
    "G3MTool-Windows-x64-1.2.5.zip"
)
$ReleaseArchiveSha256 = (
    "408B09B8D43416C4C05779329887D3A2D53C7C6C2FE8C240CD3BC2B1E41C5AB6"
)
$ReleaseExecutableSha256 = (
    "3D313FABBF0454DB9837196F2A7039DEFFE7013E02AA0ED3AAC1C546EAA242E6"
)

function Get-G3MToolVersion([string]$Path) {
    $value = [Diagnostics.FileVersionInfo]::GetVersionInfo($Path).ProductVersion
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "G3MTool has no readable product version: $Path"
    }
    return [Version]($value.Split("+")[0])
}

function Assert-ExpectedHash(
    [string]$Path,
    [string]$Expected,
    [string]$Description
) {
    $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
    if ($actual -ne $Expected) {
        throw (
            "$Description failed SHA-256 verification. Expected $Expected, " +
            "observed $actual. No installed file was replaced."
        )
    }
}

function Assert-DeltaModStopped {
    $running = @(
        Get-Process -ErrorAction SilentlyContinue |
            Where-Object {
                $_.ProcessName -in @("deltamod", "G3MTool", "G3MTool-win32")
            }
    )
    if ($running.Count -gt 0) {
        throw "Close DeltaMod and G3MTool before updating or restoring the merge tool."
    }
}

$toolsDirectory = Join-Path $DeltaModRoot "tools"
$target = Join-Path $toolsDirectory "G3MTool-win32.exe"
$backup = Join-Path $toolsDirectory "G3MTool-win32.exe.pre-1.2.5.bak"
$newVersionBackup = Join-Path $toolsDirectory "G3MTool-win32.exe.1.2.5.bak"

if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
    throw "DeltaMod's G3MTool was not found: $target"
}

Assert-DeltaModStopped

if ($Restore) {
    if (-not (Test-Path -LiteralPath $backup -PathType Leaf)) {
        throw "No pre-1.2.5 backup was found: $backup"
    }
    $backupVersion = Get-G3MToolVersion $backup
    $backupHash = (Get-FileHash -LiteralPath $backup -Algorithm SHA256).Hash
    if ($PSCmdlet.ShouldProcess($target, "Restore G3MTool $backupVersion")) {
        $restoreCandidate = Join-Path $toolsDirectory ".G3MTool-win32.restore.exe"
        Copy-Item -LiteralPath $backup -Destination $restoreCandidate -Force
        $restoreRollback = $newVersionBackup
        $temporaryRollback = $false
        if (Test-Path -LiteralPath $restoreRollback) {
            $restoreRollback = Join-Path (
                $toolsDirectory
            ) (".G3MTool-win32.restore-rollback-" + [Guid]::NewGuid() + ".bak")
            $temporaryRollback = $true
        }
        try {
            [IO.File]::Replace(
                $restoreCandidate,
                $target,
                $restoreRollback,
                $true
            )
            Assert-ExpectedHash $target $backupHash "Restored executable"
            if ($temporaryRollback) {
                Remove-Item -LiteralPath $restoreRollback -Force
            }
        }
        finally {
            Remove-Item -LiteralPath $restoreCandidate -Force -ErrorAction SilentlyContinue
        }
        Write-Host "Restored G3MTool $backupVersion. The 1.2.5 copy is at:"
        Write-Host "  $newVersionBackup"
    }
    return
}

$installedVersion = Get-G3MToolVersion $target
if ($installedVersion -gt $RequiredVersion) {
    Write-Host "G3MTool $installedVersion is newer than required; no change."
    return
}
if ($installedVersion -eq $RequiredVersion) {
    Assert-ExpectedHash $target $ReleaseExecutableSha256 "Installed G3MTool 1.2.5"
    Write-Host "G3MTool 1.2.5 is already installed and verified."
    return
}

$installAction = "Back up G3MTool $installedVersion and install 1.2.5"
if (-not $PSCmdlet.ShouldProcess($target, $installAction)) {
    return
}

$temporaryRoot = Join-Path (
    [IO.Path]::GetTempPath()
) ("ai-speed-g3mtool-" + [Guid]::NewGuid().ToString("N"))
$archivePath = Join-Path $temporaryRoot "G3MTool-Windows-x64-1.2.5.zip"
$extractPath = Join-Path $temporaryRoot "release"

New-Item -ItemType Directory -Path $temporaryRoot | Out-Null
try {
    if ([string]::IsNullOrWhiteSpace($ReleaseArchivePath)) {
        Write-Host "Downloading official G3MTool 1.2.5 release..."
        Invoke-WebRequest -UseBasicParsing -Uri $ReleaseUrl -OutFile $archivePath
    }
    else {
        $providedArchive = (
            Resolve-Path -LiteralPath $ReleaseArchivePath
        ).Path
        Write-Host "Using the supplied G3MTool 1.2.5 release archive..."
        Copy-Item -LiteralPath $providedArchive -Destination $archivePath
    }
    Assert-ExpectedHash $archivePath $ReleaseArchiveSha256 "Release archive"
    Expand-Archive -LiteralPath $archivePath -DestinationPath $extractPath

    $releaseExecutable = Join-Path $extractPath "G3MTool.exe"
    if (-not (Test-Path -LiteralPath $releaseExecutable -PathType Leaf)) {
        throw "The verified release archive did not contain G3MTool.exe."
    }
    Assert-ExpectedHash (
        $releaseExecutable
    ) $ReleaseExecutableSha256 "Release executable"
    $releaseVersion = Get-G3MToolVersion $releaseExecutable
    if ($releaseVersion -ne $RequiredVersion) {
        throw "Expected G3MTool 1.2.5 but the release reports $releaseVersion."
    }

    $installCandidate = Join-Path $toolsDirectory ".G3MTool-win32.1.2.5.exe"
    Copy-Item -LiteralPath $releaseExecutable -Destination $installCandidate -Force
    $installRollback = $backup
    $temporaryRollback = $false
    if (Test-Path -LiteralPath $installRollback) {
        $installRollback = Join-Path (
            $toolsDirectory
        ) (".G3MTool-win32.install-rollback-" + [Guid]::NewGuid() + ".bak")
        $temporaryRollback = $true
    }
    try {
        Assert-ExpectedHash (
            $installCandidate
        ) $ReleaseExecutableSha256 "Staged executable"
        [IO.File]::Replace(
            $installCandidate,
            $target,
            $installRollback,
            $true
        )
        Assert-ExpectedHash $target $ReleaseExecutableSha256 "Installed executable"
        if ($temporaryRollback) {
            Remove-Item -LiteralPath $installRollback -Force
        }
    }
    finally {
        Remove-Item -LiteralPath $installCandidate -Force -ErrorAction SilentlyContinue
    }
    Write-Host "Installed and verified G3MTool 1.2.5."
    Write-Host "Backup retained at:"
    Write-Host "  $backup"
}
finally {
    Remove-Item -LiteralPath $temporaryRoot -Recurse -Force -ErrorAction SilentlyContinue
}
