# Windows personal installation

GenGate setup runs as the signed-in desktop user. It compares the process SID
with the user of its Windows session, before discovering folders or preparing
software. Over-the-shoulder elevation with another account is rejected with
instructions to run Setup normally. The editor updater uses the same check.

A writable PhotoCraft installation is patched in place. For Program Files,
ProgramData or a host whose files cannot be replaced, setup prepares a personal
copy in `%LOCALAPPDATA%\Programs\ArtCraft\PhotoCraft-GenGate`. It uses the exact
official file manifest and verified original hashes. If those originals are
unavailable locally, it downloads the pinned official portable archive and
verifies its SHA-256 and Authenticode publisher. A known newer editor is never
replaced with an older supported version.

Only the signed-in user's profile is copied to `PhotoCraftData`; the shared
editor, its GenGate records and other users' profiles remain unchanged. The
personal copy is a portable installation, so later updates avoid Windows
Installer and UAC. User shortcuts point to the personal editor. Shared Start
menu/Desktop shortcuts are not deleted. Direct and the GenGate host are
installed under the same account that will run generation; setup reuses an
existing compatible interpreter when possible.

The private GPU interpreter lives in `runtime/.runtime/direct`. Setup copies an
existing installation-owned `qwen-direct` interpreter into that generic folder,
including its dependencies, without downloading them again. The old version is
retained for recovery. Independently configured Python installations are reused
at their original paths and are not modified.

The personal destination must be empty or owned by an earlier GenGate personal
installation. A linked destination is rejected. Preparation failures remove
only the new owned staging folder. Existing personal installations retain their
current profile on repeat setup. An older personal copy needing an editor
update must be selected in Setup; discovery lists that copy ahead of shared
editors.

## Findings from the 2026-11-09 standard-user field report

| Finding | Change |
|---|---|
| Default PhotoCraft preferences exceed 128 KB | Native preferences have a separate 8 MiB discovery limit; small GenGate records retain 128 KiB. Bad candidates are skipped; actual installation still validates preferences before writing. |
| Administrator's private runtime injected into a shared editor | Personal copy and normal-user runtime; account mismatch rejected before setup/update work. No broad ACL grants or full-setup elevation. |
| Access denied shown as missing/updating | Native metadata and process-launch errors distinguish access denial and include the path. Python engine launch and destination probes provide the same repair direction. |
| Stale paths retained in another profile | Native preference loading reconciles installation-owned paths with the current host state. Discovery clears inaccessible old paths. Restore does not reintroduce inaccessible previous runtimes. Other users' profiles are not edited. |
| ZIP uninstaller cannot find installation metadata | It uses discovery; when necessary the package's own Python can restore a selected legacy host without reading the administrator's private runtime. |
| Legacy system-wide uninstall needs elevation | Only the restore operation requests UAC for an unwritable legacy host. Explicit host selection survives the account switch. New personal installs do not need it. |
| Recovery files left after removal | Optional cleanup runs only after both executables match the recorded originals, with no state/journal left. Linked roots/folders are rejected. Documents, models and unrelated runtime content are retained. |
| Launcher MSI defaults to Program Files | README describes portable ZIP installation and the automatic personal-copy path for existing MSI hosts. |

## Validation

Evidence stays under `.runtime/standard-user-fix/`; packages under
`dist/standard-user-20261109*` are review candidates, not replacements for the
published 0.10.0 release. No user installation is changed by these checks.

- Full lightweight Python suite, native GenGate engine/UI suites, optimized
  editor/CLI, Clippy, layer and WASM checks.
- Real Windows ACL denial of executable launch and folder creation, on isolated
  fixtures, with ACL restoration in `finally`. No system/app permissions changed.
- Process/session account comparison exercised on the current Windows session;
  a different-account SID is a synthetic fixture. No second account was created
  and no interactive credential prompt was submitted.
- Packaged personal install from verified official shared-host files, large
  roaming preferences with stale Admin paths, original-source hash preservation,
  repeat update with Direct reuse, official refresh with lost add-on preferences,
  restore and optional recovery cleanup. Existing GPU Python is inspected only;
  no dependencies, model weights or CUDA inference are installed/run by tests.
- Native MCP checks preference repair with no installation state and with a new
  active runtime, while preserving the user's model directory.
