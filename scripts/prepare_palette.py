"""Prepare the floating palette and a narrow, safe eframe creation hook."""
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tomllib

ROOT = Path(__file__).resolve().parents[1]
REV = "6d518fc799681571963842f95b55a6b68431b15a"


def toml_text(value):
    """Serialize Cargo's scalar/table/array subset without another build dependency."""
    lines = []
    def scalar(item):
        if isinstance(item, bool): return "true" if item else "false"
        if isinstance(item, (int, float)): return str(item)
        if isinstance(item, list): return "[" + ", ".join(scalar(v) for v in item) + "]"
        return json.dumps(item, ensure_ascii=False)
    def table(data, keys=(), array=False):
        if keys:
            name = ".".join(json.dumps(k) for k in keys)
            lines.append(("[[" if array else "[") + name + ("]]" if array else "]"))
        for key, item in data.items():
            if not isinstance(item, dict) and not (isinstance(item, list) and item and isinstance(item[0], dict)):
                lines.append(json.dumps(key) + " = " + scalar(item))
        for key, item in data.items():
            if isinstance(item, dict): table(item, (*keys, key))
            elif isinstance(item, list) and item and isinstance(item[0], dict):
                for row in item: table(row, (*keys, key), True)
        lines.append("")
    table(value)
    return "\n".join(lines)


def standalone_manifest(crate, workspace):
    value = deepcopy(crate)
    for key, item in list(value["package"].items()):
        if isinstance(item, dict) and item.get("workspace"):
            value["package"][key] = workspace["package"][key]
    value["package"]["include"] = ["LICENSE-APACHE", "LICENSE-MIT", "**/*.rs", "Cargo.toml", "data/icon.png"]
    value["lints"] = deepcopy(workspace.get("lints", {}))
    def dependencies(data):
        for key, item in data.items():
            if key in ("dependencies", "dev-dependencies", "build-dependencies"):
                for name, spec in list(item.items()):
                    if isinstance(spec, dict) and spec.get("workspace"):
                        base = workspace["dependencies"][name]
                        base = {"version": base} if isinstance(base, str) else deepcopy(base)
                        extra = {k: v for k, v in spec.items() if k != "workspace"}
                        features = list(dict.fromkeys(base.get("features", []) + extra.pop("features", [])))
                        base.update(extra)
                        if features: base["features"] = features
                        base.pop("path", None)
                        item[name] = base
            elif isinstance(item, dict): dependencies(item)
    dependencies(value)
    value["workspace"] = {}
    return value


def prepare_framework(source):
    cache = Path(os.environ.get("CARGO_HOME", Path.home() / ".cargo")) / "git/checkouts"
    candidates = list(cache.glob("egui-*/" + REV[:7] + "/crates/eframe"))
    if not candidates:
        subprocess.run(["cargo", "fetch", "--locked", "--manifest-path", str(source / "Cargo.toml")], check=True)
        candidates = list(cache.glob("egui-*/" + REV[:7] + "/crates/eframe"))
    if len(candidates) != 1:
        raise RuntimeError("Cannot locate the pinned eframe source for the palette hook")
    original = candidates[0]
    git_root = original.parents[1]
    revision = subprocess.check_output(["git", "-C", str(git_root), "rev-parse", "HEAD"], text=True).strip()
    if revision != REV: raise RuntimeError("Unexpected eframe revision")
    target = source / ".gengate-build/eframe"
    shutil.copytree(original, target, dirs_exist_ok=True)
    workspace = tomllib.loads((git_root / "Cargo.toml").read_text(encoding="utf-8"))["workspace"]
    manifest = standalone_manifest(tomllib.loads((original / "Cargo.toml").read_text(encoding="utf-8")), workspace)
    (target / "Cargo.toml").write_text(toml_text(manifest), encoding="utf-8")
    for name in ("LICENSE-APACHE", "LICENSE-MIT"):
        shutil.copyfile(git_root / name, target / name)
    hooks = target / "src/native/viewport_hooks.rs"
    text = hooks.read_text(encoding="utf-8")
    anchor = "pub trait ViewportHooks: Send + Sync {"
    if text.count(anchor) != 1: raise RuntimeError("eframe viewport hook changed")
    text = text.replace(anchor, anchor + """
    /// Customize safe native attributes before creating a child viewport.
    fn window_attributes(&self, _ctx: &egui::Context, _viewport: egui::ViewportId,
        _attributes: &mut winit::window::WindowAttributes) {}
""")
    hooks.write_text(text, encoding="utf-8")
    path = target / "src/native/wgpu_integration.rs"
    text = path.read_text(encoding="utf-8")
    anchor = "match egui_winit::create_window(egui_ctx, event_loop, &self.builder) {"
    if text.count(anchor) != 1: raise RuntimeError("eframe child-window creation changed")
    text = text.replace(anchor, """let mut attributes = egui_winit::apply_monitor_to_window_attributes(
            egui_winit::create_winit_window_attributes(egui_ctx, self.builder.clone()), &self.builder, event_loop);
        super::viewport_hooks::with(|hooks| hooks.window_attributes(egui_ctx, viewport_id, &mut attributes));
        match event_loop.create_window(attributes).inspect(|window| {
            egui_winit::apply_viewport_builder_to_window(egui_ctx, window, &self.builder);
        }) {""")
    # The pinned immediate-viewport path paints with an empty capture list and
    # never drains clipboard actions. Use the regular viewport's same safe path.
    anchor = """    let clipped_primitives = egui_ctx.tessellate(shapes, pixels_per_point);
    painter.paint_and_update_textures("""
    if text.count(anchor) != 1: raise RuntimeError("eframe immediate viewport painting changed")
    text = text.replace(anchor, """    let mut screenshot_commands = vec![];
    viewport.actions_requested.retain(|action| {
        if let ActionRequested::Screenshot(callback) = action {
            screenshot_commands.push(callback.clone()); false
        } else { true }
    });
    let clipped_primitives = egui_ctx.tessellate(shapes, pixels_per_point);
    painter.paint_and_update_textures(""")
    anchor = """        &mut viewport.pending_delta,
        vec![],
        window,
    );"""
    if text.count(anchor) != 1: raise RuntimeError("eframe immediate viewport captures changed")
    text = text.replace(anchor, """        &mut viewport.pending_delta,
        screenshot_commands,
        window,
    );
    for action in viewport.actions_requested.drain(..) {
        match action {
            ActionRequested::Screenshot(_) | ActionRequested::PaintWhileHidden => {}
            ActionRequested::Cut => egui_winit.request_cut(window),
            ActionRequested::Copy => egui_winit.request_copy(window),
            ActionRequested::Paste => egui_winit.request_paste(window),
        }
    }""")
    path.write_text(text, encoding="utf-8")
    path = source / "Cargo.toml"
    text = path.read_text(encoding="utf-8")
    text, count = re.subn(r'^eframe = \{ git = "https://github.com/storytold/egui.git", rev = "' + REV + r'" \}$',
                          'eframe = { path = ".gengate-build/eframe" }', text, flags=re.M)
    if count != 1 and 'eframe = { path = ".gengate-build/eframe" }' not in text:
        raise RuntimeError("PhotoCraft eframe patch changed")
    git_patch = '[patch."https://github.com/storytold/egui.git"]'
    if git_patch not in text:
        text += '\n' + git_patch + '\neframe = { path = ".gengate-build/eframe" }\n'
    path.write_text(text, encoding="utf-8")
    lock = source / "Cargo.lock"
    text = lock.read_text(encoding="utf-8")
    entries = text.split("[[package]]")
    found = False
    kept = [entries[0]]
    for entry in entries[1:]:
        if entry.lstrip().startswith('name = "eframe"\n'):
            if found: continue
            found = True
            entry = re.sub(r'^source = .*\n', '', entry, flags=re.M)
        entry = re.sub(r'"eframe 0\.37\.0(?: \(git\+[^"\n]+\))?"', '"eframe"', entry)
        kept.append(entry)
    lock.write_text('[[package]]'.join(kept), encoding="utf-8")


def prepare(source, replace):
    source = Path(source)
    prepare_framework(source)
    ui = source / "crates/ui-egui/src"
    desktop = source / "apps/photocraft"
    shutil.copyfile(ROOT / "photocraft/genstudio_palette.rs", ui / "genstudio_palette.rs")
    shutil.copyfile(ROOT / "photocraft/genstudio_palette_native.rs", desktop / "src/genstudio_palette_native.rs")
    for path, anchor, addition in (
        (ui / "lib.rs", "pub mod genstudio_ui;", "pub mod genstudio_palette;"),
        (desktop / "src/main.rs", "mod window_frame;", "mod genstudio_palette_native;"),
    ):
        if addition not in path.read_text(encoding="utf-8"): replace(path, anchor, anchor + "\n" + addition)
    replace(desktop / "src/main.rs", "        window_builder: Some(Box::new(restored_window)),",
            "        window_builder: Some(Box::new(restored_window)),\n        viewport_hooks: Some(std::sync::Arc::new(genstudio_palette_native::Hooks::default())),")
    path = desktop / "Cargo.toml"
    text = path.read_text(encoding="utf-8")
    heading = "[target.'cfg(target_os = \"windows\")'.dependencies]"
    addition = 'winit = { version = "=0.30.13", default-features = false }'
    if heading + "\n" + addition not in text: text = text.replace(heading, heading + "\n" + addition)
    path.write_text(text, encoding="utf-8")
    path = desktop / "src/window_frame.rs"
    text = path.read_text(encoding="utf-8")
    # Normalize our two wrappers before insertion: rustfmt changes their layout,
    # so matching an unformatted replacement verbatim is not an idempotency test.
    text = re.sub(r'#\[cfg\(target_os = "windows"\)\]\s*pub\(crate\) fn palette_frame\(target: isize\)\s*\{\s*platform::palette_frame\(target\);\s*\}\s*', '', text)
    text = re.sub(r'pub\(super\) fn palette_frame\(target: isize\)\s*\{\s*if let Some\(hwnd\) = find_window\(target\)\s*\{\s*strip\(&hwnd\);\s*\}\s*\}\s*', '', text)
    path.write_text(text, encoding="utf-8")
    replace(path, "pub use platform::service;", "pub use platform::service;\n#[cfg(target_os = \"windows\")]\npub(crate) fn palette_frame(target: isize) { platform::palette_frame(target); }")
    replace(path, "fn reapply(hwnd: &HWND) {", "pub(super) fn palette_frame(target: isize) { if let Some(hwnd) = find_window(target) { strip(&hwnd); } }\n\n    fn reapply(hwnd: &HWND) {")
    # Both captured root releases and releases delivered to the palette can finish a layer drag.
    path = ui / "panels/layer_transfer.rs"
    text = path.read_text(encoding="utf-8")
    if "pub(crate) fn finish_palette" not in text:
        text = text.replace("fn clear(ctx: &egui::Context)", "pub(crate) fn clear(ctx: &egui::Context)")
        anchor = "/// The document under screen point"
        code = '''pub(crate) fn finish_palette(app: &mut PhotocraftApp, ctx: &egui::Context) {
    let Some(t) = get(ctx) else { return };
    if ctx.input(|i| i.pointer.any_released()) {
        if let Some(layer) = t.layers.first() { crate::genstudio_canvas::layer_drop(app,ctx,t.source.0,*layer); }
        clear(ctx);
        ctx.data_mut(|d|d.remove::<u64>(egui::Id::unique("layer-drag")));
    } else if let Some(p) = ctx.pointer_hover_pos() { ghost(ctx,p,&t.label); }
}

'''
        text = text.replace(anchor, code + anchor, 1)
    path.write_text(text, encoding="utf-8")
