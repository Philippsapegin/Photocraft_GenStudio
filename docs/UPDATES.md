# PhotoCraft and GenGate updates

GenGate adds native controls to PhotoCraft's editor binaries and keeps its Python runtime separately. Updating PhotoCraft replaces those binaries, so the editor update can remove GenGate's controls. Documents, the separate runtime and model weights are not part of the editor overlay.

When PhotoCraft finds a newer release, **Update and Restart** opens GenGate's external update window. The same helper is available as **Update PhotoCraft** in GenGate Setup when the installed editor is too old. It keeps the existing GenGate location, Direct runtime, model and ComfyUI folders, workflows and generation settings.

The helper downloads the official PhotoCraft release supported by the selected GenGate package. It verifies the release checksum and the official publisher's Windows signatures before installation. This works even though GenGate's modified editor cannot use PhotoCraft's signature-dependent self-updater. No Windows signature checks are disabled.

Save your documents and close PhotoCraft when the update window asks. The helper waits without closing the editor for you. Portable installations update in place with rollback if a file replacement fails or the operation is cancelled. MSI installations use the official Windows Installer package in the existing folder and installation scope, followed by GenGate. While Windows Installer is running, cancellation waits for that step to finish. If Windows needs a restart, Setup explains how to continue; it never restarts Windows automatically.

If the editor update succeeds but applying GenGate fails, run Setup again. The official editor remains installed, and saved settings and prepared runtimes are retained for retry. A newer PhotoCraft is never downgraded. If the requested editor version is newer than the package supports, the update window links to a compatible GenGate release first.

GenGate checks its own GitHub releases once per launch when **General > Automatically Check for Updates** is enabled. A newer stable GenGate release opens a native notification with **Update GenGate…**, linking directly to that release's download page. Automatic failures stay quiet, and checks do not start a model engine. Manual checks are available in GenGate Settings and the update window even when automatic checks are disabled.

The update offer does not imply compatibility with every PhotoCraft version: consult the GenGate release's compatibility list. Installation verifies both editor hashes. An unrecognized same-version editor build requires the official supported release.

If an official PhotoCraft build saved preferences without the unknown GenGate section, the installer can recover the previous add-on settings from its separately installed runtime. Unrelated PhotoCraft preferences are retained.

GenGate **0.9.9** supports official PhotoCraft **0.21.0 Windows x64**, pinned at `efb7ebd0c0dab1fc108ee4642aa47c22694271a8`. GenGate **0.9.8** remains available for PhotoCraft **0.20.2**, **0.9.7** for **0.20.1**, **0.9.6** for **0.18.0**, **0.9.5** for **0.17.0**, and **0.9.4** for **0.16.0**.

The current build scripts default to 0.21.0 and retain the explicit 0.17/0.18/0.19/0.20.1/0.20.2 source options. Earlier editor integrations remain available from their corresponding GenGate release tags and archived packages.
