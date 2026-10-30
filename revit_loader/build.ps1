param(
    [Parameter(Mandatory = $true)][string]$OutputDir,
    [ValidateSet('2024','2025','2025-net10','2026','2026-net10','2027')][string]$WorkerKey = '2027',
    [string]$ReferencesRoot = '',
    [string]$RevitDir = '',
    [string]$DotnetRoot = 'C:\Program Files\dotnet',
    [string]$FrameworkRoot = 'C:\Program Files (x86)\Reference Assemblies\Microsoft\Framework\.NETFramework\v4.8'
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\revit_worker\build-common.ps1')
$destination = [IO.Path]::GetFullPath($OutputDir)
if (!(Test-Path -LiteralPath $destination -PathType Container)) { throw 'OutputDir must be an existing approved directory.' }
$configuration = Get-WorkerConfiguration -WorkerKey $WorkerKey -ReferencesRoot $ReferencesRoot -DotnetRoot $DotnetRoot -FrameworkRoot $FrameworkRoot
$year = $WorkerKey.Substring(0,4)
if (!$RevitDir) {
    if ($year -eq '2027') { $RevitDir = Join-Path $env:ProgramFiles 'Autodesk\Revit 2027' }
    else {
        $apiVersion = @{ '2024' = '2024.0.0'; '2025' = '2025.0.2'; '2026' = '2026.0.4' }[$year]
        $target = if ($year -eq '2024') { 'net48' } else { 'net8.0' }
        $apiPath = Join-Path $ReferencesRoot "nice3point.revit.api.revitapi.$apiVersion\ref\$target\RevitAPI.dll"
        $apiUiPath = Join-Path $ReferencesRoot "nice3point.revit.api.revitapiui.$apiVersion\ref\$target\RevitAPIUI.dll"
    }
}
if ($RevitDir) { $apiPath = Join-Path $RevitDir 'RevitAPI.dll'; $apiUiPath = Join-Path $RevitDir 'RevitAPIUI.dll' }
foreach ($path in @($apiPath, $apiUiPath)) {
    if (!(Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing real API reference: $path" }
    if ([Reflection.AssemblyName]::GetAssemblyName($path).Version.Major -ne ([int]$year - 2000)) { throw 'Revit SDK major mismatch.' }
}
$keySymbol = 'LOADER_' + $WorkerKey.Replace('-', '_').ToUpperInvariant()
$arguments = @('/nologo','/target:library','/langversion:latest','/nullable:enable','/deterministic+','/optimize+','/warnaserror+',
    ('/define:' + $keySymbol), ('/pathmap:' + $PSScriptRoot + '=/_src/revit_loader'),
    ('/out:' + (Join-Path $destination 'XIVgate.RevitBridge.Loader.dll')))
$arguments += $configuration.Arguments
$arguments += @(('/reference:' + $apiPath), ('/reference:' + $apiUiPath))
$arguments += Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*.cs' | Sort-Object Name | ForEach-Object FullName
$responseFile = Join-Path $destination '.loader_compile.rsp'
$arguments | ForEach-Object { '"' + $_ + '"' } | Set-Content -LiteralPath $responseFile -Encoding utf8
try { & (Join-Path $DotnetRoot 'dotnet.exe') $configuration.Compiler ('@' + $responseFile) }
finally { Remove-Item -LiteralPath $responseFile -ErrorAction SilentlyContinue }
if ($LASTEXITCODE -ne 0) { throw "Loader $WorkerKey compilation failed." }
# Deliberately copy no worker, Autodesk, JSON or Framework dependency DLLs.
Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $destination 'XIVgate.RevitBridge.Loader.dll')
