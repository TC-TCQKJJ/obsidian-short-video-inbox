param(
    [Parameter(Mandatory = $true)]
    [string]$Executable
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $Executable)) {
    throw "Process capture helper was not found: $Executable"
}

$process = Start-Process `
    -FilePath $Executable `
    -Verb RunAs `
    -PassThru `
    -WindowStyle Hidden

Write-Output $process.Id
