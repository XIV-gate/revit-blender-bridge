function Get-WorkerConfiguration {
    param([string]$WorkerKey, [string]$ReferencesRoot, [string]$DotnetRoot, [string]$FrameworkRoot)
    $sdk = Get-ChildItem -LiteralPath (Join-Path $DotnetRoot 'sdk') -Directory | Where-Object Name -Like '10.*' | Sort-Object Name -Descending | Select-Object -First 1
    if (!$sdk) { throw '.NET 10 SDK compiler required.' }
    $dependencies = @()
    $compileArguments = @()
    if ($WorkerKey -eq '2024') {
        $compileArguments += '/define:NETFRAMEWORK'
        $refs = (@(Get-ChildItem -LiteralPath $FrameworkRoot -Filter '*.dll') + @(Get-ChildItem -LiteralPath (Join-Path $FrameworkRoot 'Facades') -Filter '*.dll')) | Where-Object { $_.Name -notin @('System.EnterpriseServices.Wrapper.dll', 'System.EnterpriseServices.Thunk.dll') }
        $dependencyPaths = @(
            'system.text.json.8.0.6\lib\net462\System.Text.Json.dll',
            'system.text.encodings.web.8.0.0\lib\net462\System.Text.Encodings.Web.dll',
            'microsoft.bcl.asyncinterfaces.8.0.0\lib\net462\Microsoft.Bcl.AsyncInterfaces.dll',
            'system.memory.4.5.5\lib\net461\System.Memory.dll',
            'system.buffers.4.5.1\lib\net461\System.Buffers.dll',
            'system.runtime.compilerservices.unsafe.6.0.0\lib\net461\System.Runtime.CompilerServices.Unsafe.dll',
            'system.threading.tasks.extensions.4.5.4\lib\net461\System.Threading.Tasks.Extensions.dll',
            'system.numerics.vectors.4.4.0\lib\net46\System.Numerics.Vectors.dll'
        )
        foreach ($relative in $dependencyPaths) {
            $path = Join-Path $ReferencesRoot $relative
            if (!(Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing Framework dependency: $path" }
            $dependencies += $path
            $compileArguments += '/reference:' + $path
        }
    } else {
        $target = if ($WorkerKey -in @('2025','2026')) { 'net8.0' } else { 'net10.0' }
        # Autodesk's .NET 10 host transition retains same-major add-in/API compatibility.
        # Baseline 2025/26 SDK references target Runtime8; this intentional unification is the
        # only warning exempted from warn-as-error for their explicitly selected net10 builds.
        if ($WorkerKey -in @('2025-net10','2026-net10')) { $compileArguments += '/warnaserror-:1701' }
        if ($target -eq 'net8.0') { $refDir = Join-Path $ReferencesRoot 'microsoft.netcore.app.ref.8.0.33\ref\net8.0' }
        else {
            $pack = Get-ChildItem -LiteralPath (Join-Path $DotnetRoot 'packs\Microsoft.NETCore.App.Ref') -Directory | Where-Object Name -Like '10.*' | Sort-Object Name -Descending | Select-Object -First 1
            $refDir = Join-Path $pack.FullName 'ref\net10.0'
        }
        $refs = @(Get-ChildItem -LiteralPath $refDir -Filter '*.dll')
    }
    $compileArguments += $refs | ForEach-Object { '/reference:' + $_.FullName }
    return [PSCustomObject]@{ Compiler = (Join-Path $sdk.FullName 'Roslyn\bincore\csc.dll'); Arguments = $compileArguments; Dependencies = $dependencies }
}
