//! Generation workspace. References are session data; model work stays in the host.
use crate::PhotocraftApp;
use base64::Engine as _;
use egui::{ColorImage, Rect, Sense, TextureHandle, TextureOptions, pos2, vec2};
use photocraft_codecs::Image;
use photocraft_engine::{genstudio_generate as core, jobs::JobId};
use serde_json::{Value, json};
use std::sync::{Arc, Mutex};

#[derive(Clone, Debug, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(default)]
pub struct Workspace {
    pub prompt: String,
    pub batch: u8,
    pub resolution: u32,
    pub monospace: bool,
}
impl Default for Workspace {
    fn default() -> Self {
        Self { prompt: String::new(), batch: 1, resolution: 1024, monospace: false }
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
enum Slot {
    Main,
    Extra(usize),
}
struct Reference {
    id: u64,
    name: String,
    image: Image,
    png: String,
    texture: Option<TextureHandle>,
    selection: bool,
}
struct Loaded {
    slot: Slot,
    ticket: u64,
    name: String,
    result: Result<(Image, String), String>,
    selection: bool,
}
#[cfg(not(target_arch = "wasm32"))]
static LOAD_LOCK: Mutex<()> = Mutex::new(());
#[cfg(not(target_arch = "wasm32"))]
static LOAD_COUNT: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
#[cfg(not(target_arch = "wasm32"))]
struct LoadPermit;
#[cfg(not(target_arch = "wasm32"))]
impl Drop for LoadPermit {
    fn drop(&mut self) {
        LOAD_COUNT.fetch_sub(1, std::sync::atomic::Ordering::Relaxed);
    }
}
#[derive(Default)]
pub struct Cache {
    main: Option<Reference>,
    extras: [Option<Reference>; 9],
    next: u64,
    loading: std::collections::HashMap<Slot, u64>,
    inbox: Arc<Mutex<Vec<Loaded>>>,
    rects: Vec<(Slot, Rect)>,
    pub info: Value,
    prompt_rect: Option<Rect>,
    capture: Option<(JobId, u64)>,
    selection_doc: Option<std::sync::Weak<photocraft_doc::Document>>,
}
#[derive(Clone)]
struct RefDrag(u64);
fn err(e: impl std::fmt::Display) -> String {
    e.to_string()
}
fn get(cache: &Cache, slot: Slot) -> Option<&Reference> {
    match slot {
        Slot::Main => cache.main.as_ref(),
        Slot::Extra(i) => cache.extras.get(i).and_then(Option::as_ref),
    }
}
fn get_mut(cache: &mut Cache, slot: Slot) -> Option<&mut Reference> {
    match slot {
        Slot::Main => cache.main.as_mut(),
        Slot::Extra(i) => cache.extras.get_mut(i).and_then(Option::as_mut),
    }
}
fn ids(cache: &Cache) -> Vec<u64> {
    cache.main.iter().chain(cache.extras.iter().filter_map(Option::as_ref)).map(|r| r.id).collect()
}
fn next_slot(cache: &Cache) -> Option<Slot> {
    cache.extras.iter().enumerate().find(|(i, r)| r.is_none() && !cache.loading.contains_key(&Slot::Extra(*i))).map(|(i, _)| Slot::Extra(i))
}
fn renumber(prompt: &str, before: &[u64], after: &[u64]) -> String {
    let mut out = String::new();
    let mut rest = prompt;
    while let Some(start) = rest.find("[Image ") {
        out.push_str(rest.get(..start).unwrap_or_default());
        let tail = rest.get(start..).unwrap_or_default();
        if let Some(end) = tail.find(']')
            && let Some(number) = tail.get(7..end).and_then(|s| s.parse::<usize>().ok())
        {
            if let Some(id) = number.checked_sub(1).and_then(|n| before.get(n)) {
                if let Some(index) = after.iter().position(|n| n == id) {
                    out.push_str(&format!("[Image {}]", index + 1));
                }
            } else {
                out.push_str(tail.get(..=end).unwrap_or_default());
            }
            rest = tail.get(end + 1..).unwrap_or_default();
        } else {
            out.push_str("[Image ");
            rest = tail.get(7..).unwrap_or_default();
        }
    }
    out.push_str(rest);
    out
}
pub fn compiled_prompt(text: &str, count: usize) -> Result<String, String> {
    let mut out = String::new();
    let mut rest = text;
    while let Some(start) = rest.find("[Image ") {
        out.push_str(rest.get(..start).unwrap_or_default());
        let tail = rest.get(start..).unwrap_or_default();
        let end = tail.find(']').ok_or("Complete or remove the reference chip")?;
        let number =
            tail.get(7..end).and_then(|s| s.parse::<usize>().ok()).filter(|n| *n >= 1 && *n <= count).ok_or("The prompt refers to an image that is missing")?;
        out.push_str(&format!("image {number}"));
        rest = tail.get(end + 1..).unwrap_or_default();
    }
    out.push_str(rest);
    Ok(out)
}
fn insert(text: &mut String, at: usize, number: usize) -> usize {
    let byte = text.char_indices().nth(at).map_or(text.len(), |(index, _)| index);
    let chip = format!("[Image {number}]");
    text.insert_str(byte, &chip);
    at + chip.chars().count()
}
fn start_load(
    cache: &mut Cache,
    slot: Slot,
    name: String,
    selection: bool,
    load: impl FnOnce() -> Result<Vec<u8>, String> + Send + 'static,
) -> Result<(), String> {
    #[cfg(not(target_arch = "wasm32"))]
    let permit = {
        if LOAD_COUNT.fetch_add(1, std::sync::atomic::Ordering::Relaxed) >= 12 {
            LOAD_COUNT.fetch_sub(1, std::sync::atomic::Ordering::Relaxed);
            return Err("Wait for the reference images to finish loading".into());
        }
        LoadPermit
    };
    cache.next = cache.next.wrapping_add(1);
    let ticket = cache.next;
    cache.loading.insert(slot, ticket);
    let inbox = cache.inbox.clone();
    #[cfg(not(target_arch = "wasm32"))]
    {
        let worker = inbox.clone();
        if let Err(e) = std::thread::Builder::new().name("genstudio-reference".into()).spawn(move || {
            let _permit = permit;
            let _serial = LOAD_LOCK.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
            let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                load()
                    .and_then(|bytes| core::prepare_reference(&bytes).map_err(err))
                    .and_then(|image| core::encoded(&image).map(|png| (image, png)).map_err(err))
            }))
            .unwrap_or_else(|_| Err("Could not read this reference image".into()));
            worker.lock().unwrap_or_else(std::sync::PoisonError::into_inner).push(Loaded { slot, ticket, name, result, selection });
        }) {
            cache.loading.remove(&slot);
            return Err(err(e));
        }
    }
    #[cfg(target_arch = "wasm32")]
    {
        let result =
            load().and_then(|bytes| core::prepare_reference(&bytes).map_err(err)).and_then(|image| core::encoded(&image).map(|png| (image, png)).map_err(err));
        inbox.lock().unwrap_or_else(std::sync::PoisonError::into_inner).push(Loaded { slot, ticket, name, result, selection });
    }
    Ok(())
}
#[cfg(not(target_arch = "wasm32"))]
fn read(path: &std::path::Path) -> Result<Vec<u8>, String> {
    use std::io::Read;
    let mut bytes = Vec::new();
    std::fs::File::open(path).map_err(err)?.take((64 << 20) + 1).read_to_end(&mut bytes).map_err(err)?;
    if bytes.len() > 64 << 20 {
        return Err("Reference files must be smaller than 64 MB".into());
    }
    Ok(bytes)
}
fn slot(p: &Value) -> Result<Slot, String> {
    if p["slot"] == "main" {
        Ok(Slot::Main)
    } else {
        p["slot"].as_u64().filter(|v| *v < 9).map(|v| Slot::Extra(v as usize)).ok_or("Choose the main reference or a reference slot from 0 to 8".into())
    }
}
fn remove(app: &mut PhotocraftApp, target: Slot) {
    let before = ids(&app.studio);
    app.studio.loading.remove(&target);
    match target {
        Slot::Main => app.studio.main = None,
        Slot::Extra(i) => {
            if let Some(r) = app.studio.extras.get_mut(i) {
                *r = None;
            }
        }
    }
    app.ui.gen_studio.workspace.prompt = renumber(&app.ui.gen_studio.workspace.prompt, &before, &ids(&app.studio));
}
pub fn opened(app: &mut PhotocraftApp) {
    if !photocraft_engine::genstudio_ai::selected_bounds(&app.session).is_empty() {
        if let Err(e) = capture(app) {
            crate::notices::error(app, e);
        }
    } else if app.studio.main.as_ref().is_some_and(|r| r.selection) {
        remove(app, Slot::Main);
    }
}
fn capture(app: &mut PhotocraftApp) -> Result<Value, String> {
    let result = app.run("genstudio.reference.capture", json!({}))?;
    app.studio.next = app.studio.next.wrapping_add(1);
    let ticket = app.studio.next;
    app.studio.loading.insert(Slot::Main, ticket);
    app.studio.selection_doc = app.session.active().map(|s| Arc::downgrade(&s.doc));
    if let Some(id) = result["job"].as_u64() {
        app.studio.capture = Some((JobId(id), ticket));
    } else {
        capture_result(&mut app.studio, &result, ticket)?;
    }
    Ok(result)
}
fn capture_result(cache: &mut Cache, result: &Value, ticket: u64) -> Result<(), String> {
    if cache.loading.get(&Slot::Main) != Some(&ticket) {
        return Ok(());
    }
    let encoded = result["image"].as_str().ok_or("Selection capture did not return an image")?;
    let bytes = base64::engine::general_purpose::STANDARD.decode(encoded).map_err(err)?;
    start_load(cache, Slot::Main, "Selection".into(), true, move || Ok(bytes))
}
pub fn menu(app: &mut PhotocraftApp, id: &str, p: &Value) -> Option<Result<Value, String>> {
    match id {
        "genstudio.studio.inspect" => Some(Ok(app.studio.info.clone())),
        "genstudio.studio.configure" => Some((|| {
            let mut state = app.ui.gen_studio.workspace.clone();
            if let Some(v) = p.get("prompt") {
                state.prompt = v.as_str().filter(|s| s.len() <= 12000).ok_or("Invalid prompt")?.to_owned();
            }
            if let Some(v) = p.get("batch") {
                state.batch = v.as_u64().filter(|n| (1..=8).contains(n)).ok_or("Batch must be 1 to 8")? as u8;
            }
            if let Some(v) = p.get("resolution") {
                state.resolution = v.as_u64().filter(|n| *n == 1024 || *n == 2048).ok_or("Resolution must be 1024 or 2048")? as u32;
            }
            if let Some(v) = p.get("monospace") {
                state.monospace = v.as_bool().ok_or("Monospace must be boolean")?;
            }
            let settings_open = p.get("settingsOpen").map(|v| v.as_bool().ok_or("Settings open must be boolean")).transpose()?;
            let advanced_open = p.get("advancedOpen").map(|v| v.as_bool().ok_or("Advanced open must be boolean")).transpose()?;
            if let Some(open) = settings_open {
                app.ui.gen_studio.settings_open = open;
            }
            if let Some(open) = advanced_open {
                app.ui.gen_studio.advanced_open = open;
            }
            app.ui.gen_studio.workspace = state;
            Ok(json!({"configured":true}))
        })()),
        "genstudio.studio.reference.remove" => Some(slot(p).map(|target| {
            remove(app, target);
            json!({"removed":true})
        })),
        "genstudio.studio.reference.selection" => Some(capture(app)),
        "genstudio.studio.reference.set" => Some((|| {
            let target = slot(p)?;
            let value = p["image"].as_str().filter(|s| s.len() < 90 << 20).ok_or("Choose a bounded PNG image")?;
            let bytes = base64::engine::general_purpose::STANDARD.decode(value).map_err(err)?;
            let name = p["name"].as_str().unwrap_or("Reference").chars().take(200).collect();
            start_load(&mut app.studio, target, name, false, move || Ok(bytes))?;
            Ok(json!({"loading":true}))
        })()),
        "genstudio.studio.reference.browse" => Some((|| {
            let target = slot(p)?;
            app.ask_file(
                crate::file_dialog::FileDialogRequest::Open {
                    multiple: false,
                    initial_dir: None,
                    extensions: Some(vec!["png".into(), "jpg".into(), "jpeg".into(), "webp".into(), "tif".into(), "tiff".into()]),
                },
                move |app, answer| {
                    match answer {
                        #[cfg(not(target_arch = "wasm32"))]
                        crate::file_dialog::FileDialogAnswer::Paths(paths) => {
                            let path = std::path::PathBuf::from(paths.first().ok_or("No image selected")?);
                            let name = path.file_name().map_or("Reference".into(), |n| n.to_string_lossy().into_owned());
                            start_load(&mut app.studio, target, name, false, move || read(&path))?;
                        }
                        crate::file_dialog::FileDialogAnswer::Contents(name, bytes) => start_load(&mut app.studio, target, name, false, move || Ok(bytes))?,
                        _ => return Err("Choose an image file".into()),
                    }
                    Ok(json!({"loading":true}))
                },
            )
        })()),
        "genstudio.studio.submit" => Some((|| {
            if !app.studio.loading.is_empty() {
                return Err("Wait for references to finish loading".into());
            }
            if let Some(settings) = app.ui.gen_studio.draft.clone().filter(|s| *s != app.session.prefs().gen_studio) {
                app.run("genstudio.engine.configure", serde_json::to_value(settings).map_err(err)?)?;
            }
            let state = &app.ui.gen_studio.workspace;
            let references: Vec<_> = app.studio.main.iter().chain(app.studio.extras.iter().filter_map(Option::as_ref)).collect();
            let prompt = compiled_prompt(&state.prompt, references.len())?;
            let (width, height) = dimensions(state.resolution, app.studio.main.as_ref().map(|r| r.image.dimensions()));
            let references: Vec<_> = references.into_iter().map(|r| r.png.clone()).collect();
            app.run("genstudio.studio.generate", json!({"prompt":prompt,"references":references,"width":width,"height":height,"batch":state.batch}))
        })()),
        _ => None,
    }
}
fn dimensions(side: u32, image: Option<(u32, u32)>) -> (u32, u32) {
    let side = if side == 2048 { 2048 } else { 1024 };
    let ratio = image.map_or(1.0, |(w, h)| f64::from(w) / f64::from(h.max(1))).clamp(0.125, 8.0);
    let round = |v: f64| ((v / 32.0).round() as u32 * 32).clamp(256, side);
    if ratio >= 1.0 { (side, round(f64::from(side) / ratio)) } else { (round(f64::from(side) * ratio), side) }
}
pub fn tick(app: &mut PhotocraftApp, ctx: &egui::Context) {
    if let Some((id, ticket)) = app.studio.capture
        && let Some(job) = app.session.jobs_with_recent().into_iter().find(|j| j.id == id && j.state != "running")
    {
        app.studio.capture = None;
        let result = if job.state == "done" {
            capture_result(&mut app.studio, &job.result.unwrap_or(Value::Null), ticket)
        } else {
            if app.studio.loading.get(&Slot::Main) == Some(&ticket) {
                app.studio.loading.remove(&Slot::Main);
            }
            Err(job.error.unwrap_or("Selection capture cancelled".into()))
        };
        if let Err(e) = result {
            crate::notices::error(app, e);
        }
    }
    let loaded = std::mem::take(&mut *app.studio.inbox.lock().unwrap_or_else(std::sync::PoisonError::into_inner));
    for item in loaded {
        if app.studio.loading.get(&item.slot) != Some(&item.ticket) {
            continue;
        }
        app.studio.loading.remove(&item.slot);
        match item.result {
            Ok((image, png)) => {
                let before = ids(&app.studio);
                let id = get(&app.studio, item.slot).map_or(item.ticket, |r| r.id);
                let reference = Some(Reference { id, name: item.name, image, png, texture: None, selection: item.selection });
                match item.slot {
                    Slot::Main => app.studio.main = reference,
                    Slot::Extra(i) => {
                        if let Some(slot) = app.studio.extras.get_mut(i) {
                            *slot = reference;
                        }
                    }
                }
                app.ui.gen_studio.workspace.prompt = renumber(&app.ui.gen_studio.workspace.prompt, &before, &ids(&app.studio));
            }
            Err(e) => crate::notices::error(app, e),
        }
    }
    if !app.studio.loading.is_empty() {
        ctx.request_repaint_after(std::time::Duration::from_millis(100));
    }
}
fn slot_widget(ui: &mut egui::Ui, cache: &mut Cache, slot: Slot, size: egui::Vec2, actions: &mut Vec<(&'static str, Value)>) {
    let tokens = crate::theme::Tokens::get(ui.ctx());
    let (rect, response) = ui.allocate_exact_size(size, Sense::click_and_drag());
    cache.rects.push((slot, rect));
    let ordinal = get(cache, slot).and_then(|r| ids(cache).iter().position(|id| *id == r.id)).map(|n| n + 1);
    let value = match slot {
        Slot::Main => json!("main"),
        Slot::Extra(i) => json!(i),
    };
    ui.painter().rect_filled(rect, tokens.radius, tokens.field);
    ui.painter().rect_stroke(
        rect,
        tokens.radius,
        egui::Stroke::new(1.0, if response.hovered() { tokens.accent_border } else { tokens.field_border }),
        egui::StrokeKind::Inside,
    );
    if let Some(reference) = get_mut(cache, slot) {
        if reference.texture.is_none()
            && let Ok(preview) = core::resized(&reference.image, 256)
        {
            reference.texture = Some(ui.ctx().load_texture(
                format!("genstudio-ref-{}", reference.id),
                ColorImage::from_rgba_unmultiplied([preview.width() as usize, preview.height() as usize], preview.data()),
                TextureOptions::LINEAR,
            ));
        }
        let inner = rect.shrink(6.0);
        let picture = Rect::from_min_max(inner.min, pos2(inner.max.x, inner.max.y - 21.0));
        if let Some(texture) = &reference.texture {
            let source = texture.size_vec2();
            let scale = (picture.width() / source.x).min(picture.height() / source.y);
            let fit = Rect::from_center_size(picture.center(), source * scale);
            ui.painter().image(texture.id(), fit, Rect::from_min_max(pos2(0.0, 0.0), pos2(1.0, 1.0)), egui::Color32::WHITE);
        }
        let label = format!("Image {}", ordinal.unwrap_or(1));
        ui.painter().text(pos2(rect.min.x + 8.0, rect.max.y - 14.0), egui::Align2::LEFT_CENTER, label, egui::FontId::proportional(11.0), tokens.text_dim);
        response.dnd_set_drag_payload(RefDrag(reference.id));
        if ui.place(Rect::from_min_size(rect.right_top() - vec2(25.0, -4.0), vec2(20.0, 20.0)), egui::Button::new("×").small()).clicked() {
            actions.push(("genstudio.studio.reference.remove", json!({"slot":value})));
        }
        response.clone().on_hover_text(format!("{}\nDrag into the prompt to insert a reference", reference.name));
    } else {
        ui.painter().text(rect.center() - vec2(0.0, 8.0), egui::Align2::CENTER_CENTER, "+", egui::FontId::proportional(24.0), tokens.text_dim);
        ui.painter().text(
            rect.center() + vec2(0.0, 17.0),
            egui::Align2::CENTER_CENTER,
            if cache.loading.contains_key(&slot) {
                "Loading…"
            } else if slot == Slot::Main {
                "Click or drop image"
            } else {
                "Add image"
            },
            egui::FontId::proportional(11.0),
            tokens.text_faint,
        );
        response.clone().on_hover_text("Click or drop an image");
    }
    if response.clicked() {
        actions.push(("genstudio.studio.reference.browse", json!({"slot":value})));
    }
}
pub fn show(app: &mut PhotocraftApp, ui: &mut egui::Ui) -> Vec<(&'static str, Value)> {
    let mut cache = std::mem::take(&mut app.studio);
    cache.rects.clear();
    let mut actions = Vec::new();
    let state = &mut app.ui.gen_studio.workspace;
    let tokens = crate::theme::Tokens::get(ui.ctx());
    let width = ui.available_width();
    let main_width = (width * 0.29).clamp(170.0, 250.0);
    ui.horizontal_top(|ui| {
        ui.vertical(|ui| {
            slot_widget(ui, &mut cache, Slot::Main, vec2(main_width, 230.0), &mut actions);
            if ui.add_enabled(!photocraft_engine::genstudio_ai::selected_bounds(&app.session).is_empty(), egui::Button::new("Use selection").small()).clicked()
            {
                actions.push(("genstudio.studio.reference.selection", json!({})));
            }
        });
        ui.add_space(6.0);
        ui.vertical(|ui| {
            let font = if state.monospace { egui::FontId::monospace(14.0) } else { egui::FontId::proportional(14.0) };
            let mut layouter = |ui: &egui::Ui, text: &dyn egui::TextBuffer, wrap: f32| {
                let mut job = egui::text::LayoutJob::default();
                job.wrap.max_width = wrap;
                let mut rest = text.as_str();
                let normal = egui::TextFormat { font_id: font.clone(), color: tokens.text, ..Default::default() };
                while let Some(start) = rest.find("[Image ") {
                    job.append(rest.get(..start).unwrap_or_default(), 0.0, normal.clone());
                    let tail = rest.get(start..).unwrap_or_default();
                    if let Some(end) = tail.find(']') {
                        job.append(
                            tail.get(..=end).unwrap_or_default(),
                            0.0,
                            egui::TextFormat { background: tokens.accent_soft, color: tokens.accent_text, ..normal.clone() },
                        );
                        rest = tail.get(end + 1..).unwrap_or_default();
                    } else {
                        job.append(tail, 0.0, normal.clone());
                        rest = "";
                        break;
                    }
                }
                job.append(rest, 0.0, normal);
                ui.fonts_mut(|fonts| fonts.layout_job(job))
            };
            let output = egui::TextEdit::multiline(&mut state.prompt)
                .id_salt("genstudio-prompt")
                .hint_text("Prompt goes here…")
                .desired_width((width - main_width - 20.0).max(240.0))
                .min_size(vec2(200.0, 193.0))
                .desired_rows(9)
                .char_limit(12000)
                .layouter(&mut layouter)
                .show(ui);
            cache.prompt_rect = Some(output.response.rect);
            if let Some(payload) = output.response.dnd_release_payload::<RefDrag>()
                && let Some(index) = ids(&cache).iter().position(|id| *id == payload.0)
            {
                let at = ui
                    .ctx()
                    .pointer_latest_pos()
                    .map(|p| usize::from(output.galley.cursor_from_pos(p - output.galley_pos).index))
                    .unwrap_or_else(|| state.prompt.chars().count());
                let cursor = insert(&mut state.prompt, at, index + 1);
                let mut edit = output.state;
                edit.cursor.set_char_range(Some(egui::text::CCursorRange::one(egui::text::CCursor::new(cursor))));
                edit.store(ui.ctx(), output.response.id);
                output.response.request_focus();
            }
            ui.add_space(6.0);
            ui.horizontal(|ui| {
                ui.spacing_mut().item_spacing.x = 4.0;
                ui.small("Batch");
                if ui.add_enabled(state.batch > 1, egui::Button::new("−").small()).clicked() {
                    state.batch = state.batch.saturating_sub(1).max(1);
                }
                ui.add(egui::DragValue::new(&mut state.batch).range(1..=8).speed(0.1));
                if ui.add_enabled(state.batch < 8, egui::Button::new("+").small()).clicked() {
                    state.batch = state.batch.saturating_add(1).min(8);
                }
                ui.add_space(8.0);
                if ui.button(if state.resolution == 2048 { "2k" } else { "1k" }).on_hover_text("Output resolution").clicked() {
                    state.resolution = if state.resolution == 2048 { 1024 } else { 2048 };
                }
                if ui.selectable_label(state.monospace, "Aa / { }").on_hover_text("Monospaced prompt").clicked() {
                    state.monospace = !state.monospace;
                }
                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                    let running = app.session.jobs().iter().any(|j| j.command == "genstudio.studio.generate" && j.state == "running");
                    let ready = !running && cache.loading.is_empty() && !state.prompt.trim().is_empty();
                    if ui.add_enabled(ready, egui::Button::new("Generate").fill(tokens.primary_bg)).clicked() {
                        actions.push(("genstudio.studio.submit", json!({})));
                    }
                    if let Some(job) = app.session.jobs().into_iter().find(|j| j.command == "genstudio.studio.generate" && j.state == "running")
                        && ui.button("Cancel").clicked()
                    {
                        actions.push(("jobs.cancel", json!({"job":job.id.0})));
                    }
                });
            });
        });
    });
    ui.add_space(12.0);
    ui.small("Reference images");
    ui.add_space(4.0);
    let mut visible: Vec<Slot> =
        cache.extras.iter().enumerate().filter(|(i, r)| r.is_some() || cache.loading.contains_key(&Slot::Extra(*i))).map(|(i, _)| Slot::Extra(i)).collect();
    if let Some(blank) = next_slot(&cache) {
        visible.push(blank);
    }
    egui::ScrollArea::horizontal().id_salt("genstudio-refs").show(ui, |ui| {
        ui.horizontal(|ui| {
            for slot in visible {
                slot_widget(ui, &mut cache, slot, vec2(92.0, 104.0), &mut actions);
            }
        });
    });
    let list: Vec<_> = cache
        .main
        .iter()
        .chain(cache.extras.iter().filter_map(Option::as_ref))
        .enumerate()
        .map(|(i, r)| json!({"id":r.id,"number":i+1,"name":r.name,"width":r.image.width(),"height":r.image.height()}))
        .collect();
    cache.info = json!({"prompt":state.prompt,"batch":state.batch,"resolution":state.resolution,"monospace":state.monospace,"references":list,
            "loading":!cache.loading.is_empty(),"promptRect":cache.prompt_rect.map(|r|[r.min.x,r.min.y,r.width(),r.height()]),
        "slots":cache.rects.iter().map(|(s,r)|json!({"slot":match s {Slot::Main=>json!("main"),Slot::Extra(i)=>json!(i)},"rect":[r.min.x,r.min.y,r.width(),r.height()]})).collect::<Vec<_>>()});
    app.studio = cache;
    actions
}
pub fn dropped(app: &mut PhotocraftApp, ctx: &egui::Context, files: &[egui::DroppedFileHandle], at: Option<egui::Pos2>) -> bool {
    if !app.ui.gen_studio.open {
        return false;
    }
    let Some(at) = at.or_else(|| ctx.pointer_latest_pos()) else {
        return false;
    };
    let Some((target, _)) = app.studio.rects.iter().find(|(_, rect)| rect.contains(at)).copied() else {
        return false;
    };
    for (index, file) in files.iter().enumerate() {
        let slot = if index == 0 { Some(target) } else { next_slot(&app.studio) };
        let Some(slot) = slot else {
            crate::notices::error(app, "The nine additional reference slots are full".into());
            break;
        };
        let file = file.clone();
        let name = file.path().file_name().map_or("Reference".into(), |n| n.to_string_lossy().into_owned());
        if let Err(e) = start_load(&mut app.studio, slot, name, false, move || {
            #[cfg(not(target_arch = "wasm32"))]
            {
                read(file.path())
            }
            #[cfg(target_arch = "wasm32")]
            {
                Err("Use the image picker for browser references".into())
            }
        }) {
            crate::notices::error(app, e);
        }
    }
    true
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn a_completed_background_selection_capture_populates_the_main_reference() {
        let mut app = PhotocraftApp::new(photocraft_engine::Session::new(), crate::Services::default());
        app.background_jobs = true;
        app.session.execute("file.new", json!({"width":32,"height":24,"background":"white"})).unwrap();
        app.session.execute("select.rect", json!({"x":4,"y":3,"width":16,"height":8})).unwrap();
        opened(&mut app);
        assert!(app.studio.capture.is_some());
        let ctx = egui::Context::default();
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
        while app.studio.main.is_none() && std::time::Instant::now() < deadline {
            crate::jobs_ui::tick(&mut app, &ctx);
            tick(&mut app, &ctx);
            std::thread::sleep(std::time::Duration::from_millis(10));
        }
        let reference = app.studio.main.as_ref().expect("completed selection must be collected from recent jobs");
        assert!(reference.selection);
        assert_eq!(reference.image.dimensions(), (16, 8));
        assert!(app.studio.loading.is_empty());
    }
    #[test]
    fn chip_insertion_uses_unicode_character_positions_and_keeps_numbering() {
        let mut text = "до после".to_owned();
        insert(&mut text, 3, 2);
        assert_eq!(text, "до [Image 2]после");
        assert_eq!(renumber("[Image 1] + [Image 2]", &[20, 30], &[30]), " + [Image 1]");
        assert_eq!(compiled_prompt("Use [Image 2]", 2).unwrap(), "Use image 2");
        assert!(compiled_prompt("Use [Image 2]", 1).is_err());
    }
    #[test]
    fn defaults_are_ready_for_text_to_image_and_output_sizes_are_bounded() {
        let s = Workspace::default();
        assert_eq!(s.batch, 1);
        assert_eq!(s.resolution, 1024);
        assert!(!s.monospace);
        assert_eq!(dimensions(2048, None), (2048, 2048));
        assert_eq!(dimensions(1024, Some((1200, 800))), (1024, 672));
        assert_eq!(dimensions(2048, Some((1, 20000))), (256, 2048));
    }
    #[test]
    fn dragging_a_reference_inserts_a_chip_at_the_drop_cursor() {
        use egui_kittest::kittest::Queryable;
        let mut app = PhotocraftApp::new(photocraft_engine::Session::new(), crate::Services::default());
        app.ui.gen_studio.open = true;
        app.ui.gen_studio.initialized = true;
        app.ui.gen_studio.workspace.prompt = "Before the scene after".into();
        let image = Image::from_u8(8, 8, photocraft_codecs::ChannelLayout::Rgba, [120, 80, 20, 255].repeat(64)).unwrap();
        app.studio.main = Some(Reference { id: 1, name: "Fixture".into(), png: core::encoded(&image).unwrap(), image, texture: None, selection: false });
        let mut harness = egui_kittest::Harness::builder().with_size(vec2(1100.0, 800.0)).build_ui_state(
            |ui, app: &mut PhotocraftApp| {
                if ui.ctx().fonts(|f| f.families().contains(&egui::FontFamily::Name("medium".into()))) {
                    crate::genstudio_ui::show(app, ui.ctx());
                }
            },
            app,
        );
        PhotocraftApp::setup_context(&harness.ctx, crate::theme::ThemeKind::Studio);
        harness.run_steps(6);
        assert!(
            harness.get_by_label("Use selection").rect().min.y >= harness.state().studio.rects.first().unwrap().1.max.y,
            "reference overlay buttons must not move the layout cursor"
        );
        let from = harness.state().studio.rects.first().unwrap().1.center();
        let to = harness.state().studio.prompt_rect.unwrap().min + vec2(53.0, 12.0);
        harness.event(egui::Event::PointerMoved(from));
        harness.run_steps(1);
        harness.event(egui::Event::PointerButton { pos: from, button: egui::PointerButton::Primary, pressed: true, modifiers: egui::Modifiers::NONE });
        harness.run_steps(1);
        harness.event(egui::Event::PointerMoved(from + vec2(15.0, 3.0)));
        harness.run_steps(2);
        harness.event(egui::Event::PointerMoved(to));
        harness.run_steps(2);
        harness.event(egui::Event::PointerButton { pos: to, button: egui::PointerButton::Primary, pressed: false, modifiers: egui::Modifiers::NONE });
        harness.run_steps(2);
        let text = &harness.state().ui.gen_studio.workspace.prompt;
        assert!(text.contains("[Image 1]"), "{text}");
        assert!(!text.ends_with("[Image 1]"), "uses the drop position, not append");
    }
}
