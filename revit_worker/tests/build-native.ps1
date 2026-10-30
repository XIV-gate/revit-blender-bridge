param(
    [Parameter(Mandatory = $true)][string]$OutputDir,
    [switch]$LoaderCompatible,
    [string]$RevitDir = 'C:\Program Files\Autodesk\Revit 2027',
    [string]$DotnetRoot = 'C:\Program Files\dotnet'
)
$ErrorActionPreference = 'Stop'
$worker = Split-Path -Parent $PSScriptRoot
. (Join-Path $worker 'build-common.ps1')
$destination = [IO.Path]::GetFullPath($OutputDir)
if (!(Test-Path -LiteralPath $destination -PathType Container)) { throw 'OutputDir must already exist inside the explicitly approved task workspace.' }
$configuration = Get-WorkerConfiguration -WorkerKey '2027' -ReferencesRoot '' -DotnetRoot $DotnetRoot -FrameworkRoot ''
$filename = if ($LoaderCompatible) { 'XIVgate.RevitBridge.Worker.dll' } else { 'XIVgate.RevitBridge.NativeRegression.dll' }
$target = Join-Path $destination $filename
$arguments = @('/nologo', '/target:library', '/langversion:latest', '/nullable:enable', '/deterministic+', '/optimize+', '/warnaserror+', ('/out:' + $target))
$arguments += $configuration.Arguments
foreach ($filename in @('RevitAPI.dll', 'RevitAPIUI.dll')) {
    $reference = Join-Path $RevitDir $filename
    if ([Reflection.AssemblyName]::GetAssemblyName($reference).Version.Major -ne 27) { throw 'Native fixture requires real Revit 2027 SDK assemblies.' }
    $arguments += '/reference:' + $reference
}
$arguments += Join-Path $PSScriptRoot 'NativeViewRegression.cs'
foreach ($filename in @('GeometryExporter.cs','GeometryPrecision.cs','NativeInstancePolicy.cs','Compat.cs','Protocol.cs','TextureLookup.cs','TextureProperty.cs','NetFrameworkSupport.cs')) { $arguments += Join-Path $worker $filename }
$response = Join-Path $destination '.native_compile.rsp'
$arguments | ForEach-Object { '"' + $_ + '"' } | Set-Content -LiteralPath $response -Encoding utf8
try { & (Join-Path $DotnetRoot 'dotnet.exe') $configuration.Compiler ('@' + $response) }
finally { Remove-Item -LiteralPath $response -ErrorAction SilentlyContinue }
if ($LASTEXITCODE -ne 0) { throw 'Native fixture compilation failed.' }
Write-Output $target
