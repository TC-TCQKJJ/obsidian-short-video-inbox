param(
    [string]$VendorRoot = (Join-Path $PSScriptRoot "vendor")
)

$ErrorActionPreference = "Stop"

$sunnyPath = Join-Path $VendorRoot "github.com\qtgolang\SunnyNet\SunnyNet\SunnyNet.go"
$publicPath = Join-Path $VendorRoot "github.com\qtgolang\SunnyNet\src\public\constobj.go"
if (-not (Test-Path -LiteralPath $sunnyPath) -or -not (Test-Path -LiteralPath $publicPath)) {
    throw "SunnyNet vendored source was not found"
}

$sunnySource = [IO.File]::ReadAllText($sunnyPath)
$managerPattern = '(?s)\r?\nvar defaultManager = func\(\) int \{.*?\}\(\)\r?\n'
$managerMatches = [regex]::Matches($sunnySource, $managerPattern)
if ($managerMatches.Count -ne 1) {
    throw "Expected one SunnyNet default certificate manager, found $($managerMatches.Count)"
}
$sunnySource = [regex]::Replace($sunnySource, $managerPattern, "`n", 1)

$setCertPattern = '(?m)^\s*s\.SetCert\(defaultManager\)\r?\n'
$setCertMatches = [regex]::Matches($sunnySource, $setCertPattern)
if ($setCertMatches.Count -ne 1) {
    throw "Expected one SunnyNet default SetCert call, found $($setCertMatches.Count)"
}
$sunnySource = [regex]::Replace($sunnySource, $setCertPattern, "", 1)

$publicSource = [IO.File]::ReadAllText($publicPath)
$keyPattern = '(?s)const \(\r?\n\s*RootCa = `-----BEGIN CERTIFICATE-----.*?-----END RSA PRIVATE KEY-----\r?\n`\r?\n\)'
$keyMatches = [regex]::Matches($publicSource, $keyPattern)
if ($keyMatches.Count -ne 1) {
    throw "Expected one SunnyNet built-in CA/private-key block, found $($keyMatches.Count)"
}
$publicSource = [regex]::Replace($publicSource, $keyPattern, "", 1)

if ($sunnySource.Contains("defaultManager") -or $publicSource.Contains("RootKey =")) {
    throw "SunnyNet shared certificate material was not fully removed"
}

$utf8 = [Text.UTF8Encoding]::new($false)
[IO.File]::WriteAllText($sunnyPath, $sunnySource, $utf8)
[IO.File]::WriteAllText($publicPath, $publicSource, $utf8)
gofmt -w $sunnyPath $publicPath
