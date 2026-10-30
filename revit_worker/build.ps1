param(
    [Parameter(Mandatory = $true)][string]$OutputDir,
    [string]$RevitDir = 'C:\Program Files\Autodesk\Revit 2027',
    [string]$DotnetRoot = 'C:\Program Files\dotnet'
)
$ErrorActionPreference = 'Stop'
$destination = [IO.Path]::GetFullPath($OutputDir)
if (!(Test-Path -LiteralPath $destination -PathType Container)) { throw 'OutputDir must be an already approved, existing build directory.' }
$sdk = Get-ChildItem -LiteralPath (Join-Path $DotnetRoot 'sdk') -Directory | Where-Object Name -Like '10.*' | Sort-Object Name -Descending | Select-Object -First 1
$refs = Get-ChildItem -LiteralPath (Join-Path $DotnetRoot 'packs\Microsoft.NETCore.App.Ref') -Directory | Where-Object Name -Like '10.*' | Sort-Object Name -Descending | Select-Object -First 1
if (!$sdk -or !$refs) { throw '.NET 10 SDK and reference assemblies are required.' }
$arguments = @((Join-Path $sdk.FullName 'Roslyn\bincore\csc.dll'), '/nologo', '/target:library', '/langversion:latest', '/nullable:enable', '/deterministic+', '/optimize+', '/warnaserror+', ('/out:' + (Join-Path $destination 'XIVgate.RevitBridge.Worker.dll')))
$arguments += Get-ChildItem -LiteralPath (Join-Path $refs.FullName 'ref\net10.0') -Filter '*.dll' | ForEach-Object { '/reference:' + $_.FullName }
$arguments += @(('/reference:' + (Join-Path $RevitDir 'RevitAPI.dll')), ('/reference:' + (Join-Path $RevitDir 'RevitAPIUI.dll')))
$arguments += Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*.cs' | ForEach-Object FullName
& (Join-Path $DotnetRoot 'dotnet.exe') @arguments
if ($LASTEXITCODE -ne 0) { throw 'Worker compilation failed.' }
Write-Output (Join-Path $destination 'XIVgate.RevitBridge.Worker.dll')
