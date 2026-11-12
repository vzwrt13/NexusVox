<#
.SYNOPSIS
    Sign the bundled NexusVox.exe, build the Inno Setup installer around it, then sign the installer.

.DESCRIPTION
    The release is signed on the maintainer's PC, never in CI: the Certum cloud certificate (SimplySign) needs a
    one-time code from a phone for every login. Order matters. The installer contains NexusVox.exe, so the exe is
    signed first, the installer is built from that signed exe, and the installer is signed last. Both signatures
    carry an RFC 3161 timestamp, so they stay valid after the certificate expires.

    Prerequisites (docs/SIGNING.md has the full release steps):
      1. `python packaging/build.py` has produced dist\NexusVox\NexusVox.exe (the bootloader built from source).
      2. SimplySign Desktop is running and logged in, so the certificate is visible to Windows as a virtual
         smart card.
      3. signtool.exe (Windows SDK) and Inno Setup 6 are installed.

    The script never reads, prints or stores certificate secrets. The certificate is picked by its public
    thumbprint; the private key stays in the SimplySign virtual smart card.

.PARAMETER Thumbprint
    SHA-1 thumbprint of the signing certificate (public data). Defaults to $env:NEXUSVOX_SIGN_THUMBPRINT. When
    both are empty and exactly one code-signing certificate with a private key sits in the CurrentUser store,
    that one is used.

.PARAMETER TimestampUrl
    RFC 3161 timestamp server. Defaults to Certum's (http://time.certum.pl).

.PARAMETER SignTool
    Path to signtool.exe. Default: $env:SIGNTOOL, PATH, then the newest x64 signtool in the Windows SDK.

.PARAMETER Python
    The Python used to run packaging/build_installer.py. Default: python.

.PARAMETER AppExe
    The bundled exe to sign. Default: dist\NexusVox\NexusVox.exe.

.PARAMETER InstallerExe
    The installer the build produces. Default: dist\installer\NexusVox-Setup.exe.

.PARAMETER BuildInstaller
    Script block that builds the installer. Default: runs packaging/build_installer.py. The self-test replaces it.

.PARAMETER PfxPath
    TEST ONLY. Sign with this .pfx file instead of a certificate in the store. Used by
    packaging/test_sign_release.ps1 with a throw-away self-signed certificate. Never point it at a real
    certificate: Certum does not hand out the private key.

.PARAMETER PfxPassword
    TEST ONLY. Password of the test .pfx.

.PARAMETER NoTimestamp
    TEST ONLY, and only together with -PfxPath: skip the timestamp. A release is never signed without one.

.PARAMETER SkipVerify
    TEST ONLY, and only together with -PfxPath: skip `signtool verify /pa`, which fails on an untrusted
    self-signed certificate by design. A release is always verified.

.EXAMPLE
    powershell -File packaging\sign_release.ps1 -Thumbprint 0123456789ABCDEF0123456789ABCDEF01234567
#>
[CmdletBinding()]
param(
    [string]$Thumbprint = $env:NEXUSVOX_SIGN_THUMBPRINT,
    [string]$TimestampUrl = "http://time.certum.pl",
    [string]$SignTool = $env:SIGNTOOL,
    [string]$Python = "python",
    [string]$AppExe,
    [string]$InstallerExe,
    [scriptblock]$BuildInstaller,
    [string]$PfxPath,
    [string]$PfxPassword,
    [switch]$NoTimestamp,
    [switch]$SkipVerify
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$root = Split-Path -Parent $PSScriptRoot
if (-not $AppExe) { $AppExe = Join-Path $root "dist\NexusVox\NexusVox.exe" }
if (-not $InstallerExe) { $InstallerExe = Join-Path $root "dist\installer\NexusVox-Setup.exe" }
if (-not $BuildInstaller) {
    $BuildInstaller = {
        & $Python (Join-Path $root "packaging\build_installer.py")
        if ($LASTEXITCODE -ne 0) { throw "build_installer.py failed (exit code $LASTEXITCODE)" }
    }
}

function Find-SignTool {
    if ($SignTool) {
        if (-not (Test-Path -LiteralPath $SignTool)) { throw "signtool not found at $SignTool" }
        return (Resolve-Path -LiteralPath $SignTool).Path
    }
    $onPath = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }
    $kits = Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"
    if (Test-Path $kits) {
        $found = Get-ChildItem -Path $kits -Directory |
            Where-Object { $_.Name -match '^\d+(\.\d+){3}$' } |
            Sort-Object { [version]$_.Name } -Descending |
            ForEach-Object { Join-Path $_.FullName "x64\signtool.exe" } |
            Where-Object { Test-Path $_ } |
            Select-Object -First 1
        if ($found) { return $found }
    }
    throw "signtool.exe not found. Install the Windows SDK (signing tools) or pass -SignTool / set SIGNTOOL."
}

function Resolve-Thumbprint {
    if ($Thumbprint) { return ($Thumbprint -replace '\s', '').ToUpperInvariant() }
    # Only public certificate data is read here: the thumbprint and the "has a private key" flag.
    $candidates = @(Get-ChildItem Cert:\CurrentUser\My |
        Where-Object { $_.HasPrivateKey -and $_.NotAfter -gt (Get-Date) -and ($_.EnhancedKeyUsageList.ObjectId -contains "1.3.6.1.5.5.7.3.3") })
    if ($candidates.Count -eq 1) { return $candidates[0].Thumbprint }
    if ($candidates.Count -eq 0) {
        throw "No code-signing certificate with a private key in Cert:\CurrentUser\My. Is SimplySign Desktop logged in?"
    }
    throw "$($candidates.Count) code-signing certificates found. Pass -Thumbprint (or set NEXUSVOX_SIGN_THUMBPRINT)."
}

function Invoke-SignTool([string[]]$Arguments) {
    # The output is shown as it is; none of it contains secrets (the PIN is never passed on a command line).
    & $script:signtoolPath @Arguments
    if ($LASTEXITCODE -ne 0) { throw "signtool failed (exit code $LASTEXITCODE): signtool $($Arguments[0])" }
}

function Sign-File([string]$Path) {
    Write-Host "Signing $Path"
    $arguments = @("sign", "/fd", "SHA256", "/d", "NexusVox", "/du", "https://nightshift-ai.de")
    if ($PfxPath) {
        $arguments += @("/f", $PfxPath)
        if ($PfxPassword) { $arguments += @("/p", $PfxPassword) }
    } else {
        $arguments += @("/sha1", $script:certThumbprint)
    }
    if (-not $NoTimestamp) { $arguments += @("/tr", $TimestampUrl, "/td", "SHA256") }
    $arguments += $Path
    Invoke-SignTool $arguments
    if (-not $SkipVerify) { Invoke-SignTool @("verify", "/pa", "/v", $Path) }
}

# --- Checks before anything is signed or built.
if (($NoTimestamp -or $SkipVerify) -and -not $PfxPath) {
    throw "-NoTimestamp and -SkipVerify are for the self-signed test only: a release is always timestamped and verified."
}
if ($PfxPath -and -not (Test-Path -LiteralPath $PfxPath)) { throw "Test certificate not found: $PfxPath" }
if (-not (Test-Path -LiteralPath $AppExe)) { throw "$AppExe not found. Run 'python packaging/build.py' first." }
$script:signtoolPath = Find-SignTool
Write-Host "signtool: $script:signtoolPath"
if (-not $PfxPath) {
    $script:certThumbprint = Resolve-Thumbprint
    $cert = Get-Item "Cert:\CurrentUser\My\$script:certThumbprint" -ErrorAction SilentlyContinue
    if (-not $cert) { throw "Certificate $script:certThumbprint not found in Cert:\CurrentUser\My. Is SimplySign Desktop logged in?" }
    Write-Host "Certificate: $($cert.Subject) (valid until $($cert.NotAfter.ToString('yyyy-MM-dd')))"
}

# --- 1. The bundled exe, 2. the installer built from it, 3. the installer.
Sign-File $AppExe
if (Test-Path -LiteralPath $InstallerExe) { Remove-Item -LiteralPath $InstallerExe -Force }  # never sign a stale build
Write-Host "Building the installer"
& $BuildInstaller
if (-not (Test-Path -LiteralPath $InstallerExe)) { throw "The installer build did not produce $InstallerExe" }
Sign-File $InstallerExe

$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $InstallerExe).Hash
Write-Host ""
Write-Host "Done. Signed installer: $InstallerExe"
Write-Host "SHA256: $hash"
