# Helpers shared by sign_release.ps1 and test_sign_release.ps1 (dot-sourced; defines functions only).

function Find-SignToolPath {
    <#
    .SYNOPSIS
        The signtool.exe to use: the explicit path, PATH, then the newest x64 signtool in the Windows SDK.
        Throws "signtool not found" when there is none.
    #>
    param([string]$Explicit)
    if ($Explicit) {
        if (-not (Test-Path -LiteralPath $Explicit)) { throw "signtool not found at $Explicit" }
        return (Resolve-Path -LiteralPath $Explicit).Path
    }
    $onPath = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }
    $kits = Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"
    if (Test-Path $kits) {
        # Sorted as versions: 10.0.9xxx is older than 10.0.22621, which a string sort gets wrong.
        $found = Get-ChildItem -Path $kits -Directory |
            Where-Object { $_.Name -match '^\d+(\.\d+){3}$' } |
            Sort-Object { [version]$_.Name } -Descending |
            ForEach-Object { Join-Path $_.FullName "x64\signtool.exe" } |
            Where-Object { Test-Path $_ } |
            Select-Object -First 1
        if ($found) { return $found }
    }
    throw "signtool not found. Install the Windows SDK (signing tools) or pass -SignTool / set SIGNTOOL."
}

function ConvertTo-Thumbprint {
    <#
    .SYNOPSIS
        Only the hex digits, upper case. A thumbprint copied from the Windows certificate dialog carries invisible
        characters (U+200E) and spaces; both are dropped.
    #>
    param([string]$Text)
    return ($Text -replace '[^0-9A-Fa-f]', '').ToUpperInvariant()
}

function Test-CodeSigningCertificate {
    <#
    .SYNOPSIS
        True for a certificate that may sign code: it has a private key, is not expired, and its Enhanced Key Usage
        lists code signing. Reads the extension through .NET, so a certificate without one (an empty list) is simply
        "no" and never an error under Set-StrictMode.
    #>
    param([Security.Cryptography.X509Certificates.X509Certificate2]$Certificate)
    if (-not $Certificate.HasPrivateKey -or $Certificate.NotAfter -le (Get-Date)) { return $false }
    foreach ($extension in $Certificate.Extensions) {
        if ($extension -is [Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension]) {
            foreach ($usage in $extension.EnhancedKeyUsages) {
                if ($usage.Value -eq "1.3.6.1.5.5.7.3.3") { return $true }
            }
        }
    }
    return $false
}
