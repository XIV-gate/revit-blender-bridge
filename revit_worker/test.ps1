param([Parameter(Mandatory = $true)][string]$OutputDir, [string]$DotnetRoot = 'C:\Program Files\dotnet')
$ErrorActionPreference = 'Stop'
$destination = [IO.Path]::GetFullPath($OutputDir)
if (!(Test-Path -LiteralPath $destination -PathType Container)) { throw 'OutputDir must be an existing approved test directory.' }
$sdk = Get-ChildItem -LiteralPath (Join-Path $DotnetRoot 'sdk') -Directory | Where-Object Name -Like '10.*' | Sort-Object Name -Descending | Select-Object -First 1
$refs = Get-ChildItem -LiteralPath (Join-Path $DotnetRoot 'packs\Microsoft.NETCore.App.Ref') -Directory | Where-Object Name -Like '10.*' | Sort-Object Name -Descending | Select-Object -First 1
$testDll = Join-Path $destination 'XIVgate.RevitBridge.ProtocolTests.dll'
$arguments = @((Join-Path $sdk.FullName 'Roslyn\bincore\csc.dll'), '/nologo', '/target:exe', '/nullable:enable', '/warnaserror+', ('/out:' + $testDll))
$arguments += Get-ChildItem -LiteralPath (Join-Path $refs.FullName 'ref\net10.0') -Filter '*.dll' | ForEach-Object { '/reference:' + $_.FullName }
$arguments += @((Join-Path $PSScriptRoot 'Protocol.cs'), (Join-Path $PSScriptRoot 'WarningPolicy.cs'), (Join-Path $PSScriptRoot 'tests\ProtocolTests.cs'))
& (Join-Path $DotnetRoot 'dotnet.exe') @arguments
if ($LASTEXITCODE -ne 0) { throw 'Protocol test compilation failed.' }
$config = Join-Path $destination 'XIVgate.RevitBridge.ProtocolTests.runtimeconfig.json'
@{ runtimeOptions = @{ tfm = 'net10.0'; framework = @{ name = 'Microsoft.NETCore.App'; version = '10.0.0' } } } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $config -Encoding utf8
& (Join-Path $DotnetRoot 'dotnet.exe') $testDll $destination
if ($LASTEXITCODE -ne 0) { throw 'Protocol tests failed.' }
