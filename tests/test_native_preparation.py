"""Synthetic compatibility fixtures; no native process or inference."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
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
