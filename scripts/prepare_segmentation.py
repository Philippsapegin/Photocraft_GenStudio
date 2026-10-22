"""Wire independent Direct segmentation into the existing native commands and tool."""
from pathlib import Path
import shutil
import re

ROOT = Path(__file__).resolve().parents[1]


def prepare(source, replace):
    engine, ui = source / "crates/engine/src", source / "crates/ui-egui/src"
    shutil.copyfile(ROOT / "photocraft/gengate_segmentation.rs", engine / "gengate_segmentation.rs")
    shutil.copyfile(ROOT / "photocraft/gengate_ui_tests.rs", ui / "gengate_ui_tests.rs")
    path=ui/"lib.rs"
    text=path.read_text(encoding="utf-8")
    if 'mod gengate_ui_tests;' not in text:
        path.write_text(text+'\n#[cfg(test)]\nmod gengate_ui_tests;\n',encoding="utf-8")
    for path, anchor, addition in ((engine / "lib.rs", "pub mod genstudio;", "pub mod gengate_segmentation;"), (engine / "commands.rs", "    v.extend(crate::genstudio::specs());", "    v.extend(crate::gengate_segmentation::specs());")):
        text=path.read_text(encoding="utf-8")
        lines=[row for row in text.splitlines() if row.strip()!=addition.strip()]
        text="\n".join(lines)+"\n"
        path.write_text(text.replace(anchor,anchor+"\n"+addition,1),encoding="utf-8")
    for name, function, kind in (("smartselect_cmds.rs", "select_subject", "subject"), ("select_extra_cmds.rs", "select_sky", "sky")):
        anchor = f"fn {function}(s: &mut Session, p: &Value) -> Result<Value> {{"
        if f'gengate_segmentation::run(s,p,"{kind}")' not in re.sub(r'\s+','',(engine/name).read_text(encoding="utf-8")):
            replace(engine / name, anchor, anchor + f'\n    if s.prefs().gen_studio.segmentation.enabled {{ return crate::gengate_segmentation::run(s,p,"{kind}"); }}')
    anchor = 'fn object_selection(s: &mut Session, p: &Value) -> Result<Value> {'
    if 'gengate_segmentation::run(s,p,"object")' not in re.sub(r'\s+','',(engine/"smartselect_cmds.rs").read_text(encoding="utf-8")):
        replace(engine / "smartselect_cmds.rs", anchor, anchor + '\n    if p.get("aiSelect").and_then(Value::as_bool)==Some(true) { return crate::gengate_segmentation::run(s,p,"object"); }')
    anchor = '    let sample_all = p.get("sampleAllLayers").and_then(Value::as_bool).unwrap_or(false);'
    if 'gengate_segmentation::run(s,p,"background")' not in re.sub(r'\s+','',(engine/"cutout_cmds.rs").read_text(encoding="utf-8")):
        replace(engine / "cutout_cmds.rs", anchor, '    if s.prefs().gen_studio.segmentation.enabled {return crate::gengate_segmentation::run(s,p,"background");}\n' + anchor)
    if 'pub object_ai_select:' not in (ui/"state.rs").read_text(encoding="utf-8"):
        replace(ui / "state.rs", "pub struct ToolOptions {", "pub struct ToolOptions {\n    pub object_ai_select: bool,")
        replace(ui / "state.rs", "            sample_all_layers: false,", "            sample_all_layers: false,\n            object_ai_select: false,")
    path = ui / "retouch_ui.rs"
    text = path.read_text(encoding="utf-8")
    start, end = text.index("pub fn finish_object_selection"), text.index("/// Reports a smart-selection", text.index("pub fn finish_object_selection"))
    part = text[start:end]
    if 'let ai_select' not in part:
        part = part.replace('    let sample_all =', '    let ai_select = app.ui.tool_options.object_ai_select && app.session.prefs().gen_studio.segmentation.enabled;\n    let sample_all =', 1)
        part = part.replace('    if w < 2.0 || h < 2.0 {', '    if w < 2.0 || h < 2.0 {\n        if ai_select { return; }', 1)
        part = part.replace('    let mode = if mods.alt {\n', '    let mode = if mods.alt && mods.shift {\n        "intersect"\n    } else if mods.alt {\n', 1)
        part = part.replace('"sampleAllLayers": sample_all}));\n    report_smart_result', '"sampleAllLayers": sample_all, "aiSelect": ai_select}));\n    report_smart_result')
        text = text[:start] + part + text[end:]
    anchor = '        Tool::ObjectSelection => {\n'
    if 'checkbox(ui, &mut o.object_ai_select' not in text:
        text = text.replace(anchor, anchor + '            ui.add_enabled_ui(app.session.prefs().gen_studio.segmentation.enabled, |ui| {crate::widgets::checkbox(ui, &mut o.object_ai_select, "Ai Select");}).response.on_hover_text("Enable AI segmentation in GenGate Settings");\n', 1)
    path.write_text(text, encoding="utf-8")
    path=ui/"control.rs"
    text=path.read_text(encoding="utf-8")
    if 'let object_ai_select = bool_field' not in text:
        text=re.sub(r'pub const UI_SET_FIELDS: \[&str; (\d+)\] = \[',lambda m:f'pub const UI_SET_FIELDS: [&str; {int(m.group(1))+1}] = [\n    "objectAiSelect",',text,count=1)
        text=text.replace('                let gradient_classic = bool_field(p, "gradientClassic")?;', '                let object_ai_select = bool_field(p, "objectAiSelect")?;\n                let gradient_classic = bool_field(p, "gradientClassic")?;',1)
        text=text.replace('                let gradient_before = app.ui.tool_options.clone();','                if let Some(enabled)=object_ai_select {app.ui.tool_options.object_ai_select=enabled;}\n                let gradient_before = app.ui.tool_options.clone();',1)
        path.write_text(text,encoding="utf-8")
    # Add the flag to Object Selection's reset alongside shared sampling options.
    path = ui / "tool_reset.rs"
    text = path.read_text(encoding="utf-8")
    anchor = 'Tool::QuickSelection | Tool::ObjectSelection => reset!(sample_all_layers, enhance_edge),'
    if anchor in text:
        text = text.replace(anchor, 'Tool::QuickSelection => reset!(sample_all_layers, enhance_edge),\n        Tool::ObjectSelection => reset!(sample_all_layers, enhance_edge, object_ai_select),', 1)
        path.write_text(text, encoding="utf-8")
    for path in (ui / "i18n").glob("*.tsv"):
        text = path.read_text(encoding="utf-8")
        for label in ("Prepare segmentation models", "Segmentation", "Enabled", "Detector", "Segmenter", "Python", "Ai Select"):
            if "\t" + label + "\t" not in text:
                text += "\n\t" + label + "\t" + label + "\n"
        path.write_text(text, encoding="utf-8")
