# GenGate 0.9.1 add-on (local candidate)

GenGate installs separately on top of an existing PhotoCraft instead of shipping
another complete editor. All current native tools and interface remain.

- Select an existing PhotoCraft installation in Setup.
- GenGate software/runtime lives in its own folder and reuses shared model files.
- Checksum-bound editor/CLI deltas are applied locally, with verified originals
  and recoverable transactions for rollback.
- The GenGate launcher detects replaced editor files. Supported bases can be
  repatched; unknown PhotoCraft updates stay untouched and open with a notice.
- Uninstall restores owned files and settings, preserving newer editor files,
  documents and shared models. Software and recovery backups are retained.

**Supported hosts: official PhotoCraft 0.8.2 and 0.12.0 Windows x64.** Other releases
require native integration adaptation and new verified deltas.
This is a local editor patch, not a new official plug-in API.

The editor is listed simply as **PhotoCraft**. Setup backs up and removes old
editor launcher duplicates, and both Start and Desktop use the same launcher.
Uninstall returns them to the ordinary editor executable.

AI edits now preserve native crop resolution by default. Optional economy mode
keeps crops up to 768 px unchanged and progressively reduces larger crops,
capped at 2×. The floating prompt has stable keyboard ownership for repeated
Backspace/Delete, including while the pointer is outside the field.

Current source: 123 Python tests passed, 5 tensor tests skipped; 1,924 engine and 3,270 UI tests passed. Earlier extracted-add-on checks cover installation/update/refresh/restore/reinstall. Official MCP uses synthetic workers; real inference was not run. See [validation](VALIDATION.md) for artifact-specific evidence.

This candidate is not published. The public 0.9.0 release is the previous
standalone distribution.
