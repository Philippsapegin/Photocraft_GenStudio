# PhotoCraft and GenGate updates

GenGate adds native controls to PhotoCraft's editor binaries and keeps its Python runtime separately. Updating PhotoCraft replaces those binaries, so the editor update can remove GenGate's controls. Documents, the separate runtime and model weights are not part of the editor overlay.

When PhotoCraft finds a newer release, its update window first shows this compatibility warning and the GenGate release status. **Update and Restart** then opens PhotoCraft's normal updater. GenGate does not replace or bypass its unsaved-document handling.

GenGate checks its own GitHub releases once per launch when **General > Automatically Check for Updates** is enabled. A newer stable GenGate release opens a native notification with **Update GenGate…**, linking directly to that release's download page. Automatic failures stay quiet, and checks do not start a model engine. Manual checks are available in GenGate Settings and the update window even when automatic checks are disabled.

Before updating PhotoCraft, check that a GenGate package supports the intended editor version. After updating PhotoCraft, close the editor and run the compatible GenGate installer. The update offer does not imply compatibility with every PhotoCraft version: consult the release's compatibility list. Installation verifies both editor hashes and refuses unknown builds rather than downgrading them.

If an official PhotoCraft build saved preferences without the unknown GenGate section, the installer can recover the previous add-on settings from its separately installed runtime. Unrelated PhotoCraft preferences are retained.

GenGate **0.9.5** supports official PhotoCraft **0.17.0 Windows x64**, pinned at `b8c3f3aa8e2918032cadc0e042664414a03be333`. GenGate **0.9.4** remains available for PhotoCraft **0.16.0**.

The current build scripts target 0.17.0. Earlier editor integrations remain available from their corresponding GenGate release tags and archived packages.
