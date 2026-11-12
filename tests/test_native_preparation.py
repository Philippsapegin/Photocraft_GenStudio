"""Synthetic compatibility fixtures; no native process or inference."""
import importlib.util
from pathlib import Path
import unittest
import tempfile
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from prepare_updates import prepare_desktop_transport, prepare_update_notice
from prepare_photocraft_012 import anchors, prepare_preferences_tests
from prepare_photocraft import replace
from prepare_studio import studio_enabled, preferences_dialog_hooks
spec = importlib.util.spec_from_file_location("prepare_selection_fill", ROOT / "scripts/prepare_selection_fill.py")
preparer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preparer)
queue_spec = importlib.util.spec_from_file_location("prepare_fill_ux", ROOT / "scripts/prepare_fill_ux.py")
queue_preparer = importlib.util.module_from_spec(queue_spec)
queue_spec.loader.exec_module(queue_preparer)


class RemoveDispatchTests(unittest.TestCase):
    def test_remove_stroke_fixture_adapts_only_the_new_line_flag(self):
        fixture = 'assert!(crate::retouch_ui::finish_stroke(app, tool, points, egui::Modifiers::NONE));'
        legacy = 'pub fn finish_stroke(app: App, tool: Tool, points: Points, mods: egui::Modifiers) -> bool {'
        modern = legacy.replace('Modifiers)', 'Modifiers, line: bool)')
        self.assertEqual(queue_preparer.prepare_remove_tests(fixture, legacy), fixture)
        changed = queue_preparer.prepare_remove_tests(fixture, modern)
        self.assertIn('egui::Modifiers::NONE, false));', changed)
        self.assertEqual(queue_preparer.prepare_remove_tests(changed, modern), changed)

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


class PreferencesHookTests(unittest.TestCase):
    def test_modern_confirm_keeps_live_merge_and_gengate_validation(self):
        original = '''fn committed_values(app: App, f: Fields) {}
        && committed_values(app, fields) != Value::Object(preference_values(app.session.prefs()))
            let values = committed_values(app, f);
            app.run("prefs.set", json!({"path": "", "value": values}))'''
        changed = preferences_dialog_hooks(original)
        self.assertIn('fields.insert("values".into(), committed_values(app, f));', changed)
        self.assertIn('crate::genstudio_ui::confirm_preferences(app, &fields)', changed)
        self.assertIn('fields.get("__genstudioView")', changed)
        self.assertEqual(preferences_dialog_hooks(changed), changed)

    def test_legacy_hook_is_idempotent_and_unknown_contract_is_refused(self):
        original = '''is_preferences(fields) && fields.get("values").and_then(Value::as_object).is_some_and(|values| *values != preference_values(app.session.prefs()))
            let values = f.get("values").cloned().unwrap_or(Value::Null);
            app.run("prefs.set", json!({"path": "", "value": values}))'''
        changed = preferences_dialog_hooks(original)
        self.assertIn('crate::genstudio_ui::confirm_preferences(app,f)', changed)
        self.assertEqual(preferences_dialog_hooks(changed), changed)
        with self.assertRaisesRegex(ValueError, 'preferences hook changed'):
            preferences_dialog_hooks('new_preferences_api()')


class UpdateTransportTests(unittest.TestCase):
    def test_022_update_notice_precedes_shared_result_without_native_offer(self):
        native = '''        #[cfg(not(target_arch = "wasm32"))]
        {
            let updater = &mut app.updates_rt.updater;
            updater.offer(release_of(release));
            // While an update installs or waits for the app to close, or while the launcher keeps
            // this version, no prompt opens: a manual check reports that state in the result window.
            if std::mem::take(&mut app.updates_rt.requested) && !updater.prompt_open() {
                updater.check_now(ctx);
            }
        }
    let status = shown_status(app);
    let dialog = craft_update::CheckResultDialog::new(&crate::UPDATE_APP, VERSION);
'''
        changed = prepare_update_notice(native)
        self.assertNotIn('updater.offer(', changed)
        self.assertNotIn('updater.check_now(', changed)
        self.assertLess(changed.index('gengate_updates_ui::photocraft_notice'),
                        changed.index('let status = shown_status(app);'))
        self.assertIn('craft_update::CheckResultDialog::new', changed)
        self.assertEqual(prepare_update_notice(changed), changed)

    def test_021_preferences_oracle_keeps_native_rows_and_adds_gengate_once(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'crates/ui-egui/src/preferences_submenu_tests.rs'
            path.parent.mkdir(parents=True)
            original = '            "Plugins…",\n            "Type…",\n            "edit.preferences.plugIns",\n            "edit.preferences.type",'
            path.write_text(original, encoding='utf-8')
            prepare_preferences_tests(root)
            changed = path.read_text(encoding='utf-8')
            self.assertEqual(changed, original.replace('"Plugins…",', '"Plugins…",\n            "GenGate Settings…",').replace('"edit.preferences.plugIns",', '"edit.preferences.plugIns",\n            "edit.preferences.genStudio",'))
            prepare_preferences_tests(root)
            self.assertEqual(path.read_text(encoding='utf-8'), changed)

    def test_repeated_studio_preparation_removes_duplicate_enabled_arms(self):
        original='pub fn is_enabled() {\n    match id {\n        "genstudio.studio.inspect" => true,\n        "genstudio.studio.inspect" | "genstudio.studio.submit" => true,\n        "native.command" => true,\n    }\n}'
        changed=studio_enabled(original,('inspect','submit'))
        self.assertEqual(changed.count('"genstudio.studio.inspect" => true,'),1)
        self.assertEqual(changed.count('"genstudio.studio.submit" => true,'),1)
        self.assertIn('"native.command" => true,',changed)
        self.assertEqual(studio_enabled(changed,('inspect','submit')),changed)

    def test_021_plugin_labels_preserve_the_native_spelling(self):
        for name, native, old in (
                ('prefs.rs', '        section!("edit.preferences.plugIns", "Plugins…"),', '        section!("edit.preferences.plugIns", "Plug-ins…"),'),
                ('menu_catalog.rs', '    (&["Edit", "Preferences"], "Plugins…", None, "edit.preferences.plugIns"),', '    (&["Edit", "Preferences"], "Plug-ins…", None, "edit.preferences.plugIns"),'),
                ('menus.rs', '("genstudio.open", "Gen Studio…", &["Filter", "Plugins"], None),', '("genstudio.open", "Gen Studio…", &["Filter", "Plug-ins"], None),')):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / name
                path.write_text(native, encoding='utf-8')
                replace(path, old, old + '\n// GenGate hook')
                changed = path.read_text(encoding='utf-8')
                self.assertEqual(changed, native + '\n// GenGate hook')
                replace(path, old, old + '\n// GenGate hook')
                self.assertEqual(path.read_text(encoding='utf-8'), changed)

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

    def test_021_job_dialog_keeps_place_progress_in_status_bar(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'jobs_ui.rs'
            native = '    if j.command == "file.automate.batch" || j.elapsed_ms < DIALOG_DELAY_MS || place || app.jobs.saves.iter().any(|s| s.job == j.id) {\n        return;\n    }'
            path.write_text(native, encoding='utf-8')
            old = '    if j.elapsed_ms < DIALOG_DELAY_MS {'
            new = '    if j.elapsed_ms < DIALOG_DELAY_MS || j.command == "paint.remove" && j.label == "Remove · Qwen" {'
            replace(path, old, new)
            changed = path.read_text(encoding='utf-8')
            self.assertIn('|| place || app.jobs.saves.iter().any(|s| s.job == j.id)', changed)
            self.assertIn('if j.command == "file.automate.batch" ||', changed)
            self.assertIn('j.label == "Remove · Qwen"', changed)
            replace(path, old, new)
            self.assertEqual(path.read_text(encoding='utf-8'), changed)

    def test_shared_update_result_routes_available_updates_through_gengate(self):
        original = '        #[cfg(not(target_arch = "wasm32"))]\n        app.updates_rt.updater.offer(release_of(release));\n    if app.ui.updates_open {\n        let dialog = craft_update::CheckResultDialog::new(&crate::UPDATE_APP, version);\n'
        changed = prepare_update_notice(original)
        self.assertIn('craft_update::CheckResultDialog::new', changed)
        self.assertLess(changed.index('photocraft_notice'), changed.index('gengate_updates_ui::open_photocraft_update'))
        self.assertNotIn('app.updates_rt.updater.offer(', changed)
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
