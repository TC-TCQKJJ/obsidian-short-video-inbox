param(
    [Parameter(Mandatory = $true)]
    [string]$Executable
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $Executable)) {
    throw "Process capture executable was not found: $Executable"
}

$binaryText = [Text.Encoding]::ASCII.GetString(
    [IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $Executable))
)
$upstreamPrivateKeyMarker = "MIIEpAIBAAKCAQEAzU+hPfoE"
if ($binaryText.Contains($upstreamPrivateKeyMarker)) {
    throw "Build contains SunnyNet's public default private key"
}

Write-Output "Verified: SunnyNet's public default private key is absent."
