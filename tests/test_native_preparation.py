"""Synthetic compatibility fixtures; no native process or inference."""
import importlib.util
from pathlib import Path
import unittest
import tempfile
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from prepare_updates import prepare_desktop_transport, prepare_update_notice
from prepare_photocraft_012 import anchors
from prepare_photocraft import replace
spec = importlib.util.spec_from_file_location("prepare_selection_fill", ROOT / "scripts/prepare_selection_fill.py")
preparer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preparer)
queue_spec = importlib.util.spec_from_file_location("prepare_fill_ux", ROOT / "scripts/prepare_fill_ux.py")
queue_preparer = importlib.util.module_from_spec(queue_spec)
queue_spec.loader.exec_module(queue_preparer)


class RemoveDispatchTests(unittest.TestCase):
    def test_current_and_legacy_dispatch_queue_before_starting_a_native_job(self):
        for dispatch in ('    let result = app.run(cmd, p);\n    match result {',
                         '    match app.run(cmd, p) {'):
            with self.subTest(dispatch=dispatch):
                source = 'pub fn finish_stroke(\n    p["target"] = target;\n' + dispatch + '\n}\n'
                changed = queue_preparer.prepare_remove_dispatch(source)
                self.assertLess(changed.index('queue_ai_fill(app, p);'), changed.index(dispatch))
                self.assertLess(changed.index('p["target"]'), changed.index('queue_ai_fill(app, p);'))
                self.assertEqual(queue_preparer.prepare_remove_dispatch(changed), changed)

    def test_unknown_dispatch_is_refused(self):
        with self.assertRaises((RuntimeError, ValueError)):
            queue_preparer.prepare_remove_dispatch('pub fn finish_stroke(\n    new_dispatch(p);\n}')


class UpdateTransportTests(unittest.TestCase):
    def test_0202_job_dialog_keeps_batch_and_save_exclusions(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'jobs_ui.rs'
            native = '    if j.command == "file.automate.batch" || j.elapsed_ms < DIALOG_DELAY_MS || app.jobs.saves.iter().any(|s| s.job == j.id) {\n        return;\n    }'
            path.write_text(native, encoding='utf-8')
            old = '    if j.elapsed_ms < DIALOG_DELAY_MS {'
            new = '    if j.elapsed_ms < DIALOG_DELAY_MS || j.command == "paint.remove" && j.label == "Remove · Qwen" {'
            replace(path, old, new)
            first = path.read_text(encoding='utf-8')
            self.assertIn('if j.command == "file.automate.batch" ||', first)
            self.assertIn('|| app.jobs.saves.iter().any(|s| s.job == j.id)', first)
            self.assertIn('j.label == "Remove · Qwen"', first)
            replace(path, old, new)
            self.assertEqual(path.read_text(encoding='utf-8'), first)

    def test_shared_update_result_keeps_the_native_dialog_and_adds_a_warning_before_install(self):
        original = '        #[cfg(not(target_arch = "wasm32"))]\n        app.updates_rt.updater.offer(release_of(release));\n    if app.ui.updates_open {\n        let dialog = craft_update::CheckResultDialog::new(&crate::UPDATE_APP, version);\n'
        changed = prepare_update_notice(original)
        self.assertIn('craft_update::CheckResultDialog::new', changed)
        self.assertLess(changed.index('photocraft_notice'), changed.index('app.updates_rt.updater.offer('))
        self.assertEqual(prepare_update_notice(changed), changed)

    def test_019_native_move_default_and_composite_guard_are_preserved(self):
        old='\n            tool: Tool::Brush,'
        a,b=anchors(Path('state.rs'), '\n            tool: Tool::Move,', old, old+'\n gen_studio: Default::default(),')
        self.assertIn('Tool::Move', a)
        self.assertNotIn('Tool::Brush', b)
        old='json!({"sampleAllLayers": o.sample_all_layers, "newLayer": o.remove_new_layer})'
        a,b=anchors(Path('retouch_ui.rs'), 'o.sample_all.remove o.remove_new_layer && composite', old, old.replace('})', ',"useQwen": o.remove_use_qwen})'))
        self.assertIn('o.remove_new_layer && composite', b)
        self.assertIn('o.sample_all.remove', a)

    def test_017_and_018_keep_native_transport_and_add_independent_check_once(self):
        for native in ('Some(crate::updates::service())', 'update_transport(std::env::var_os("FLATPAK_ID").as_deref())'):
            with self.subTest(native=native), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'services.rs'
                original = 'Services {\n        check_updates: ' + native + ',\n}\n'
                path.write_text(original, encoding='utf-8')
                prepare_desktop_transport(path, replace)
                first = path.read_text(encoding='utf-8')
                prepare_desktop_transport(path, replace)
                self.assertEqual(path.read_text(encoding='utf-8'), first)
                self.assertEqual(first.replace('\n        check_gengate_updates: Some(crate::gengate_updates_transport::service()),', ''), original)

    def test_unknown_native_transport_is_rejected_without_rewriting_it(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'services.rs'
            original = 'check_updates: new_upstream_transport(),\n'
            path.write_text(original, encoding='utf-8')
            with self.assertRaises(RuntimeError):
                prepare_desktop_transport(path, replace)
            self.assertEqual(path.read_text(encoding='utf-8'), original)


class ClearDispatchTests(unittest.TestCase):
    def fixture(self, current):
        result = '(id.clone(), *key == Key::Tab, false)' if current else '(id.clone(), *key == Key::Tab)'
        fallback = '\nlast.map(|(id, _)| (id.clone(), false, true));' if current else ''
        unpack = 'id, tab, last' if current else 'id, tab'
        return ('pub fn dispatch_pressed(\n'
                'egui::Event::Key { key, physical_key, pressed: true, modifiers, .. } => {\n'
                + result + fallback + '\nlet mut ran = false;\n'
                + 'let Some((' + unpack + ')) = next else { break };\n'
                + 'let owner = key_owner(app, ctx);\n'
                + 'dispatch_as(app, ctx, &id, last);\n}\n'
                + "/// Clear's key\n")

    def test_current_dispatch_retains_last_adjustment_settings_and_repeat(self):
        changed = preparer.prepare_clear_dispatch(self.fixture(True))
        self.assertIn('(id.clone(), *key == Key::Tab, false, *repeat)', changed)
        self.assertIn('(id.clone(), false, true, *repeat)', changed)
        self.assertIn('let Some((id, tab, last, repeat))', changed)
        self.assertIn('dispatch_as(app, ctx, &id, last)', changed)
        self.assertIn('if id == "edit.clear" && repeat && cleared_this_frame', changed)
        self.assertEqual(preparer.prepare_clear_dispatch(changed), changed)

    def test_earlier_dispatch_keeps_its_original_tuple_shape(self):
        changed = preparer.prepare_clear_dispatch(self.fixture(False))
        self.assertIn('(id.clone(), *key == Key::Tab, *repeat)', changed)
        self.assertIn('let Some((id, tab, repeat))', changed)
        self.assertEqual(preparer.prepare_clear_dispatch(changed), changed)

    def test_unknown_dispatch_is_refused(self):
        with self.assertRaises(RuntimeError):
            preparer.prepare_clear_dispatch(self.fixture(True).replace('(id.clone(), false, true)', 'changed_upstream()'))
