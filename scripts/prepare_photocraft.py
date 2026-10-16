"""Apply the small Gen Studio integration to pinned PhotoCraft v0.8.2 sources."""
from pathlib import Path
import argparse
import json
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from prepare_fill_ux import prepare as prepare_fill_ux
from prepare_selection_fill import prepare as prepare_selection_fill


def replace(path, old, new):
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    if text.count(old) != 1:
        raise RuntimeError(f"PhotoCraft source changed: {path}: expected one integration point.")
    path.write_text(text.replace(old, new), encoding="utf-8")


def prepare(source):
    engine, ui = source / "crates/engine/src", source / "crates/ui-egui/src"
    shutil.copyfile(ROOT / "photocraft/genstudio.rs", engine / "genstudio.rs")
    shutil.copyfile(ROOT / "photocraft/genstudio_ui.rs", ui / "genstudio_ui.rs")
    (engine / "genstudio-defaults.json").write_text(json.dumps({
        "engine": "comfy", "comfyUrl": "http://127.0.0.1:8190", "comfyDir": "",
        "comfyPython": "", "directPython": "", "modelsDir": "", "runtimeRoot": "."
    }), encoding="utf-8")
    stamp = source / ".genstudio-integration-v1"
    # The suite owns its window under Filter > Plug-ins. Persist its state in
    # preferences without adding an unrelated general Preferences section.
    prefs = engine / "prefs.rs"
    existing = prefs.read_text(encoding="utf-8")
    existing = existing.replace('pub const SECTIONS: [(&str, &str); 19]', 'pub const SECTIONS: [(&str, &str); 18]')
    existing = existing.replace('    ("genStudio", "Gen Studio"),\n', '')
    prefs.write_text(existing, encoding="utf-8")
    menu_source = ui / "menus.rs"
    menu_text = menu_source.read_text(encoding="utf-8").replace('        "genstudio.open" | "genstudio.models.browse" => true,\n', '')
    enabled_line='        "genstudio.open" | "genstudio.models.browse" | "genstudio.progress.set" => true,'
    if enabled_line not in menu_text:
        menu_text=menu_text.replace('        "window.panel.activate" | "window.panel.layout" => true,',enabled_line+'\n        "window.panel.activate" | "window.panel.layout" => true,')
    menu_source.write_text(menu_text, encoding="utf-8")
    locales = json.loads((ROOT / "photocraft/menu-locales.json").read_text(encoding="utf-8"))
    for code, label in locales.items():
        catalog = ui / "i18n" / (code + ".tsv")
        contents = catalog.read_text(encoding="utf-8")
        for key, translation in (("Gen Studio…", "Gen Studio…"), ("Choose Models Folder", label)):
            if "\t" + key + "\t" not in contents:
                contents += "\n\t" + key + "\t" + translation + "\n"
        catalog.write_text(contents, encoding="utf-8")
    if stamp.exists():
        menu = ui / "menus.rs"
        text = menu.read_text(encoding="utf-8")
        if '("genstudio.models.browse",' not in text:
            text = text.replace('("genstudio.open", "Gen Studio…", &["Filter", "Plug-ins"], None),',
                '("genstudio.open", "Gen Studio…", &["Filter", "Plug-ins"], None),\n    ("genstudio.models.browse", "Choose Models Folder", &[], None),')
            menu.write_text(text, encoding="utf-8")
        prepare_remove(engine, ui)
        prepare_fill_ux(source, replace)
        prepare_selection_fill(source, replace)
        return
    replace(engine / "lib.rs", "pub mod plugin_cmds;", "pub mod plugin_cmds;\npub mod genstudio;")
    replace(engine / "lib.rs", "pub struct Session {", "pub struct Session {\n    pub gen_studio: genstudio::Runtime,")
    replace(engine / "commands.rs", "v.extend(crate::plugin_cmds::specs());", "v.extend(crate::plugin_cmds::specs());\n    v.extend(crate::genstudio::specs());")
    replace(engine / "prefs.rs", "pub struct Preferences {", "pub struct Preferences {\n    pub gen_studio: crate::genstudio::Settings,")
    replace(engine / "prefs.rs", '"general.colorPicker" => ColorPicker::NAMES,', '"genStudio.engine" => &["comfy", "direct"],\n        "genStudio.comfyStartup" | "genStudio.directStartup" => &["off", "appStart", "firstRequest"],\n        "general.colorPicker" => ColorPicker::NAMES,')
    replace(ui / "lib.rs", "pub mod plugin_ui;", "pub mod plugin_ui;\npub mod genstudio_ui;")
    replace(ui / "lib.rs", "dialogs::show(self, &ctx);", "dialogs::show(self, &ctx);\n        genstudio_ui::show(self, &ctx);")
    replace(ui / "control.rs", '"toolOptions": app.ui.tool_options,', '"toolOptions": app.ui.tool_options,\n        "genStudio": app.ui.gen_studio,')
    replace(ui / "state.rs", "pub struct UiState {", "pub struct UiState {\n    #[serde(default)]\n    pub gen_studio: crate::genstudio_ui::State,")
    replace(ui / "state.rs", "\n            tool: Tool::Brush,", "\n            tool: Tool::Brush,\n            gen_studio: Default::default(),")
    replace(ui / "menus.rs", 'pub const UI_COMMANDS: &[(&str, &str, &[&str], Option<&str>)] = &[', 'pub const UI_COMMANDS: &[(&str, &str, &[&str], Option<&str>)] = &[\n    ("genstudio.open", "Gen Studio…", &["Filter", "Plug-ins"], None),\n    ("genstudio.models.browse", "Choose Models Folder", &[], None),')
    anchor = 'pub(crate) fn invoke_unguarded(app: &mut PhotocraftApp, ctx: &egui::Context, id: &str, params: Value) -> Result<Value, String> {'
    replace(ui / "menus.rs", anchor, anchor + '\n    if let Some(result) = crate::genstudio_ui::menu(app, id) { return result; }')
    stamp.write_text("Gen Studio 0.3.0 on PhotoCraft v0.8.2\n", encoding="utf-8")
    prepare_remove(engine, ui)
    prepare_fill_ux(source, replace)
    prepare_selection_fill(source, replace)


def prepare_remove(engine, ui):
    shutil.copyfile(ROOT / "photocraft/genstudio_edit.rs", engine / "genstudio_edit.rs")
    child = engine / "retouch_cmds/remove"
    child.mkdir(exist_ok=True)
    shutil.copyfile(ROOT / "photocraft/remove_qwen.rs", child / "qwen.rs")
    if 'j.label == "Remove · Qwen"' not in (ui / "jobs_ui.rs").read_text(encoding="utf-8"):
        replace(ui / "jobs_ui.rs", "    if j.elapsed_ms < DIALOG_DELAY_MS {",
                '    if j.elapsed_ms < DIALOG_DELAY_MS || j.command == "paint.remove" && j.label == "Remove · Qwen" {')
    stamp = engine.parents[2] / ".genstudio-remove-v1"
    if stamp.exists():
        return
    replace(engine / "lib.rs", "pub mod genstudio;", "pub mod genstudio;\nmod genstudio_edit;")
    remove = engine / "retouch_cmds/remove.rs"
    replace(remove, 'const CMD: &str = "paint.remove";', 'mod qwen;\n\nconst CMD: &str = "paint.remove";')
    anchor = "pub(super) fn remove(s: &mut Session, p: &Value) -> Result<Value> {"
    replace(remove, anchor, anchor + '''
    if p.get("useQwen").is_some_and(|v| !v.is_boolean()) {
        return Err(bad(CMD, "`useQwen` must be a boolean"));
    }
    if flag(p, "useQwen", false) { return qwen::remove(s, p); }
''')
    replace(engine / "retouch_cmds.rs", '"sampleAllLayers":bool=false,"newLayer":bool=false (the fill goes',
            '"sampleAllLayers":bool=false,"useQwen":bool=false (use the selected Gen Studio engine),"newLayer":bool=false (the fill goes')
    replace(ui / "state.rs", "    pub remove_new_layer: bool,", "    pub remove_new_layer: bool,\n    pub remove_use_qwen: bool,")
    replace(ui / "state.rs", "            remove_new_layer: false,", "            remove_new_layer: false,\n            remove_use_qwen: false,")
    replace(ui / "retouch_ui.rs", 'json!({"sampleAllLayers": o.sample_all_layers, "newLayer": o.remove_new_layer})',
            'json!({"sampleAllLayers": o.sample_all_layers, "newLayer": o.remove_new_layer, "useQwen": o.remove_use_qwen})')
    replace(ui / "retouch_ui.rs", '            crate::widgets::checkbox(ui, &mut o.remove_new_layer, tl!("Create a new layer"));',
            '            crate::widgets::checkbox(ui, &mut o.remove_use_qwen, "Use Qwen");\n            crate::widgets::checkbox(ui, &mut o.remove_new_layer, tl!("Create a new layer"));')
    replace(ui / "tool_reset.rs", '        Tool::SpotHealing => reset!(spot_type, sample_all_layers),',
            '        Tool::Remove => reset!(sample_all_layers, remove_new_layer, remove_after_each_stroke, remove_use_qwen),\n        Tool::SpotHealing => reset!(spot_type, sample_all_layers),')
    reset = ui / "tool_reset.rs"
    contents = reset.read_text(encoding="utf-8").replace("        | Tool::Remove\n", "")
    reset.write_text(contents, encoding="utf-8")
    stamp.write_text("Qwen backend for the existing Remove Tool\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    prepare(parser.parse_args().source)
