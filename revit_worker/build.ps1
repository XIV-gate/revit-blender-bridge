param(
    [Parameter(Mandatory = $true)][string]$OutputDir,
    [ValidateSet('2024','2025','2025-net10','2026','2026-net10','2027')][string]$WorkerKey = '2027',
    [string]$ReferencesRoot = '',
    [string]$RevitDir = '',
    [string]$DotnetRoot = 'C:\Program Files\dotnet',
    [string]$FrameworkRoot = 'C:\Program Files (x86)\Reference Assemblies\Microsoft\Framework\.NETFramework\v4.8'
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'build-common.ps1')
$destination = [IO.Path]::GetFullPath($OutputDir)
if (!(Test-Path -LiteralPath $destination -PathType Container)) { throw 'OutputDir must be an already approved, existing build directory.' }
$configuration = Get-WorkerConfiguration -WorkerKey $WorkerKey -ReferencesRoot $ReferencesRoot -DotnetRoot $DotnetRoot -FrameworkRoot $FrameworkRoot
$year = $WorkerKey.Substring(0,4)
if (!$RevitDir) {
    if ($year -eq '2027') { $RevitDir = Join-Path $env:ProgramFiles 'Autodesk\Revit 2027' }
    else {
        $apiVersion = @{ '2024' = '2024.0.0'; '2025' = '2025.0.2'; '2026' = '2026.0.4' }[$year]
        $apiTarget = if ($year -eq '2024') { 'net48' } else { 'net8.0' }
        $apiPath = Join-Path $ReferencesRoot "nice3point.revit.api.revitapi.$apiVersion\ref\$apiTarget\RevitAPI.dll"
        $apiUiPath = Join-Path $ReferencesRoot "nice3point.revit.api.revitapiui.$apiVersion\ref\$apiTarget\RevitAPIUI.dll"
    }
}
if ($RevitDir) { $apiPath = Join-Path $RevitDir 'RevitAPI.dll'; $apiUiPath = Join-Path $RevitDir 'RevitAPIUI.dll' }
foreach ($path in @($apiPath, $apiUiPath)) { if (!(Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing real Revit API reference: $path" } }
foreach ($path in @($apiPath, $apiUiPath)) {
    if ([Reflection.AssemblyName]::GetAssemblyName($path).Version.Major -ne ([int]$year - 2000)) { throw "Revit SDK major does not match requested worker $WorkerKey`: $path" }
}
$arguments = @($configuration.Compiler, '/nologo', '/target:library', '/langversion:latest', '/nullable:enable', '/deterministic+', '/optimize+', '/warnaserror+', ('/out:' + (Join-Path $destination 'XIVgate.RevitBridge.Worker.dll')))
$arguments += $configuration.Arguments
$arguments += @(('/reference:' + $apiPath), ('/reference:' + $apiUiPath))
$arguments += Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*.cs' | ForEach-Object FullName
$responseFile = Join-Path $destination '.worker_compile.rsp'
$arguments[1..($arguments.Length - 1)] | ForEach-Object { '"' + $_ + '"' } | Set-Content -LiteralPath $responseFile -Encoding utf8
try { & (Join-Path $DotnetRoot 'dotnet.exe') $arguments[0] ('@' + $responseFile) }
finally { Remove-Item -LiteralPath $responseFile -ErrorAction SilentlyContinue }
if ($LASTEXITCODE -ne 0) { throw "Worker $WorkerKey compilation failed." }
foreach ($dependency in $configuration.Dependencies) { Copy-Item -LiteralPath $dependency -Destination $destination -Force }
Write-Output (Join-Path $destination 'XIVgate.RevitBridge.Worker.dll')
