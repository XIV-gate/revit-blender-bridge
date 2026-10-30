param(
    [Parameter(Mandatory = $true)][string]$OutputDir,
    [ValidateSet('2024','2025','2027')][string]$WorkerKey = '2027',
    [string]$ReferencesRoot = '',
    [string]$DotnetRoot = 'C:\Program Files\dotnet',
    [string]$FrameworkRoot = 'C:\Program Files (x86)\Reference Assemblies\Microsoft\Framework\.NETFramework\v4.8'
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'build-common.ps1')
$destination = [IO.Path]::GetFullPath($OutputDir)
if (!(Test-Path -LiteralPath $destination -PathType Container)) { throw 'OutputDir must be an existing approved test directory.' }
$configuration = Get-WorkerConfiguration -WorkerKey $WorkerKey -ReferencesRoot $ReferencesRoot -DotnetRoot $DotnetRoot -FrameworkRoot $FrameworkRoot
$extension = if ($WorkerKey -eq '2024') { '.exe' } else { '.dll' }
$testDll = Join-Path $destination ('XIVgate.RevitBridge.ProtocolTests' + $extension)
$arguments = @($configuration.Compiler, '/nologo', '/target:exe', '/langversion:latest', '/nullable:enable', '/warnaserror+', ('/out:' + $testDll))
$arguments += $configuration.Arguments
foreach ($file in @('Compat.cs','NetFrameworkSupport.cs','FrameworkDependencies.cs','Protocol.cs','WarningPolicy.cs','RequestWake.cs','TextureLookup.cs','tests\ProtocolTests.cs')) { $arguments += Join-Path $PSScriptRoot $file }
$responseFile = Join-Path $destination '.worker_tests.rsp'
$arguments[1..($arguments.Length - 1)] | ForEach-Object { '"' + $_ + '"' } | Set-Content -LiteralPath $responseFile -Encoding utf8
try { & (Join-Path $DotnetRoot 'dotnet.exe') $arguments[0] ('@' + $responseFile) }
finally { Remove-Item -LiteralPath $responseFile -ErrorAction SilentlyContinue }
if ($LASTEXITCODE -ne 0) { throw 'Protocol test compilation failed.' }
foreach ($dependency in $configuration.Dependencies) { Copy-Item -LiteralPath $dependency -Destination $destination -Force }
if ($WorkerKey -eq '2024') { & $testDll $destination }
else {
    $major = if ($WorkerKey -eq '2025') { '8' } else { '10' }
    $config = Join-Path $destination 'XIVgate.RevitBridge.ProtocolTests.runtimeconfig.json'
    @{ runtimeOptions = @{ tfm = "net$major.0"; rollForward = 'Major'; framework = @{ name = 'Microsoft.NETCore.App'; version = "$major.0.0" } } } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $config -Encoding utf8
    & (Join-Path $DotnetRoot 'dotnet.exe') $testDll $destination
}
if ($LASTEXITCODE -ne 0) { throw 'Protocol tests failed.' }
