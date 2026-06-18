# Windows to Cross-Platform Guidance

## Current Limitation

The checked `UiPath.Upgrade.Cli` source does not contain a generic Windows to Cross-platform/Portable updater. The framework update step only changes `TargetFramework.Legacy` to `TargetFramework.Windows`, and restore logic uses Windows as the target framework.

Before claiming support, verify the target branch with:

```bash
rg -n "TargetFramework\\.(Portable|Windows|Legacy)|Cross.?platform|CrossPlatform" Upgrade --glob "*.cs"
```

## If Asked To Run Migration

If a built CLI supports a Windows to Cross-platform mode, use that documented command. If the checked source does not support it, say so and offer to implement support. Do not fake the migration by changing only `project.json`; XAML namespaces, package compatibility, generated assemblies, dependency restore, and validation must go through a tested pipeline.

## If Asked To Implement Support

Use the existing Activity Migrator architecture rather than a one-off script:

1. Add an explicit target framework option, for example `--target-framework Windows|Portable`, instead of inferring from project state.
2. Update `UpgradeOptions` to carry the target framework.
3. Replace or extend `ProjectFrameworkUpdaterStep` so it can update only supported transitions:
   - `Legacy -> Windows`
   - `Windows -> Portable` only after compatibility checks pass
4. Update restore/search logic so package restore uses the selected target framework instead of hard-coded Windows.
5. Add a validation step or rule for unsupported Windows-only dependencies and activities.
6. Keep activity migrations extension-driven; do not fold UIA/Mail/Microsoft replacement rules into the core framework step.
7. Add tests with representative Windows projects and assert:
   - target framework in `project.json`
   - dependency resolution
   - XAML load/type-check success
   - SARIF warnings for unsupported activities
   - output path behavior

Use SARIF rule IDs for every intentional warning or unsupported conversion so users can triage the output.
