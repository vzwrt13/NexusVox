<#
.SYNOPSIS
    Test packaging/sign_release.ps1 with a throw-away self-signed certificate.

.DESCRIPTION
    Creates a self-signed code-signing certificate in memory, exports it to a temporary .pfx, runs sign_release.ps1
    against stand-in executables in a temporary folder and checks that
      - the app exe carries the signature,
      - the installer build runs after the app exe is signed (the order the release depends on),
      - the installer carries the signature, and a stale installer from an earlier build is not kept.
    It never touches the real certificate, SimplySign or the dist\ folder, and it needs no network (no timestamp).
    The certificate is not trusted, so `signtool verify /pa` is expected to fail on it: the script's own verify step
    is therefore skipped here (-SkipVerify in sign_release.ps1) and the embedded certificate is read instead.

    Run:  powershell -File packaging\test_sign_release.ps1
    Exit code 0 = passed. Needs signtool.exe (Windows SDK).
#>
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$script = Join-Path $PSScriptRoot "sign_release.ps1"
$work = Join-Path ([IO.Path]::GetTempPath()) ("nexusvox-sign-test-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $work | Out-Null
$failures = New-Object System.Collections.Generic.List[string]

# The subject of the certificate embedded in the file ("" if none). Get-AuthenticodeSignature is not used: for a
# system file it reports the catalog signature, not the one signtool just wrote.
function Get-EmbeddedSigner([string]$Path) {
    try { return [Security.Cryptography.X509Certificates.X509Certificate]::CreateFromSignedFile($Path).Subject } catch { return "" }
}

function Check([bool]$Condition, [string]$Message) {
    if ($Condition) { Write-Host "ok   $Message" } else { Write-Host "FAIL $Message"; $failures.Add($Message) }
}

try {
    # A throw-away certificate: built in memory with .NET (the user's certificate store is never touched), written to
    # a .pfx in the temporary folder, never trusted, never used for a release.
    $password = [guid]::NewGuid().ToString("N")
    $key = [Security.Cryptography.RSA]::Create(2048)
    $request = [Security.Cryptography.X509Certificates.CertificateRequest]::new(
        "CN=NexusVox signing self-test", $key, [Security.Cryptography.HashAlgorithmName]::SHA256, [Security.Cryptography.RSASignaturePadding]::Pkcs1)
    $usage = New-Object Security.Cryptography.OidCollection
    [void]$usage.Add((New-Object Security.Cryptography.Oid "1.3.6.1.5.5.7.3.3"))  # code signing
    $request.CertificateExtensions.Add((New-Object Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension $usage, $false))
    $request.CertificateExtensions.Add((New-Object Security.Cryptography.X509Certificates.X509KeyUsageExtension ([Security.Cryptography.X509Certificates.X509KeyUsageFlags]::DigitalSignature), $true))
    $cert = $request.CreateSelfSigned([DateTimeOffset]::UtcNow.AddHours(-1), [DateTimeOffset]::UtcNow.AddDays(1))
    $pfx = Join-Path $work "test.pfx"
    [IO.File]::WriteAllBytes($pfx, $cert.Export([Security.Cryptography.X509Certificates.X509ContentType]::Pfx, $password))

    # Stand-in executables: any PE file will do. A signed system exe is copied and its signature is replaced.
    $stub = Join-Path $env:windir "System32\whoami.exe"
    $appExe = Join-Path $work "app\NexusVox.exe"
    $installer = Join-Path $work "installer\NexusVox-Setup.exe"
    New-Item -ItemType Directory -Path (Split-Path $appExe), (Split-Path $installer) | Out-Null
    Copy-Item $stub $appExe
    Copy-Item (Join-Path $env:windir "System32\cmd.exe") $installer  # a stale installer from an earlier build
    $stale = (Get-FileHash $installer).Hash

    # The "installer build": records whether the app exe was already signed when it ran, then writes the installer.
    $log = Join-Path $work "build.log"
    $build = {
        Set-Content -Path $log -Value (Get-EmbeddedSigner $appExe)
        Copy-Item $stub $installer
    }.GetNewClosure()

    & $script -PfxPath $pfx -PfxPassword $password -NoTimestamp -SkipVerify -AppExe $appExe -InstallerExe $installer -BuildInstaller $build
    Check $true "sign_release.ps1 ran to the end"

    Check ((Get-EmbeddedSigner $appExe) -eq "CN=NexusVox signing self-test") "the app exe is signed with the test certificate"
    Check ((Get-EmbeddedSigner $installer) -eq "CN=NexusVox signing self-test") "the installer is signed with the test certificate"
    Check ((Get-Content $log) -eq "CN=NexusVox signing self-test") "the installer was built after the app exe was signed"
    Check ((Get-FileHash $installer).Hash -ne $stale) "the stale installer was replaced"

    # -NoTimestamp is for tests only: without a .pfx the script must refuse it before it signs anything.
    $refused = $false
    try { & $script -NoTimestamp -AppExe $appExe -InstallerExe $installer -BuildInstaller $build } catch { $refused = $true }
    Check $refused "-NoTimestamp without -PfxPath is refused"

    # A missing app exe stops the script before the installer is built.
    $missing = $false
    try { & $script -PfxPath $pfx -PfxPassword $password -NoTimestamp -SkipVerify -AppExe (Join-Path $work "nope.exe") -InstallerExe $installer -BuildInstaller $build } catch { $missing = $true }
    Check $missing "a missing app exe stops the script"
} finally {
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}

if ($failures.Count -gt 0) { Write-Host "`n$($failures.Count) check(s) failed."; exit 1 }
Write-Host "`nAll checks passed."
