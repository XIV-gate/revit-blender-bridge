param(
    [Parameter(Mandatory = $true)][string]$OutputDir,
    [ValidateSet('2024','2027')][string]$WorkerKey = '2027',
    [string]$ReferencesRoot = '',
    [string]$DotnetRoot = 'C:\Program Files\dotnet',
    [string]$FrameworkRoot = 'C:\Program Files (x86)\Reference Assemblies\Microsoft\Framework\.NETFramework\v4.8'
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\revit_worker\build-common.ps1')
$destination = [IO.Path]::GetFullPath($OutputDir)
if (!(Test-Path -LiteralPath $destination -PathType Container)) { throw 'OutputDir must exist in the approved test root.' }
$configuration = Get-WorkerConfiguration -WorkerKey $WorkerKey -ReferencesRoot $ReferencesRoot -DotnetRoot $DotnetRoot -FrameworkRoot $FrameworkRoot
$dotnet = Join-Path $DotnetRoot 'dotnet.exe'
$symbol = 'LOADER_' + $WorkerKey
$base = @('/nologo','/langversion:latest','/nullable:enable','/warnaserror+','/deterministic+',('/define:' + $symbol)) + $configuration.Arguments
function Compile-LoaderFixture {
    param([string]$Target, [string]$Output, [string[]]$Sources, [string[]]$Additional = @())
    $response = Join-Path $destination '.loader_test_compile.rsp'
    $arguments = $base + @(('/target:' + $Target), ('/out:' + $Output)) + $Additional + $Sources
    $arguments | ForEach-Object { '"' + $_ + '"' } | Set-Content -LiteralPath $response -Encoding utf8
    try { & $dotnet $configuration.Compiler ('@' + $response) }
    finally { Remove-Item -LiteralPath $response -ErrorAction SilentlyContinue }
    if ($LASTEXITCODE -ne 0) { throw 'Loader fixture/test compilation failed.' }
}
$api = Join-Path $destination 'RevitAPIUI.dll'
Compile-LoaderFixture -Target library -Output $api -Sources @((Join-Path $PSScriptRoot 'tests\ApiStub.cs'))
$loaderDirectory = Join-Path $destination "addon\runtime\revit\$WorkerKey\loader\1"
New-Item -ItemType Directory -Path $loaderDirectory -Force | Out-Null
$loader = Join-Path $loaderDirectory 'XIVgate.RevitBridge.Loader.dll'
Compile-LoaderFixture -Target library -Output $loader -Sources @(Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*.cs' | Sort-Object Name | ForEach-Object FullName) -Additional @('/reference:' + $api)
$workerDirectory = Join-Path $destination 'worker_fixture'
New-Item -ItemType Directory -Path $workerDirectory -Force | Out-Null
$worker = Join-Path $workerDirectory 'XIVgate.RevitBridge.Worker.dll'
Compile-LoaderFixture -Target library -Output $worker -Sources @((Join-Path $PSScriptRoot 'tests\WorkerStub.cs')) -Additional @('/reference:' + $api)
$extension = if ($WorkerKey -eq '2024') { '.exe' } else { '.dll' }
$test = Join-Path $destination ('AGR.Loader.Tests' + $extension)
Compile-LoaderFixture -Target exe -Output $test -Sources @((Join-Path $PSScriptRoot 'LoaderGuard.cs'), (Join-Path $PSScriptRoot 'tests\LoaderTests.cs')) -Additional @('/reference:' + $api)
if ($WorkerKey -eq '2024') { & $test $destination $loader $worker }
else {
    @{ runtimeOptions = @{ tfm = 'net10.0'; framework = @{ name = 'Microsoft.NETCore.App'; version = '10.0.0' }; rollForward = 'Major' } } |
        ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $destination 'AGR.Loader.Tests.runtimeconfig.json') -Encoding utf8
    & $dotnet $test $destination $loader $worker
}
if ($LASTEXITCODE -ne 0) { throw 'Loader tests failed.' }
