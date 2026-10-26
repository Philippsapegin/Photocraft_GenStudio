"""Selection AI layers, Properties gallery, and contextual prompt bar."""
import json
import shutil
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def prepare_clear_dispatch(text):
    if 'let mut cleared_this_frame = false;' in text:
        return text
    start=text.index('pub fn dispatch_pressed(')
    end=text.index("/// Clear's key",start)
    block=text[start:end]
    last_settings = '(id.clone(), *key == Key::Tab, false)' in block
    tuple_edits = (
        ('(id.clone(), *key == Key::Tab, false)', '(id.clone(), *key == Key::Tab, false, *repeat)'),
        ('(id.clone(), false, true)', '(id.clone(), false, true, *repeat)'),
        ('let Some((id, tab, last)) = next else { break };', 'let Some((id, tab, last, repeat)) = next else { break };'),
    ) if last_settings else (
        ('(id.clone(), *key == Key::Tab)', '(id.clone(), *key == Key::Tab, *repeat)'),
        ('let Some((id, tab)) = next else { break };', 'let Some((id, tab, repeat)) = next else { break };'),
    )
    edits=(
        ('egui::Event::Key { key, physical_key, pressed: true, modifiers, .. }',
         'egui::Event::Key { key, physical_key, pressed: true, modifiers, repeat, .. }'),
        *tuple_edits,
        ('let mut ran = false;', 'let mut ran = false;\n    let mut cleared_this_frame = false;'),
        ('let owner = key_owner(app, ctx);',
         '// Coalesce only autorepeated Clear in this frame. A delayed frame\n'
         '        // must not replay a long queue of full-resolution pixel edits.\n'
         '        // Separate presses and commands between clears retain their order.\n'
         '        if id == "edit.clear" && repeat && cleared_this_frame { continue; }\n'
         '        cleared_this_frame = id == "edit.clear";\n'
         '        let owner = key_owner(app, ctx);'),
    )
    for old,new in edits:
        if block.count(old)!=1:raise RuntimeError("Changed Clear autorepeat integration point: "+old)
        block=block.replace(old,new,1)
    return text[:start]+block+text[end:]


def prepare(source,replace):
    engine,ui=source/"crates/engine/src",source/"crates/ui-egui/src"
    shutil.copyfile(ROOT/"photocraft/genstudio_ai.rs",engine/"genstudio_ai.rs")
    shutil.copyfile(ROOT/"photocraft/genstudio_ai_ui.rs",ui/"genstudio_ai_ui.rs")
    shortcuts=ui/"shortcuts.rs"
    text=shortcuts.read_text(encoding="utf-8")
    guard='    if crate::genstudio_ai_ui::prompt_delete_frame(app, ctx) { return; }'
    if guard not in text and 'if crate::genstudio_ai_ui::prompt_delete_frame(app, ctx)' not in text:
        anchor='pub fn handle(app: &mut PhotocraftApp, ctx: &egui::Context) {'
        if anchor not in text:raise RuntimeError("Missing keyboard dispatcher integration point")
        shortcuts.write_text(text.replace(anchor,anchor+'\n'+guard,1),encoding="utf-8")
    dispatch=ui/"shortcut_dispatch.rs"
    text=dispatch.read_text(encoding="utf-8")
    dispatch.write_text(prepare_clear_dispatch(text),encoding="utf-8")
    labels=json.loads((ROOT/"photocraft/selection-locales.json").read_text(encoding="utf-8"))
    for code,values in labels.items():
        path=ui/"i18n"/(code+".tsv")
        text=path.read_text(encoding="utf-8")
        for key,label in zip(("Ai Fill","AI Prompt Bar"),values):
            if "\t"+key+"\t" not in text:text+="\n\t"+key+"\t"+label+"\n"
        path.write_text(text,encoding="utf-8")
    # Legacy gesture fixtures deliberately click where the new floating control
    # can appear. Test their canvas behavior with the optional bar hidden; the
    # dedicated AI harness exercises the enabled default and its actual buttons.
    for filename in ("marquee_tests.rs","canvas_menu_tests.rs"):
        path=ui/filename
        text=path.read_text(encoding="utf-8")
        anchor='app.ui.tool = tool;'
        if 'app.ui.gen_studio.hide_ai_bar = true;' not in text:
            text=text.replace(anchor,'app.ui.gen_studio.hide_ai_bar = true;\n    '+anchor,1)
            path.write_text(text,encoding="utf-8")
    stamp=source/".genstudio-selection-v1"
    if stamp.exists():return
    replace(engine/"lib.rs","pub mod genstudio;","pub mod genstudio;\npub mod genstudio_ai;")
    replace(engine/"commands.rs","v.extend(crate::genstudio::specs());","v.extend(crate::genstudio::specs());\n    v.extend(crate::genstudio_ai::specs());")
    replace(ui/"lib.rs","pub mod genstudio_ui;","pub mod genstudio_ui;\npub mod genstudio_ai_ui;")
    replace(ui/"lib.rs","    pub(crate) ai_fill_overlays:","    pub(crate) ai_layers: crate::genstudio_ai_ui::Cache,\n    pub(crate) ai_fill_overlays:")
    replace(ui/"lib.rs","            ai_fill_overlays: Default::default(),","            ai_layers: Default::default(),\n            ai_fill_overlays: Default::default(),")
    replace(ui/"canvas.rs","        crate::genstudio_fill_ui::show(app, ui, &xf, idx);","        crate::genstudio_fill_ui::show(app, ui, &xf, idx);\n        crate::genstudio_ai_ui::show_bar(app, ui, &xf, idx);")
    replace(ui/"control.rs",'"aiFillOverlays": app.ai_fill_overlays.info,','"aiFillOverlays": app.ai_fill_overlays.info,\n        "aiFillLayer": {"bar":app.ai_layers.bar_info,"properties":app.ai_layers.properties_info},')
    catalog=ui/"menu_catalog.rs"
    anchor='    (&["Edit"], "Fill…", Some("Shift+F5"), "edit.fill"),'
    replace(catalog,anchor,'    (&["Edit"], "Ai Fill", None, "edit.aiFill"),\n    (&["Edit"], "---", None, "---"),\n'+anchor)
    replace(ui/"menus.rs",'    ("genstudio.progress.set",','    ("genstudio.ai.bar.set", "AI Prompt Bar", &[], None),\n    ("genstudio.progress.set",')
    replace(ui/"menus.rs",'        "window.panel.activate" | "window.panel.layout" => true,','        "genstudio.ai.bar.set" => true,\n        "window.panel.activate" | "window.panel.layout" => true,')
    panels=ui/"panels.rs"
    if 'fn properties_body_inner(' not in panels.read_text(encoding='utf-8'):
        replace(panels,'    if !mask_target && !vector_target && !matches!(layer.content, LayerContent::Adjustment(_) | LayerContent::Fill(_)) {',
            '    if !mask_target && !vector_target && !photocraft_engine::genstudio_ai::is_layer(layer) && !matches!(layer.content, LayerContent::Adjustment(_) | LayerContent::Fill(_)) {')
        replace(panels,'                } else if let LayerContent::Adjustment(adj) = &layer.content {',
            '                } else if crate::genstudio_ai_ui::properties(app, ui, layer) {\n                } else if let LayerContent::Adjustment(adj) = &layer.content {')
    replace(panels,'    let is_adjustment = matches!(layer.content, LayerContent::Adjustment(_));',
        '    if crate::genstudio_ai_ui::properties(app, ui, layer) { return; }\n    let is_adjustment = matches!(layer.content, LayerContent::Adjustment(_));')
    stamp.write_text("Selection Ai Fill layers and persistent gallery\n",encoding="utf-8")
