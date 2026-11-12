<#
.SYNOPSIS
    Test packaging/sign_release.ps1 and packaging/sign_helpers.ps1 with throw-away self-signed certificates.

.DESCRIPTION
    Creates self-signed code-signing certificates in memory, exports one to a temporary .pfx, runs sign_release.ps1
    against stand-in executables in a temporary folder and checks that
      - the app exe carries the signature,
      - the installer build runs after the app exe is signed (the order the release depends on),
      - the installer carries the signature, and a stale installer from an earlier build is not kept,
      - the guards refuse what they must (with the intended message, not just any error),
      - the helpers pick certificates and thumbprints correctly, including a certificate without a key usage.
    It never touches the real certificate, SimplySign, the certificate store or the dist\ folder, and it needs no
    network (no timestamp). The certificate is not trusted, so `signtool verify /pa` is expected to fail on it: the
    script's own verify step is skipped here (-SkipVerify in sign_release.ps1) and the embedded certificate is read
    instead.

    Run:  pwsh -File packaging\test_sign_release.ps1   (or powershell -File ...)
    Exit code 0 = passed, 1 = a check failed, 77 = signtool.exe (Windows SDK) not installed (the test is skipped).
#>
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "sign_helpers.ps1")
try { $null = Find-SignToolPath "" } catch { Write-Host "SKIP: $($_.Exception.Message)"; exit 77 }

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

# The message of the error a script block throws ("" if it does not throw).
function Get-ErrorMessage([scriptblock]$Block) {
    try { & $Block } catch { return $_.Exception.Message }
    return ""
}

# A self-signed certificate with a private key, built in memory with .NET.
function New-TestCertificate([string]$Subject, [bool]$CodeSigning, [int]$ValidDays = 1) {
    $key = [Security.Cryptography.RSA]::Create(2048)
    $request = [Security.Cryptography.X509Certificates.CertificateRequest]::new(
        $Subject, $key, [Security.Cryptography.HashAlgorithmName]::SHA256, [Security.Cryptography.RSASignaturePadding]::Pkcs1)
    if ($CodeSigning) {
        $usage = New-Object Security.Cryptography.OidCollection
        [void]$usage.Add((New-Object Security.Cryptography.Oid "1.3.6.1.5.5.7.3.3"))  # code signing
        $request.CertificateExtensions.Add((New-Object Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension $usage, $false))
    }
    return $request.CreateSelfSigned([DateTimeOffset]::UtcNow.AddDays(-2), [DateTimeOffset]::UtcNow.AddDays($ValidDays))
}

try {
    # --- The helpers.
    $withUsage = New-TestCertificate "CN=with usage" $true
    $noUsage = New-TestCertificate "CN=no usage" $false
    $expired = New-TestCertificate "CN=expired" $true -1
    $publicOnly = [Security.Cryptography.X509Certificates.X509Certificate2]::new($withUsage.Export([Security.Cryptography.X509Certificates.X509ContentType]::Cert))
    Check (Test-CodeSigningCertificate $withUsage) "a certificate with the code-signing usage and a key is accepted"
    Check (-not (Test-CodeSigningCertificate $noUsage)) "a certificate without any key usage is rejected, not an error"
    Check (-not (Test-CodeSigningCertificate $expired)) "an expired certificate is rejected"
    Check (-not (Test-CodeSigningCertificate $publicOnly)) "a certificate without a private key is rejected"
    # The Details tab of the Windows certificate dialog shows the thumbprint in pairs, with U+200E in front.
    $dialog = [string][char]0x200E + (($withUsage.Thumbprint -replace '(..)', '$1 ').Trim().ToLowerInvariant())
    Check ((ConvertTo-Thumbprint $dialog) -eq $withUsage.Thumbprint) "a thumbprint copied from the Windows dialog loses spaces and invisible characters"
    Check ((Get-ErrorMessage { Find-SignToolPath (Join-Path $work "missing\signtool.exe") }) -match "signtool not found") "a missing signtool path is reported"

    # --- The whole flow with a .pfx.
    $password = [guid]::NewGuid().ToString("N")
    $signer = New-TestCertificate "CN=NexusVox signing self-test" $true
    $pfx = Join-Path $work "test.pfx"
    [IO.File]::WriteAllBytes($pfx, $signer.Export([Security.Cryptography.X509Certificates.X509ContentType]::Pfx, $password))

    # Stand-in executables: any PE file will do. A signed system exe is copied and its signature is replaced.
    $stub = Join-Path $env:windir "System32\whoami.exe"
    $appExe = Join-Path $work "app\NexusVox.exe"
    $installer = Join-Path $work "installer\NexusVox-Setup.exe"
    New-Item -ItemType Directory -Path (Split-Path $appExe), (Split-Path $installer) | Out-Null
    Copy-Item $stub $appExe
    Copy-Item (Join-Path $env:windir "System32\cmd.exe") $installer  # a stale installer from an earlier build
    $stale = (Get-FileHash $installer).Hash

    # The "installer build": records who had signed the app exe when it ran, then writes the installer.
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

    # --- The guards: each must refuse for its own reason.
    $message = Get-ErrorMessage { & $script -NoTimestamp -AppExe $appExe -InstallerExe $installer -BuildInstaller $build }
    Check ($message -match "are for the self-signed test only") "-NoTimestamp without -PfxPath is refused by its guard ($message)"
    $message = Get-ErrorMessage { & $script -SkipVerify -AppExe $appExe -InstallerExe $installer -BuildInstaller $build }
    Check ($message -match "are for the self-signed test only") "-SkipVerify without -PfxPath is refused by its guard ($message)"
    $message = Get-ErrorMessage { & $script -PfxPath $pfx -PfxPassword $password -NoTimestamp -SkipVerify -AppExe (Join-Path $work "nope.exe") -InstallerExe $installer -BuildInstaller $build }
    Check ($message -match "nope\.exe not found") "a missing app exe stops the script ($message)"
} finally {
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}

if ($failures.Count -gt 0) { Write-Host "`n$($failures.Count) check(s) failed."; exit 1 }
Write-Host "`nAll checks passed."
