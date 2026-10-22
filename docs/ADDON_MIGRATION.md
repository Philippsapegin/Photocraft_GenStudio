# Separate GenGate installation

Decision: the user chose local editor patches, accepting that new PhotoCraft
versions require adaptation. The 0.9.1 add-on implements this route. No new
official PhotoCraft API is claimed.

## Requirement

GenGate must install into an existing, independently updated PhotoCraft without
shipping a complete editor or freezing its version. Local, reversible changes to
the installed executable are allowed. Keep the current native
tools, appearance, settings, layers, masks, variants and undo behaviour.

## Verified limitation

Checked on 2026-10-22 against PhotoCraft main commit
[`6d8cbac7dcc4a2c8a32e0ceefe637cd72c98da32`](https://github.com/storytold/photocraft/tree/6d8cbac7dcc4a2c8a32e0ceefe637cd72c98da32).
The latest published release at the time of the audit was v0.12.0.

The [plug-in contract](https://github.com/storytold/photocraft/blob/6d8cbac7dcc4a2c8a32e0ceefe637cd72c98da32/docs/plugins.md)
provides ABI v1 WebAssembly **filters only**. Modules have no host imports,
network or filesystem access. The documentation explicitly excludes UI panels,
access to other layers/documents, progress and cancellation callbacks.
The manifest's `Kind` enum contains only `Filter`; the native plug-in UI generates
filter dialogs, rather than loading arbitrary extension views.

The [official MCP interface](https://github.com/storytold/photocraft/blob/6d8cbac7dcc4a2c8a32e0ceefe637cd72c98da32/docs/mcp.md)
can drive existing commands and inspect documents. It does not register new
native views, Preferences sections, tool options or stroke handlers.

| Existing GenGate feature | Host support needed for an independent add-on |
| --- | --- |
| Workspace, title/chip, references and prompt chips | Native extension views, themed widgets, drag/drop, file picking and view state |
| GenGate Settings in Preferences | Extension-owned settings section, validation and Apply/Cancel lifecycle |
| Remove Tool's Ai Fill option | Tool-options extension and completed-stroke mask callback, preserving existing options |
| Selection Ai Fill and floating bar | Menu registration, selection notifications and native canvas overlays |
| Prompts and variant gallery in Properties | Layer-properties extension and persistent extension metadata |
| Select Subject / Sky / Remove Background / Ai Select | Explicit opt-in command providers and rectangle-selection events |
| Queued masks, progress and cancellation | Asynchronous extension jobs and document-coordinate overlays |
| Colour-correct, undoable layer/mask output | Bounded pixel capture and atomic document/history transactions |
| Engine startup and shared model paths | Extension activation lifecycle and a permitted local service connection |

These are current source-integration points in `scripts/prepare_*.py` and
`photocraft/*.rs`. Installing those Rust source files in a stock editor's plug-in
folder cannot activate them.

## Possible future upstream route

Extend PhotoCraft with a general, versioned native extension API and migrate
GenGate to that API. The host owns drawing, document transactions and explicit
permissions; GenGate owns its tools, view descriptions, settings and external
inference service. Preserve the existing isolated filter sandbox.

The API must first be available in the PhotoCraft version receiving the add-on.
Getting it into official releases requires the PhotoCraft maintainers' agreement;
this repository cannot guarantee acceptance or a release date. No upstream issue,
message or pull request has been sent as part of this audit.

A prospective separate installer should:

1. Locate an existing PhotoCraft installation and query its advertised extension
   capabilities. Version strings alone are insufficient.
2. Refuse installation before changing anything when required capabilities are
   absent, explaining which supported PhotoCraft version is needed.
3. Install only GenGate code, its private software runtime and registration into
   an extension directory outside PhotoCraft's versioned application files.
4. Reuse model files and optional existing GPU Python without duplicating weights
   or modifying that Python environment.
5. Keep GenGate settings and existing prompt/variant metadata compatible. Do not
   replace the user's PhotoCraft profile or its normal launcher.
6. Update and uninstall GenGate independently. Removal unregisters its providers
   and leaves ordinary PhotoCraft tools, user documents and shared models intact.
7. Recheck host capabilities after PhotoCraft updates. Disable an incompatible
   extension with a clear message rather than applying a guessed binary patch.

The UI and tool parity tests must run against an unmodified official editor that
implements this API. Tests against our currently modified executable do not prove
stock-editor compatibility. Real model inference remains opt-in.

## Alternatives and their limits

- Rebuilding/replacing PhotoCraft after each update can preserve native features,
  but retains a patched editor and requires ongoing adaptation. A smaller download
  or binary delta does not turn it into an independent plug-in.
- A separate MCP-driven application can use the existing editor, but cannot keep
  the current embedded workspace, Preferences, native tool checkboxes and canvas
  feedback through the published API alone.

The user accepted the local-patch route with explicit version adaptation. It
keeps the native tools, but is not an update-independent extension. The separate
MCP-only application still fails the native-interface requirement.

## Implemented overlay

`genstudio/editor_patch.py` checks complete editor and CLI hashes against a
versioned catalog, reconstructs the existing validated GenGate binaries from
BSDIFF deltas, then commits both files, the runtime pointer and settings through
a recoverable transaction. Originals and transaction snapshots are kept under
the selected editor's `.gengate/backups` directory. The separate GenGate folder
contains software/runtime and deltas, without a complete editor or private editor
profile. Native PhotoCraft shortcuts still open the same application path.

The GenGate launcher detects replaced binaries and reapplies only an exactly
matching supported patch. Unknown updates remain untouched and are opened as
ordinary PhotoCraft with a compatibility notice. Uninstall restores only files
that still match our patched output and preserves newer upstream files.

Supported bases: official PhotoCraft **0.8.2 and 0.12.0 Windows x64**, with hashes
and archive provenance in `photocraft/editor-bases.json`. Each has its own native
integration and delta; no guessed cross-version binary patching is used.

The shell has one PhotoCraft identity: canonical Start and optional Desktop links
call the stable add-on launcher. Obsolete editor links are backed up and removed;
uninstall makes those canonical links open ordinary PhotoCraft directly.

## Legacy installation

The published 0.9.0 package is a modified editor distribution, not a standalone
add-on. The public release remains the legacy distribution until a replacement
release is explicitly published. Existing standalone installations are retained
during migration rather than recursively deleted.
