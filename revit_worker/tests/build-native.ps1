param(
    [Parameter(Mandatory = $true)][string]$OutputDir,
    [switch]$LoaderCompatible,
    [ValidateSet('Views','References','Selective','Curves')][string]$Case = 'Views',
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
$fixture = switch ($Case) { 'References' { 'NativeServerReferenceProbe.cs' }; 'Selective' { 'NativeSelectiveRegression.cs' }; 'Curves' { 'NativeCurveDetailRegression.cs' }; default { 'NativeViewRegression.cs' } }
$arguments += Join-Path $PSScriptRoot $fixture
if ($Case -eq 'Selective') {
    # Pin the full-traversal baseline; never silently compare against a changed worktree.
    $repository = Split-Path -Parent $worker
    $baseline = & git -C $repository show '00dd612:revit_worker/GeometryExporter.cs'
    if ($LASTEXITCODE -ne 0) { throw 'Pinned selective-export baseline 00dd612 is unavailable.' }
    $baseline = ($baseline -join "`n") -replace '\bGeometryExporter\b', 'BaselineGeometryExporter'
    # Instrument the old traversal only; mesh/material/transform behavior is unchanged.
    $baseline = $baseline.Replace('long material = -1, triangles;', 'long material = -1, triangles, visitedElements, materialCallbacks;')
    $baseline = $baseline.Replace('materials = context.materials.Count, bytes }', 'materials = context.materials.Count, bytes, visited_elements = context.visitedElements, material_callbacks = context.materialCallbacks }')
    $baseline = $baseline.Replace('        var ancestor = elements.Count', "        visitedElements++;`n        var ancestor = elements.Count")
    $baseline = $baseline.Replace('        long sourceMaterial = node.MaterialId.Value;', "        materialCallbacks++;`n        long sourceMaterial = node.MaterialId.Value;")
    $baselinePath = Join-Path $destination 'BaselineGeometryExporter.cs'
    [IO.File]::WriteAllText($baselinePath, $baseline, [Text.UTF8Encoding]::new($false))
    $arguments += $baselinePath
}

$shared = @('Compat.cs','Protocol.cs','NetFrameworkSupport.cs')
if ($Case -eq 'References') { $shared += @('WarningPolicy.cs','OfflineReferencePreparer.cs','OfflineReferenceValidation.cs') }
else { $shared += @('GeometryExporter.cs','GeometryPrecision.cs','NativeInstancePolicy.cs','TextureLookup.cs','TextureProperty.cs') }
foreach ($filename in $shared) { $arguments += Join-Path $worker $filename }
$response = Join-Path $destination '.native_compile.rsp'
$arguments | ForEach-Object { '"' + $_ + '"' } | Set-Content -LiteralPath $response -Encoding utf8
try { & (Join-Path $DotnetRoot 'dotnet.exe') $configuration.Compiler ('@' + $response) }
finally { Remove-Item -LiteralPath $response -ErrorAction SilentlyContinue }
if ($LASTEXITCODE -ne 0) { throw 'Native fixture compilation failed.' }
Write-Output $target
