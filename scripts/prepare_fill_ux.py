"""Native AI fill queue and canvas feedback on top of the pinned Remove integration."""
import shutil
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def prepare(source, replace):
    engine, ui = source / "crates/engine/src", source / "crates/ui-egui/src"
    for src, dest in (("genstudio_queue.rs", engine / "genstudio_queue.rs"),
                      ("genstudio_fill_ui.rs", ui / "genstudio_fill_ui.rs")):
        shutil.copyfile(ROOT / "photocraft" / src, dest)
    for code, label in json.loads((ROOT/"photocraft/progress-locales.json").read_text(encoding="utf-8")).items():
        path=ui/"i18n"/(code+".tsv")
        text=path.read_text(encoding="utf-8")
        if "\tMask Progress Bars\t" not in text:
            path.write_text(text+"\n\tMask Progress Bars\t"+label+"\n",encoding="utf-8")
    attribution=source/"ATTRIBUTION.md"
    text=attribution.read_text(encoding="utf-8")
    if "Gen Studio labels and translations" not in text:
        attribution.write_text(text+'''

## Local Gen Studio integration

| Local path | Title | Author | Source | License |
| --- | --- | --- | --- | --- |
| `crates/ui-egui/src/i18n/*.tsv` (Gen Studio entries) | Gen Studio labels and translations | Gen Studio contributors | Original work; source catalogs in the parent project's `photocraft/` | MIT OR Apache-2.0; existing `LICENSE-translations.txt` retained |
''',encoding="utf-8")
    retouch = engine / "retouch_cmds.rs"
    text = retouch.read_text(encoding="utf-8")
    if "pub(crate) use remove::qwen::run_ai_with;" not in text:
        retouch.write_text(text+'\n#[cfg(test)]\npub(crate) use remove::qwen::run_ai_with;\n',encoding="utf-8")
    retouch_ui=ui/"retouch_ui.rs"
    text=retouch_ui.read_text(encoding="utf-8")
    helper=text.rfind("\nfn queue_ai_fill(")
    tests=text.find("\n#[cfg(test)]\nmod tests {")
    if helper>tests>=0:
        text=text[:tests]+text[helper:]+"\n"+text[tests:helper]
        retouch_ui.write_text(text,encoding="utf-8")
    stamp = source / ".genstudio-fill-ux-v1"
    if stamp.exists():
        return
    replace(engine / "lib.rs", "pub mod genstudio;", "pub mod genstudio;\npub mod genstudio_queue;")
    replace(engine / "commands.rs", "v.extend(crate::genstudio::specs());", "v.extend(crate::genstudio::specs());\n    v.extend(crate::genstudio_queue::specs());")
    replace(engine / "retouch_cmds.rs", "mod remove;", "mod remove;\npub(crate) use remove::qwen::{AiRequest, capture_ai, run_ai};")
    replace(engine / "retouch_cmds/remove.rs", "mod qwen;", "pub(crate) mod qwen;")
    parent = engine / "retouch_cmds/remove.rs"
    text = parent.read_text(encoding="utf-8")
    start = text.index('    if p.get("useQwen")')
    end = text.index('\n    let plan = plan(s, p)?;', start)
    text = text[:start] + '''    for key in ["useAiFill", "useQwen"] {
        if p.get(key).is_some_and(|v| !v.is_boolean()) { return Err(bad(CMD, format!("`{key}` must be a boolean"))); }
    }
    if flag(p, "useAiFill", flag(p, "useQwen", false)) { return qwen::remove(s, p); }
''' + text[end:]
    parent.write_text(text, encoding="utf-8")
    params = engine / "retouch_cmds.rs"
    text = params.read_text(encoding="utf-8").replace('"useQwen":bool=false (use the selected Gen Studio engine)',
        '"useAiFill":bool=false (use the selected Gen Studio engine; useQwen is a legacy alias)')
    params.write_text(text, encoding="utf-8")
    replace(engine / "jobs.rs", "        std::mem::take(&mut self.jobs.events)", "        crate::genstudio_queue::tick(self);\n        std::mem::take(&mut self.jobs.events)")
    for file in ("state.rs", "tool_reset.rs", "retouch_ui.rs"):
        path = ui / file
        path.write_text(path.read_text(encoding="utf-8").replace("remove_use_qwen", "remove_ai_fill"), encoding="utf-8")
    replace(ui / "state.rs", "    pub remove_ai_fill: bool,", '    #[serde(alias = "remove_use_qwen")]\n    pub remove_ai_fill: bool,')
    retouch = ui / "retouch_ui.rs"
    text = retouch.read_text(encoding="utf-8")
    text = text.replace('            crate::widgets::checkbox(ui, &mut o.remove_ai_fill, "Use Qwen");\n', '')
    line = '            crate::widgets::checkbox(ui, &mut o.remove_after_each_stroke, tl!("Remove after each stroke"));'
    text = text.replace(line,line+'\n            crate::widgets::checkbox(ui, &mut o.remove_ai_fill, "Ai Fill");')
    text = text.replace('"useQwen": o.remove_ai_fill','"useAiFill": o.remove_ai_fill')
    marker = '    match app.run(cmd, p) {'
    text = text.replace(marker, '''    if tool == Tool::Remove && o.remove_ai_fill {
        queue_ai_fill(app, p);
        return true;
    }
''' + marker, 1)
    marker = '    if let Err(e) = app.run("paint.remove", p) {'
    text = text.replace(marker, '''    if app.ui.tool_options.remove_ai_fill {
        queue_ai_fill(app, p);
        return true;
    }
''' + marker, 1)
    helper = '''
fn queue_ai_fill(app: &mut PhotocraftApp, params: Value) {
    if let Err(error) = app.run("genstudio.fill.enqueue",json!({"command":"paint.remove","params":params})) {
        app.ui.status = error;
        app.ui.status_error = true;
    } else {
        app.ui.status_error = false;
    }
}
'''
    text=text.replace('\n#[cfg(test)]\nmod tests {',helper+'\n#[cfg(test)]\nmod tests {',1)
    retouch.write_text(text, encoding="utf-8")
    replace(ui / "lib.rs", "pub mod genstudio_ui;", "pub mod genstudio_ui;\npub mod genstudio_fill_ui;")
    replace(ui / "lib.rs", "    pub(crate) remove_pending_doc:", "    pub(crate) ai_fill_overlays: crate::genstudio_fill_ui::Cache,\n    pub(crate) remove_pending_doc:")
    replace(ui / "lib.rs", "            remove_pending_doc: None,", "            ai_fill_overlays: Default::default(),\n            remove_pending_doc: None,")
    replace(ui / "canvas.rs", "        draw_drag_preview(app, &painter, &xf);\n        if app.session.active_index()", "        draw_drag_preview(app, &painter, &xf);\n        crate::genstudio_fill_ui::show(app, ui, &xf, idx);\n        if app.session.active_index()")
    replace(ui / "jobs_ui.rs", 'j.command == "paint.remove" && j.label == "Remove · Qwen"',
        'j.command.starts_with("genstudio.fill.") || j.label == "Remove · Ai Fill" || j.command == "paint.remove" && j.label == "Remove · Qwen"')
    replace(ui / "jobs_ui.rs", "    if !app.session.has_jobs() {", "    if !app.session.has_jobs() && !photocraft_engine::genstudio_queue::has_pending(&app.session) {")
    replace(ui / "jobs_ui.rs", "    // A focused open tab that ended", '''    for error in photocraft_engine::genstudio_queue::take_errors(&mut app.session) { notices::error(app,error); }
    // A focused open tab that ended''')
    replace(ui / "control.rs", '"genStudio": app.ui.gen_studio,', '"genStudio": app.ui.gen_studio,\n        "aiFillQueue": photocraft_engine::genstudio_queue::snapshot(&app.session),\n        "aiFillOverlays": app.ai_fill_overlays.info,')
    replace(ui / "menus.rs", '    ("genstudio.open",', '    ("genstudio.progress.set", "Mask Progress Bars", &[], None),\n    ("genstudio.open",')
    replace(ui / "menus.rs", '"genstudio.open" | "genstudio.models.browse" => true,', '"genstudio.open" | "genstudio.models.browse" | "genstudio.progress.set" => true,')
    replace(ui / "menus.rs", 'crate::genstudio_ui::menu(app, id)', 'crate::genstudio_ui::menu(app, id, &params)')
    stamp.write_text("Shared AI fill queue and canvas feedback\n", encoding="utf-8")
