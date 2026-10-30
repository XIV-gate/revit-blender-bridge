# AGR Revit Bridge 0.3.0 — stable channel

- Release browser: stable and Early Access are separate lists. The user chooses
  the exact numeric version; installing an older version is allowed deliberately.
- GitHub prerelease status defines Early Access; branch names never install code.
- Explicit confirmation identifies test builds and downgrades. SHA256, package
  validation, locked-worker preservation and automatic rollback remain required.
- Worker routing covers Revit 2024, 2025, 2026, 2027; 2025/2026 .NET10 updates use
  separate workers. Executable metadata determines the year; directory names do not.
- Shared geometry instances, materials, available textures and selective reimport
  are retained.

Only Revit2027 is installed in the development environment. Other workers are
compiled against real API references and their host-independent transport is
tested; complete model import in Revit2024/2025/2026 still needs host validation.
This release does not imply support for future, untested Revit major versions.
Linked RVT geometry remains outside this release's scope.

Early Access 0.3.1 is intentionally separate: automatic setup, project cache and
verified export reuse do not change the stable0.3.0 workflow.

User workflow fixes included before publication:
- Native linked Mesh instances (Alt+D semantics): edit a window directly;
  no hidden prototype object tree or collection-instance placeholders.
- Exact subset removal without Revit/conversion, unchanged object identities.
- Canonical names restored after full replacement, including legacy .001 repair.
- Force-refresh command for deliberate full source updates.
- Real building benchmark: 12108 mesh placements/5203 shared meshes;
  removing72 placements0.419s; first full assembly45.917s on this machine.
