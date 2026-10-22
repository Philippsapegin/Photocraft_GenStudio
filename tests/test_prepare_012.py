import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("prepare_012", ROOT / "scripts/prepare_photocraft_012.py")
compat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compat)


class Prepare012Tests(unittest.TestCase):
    def test_shared_shell_catalog_keeps_native_commands_and_repeated_preparation_is_stable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            engine, ui = root / "crates/engine/src", root / "crates/ui-egui/src"
            engine.mkdir(parents=True)
            ui.mkdir(parents=True)
            table = 'pub const SHELL_COMMANDS: &[(&str, &str, &[&str], Option<&str>)] = &[\n    ("file.save", "Save", &["File"], None),\n];'
            (engine / "shell_commands.rs").write_text(table)
            menu = ui / "menus.rs"
            menu.write_text(compat.ALIAS)
            (ui / "tool_reset.rs").write_text('Tool::ObjectSelection => reset!(enhance_edge, object_finder),')
            (ui / "retouch_ui.rs").write_text('if (w < 2.0 || h < 2.0) && app.ui.tool_options.object_finder {')
            (ui / "genstudio_ui.rs").write_text('app.ui.tool_options.sample_all_layers = true;')
            (ui / "prefs_ui.rs").write_text('// Section list.\n        ui.vertical(|ui| {\n            ui.set_width(170.0);\n        });\n        // The settings fill')
            (ui / "i18n").mkdir()
            (ui / "i18n/sr.tsv").write_text('')
            remove = engine / "retouch_cmds/remove"
            remove.mkdir(parents=True)
            (remove / "qwen.rs").write_text('let hole = hole(plan, None);')
            compat.before(root)
            text = menu.read_text()
            menu.write_text(text.replace('\n];', '\n    ("genstudio.open", "GenGate", &[], None),\n];'))
            compat.after(root)
            self.assertEqual(menu.read_text(), compat.ALIAS)
            self.assertIn('"file.save"', (engine / "shell_commands.rs").read_text())
            self.assertIn('"genstudio.open"', (engine / "shell_commands.rs").read_text())
            self.assertIn('!ai_select', (ui / "retouch_ui.rs").read_text())
            self.assertIn('object_ai_select', (ui / "tool_reset.rs").read_text())
            self.assertIn('sample_all.remove', (ui / "genstudio_ui.rs").read_text())
            self.assertIn('None)?;', (remove / "qwen.rs").read_text())
            self.assertIn('id_salt("prefs-sections")', (ui / "prefs_ui.rs").read_text())
            self.assertIn('\tAi Fill\tAI popuna', (ui / "i18n/sr.tsv").read_text(encoding='utf-8'))
            before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            compat.before(root)
            compat.after(root)
            self.assertEqual(before, {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()})

    def test_older_host_without_shared_catalog_is_untouched(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            menu = root / "crates/ui-egui/src/menus.rs"
            menu.parent.mkdir(parents=True)
            menu.write_text('older native table')
            compat.before(root)
            compat.after(root)
            self.assertEqual(menu.read_text(), 'older native table')
