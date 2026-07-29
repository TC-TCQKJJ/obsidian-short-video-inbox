param(
    [string]$VendorRoot = (Join-Path $PSScriptRoot "vendor")
)

$ErrorActionPreference = "Stop"

$sunnyPath = Join-Path $VendorRoot "github.com\qtgolang\SunnyNet\SunnyNet\SunnyNet.go"
$httpPath = Join-Path $VendorRoot "github.com\qtgolang\SunnyNet\SunnyNet\http.go"
$publicPath = Join-Path $VendorRoot "github.com\qtgolang\SunnyNet\src\public\constobj.go"
$headerPath = Join-Path $VendorRoot "github.com\qtgolang\SunnyNet\src\iphlpapi\c_iphlpapi_tcp.h"
$cSourcePath = Join-Path $VendorRoot "github.com\qtgolang\SunnyNet\src\iphlpapi\c_iphlpapi_tcp.c"
$requiredPaths = @($sunnyPath, $httpPath, $publicPath, $headerPath, $cSourcePath)
if ($requiredPaths.Where({ -not (Test-Path -LiteralPath $_) }).Count -ne 0) {
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

$driverModeMarker = "`tif DevMode == CrossCompiled.DrvNF {"
if ([regex]::Matches($sunnySource, [regex]::Escape($driverModeMarker)).Count -ne 1) {
    throw "Expected one SunnyNet process-driver mode switch"
}
$driverCacheInitialization = @"
	if s.cache == nil {
		s.cache = newCache(s)
	}
	s.cache.StartJanitor()
"@
$sunnySource = $sunnySource.Replace(
    $driverModeMarker,
    $driverCacheInitialization + "`n" + $driverModeMarker
)

$httpSource = [IO.File]::ReadAllText($httpPath)
$loopbackTargetMarker = "`t`tres.Host = normalizeHostPort(req.Host)"
if ([regex]::Matches($httpSource, [regex]::Escape($loopbackTargetMarker)).Count -ne 1) {
    throw "Expected one SunnyNet HTTP target fallback marker"
}
$loopbackTargetRepair = @"
		// Restore an HTTPS target parsed behind a loopback HTTP proxy.
		targetIP := net.ParseIP(r.Target.Host)
		if res.Host != "" && targetIP != nil && targetIP.IsLoopback() && r.Target.Port != 443 {
			requestTarget := TargetInfo{}
			requestTarget.Parse(res.Host, 443)
			if requestTarget.IsDomain() {
				r.Target.Parse(res.Host, 443)
				res.URL.Host = res.Host
			}
		}

"@
$httpSource = $httpSource.Replace(
    $loopbackTargetMarker,
    $loopbackTargetMarker + "`n" + $loopbackTargetRepair
)

$headerSource = [IO.File]::ReadAllText($headerPath)
foreach ($pattern in @(
    '(?s)typedef struct _MIB_TCPROW2 \{.*?\} MIB_TCPROW2, \*PMIB_TCPROW2;\r?\n',
    '(?s)typedef struct _MIB_TCPTABLE2 \{.*?\} MIB_TCPTABLE2, \*PMIB_TCPTABLE2;\r?\n'
)) {
    $matches = [regex]::Matches($headerSource, $pattern)
    if ($matches.Count -ne 1) {
        throw "Expected one SunnyNet duplicate MinGW structure, found $($matches.Count)"
    }
    $headerSource = [regex]::Replace($headerSource, $pattern, "", 1)
}

$oldTypedef = "typedef DWORD (WINAPI * GetTcpTable2)(PMIB_TCPTABLE2 TcpTable, PULONG SizePointer, BOOL Order);"
$newTypedef = "typedef DWORD (WINAPI * GetTcpTable2Func)(PMIB_TCPTABLE2 TcpTable, PULONG SizePointer, BOOL Order);"
if ([regex]::Matches($headerSource, [regex]::Escape($oldTypedef)).Count -ne 1) {
    throw "Expected one SunnyNet GetTcpTable2 function-pointer typedef"
}
$headerSource = $headerSource.Replace($oldTypedef, $newTypedef)

$cSource = [IO.File]::ReadAllText($cSourcePath)
foreach ($replacement in @(
    @("GetTcpTable2 pGetTcpTable2;", "GetTcpTable2Func pGetTcpTable2;"),
    @("(GetTcpTable2) GetProcAddress", "(GetTcpTable2Func) GetProcAddress")
)) {
    if ([regex]::Matches($cSource, [regex]::Escape($replacement[0])).Count -ne 1) {
        throw "Expected one SunnyNet C source occurrence of $($replacement[0])"
    }
    $cSource = $cSource.Replace($replacement[0], $replacement[1])
}

$utf8 = [Text.UTF8Encoding]::new($false)
[IO.File]::WriteAllText($sunnyPath, $sunnySource, $utf8)
[IO.File]::WriteAllText($httpPath, $httpSource, $utf8)
[IO.File]::WriteAllText($publicPath, $publicSource, $utf8)
[IO.File]::WriteAllText($headerPath, $headerSource, $utf8)
[IO.File]::WriteAllText($cSourcePath, $cSource, $utf8)
gofmt -w $sunnyPath $httpPath $publicPath
