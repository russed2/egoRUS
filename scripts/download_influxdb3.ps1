param(
    [string]$Version = "3.9.2",
    [string]$Destination = "vendor\influxdb3"
)

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$targetDir = Join-Path $root $Destination
$zipPath = Join-Path $targetDir "influxdb3-core-$Version-windows_amd64.zip"
$url = "https://dl.influxdata.com/influxdb/releases/influxdb3-core-$Version-windows_amd64.zip"

New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
Write-Host "Downloading $url"
Invoke-WebRequest -Uri $url -OutFile $zipPath
Expand-Archive -Path $zipPath -DestinationPath $targetDir -Force

$exe = Get-ChildItem -Path $targetDir -Recurse -Filter "influxdb3.exe" | Select-Object -First 1
if ($null -eq $exe) {
    throw "influxdb3.exe not found after extraction"
}
if ($exe.DirectoryName -ne $targetDir) {
    Copy-Item -LiteralPath $exe.FullName -Destination (Join-Path $targetDir "influxdb3.exe") -Force
}
Remove-Item -LiteralPath $zipPath -Force
Write-Host "InfluxDB binary is ready: $(Join-Path $targetDir 'influxdb3.exe')"
