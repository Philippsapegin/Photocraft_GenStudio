"""Build-time source views for PhotoCraft's 0.17 file split.

Integration hooks still address the previous logical files. A view reads their
explicitly listed new sections together, then writes each section back in place.
The native module layout is retained; no facade files enter the Rust build.
"""
from pathlib import Path
import os


GROUPS = {
    'apps/photocraft/src/gpu_startup.rs': ['apps/photocraft/src/gpu_startup.rs', 'apps/photocraft/src/gpu_startup/tests.rs'],
    'crates/engine/src/commands.rs': ['crates/engine/src/cmd.rs'],
    'crates/engine/src/prefs.rs': ['crates/engine/src/prefs.rs', 'crates/engine/src/prefs/validate.rs', 'crates/engine/src/prefs/cmds.rs'],
    'crates/ui-egui/src/lib.rs': ['crates/ui-egui/src/lib.rs', 'crates/ui-egui/src/app.rs', 'crates/ui-egui/src/app/frame.rs'],
    'crates/ui-egui/src/state.rs': ['crates/ui-egui/src/state.rs', 'crates/ui-egui/src/state/tool_options.rs'],
    'crates/ui-egui/src/menus.rs': ['crates/ui-egui/src/menus.rs', 'crates/ui-egui/src/menus/state.rs', 'crates/ui-egui/src/menus/invoke.rs'],
    'crates/ui-egui/src/control.rs': ['crates/ui-egui/src/control.rs', 'crates/ui-egui/src/control/inspect.rs', 'crates/ui-egui/src/control/params.rs', 'crates/ui-egui/src/control/dispatch.rs'],
    'crates/ui-egui/src/prefs_ui.rs': ['crates/ui-egui/src/prefs/prefs_ui.rs', 'crates/ui-egui/src/prefs/prefs_ui/fields.rs', 'crates/ui-egui/src/prefs/prefs_ui/dialogs.rs'],
    'crates/ui-egui/src/panels.rs': ['crates/ui-egui/src/panels.rs', 'crates/ui-egui/src/panels/properties.rs'],
}
MOVED = {
    'retouch_ui.rs': 'tools/retouch_ui.rs',
    'tool_reset.rs': 'tools/tool_reset.rs',
    'shortcuts.rs': 'input/shortcuts.rs',
    'shortcut_dispatch.rs': 'input/shortcut_dispatch.rs',
    'layer_transfer.rs': 'panels/layer_transfer.rs',
}
END = '\0GENGATE-END\0'


class SourcePath(os.PathLike):
    def __init__(self, root, relative=''):
        self.root, self.relative = Path(root), Path(relative)

    def targets(self):
        relative = self.relative.as_posix()
        if relative in GROUPS:
            return [self.root / p for p in GROUPS[relative]]
        prefix = 'crates/engine/src/'
        if relative.startswith(prefix) and relative[len(prefix):].split('/')[0].split('.')[0].endswith('_cmds'):
            return [self.root / (prefix + 'cmd/' + relative[len(prefix):])]
        prefix = 'crates/ui-egui/src/'
        if relative.startswith(prefix) and relative[len(prefix):] in MOVED:
            return [self.root / prefix / MOVED[relative[len(prefix):]]]
        return [self.root / self.relative]

    def __truediv__(self, other):
        return SourcePath(self.root, self.relative / other)

    def __fspath__(self):
        return str(self.targets()[0])

    def __str__(self):
        return os.fspath(self)

    def __getattr__(self, name):
        # Logical names/parents preserve the existing anchor and stamp helpers.
        if name in ('name', 'parent', 'parents'):
            return getattr(self.root / self.relative, name)
        return getattr(self.targets()[0], name)

    def read_text(self, encoding='utf-8', **kwargs):
        paths = self.targets()
        if len(paths) == 1:
            return paths[0].read_text(encoding=encoding, **kwargs)
        return ''.join(self.marker(p) + '\n' + p.read_text(encoding=encoding, **kwargs) + END + '\n' for p in paths)

    def marker(self, path):
        return '\0GENGATE-FILE:' + path.relative_to(self.root).as_posix() + '\0'

    def write_text(self, text, encoding='utf-8', **kwargs):
        paths = self.targets()
        if len(paths) == 1:
            return paths[0].write_text(text, encoding=encoding, **kwargs)
        sections = []
        cursor = 0
        for path in paths:
            marker = self.marker(path) + '\n'
            if text.count(marker) != 1:
                raise ValueError('Integration lost a source section: ' + str(path))
            begin = text.index(marker, cursor)
            if cursor and text[cursor:begin].strip():
                raise ValueError('Integration inserted text between source sections')
            start = begin + len(marker)
            end = text.index(END, start)
            sections.append(text[start:end])
            cursor = end + len(END)
        prefix = text[:text.index(self.marker(paths[0]))]
        suffix = text[cursor:]
        sections[0] = prefix + sections[0] + (suffix if suffix.strip() else '')
        for path, section in zip(paths, sections):
            path.write_text(section, encoding=encoding, **kwargs)
        return len(text)


def source_view(source):
    source = Path(source)
    return SourcePath(source) if (source / 'crates/engine/src/cmd.rs').is_file() else source
