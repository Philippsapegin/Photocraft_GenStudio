//! Generation workspace. References are session data; model work stays in the host.
use crate::PhotocraftApp;
use base64::Engine as _;
use egui::{ColorImage, Rect, Sense, TextureHandle, TextureOptions, pos2, vec2};
use photocraft_codecs::Image;
use photocraft_engine::{
    genstudio::{MODELS, ModelSpec, model_spec},
    genstudio_generate as core,
    jobs::JobId,
};
use serde_json::{Value, json};
use std::sync::{Arc, Mutex};

#[derive(Clone, Debug, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(default)]
pub struct Workspace {
    pub prompt: String,
    pub batch: u8,
    pub resolution: u32,
    pub aspect_ratio: String,
    pub monospace: bool,
    pub loras: crate::genstudio_lora_ui::State,
}
impl Default for Workspace {
    fn default() -> Self {
        Self { prompt: String::new(), batch: 1, resolution: 1024, aspect_ratio: "1:1".into(), monospace: false, loras: Default::default() }
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
    model: String,
    reference_limit: usize,
    extras: [Option<Reference>; 9],
    next: u64,
    loading: std::collections::HashMap<Slot, u64>,
    inbox: Arc<Mutex<Vec<Loaded>>>,
    rects: Vec<(Slot, Rect)>,
    pub info: Value,
    workflow_controls:Value,
    prompt_rect: Option<Rect>,
    file_hover: Option<Slot>,
    capture: Option<(JobId, u64)>,
    generation: Option<(JobId, u64)>,
    main_revision: u64,
    selection_doc: Option<std::sync::Weak<photocraft_doc::Document>>,
    layer_capture: Option<(JobId, Slot, bool, u64)>,
    window_rect: Option<Rect>,
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
    cache
        .extras
        .iter()
        .enumerate()
        .take((if cache.reference_limit == 0 { 10 } else { cache.reference_limit }).saturating_sub(1))
        .find(|(i, r)| r.is_none() && !cache.loading.contains_key(&Slot::Extra(*i)))
        .map(|(i, _)| Slot::Extra(i))
}
// A chip is a reference identity, not a global replacement of image numbers.
// Leave JSON, filenames and user-authored prose untouched.
fn marker(text: &str) -> Option<(usize, usize, char, usize)> {
    [("[Image ", 7, ']', 0), ("<ref_image_", 11, '>', 1), ("<image_", 7, '>', 2)]
        .into_iter().filter_map(|(prefix,n,end,style)|text.find(prefix).map(|at|(at,n,end,style))).min_by_key(|v|v.0)
}
fn rewrite(prompt: &str, strict: bool, mut chip: impl FnMut(usize, usize) -> Result<String, String>) -> Result<String, String> {
    let mut out = String::new();
    let mut rest = prompt;
    while let Some((start, prefix, suffix, flux)) = marker(rest) {
        out.push_str(rest.get(..start).unwrap_or_default());
        let tail = rest.get(start..).unwrap_or_default();
        if let Some(end) = tail.find(suffix)
            && let Some(number) = tail.get(prefix..end).and_then(|s| s.parse::<usize>().ok())
            && let Some(ordinal) = if flux == 1 { number.checked_add(1) } else { Some(number) }
        {
            out.push_str(&chip(ordinal, flux)?);
            rest = tail.get(end + 1..).unwrap_or_default();
        } else if strict {
            return Err("Complete or remove the reference chip".into());
        } else {
            out.push_str(tail.get(..prefix).unwrap_or_default());
            rest = tail.get(prefix..).unwrap_or_default();
        }
    }
    out.push_str(rest);
    Ok(out)
}
fn renumber(prompt: &str, before: &[u64], after: &[u64]) -> String {
    rewrite(prompt, false, |ordinal, flux| {
        let model = MODELS.get(flux).copied().unwrap_or(MODELS[0]);
        let Some(id) = ordinal.checked_sub(1).and_then(|n| before.get(n)) else {
            return Ok(model.chip(ordinal));
        };
        Ok(after.iter().position(|n| n == id).map_or_else(String::new, |index| model.chip(index + 1)))
    })
    .unwrap_or_else(|_| prompt.to_owned())
}
fn compiled_for(text: &str, count: usize, model: ModelSpec) -> Result<String, String> {
    rewrite(text, true, |ordinal, _| {
        if ordinal == 0 || ordinal > count || count > model.reference_limit {
            return Err("The prompt refers to an image that is missing".into());
        }
        Ok(model.reference(ordinal))
    })
}
#[cfg(test)]
pub fn compiled_prompt(text: &str, count: usize) -> Result<String, String> {
    compiled_for(text, count, MODELS[0])
}
fn insert_for(text: &mut String, at: usize, number: usize, model: ModelSpec) -> usize {
    let byte = text.char_indices().nth(at).map_or(text.len(), |(index, _)| index);
    let chip = model.chip(number);
    text.insert_str(byte, &chip);
    at + chip.chars().count()
}
#[cfg(test)]
fn insert(text: &mut String, at: usize, number: usize) -> usize {
    insert_for(text, at, number, MODELS[0])
}
fn sync_model(app: &mut PhotocraftApp) -> ModelSpec {
    let model = if app.session.prefs().gen_studio.workflow_mode() {
        ModelSpec{id:"workflow",label:"ComfyUI workflow",reference_limit:10,zero_based:false}
    } else {model_spec(&app.session.prefs().gen_studio.model).unwrap_or(MODELS[0])};
    if app.studio.model != model.id {
        let prompt = &mut app.ui.gen_studio.workspace.prompt;
        *prompt = rewrite(prompt, false, |ordinal, _| Ok(model.chip(ordinal))).unwrap_or_else(|_| prompt.clone());
        app.studio.model = model.id.into();
    }
    app.studio.reference_limit = model.reference_limit;
    model
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
    if slot == Slot::Main {
        cache.main_revision = cache.main_revision.wrapping_add(1);
    }
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
    if target == Slot::Main {
        app.studio.main_revision = app.studio.main_revision.wrapping_add(1);
    }
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
    app.studio.main_revision = app.studio.main_revision.wrapping_add(1);
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
    start_load(cache, Slot::Main, result["name"].as_str().unwrap_or("Selection").into(), result["source"] != "generated", move || Ok(bytes))
}
pub fn menu(app: &mut PhotocraftApp, id: &str, p: &Value) -> Option<Result<Value, String>> {
    if id=="genstudio.studio.workflow.set" {
        return Some((||{
            if photocraft_engine::genstudio_queue::has_pending(&app.session) || app.session.jobs().iter().any(|j|j.state=="running" && j.command=="genstudio.studio.generate") {
                return Err("Wait for the current AI request before changing its workflow".into());
            }
            let mut settings=app.session.prefs().gen_studio.clone();
            if let Some(mode)=p.get("mode") {settings.studio_mode=mode.as_str().ok_or("Workflow mode must be text")?.into();}
            if let Some(path)=p.get("path") {settings.studio_workflow=path.as_str().ok_or("Workflow path must be text")?.into();}
            if settings.studio_mode=="workflow" && settings.engine!=photocraft_engine::genstudio::Engine::Comfy {
                return Err("Choose ComfyUI in Preferences > GenGate Settings to use workflows".into());
            }
            settings.validate()?;
            app.run("genstudio.engine.configure",serde_json::to_value(settings).map_err(err)?)
        })());
    }
    if id=="genstudio.studio.workflow.browse" {
        let settings=&app.session.prefs().gen_studio;
        if settings.engine!=photocraft_engine::genstudio::Engine::Comfy {return Some(Err("Choose the ComfyUI engine in GenGate Settings".into()));}
        let initial_dir=if settings.studio_workflow.is_empty(){Some(std::path::Path::new(&settings.comfy_dir).join("user/default/workflows").to_string_lossy().into_owned())}
            else {std::path::Path::new(&settings.studio_workflow).parent().map(|p|p.to_string_lossy().into_owned())};
        return Some(app.ask_file(crate::file_dialog::FileDialogRequest::Open{multiple:false,initial_dir,extensions:Some(vec!["json".into()])},|app,answer|{
            let crate::file_dialog::FileDialogAnswer::Paths(paths)=answer else{return Err("Choose a ComfyUI API workflow JSON file".into())};
            let path=paths.first().ok_or("No workflow selected")?;
            menu(app,"genstudio.studio.workflow.set",&json!({"mode":"workflow","path":path})).ok_or("Workflow action is missing")?
        }));
    }
    if let Some(result) = crate::genstudio_lora_ui::menu(app,id,p) { return Some(result); }
    match id {
        "genstudio.studio.layer.restore" => Some((|| {
            let target = if p.get("slot").is_some() { slot(p)? } else { Slot::Main };
            let restore = p.get("restore").map_or(Ok(true), |v|v.as_bool().ok_or("Restore must be boolean"))?;
            let result = app.run("genstudio.studio.layer.source",p.clone())?;
            app.studio.next=app.studio.next.wrapping_add(1);
            let ticket=app.studio.next;
            app.studio.loading.insert(target,ticket);
            if let Some(id)=result["job"].as_u64() { app.studio.layer_capture=Some((JobId(id),target,restore,ticket)); }
            else { apply_layer(app,target,restore,&result,ticket)?; }
            Ok(result)
        })()),
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
            if let Some(v) = p.get("aspectRatio") {
                state.aspect_ratio = v.as_str().filter(|s| ASPECTS.contains(s)).ok_or("Choose a supported aspect ratio")?.to_owned();
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
            let state = &app.ui.gen_studio.workspace;
            let workflow=app.session.prefs().gen_studio.workflow_mode();
            let model = if workflow {ModelSpec{id:"workflow",label:"ComfyUI workflow",reference_limit:10,zero_based:false}}
                else {model_spec(&app.session.prefs().gen_studio.model).ok_or("Choose a supported model")?};
            let references: Vec<_> = app.studio.main.iter().chain(app.studio.extras.iter().take(model.reference_limit.saturating_sub(1)).filter_map(Option::as_ref)).collect();
            let prompt = if workflow {state.prompt.clone()}else{compiled_for(&state.prompt, references.len(), model)?};
            let (width, height) = dimensions(state.resolution, app.studio.main.as_ref().map(|r| r.image.dimensions()), &state.aspect_ratio);
            let references: Vec<_> = references.into_iter().map(|r| r.png.clone()).collect();
            let result = app.run(
                "genstudio.studio.generate",
                json!({"prompt":prompt,"sourcePrompt":state.prompt,"references":references,"width":width,"height":height,"batch":state.batch,
                    "loras":crate::genstudio_lora_ui::selected(&state.loras,&app.session.prefs().gen_studio.models_dir)}),
            )?;
            if app.studio.main.is_none() {
                if let Some(id) = result["job"].as_u64() {
                    app.studio.generation = Some((JobId(id), app.studio.main_revision));
                } else {
                    generated_reference(app, &result)?;
                }
            }
            Ok(result)
        })()),
        _ => None,
    }
}
const ASPECTS: [&str; 9] = ["1:1", "3:2", "2:3", "5:4", "4:5", "16:9", "9:16", "2:1", "1:2"];
fn dimensions(side: u32, image: Option<(u32, u32)>, aspect: &str) -> (u32, u32) {
    let side = if side == 2048 { 2048 } else { 1024 };
    let chosen = match aspect {
        "3:2" => (3, 2),
        "2:3" => (2, 3),
        "5:4" => (5, 4),
        "4:5" => (4, 5),
        "16:9" => (16, 9),
        "9:16" => (9, 16),
        "2:1" => (2, 1),
        "1:2" => (1, 2),
        _ => (1, 1),
    };
    let (w, h) = image.unwrap_or(chosen);
    let ratio = (f64::from(w) / f64::from(h.max(1))).clamp(0.125, 8.0);
    let round = |v: f64| ((v / 32.0).round() as u32 * 32).clamp(256, side);
    if ratio >= 1.0 { (side, round(f64::from(side) / ratio)) } else { (round(f64::from(side) * ratio), side) }
}
fn generated_reference(app: &mut PhotocraftApp, result: &Value) -> Result<(), String> {
    let layer = result["layers"].as_array().and_then(|v| v.last()).and_then(Value::as_u64).ok_or("Generated layer is missing")?;
    let captured = app.run("genstudio.studio.result.reference", json!({"documentId":result["documentId"],"layer":layer}))?;
    app.studio.main_revision = app.studio.main_revision.wrapping_add(1);
    app.studio.next = app.studio.next.wrapping_add(1);
    let ticket = app.studio.next;
    app.studio.loading.insert(Slot::Main, ticket);
    if let Some(id) = captured["job"].as_u64() {
        app.studio.capture = Some((JobId(id), ticket));
    } else {
        capture_result(&mut app.studio, &captured, ticket)?;
    }
    Ok(())
}
fn apply_layer(app: &mut PhotocraftApp, target: Slot, restore: bool, result: &Value, ticket: u64) -> Result<(), String> {
    if app.studio.loading.get(&target)!=Some(&ticket) { return Ok(()); }
    let bytes=base64::engine::general_purpose::STANDARD.decode(result["image"].as_str().ok_or("Layer image is missing")?).map_err(err)?;
    if restore {
        let values:Vec<photocraft_engine::genstudio_lora::Selection>=serde_json::from_value(result["generation"].get("loras").cloned().unwrap_or_else(||json!([]))).map_err(err)?;
        let models=app.session.prefs().gen_studio.models_dir.clone();
        crate::genstudio_lora_ui::restore(&mut app.ui.gen_studio.workspace.loras,&models,&values)?;
        if let Some(model)=result["generation"]["model"].as_str().filter(|id|model_spec(id).is_some()) {
            app.session.edit_prefs(|p|p.gen_studio.model=model.into());
        }
        if let Some(path)=result["generation"]["workflow"]["path"].as_str().filter(|v|v.len()<=4096 && !v.contains('\0')) {
            app.session.edit_prefs(|p|{p.gen_studio.engine=photocraft_engine::genstudio::Engine::Comfy;p.gen_studio.studio_mode="workflow".into();p.gen_studio.studio_workflow=path.into();});
        } else if result["generation"].is_object() {app.session.edit_prefs(|p|p.gen_studio.studio_mode="preset".into());}
        for slot in app.studio.extras.iter_mut(){*slot=None;}
        app.studio.loading.retain(|slot,_|*slot==target);
        app.studio.main=None;
        app.studio.capture=None;app.studio.generation=None;
        app.ui.gen_studio.workspace.prompt=result["prompt"].as_str().unwrap_or_default().into();
        app.ui.gen_studio.workspace.resolution=if result["generation"]["width"].as_u64().unwrap_or(1024)>1024 {2048}else{1024};
    }
    start_load(&mut app.studio,target,result["name"].as_str().unwrap_or("Layer").into(),false,move||Ok(bytes))
}
pub fn set_window_rect(app: &mut PhotocraftApp, rect: Rect) {app.studio.window_rect=Some(rect);}
pub fn layer_drop(app: &mut PhotocraftApp, ctx: &egui::Context, document: u64, layer: u64) -> bool {
    if !app.ui.gen_studio.open || app.ui.gen_studio.collapsed {return false;}
    let Some(at)=ctx.pointer_latest_pos() else{return false};
    if !app.studio.window_rect.is_some_and(|rect|rect.contains(at)){return false;}
    let slot=app.studio.rects.iter().find(|(_,rect)|rect.contains(at)).map_or(Slot::Main,|(slot,_)|*slot);
    let mut params=json!({"documentId":document,"layer":layer,"restore":matches!(slot,Slot::Main)});
    if let Slot::Extra(i)=slot {params["slot"]=json!(i);}
    if let Err(e)=crate::menus::invoke(app,ctx,"genstudio.studio.layer.restore",params){crate::notices::error(app,e);}
    true
}
pub fn tick(app: &mut PhotocraftApp, ctx: &egui::Context) {
    if let Some((id,slot,restore,ticket))=app.studio.layer_capture
        && let Some(job)=app.session.jobs_with_recent().into_iter().find(|j|j.id==id && j.state!="running") {
        app.studio.layer_capture=None;
        if job.state=="done" {
            if let Err(e)=apply_layer(app,slot,restore,&job.result.unwrap_or(Value::Null),ticket){crate::notices::error(app,e);}
        } else {
            app.studio.loading.remove(&slot);
            crate::notices::error(app,job.error.unwrap_or("Layer capture cancelled".into()));
        }
    }
    if let Some((id, revision)) = app.studio.generation
        && let Some(job) = app.session.jobs_with_recent().into_iter().find(|j| j.id == id && j.state != "running")
    {
        app.studio.generation = None;
        if job.state == "done"
            && app.studio.main.is_none()
            && app.studio.main_revision == revision
            && !app.studio.loading.contains_key(&Slot::Main)
            && let Err(e) = generated_reference(app, &job.result.unwrap_or(Value::Null))
        {
            crate::notices::error(app, e);
        }
    }
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
fn slot_widget(ui: &mut egui::Ui, cache: &mut Cache, slot: Slot, size: egui::Vec2, selection_available: bool, actions: &mut Vec<(&'static str, Value)>) {
    let tokens = crate::theme::Tokens::get(ui.ctx());
    let (rect, response) = ui.allocate_exact_size(size, Sense::click_and_drag());
    cache.rects.push((slot, rect));
    let ordinal = get(cache, slot).and_then(|r| ids(cache).iter().position(|id| *id == r.id)).map(|n| n + 1);
    let value = match slot {
        Slot::Main => json!("main"),
        Slot::Extra(i) => json!(i),
    };
    ui.painter().rect_filled(rect, tokens.radius, tokens.field);
    let file_hover = ui.input(|i| !i.raw.hovered_files.is_empty() && i.pointer.hover_pos().is_some_and(|p| rect.contains(p)));
    if file_hover {
        cache.file_hover = Some(slot);
    }
    ui.painter().rect_stroke(
        rect,
        tokens.radius,
        egui::Stroke::new(
            if file_hover { 3.0 } else { 1.0 },
            if file_hover {
                tokens.accent
            } else if response.hovered() {
                tokens.accent_border
            } else {
                tokens.field_border
            },
        ),
        egui::StrokeKind::Inside,
    );
    let mut selection_clicked = false;
    let model = model_spec(&cache.model).unwrap_or(MODELS[0]);
    if let Some(reference) = get_mut(cache, slot) {
        if reference.texture.is_none()
            && let Ok(preview) = core::resized(&reference.image, if slot == Slot::Main { 1024 } else { 256 })
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
        let label = model.chip(ordinal.unwrap_or(1));
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
    if slot == Slot::Main && selection_available && !cache.loading.contains_key(&slot) {
        let center = if get(cache, slot).is_some() { pos2(rect.center().x, rect.bottom() - 43.0) } else { rect.center() + vec2(0.0, 55.0) };
        let button = Rect::from_center_size(center, vec2(112.0, 24.0));
        selection_clicked = ui.place(button, egui::Button::new("Use selection")).clicked();
        if selection_clicked {
            actions.push(("genstudio.studio.reference.selection", json!({})));
        }
    }
    if response.clicked() && !selection_clicked {
        actions.push(("genstudio.studio.reference.browse", json!({"slot":value})));
    }
}
fn rect_json(rect: Rect) -> Value {
    json!([rect.min.x, rect.min.y, rect.width(), rect.height()])
}
fn luminance(color: egui::Color32) -> f32 {
    let linear = |v: u8| {
        let c = f32::from(v) / 255.0;
        if c <= 0.04045 { c / 12.92 } else { ((c + 0.055) / 1.055).powf(2.4) }
    };
    0.2126 * linear(color.r()) + 0.7152 * linear(color.g()) + 0.0722 * linear(color.b())
}
fn accent_label(tokens: &crate::theme::Tokens) -> egui::Color32 {
    let background = luminance(tokens.accent);
    let contrast = |color| {
        let l = luminance(color);
        (l.max(background) + 0.05) / (l.min(background) + 0.05)
    };
    let mut color = [tokens.text, tokens.field, tokens.primary_text, tokens.primary_bg]
        .into_iter()
        .max_by(|a, b| contrast(*a).total_cmp(&contrast(*b)))
        .unwrap_or(tokens.text);
    // Keep the theme's label shade when possible, adjusting its luminance only
    // when none of its regular text/surface colours is readable on the accent.
    for _ in 0..16 {
        if contrast(color) >= 4.5 {
            break;
        }
        let channel = |v: u8| if background > 0.179 { v.saturating_sub(16) } else { v.saturating_add(16) };
        color = egui::Color32::from_rgb(channel(color.r()), channel(color.g()), channel(color.b()));
    }
    color
}

pub fn workflow_controls(app:&mut PhotocraftApp,ui:&mut egui::Ui)->Vec<(&'static str,Value)>{
    let settings=app.session.prefs().gen_studio.clone();
    let available=settings.engine==photocraft_engine::genstudio::Engine::Comfy;
    let mut mode=if settings.workflow_mode(){"workflow"}else{"preset"};
    let mut actions=Vec::new();let mut info=json!({"mode":mode,"path":settings.studio_workflow,"available":available});
    ui.horizontal(|ui|{
        ui.spacing_mut().item_spacing.x=8.0;
        ui.label("Processing");
        let combo=egui::ComboBox::from_id_salt("gengate-studio-processing").width(170.0)
            .selected_text(if mode=="workflow"{"ComfyUI workflow"}else{"Preset"}).show_ui(ui,|ui|{
                ui.selectable_value(&mut mode,"preset","Preset");
                ui.add_enabled_ui(available,|ui|ui.selectable_value(&mut mode,"workflow","ComfyUI workflow"));
            });
        info["selector"]=rect_json(combo.response.rect);
        if mode!=if settings.workflow_mode(){"workflow"}else{"preset"} {
            if mode=="workflow" && settings.studio_workflow.is_empty(){actions.push(("genstudio.studio.workflow.browse",json!({})));}
            else{actions.push(("genstudio.studio.workflow.set",json!({"mode":mode})));}
        }
        if available {
            let button=ui.button("Open workflow…").on_hover_text("Choose JSON exported with Export (API) in ComfyUI");
            info["browse"]=rect_json(button.rect);
            if button.clicked(){actions.push(("genstudio.studio.workflow.browse",json!({})));}
            if !settings.studio_workflow.is_empty(){
                let label=std::path::Path::new(&settings.studio_workflow).file_stem().and_then(|n|n.to_str()).unwrap_or("Workflow");
                ui.add(egui::Label::new(label).truncate()).on_hover_text(&settings.studio_workflow);
            }
        } else {ui.add(egui::Label::new("Choose ComfyUI in GenGate Settings to use workflows").truncate());}
    });
    app.studio.workflow_controls=info;
    actions
}

pub fn show(app: &mut PhotocraftApp, ui: &mut egui::Ui) -> Vec<(&'static str, Value)> {
    let model = sync_model(app);
    let mut cache = std::mem::take(&mut app.studio);
    cache.rects.clear();
    cache.file_hover = None;
    let mut actions = Vec::new();
    let state = &mut app.ui.gen_studio.workspace;
    let tokens = crate::theme::Tokens::get(ui.ctx());
    let width = ui.available_width();
    let main_width = (width * 0.42).max(170.0).min((width - 360.0).max(170.0));
    let row_height = (ui.available_height() - 158.0).max(205.0);
    let control_height = ui.spacing().interact_size.y.max(24.0);
    let gap = 8.0;
    let prompt_width = (width - main_width - gap).max(240.0);
    let prompt_height = (row_height - control_height - gap).max(170.0);
    let selection_available = !photocraft_engine::genstudio_ai::selected_bounds(&app.session).is_empty();
    let text_only = cache.main.is_none();
    let mut controls = json!({});
    let (row, _) = ui.allocate_exact_size(vec2(width, row_height), Sense::hover());
    let main_rect = Rect::from_min_size(row.min, vec2(main_width, row_height));
    let prompt_rect = Rect::from_min_size(pos2(main_rect.right() + gap, row.top()), vec2(prompt_width, prompt_height));
    let buttons = Rect::from_min_size(pos2(prompt_rect.left(), row.bottom() - control_height), vec2(prompt_width, control_height));
    ui.scope_builder(egui::UiBuilder::new().max_rect(main_rect), |ui| {
        slot_widget(ui, &mut cache, Slot::Main, main_rect.size(), selection_available, &mut actions);
    });
    ui.painter().rect_filled(prompt_rect, tokens.radius, tokens.field);
    ui.scope_builder(egui::UiBuilder::new().max_rect(prompt_rect.shrink(4.0)), |ui| {
        let font = if state.monospace { egui::FontId::monospace(14.0) } else { egui::FontId::proportional(14.0) };
        let mut layouter = |ui: &egui::Ui, text: &dyn egui::TextBuffer, wrap: f32| {
            let mut job = egui::text::LayoutJob::default();
            job.wrap.max_width = wrap;
            let mut rest = text.as_str();
            let normal = egui::TextFormat { font_id: font.clone(), color: tokens.text, ..Default::default() };
            while let Some((start, _, suffix, _)) = marker(rest) {
                job.append(rest.get(..start).unwrap_or_default(), 0.0, normal.clone());
                let tail = rest.get(start..).unwrap_or_default();
                if let Some(end) = tail.find(suffix) {
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
        let scroll = egui::ScrollArea::vertical()
            .id_salt("genstudio-prompt-scroll")
            .auto_shrink([false, false])
            .min_scrolled_height(prompt_height - 8.0)
            .max_height(prompt_height - 8.0)
            .show(ui, |ui| {
                egui::TextEdit::multiline(&mut state.prompt)
                    .id_salt("genstudio-prompt")
                    .hint_text("Prompt goes here…")
                    .frame(egui::Frame::NONE)
                    .margin(0.0)
                    .desired_width(ui.available_width())
                    .min_size(vec2(ui.available_width(), prompt_height - 8.0))
                    .desired_rows(1)
                    .char_limit(12000)
                    .layouter(&mut layouter)
                    .show(ui)
            });
        let output = scroll.inner;
        let mut edit = output.state;
        let mut inserted = false;
        if output.response.dnd_hover_payload::<crate::genstudio_lora_ui::Drag>().is_some() {
            ui.painter().rect_stroke(prompt_rect, ui.visuals().widgets.inactive.corner_radius,
                egui::Stroke::new(2.0, crate::theme::Tokens::get(ui.ctx()).accent), egui::StrokeKind::Inside);
        }
        if egui::DragAndDrop::payload::<crate::genstudio_lora_ui::Drag>(ui.ctx()).is_some()
            && let Some(payload)=output.response.dnd_release_payload::<crate::genstudio_lora_ui::Drag>() {
            crate::genstudio_lora_ui::append(&mut state.prompt,&payload.triggers);
            edit.cursor.set_char_range(Some(egui::text::CCursorRange::one(egui::text::CCursor::new(state.prompt.chars().count()))));
            inserted = true;
        }
        cache.prompt_rect = Some(prompt_rect);
        if let Some(payload) = output.response.dnd_release_payload::<RefDrag>()
            && let Some(index) = ids(&cache).iter().position(|id| *id == payload.0)
        {
            let at = ui
                .ctx()
                .pointer_latest_pos()
                .map(|p| usize::from(output.galley.cursor_from_pos(p - output.galley_pos).index))
                .unwrap_or_else(|| state.prompt.chars().count());
            let cursor = insert_for(&mut state.prompt, at, index + 1, model);
            edit.cursor.set_char_range(Some(egui::text::CCursorRange::one(egui::text::CCursor::new(cursor))));
            inserted = true;
        }
        if inserted {
            edit.store(ui.ctx(), output.response.id);
            output.response.request_focus();
        }
    });
    ui.painter().rect_stroke(prompt_rect, tokens.radius, egui::Stroke::new(1.0, tokens.field_border), egui::StrokeKind::Inside);
    ui.scope_builder(egui::UiBuilder::new().max_rect(buttons).layout(egui::Layout::left_to_right(egui::Align::Center)), |ui| {
        ui.spacing_mut().item_spacing = vec2(gap, 0.0);
        ui.spacing_mut().interact_size.y = control_height;
        let (group, _) = ui.allocate_exact_size(vec2(82.0, control_height), Sense::hover());
        ui.painter().rect_filled(group, tokens.radius_sm, tokens.field);
        ui.scope_builder(egui::UiBuilder::new().max_rect(group).layout(egui::Layout::left_to_right(egui::Align::Center)), |ui| {
            ui.spacing_mut().item_spacing = vec2(0.0, 0.0);
            let visuals = &mut ui.style_mut().visuals;
            for widget in [&mut visuals.widgets.inactive, &mut visuals.widgets.hovered, &mut visuals.widgets.active] {
                widget.bg_stroke = egui::Stroke::NONE;
                widget.bg_fill = egui::Color32::TRANSPARENT;
                widget.weak_bg_fill = egui::Color32::TRANSPARENT;
            }
            let minus = ui.add_enabled_ui(state.batch > 1, |ui| ui.add_sized(vec2(24.0, control_height), egui::Button::new("\u{2212}").frame(false))).inner;
            if minus.clicked() {
                state.batch = state.batch.saturating_sub(1).max(1);
            }
            let number = ui.add_sized(vec2(34.0, control_height), egui::DragValue::new(&mut state.batch).range(1..=8).speed(0.1));
            let plus = ui.add_enabled_ui(state.batch < 8, |ui| ui.add_sized(vec2(24.0, control_height), egui::Button::new("+").frame(false))).inner;
            if plus.clicked() {
                state.batch = state.batch.saturating_add(1).min(8);
            }
            controls["batchParts"] = json!([rect_json(minus.rect), rect_json(number.rect), rect_json(plus.rect)]);
        });
        ui.painter().rect_stroke(group, tokens.radius_sm, egui::Stroke::new(1.0, tokens.field_border), egui::StrokeKind::Inside);
        controls["batch"] = rect_json(group);
        if text_only {
            let resolution = ui
                .add_sized(vec2(32.0, control_height), egui::Button::new(if state.resolution == 2048 { "2k" } else { "1k" }))
                .on_hover_text("Output resolution");
            controls["resolution"] = rect_json(resolution.rect);
            if resolution.clicked() {
                state.resolution = if state.resolution == 2048 { 1024 } else { 2048 };
            }
            let aspect = egui::ComboBox::from_id_salt("genstudio-aspect").width(60.0).selected_text(&state.aspect_ratio).show_ui(ui, |ui| {
                for ratio in ASPECTS {
                    ui.selectable_value(&mut state.aspect_ratio, ratio.to_owned(), ratio);
                }
            });
            controls["aspect"] = rect_json(aspect.response.rect);
        }
        let text_mode =
            ui.add_sized(vec2(42.0, control_height), egui::Button::new(if state.monospace { "{Aa}" } else { "Aa" })).on_hover_text("Switch prompt font");
        controls["textMode"] = rect_json(text_mode.rect);
        if text_mode.clicked() {
            state.monospace = !state.monospace;
        }
        let loras=ui.add_enabled(crate::genstudio_lora_ui::available(&app.session.prefs().gen_studio.models_dir),egui::Button::new("LoRAs").min_size(vec2(54.0,control_height)));
        controls["loras"]=rect_json(loras.rect);
        if loras.clicked(){actions.push(("genstudio.loras.open",json!({})));}
        ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
            let running = app.session.jobs().iter().any(|j| j.command == "genstudio.studio.generate" && j.state == "running");
            let custom=app.session.prefs().gen_studio.workflow_mode();
            let ready = !running && cache.loading.is_empty() && (custom || !state.prompt.trim().is_empty())
                && (custom || model.id!="fibo-2-edit-turbo" || !ids(&cache).is_empty())
                && (!custom || !app.session.prefs().gen_studio.studio_workflow.is_empty());
            let generate = ui.add_enabled(
                ready,
                egui::Button::new(egui::RichText::new("Generate").color(accent_label(&tokens))).min_size(vec2(72.0, control_height)).fill(tokens.accent),
            );
            controls["generate"] = rect_json(generate.rect);
            if generate.clicked() {
                actions.push(("genstudio.studio.submit", json!({})));
            }
            if let Some(job) = app.session.jobs().into_iter().find(|j| j.command == "genstudio.studio.generate" && j.state == "running")
                && ui.button("Cancel").clicked()
            {
                actions.push(("jobs.cancel", json!({"job":job.id.0})));
            }
        });
    });
    ui.add_space(12.0);
    ui.small("Reference images");
    ui.add_space(4.0);
    let mut visible: Vec<Slot> =
        cache.extras.iter().enumerate().take(model.reference_limit.saturating_sub(1)).filter(|(i, r)| r.is_some() || cache.loading.contains_key(&Slot::Extra(*i))).map(|(i, _)| Slot::Extra(i)).collect();
    if let Some(blank) = next_slot(&cache) {
        visible.push(blank);
    }
    egui::ScrollArea::horizontal().id_salt("genstudio-refs").show(ui, |ui| {
        ui.horizontal(|ui| {
            for slot in visible {
                slot_widget(ui, &mut cache, slot, vec2(92.0, 104.0), false, &mut actions);
            }
        });
    });
    let list: Vec<_> = cache
        .main
        .iter()
        .chain(cache.extras.iter().take(model.reference_limit.saturating_sub(1)).filter_map(Option::as_ref))
        .enumerate()
        .map(|(i, r)| json!({"id":r.id,"number":i+1,"chip":model.chip(i+1),"name":r.name,"width":r.image.width(),"height":r.image.height()}))
        .collect();
    cache.info = json!({"model":model.id,"referenceLimit":model.reference_limit,"referenceSyntax":model.chip(1),"aspectRatio":state.aspect_ratio,"textOnly":text_only,"fileHover":cache.file_hover.map(|slot|match slot {Slot::Main=>json!("main"),Slot::Extra(i)=>json!(i)}),"selectionAvailable":selection_available,"controls":controls,"prompt":state.prompt,"batch":state.batch,"resolution":state.resolution,"monospace":state.monospace,"references":list,
            "loading":!cache.loading.is_empty(),"promptRect":cache.prompt_rect.map(|r|[r.min.x,r.min.y,r.width(),r.height()]),
        "slots":cache.rects.iter().map(|(s,r)|json!({"slot":match s {Slot::Main=>json!("main"),Slot::Extra(i)=>json!(i)},"rect":[r.min.x,r.min.y,r.width(),r.height()]})).collect::<Vec<_>>()});
    app.studio = cache;
    app.studio.info["workflow"]=app.studio.workflow_controls.clone();
    app.studio.info["loras"]=crate::genstudio_lora_ui::info(app);
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
    sync_model(app);
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
    fn flux_chips_are_zero_based_and_track_reference_identity() {
        let flux = MODELS[1];
        let mut text = "Before after".to_owned();
        insert_for(&mut text, 7, 2, flux);
        assert_eq!(text, "Before <ref_image_1>after");
        assert_eq!(compiled_for(&text, 2, flux).unwrap(), text);
        assert_eq!(renumber("<ref_image_0> + <ref_image_1>", &[20, 30], &[30]), " + <ref_image_0>");
        assert!(compiled_for("<ref_image_1>", 1, flux).is_err());
        assert!(compiled_for("<ref_image_18446744073709551615>", 10, flux).is_err());
        assert!(compiled_for("<ref_image_", 10, flux).is_err());
        assert_eq!(compiled_for("Use [Image 2]", 2, flux).unwrap(), "Use <ref_image_1>");
        assert_eq!(compiled_for("Use <ref_image_0>", 1, MODELS[0]).unwrap(), "Use image 1");
    }
    #[test]
    fn model_switch_migrates_chips_without_rewriting_json_or_losing_references() {
        let mut app = PhotocraftApp::new(photocraft_engine::Session::new(), crate::Services::default());
        app.ui.gen_studio.workspace.prompt = "[Image 1] <ref_image_1> {\"from\":\"ref_image_0\",\"bbox\":[1,2,3,4]}".into();
        app.session.edit_prefs(|p| p.gen_studio.model = MODELS[1].id.into());
        sync_model(&mut app);
        assert_eq!(app.studio.reference_limit, 10);
        assert_eq!(app.ui.gen_studio.workspace.prompt, "<ref_image_0> <ref_image_1> {\"from\":\"ref_image_0\",\"bbox\":[1,2,3,4]}");
        app.session.edit_prefs(|p| p.gen_studio.model = MODELS[0].id.into());
        sync_model(&mut app);
        assert!(app.ui.gen_studio.workspace.prompt.starts_with("[Image 1] [Image 2]"));
        assert!(app.ui.gen_studio.workspace.prompt.contains("\"from\":\"ref_image_0\""));
    }
    #[test]
    fn defaults_are_ready_for_text_to_image_and_output_sizes_are_bounded() {
        let s = Workspace::default();
        assert_eq!(s.batch, 1);
        assert_eq!(s.resolution, 1024);
        assert!(!s.monospace);
        assert_eq!(dimensions(2048, None, "1:1"), (2048, 2048));
        assert_eq!(dimensions(1024, Some((1200, 800)), "1:1"), (1024, 672));
        assert_eq!(dimensions(2048, Some((1, 20000)), "1:1"), (256, 2048));
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
        assert!(harness.query_by_label("Use selection").is_none(), "no selection exists in this fixture");
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
    fn fixture() -> PhotocraftApp {
        let mut app = PhotocraftApp::new(photocraft_engine::Session::new(), crate::Services::default());
        app.ui.gen_studio.initialized = true;
        app.ui.gen_studio.workspace.prompt = "A quiet forest at sunrise".into();
        app
    }
    #[test]
    fn a_lora_drag_appends_triggers_without_moving_reference_chips_or_changing_adapters() {
        let mut app=fixture();app.ui.gen_studio.open=true;
        let mut harness=egui_kittest::Harness::builder().with_size(vec2(1200.0,900.0)).build_ui_state(|ui,app:&mut PhotocraftApp|{
            if ui.ctx().fonts(|f|f.families().contains(&egui::FontFamily::Name("medium".into()))){crate::genstudio_ui::show(app,ui.ctx());}
        },app);
        PhotocraftApp::setup_context(&harness.ctx,crate::theme::ThemeKind::Studio);harness.run_steps(6);
        let to=harness.state().studio.prompt_rect.unwrap().center();
        let from=pos2(10.0,10.0);
        harness.event(egui::Event::PointerMoved(from));
        harness.event(egui::Event::PointerButton{pos:from,button:egui::PointerButton::Primary,pressed:true,modifiers:egui::Modifiers::NONE});harness.run_steps(1);
        egui::DragAndDrop::set_payload(&harness.ctx,crate::genstudio_lora_ui::Drag{name:"Portrait helper".into(),triggers:"portrait, soft light".into()});
        harness.event(egui::Event::PointerMoved(to));harness.run_steps(2);
        harness.event(egui::Event::PointerButton{pos:to,button:egui::PointerButton::Primary,pressed:false,modifiers:egui::Modifiers::NONE});harness.run_steps(2);
        assert!(harness.state().ui.gen_studio.workspace.prompt.ends_with("portrait, soft light"));
        assert!(crate::genstudio_lora_ui::selected(&harness.state().ui.gen_studio.workspace.loras,"").is_empty());
    }
    #[test]
    fn a_real_layers_panel_drag_loads_gengate_instead_of_reordering_the_document() {
        use egui_kittest::kittest::Queryable;
        let mut session=photocraft_engine::Session::new();
        session.execute("file.new",json!({"width":64,"height":48})).unwrap();
        session.execute("layer.new.layer",json!({"name":"GenGate drag fixture"})).unwrap();
        session.execute("edit.fill",json!({"color":"#cc5533"})).unwrap();
        let original=session.active().unwrap().doc.clone();
        let mut h=egui_kittest::Harness::builder().with_size(vec2(1600.0,1000.0)).with_max_steps(64).build_eframe(move|cc|{
            PhotocraftApp::setup_context(&cc.egui_ctx,crate::theme::ThemeKind::Studio);
            let mut app=PhotocraftApp::new(session,crate::Services::default());
            app.ui.gen_studio.open=true;app.ui.gen_studio.initialized=true;
            app.ui.gen_studio.workspace.prompt="Clear on ordinary layer drop".into();
            app
        });
        h.run_steps(8);
        let from=h.get_by_role_and_label(egui::accesskit::Role::Button,"GenGate drag fixture").rect().center();
        let to=h.state().studio.prompt_rect.unwrap().center();
        h.event(egui::Event::PointerMoved(from));h.run_steps(1);
        h.event(egui::Event::PointerButton{pos:from,button:egui::PointerButton::Primary,pressed:true,modifiers:egui::Modifiers::NONE});h.run_steps(1);
        for i in 1..=8 {h.event(egui::Event::PointerMoved(from+(to-from)*(i as f32/8.0)));h.run_steps(1);}
        h.event(egui::Event::PointerButton{pos:to,button:egui::PointerButton::Primary,pressed:false,modifiers:egui::Modifiers::NONE});
        for _ in 0..100 {
            h.run_steps(2);
            if h.state().studio.main.as_ref().is_some_and(|r|r.name=="GenGate drag fixture") {break;}
            std::thread::sleep(std::time::Duration::from_millis(2));
        }
        assert_eq!(h.state().studio.main.as_ref().map(|r|r.name.as_str()),Some("GenGate drag fixture"));
        assert!(h.state().ui.gen_studio.workspace.prompt.is_empty());
        assert_eq!(h.state().session.active().unwrap().doc.layers.len(),original.layers.len());
        assert_eq!(h.state().session.active().unwrap().doc.layers.iter().map(|l|l.id).collect::<Vec<_>>(),original.layers.iter().map(|l|l.id).collect::<Vec<_>>());
    }
    fn rect(value: &Value) -> Rect {
        Rect::from_min_size(
            pos2(value[0].as_f64().unwrap() as f32, value[1].as_f64().unwrap() as f32),
            vec2(value[2].as_f64().unwrap() as f32, value[3].as_f64().unwrap() as f32),
        )
    }
    #[test]
    fn aspect_ratios_apply_only_to_empty_canvas_and_validate_before_mutating() {
        for aspect in ASPECTS {
            for side in [1024, 2048] {
                let (w, h) = dimensions(side, None, aspect);
                assert_eq!(w.max(h), side);
                assert_eq!((w % 32, h % 32), (0, 0));
                let (a, b) = aspect.split_once(':').unwrap();
                let ratio = a.parse::<f64>().unwrap() / b.parse::<f64>().unwrap();
                assert!((f64::from(w) / f64::from(h) - ratio).abs() < 0.06);
                assert_eq!(dimensions(side, Some((300, 200)), aspect), dimensions(side, Some((300, 200)), "1:1"));
            }
        }
        let mut app = fixture();
        assert!(menu(&mut app, "genstudio.studio.configure", &json!({"aspectRatio":"9:16"})).unwrap().is_ok());
        assert!(menu(&mut app, "genstudio.studio.configure", &json!({"aspectRatio":"0:0","batch":8})).unwrap().is_err());
        assert_eq!(app.ui.gen_studio.workspace.aspect_ratio, "9:16");
        assert_eq!(app.ui.gen_studio.workspace.batch, 1);
    }
    #[test]
    fn selection_button_lives_inside_empty_canvas_and_reference_hides_size_controls() {
        use egui_kittest::kittest::Queryable;
        let mut app = fixture();
        app.run("file.new", json!({"width":64,"height":64})).unwrap();
        app.run("select.rect", json!({"x":2,"y":2,"width":20,"height":20})).unwrap();
        let mut h = egui_kittest::Harness::builder().with_size(vec2(900.0, 600.0)).build_ui_state(
            |ui, app: &mut PhotocraftApp| {
                if ui.ctx().fonts(|f| f.families().contains(&egui::FontFamily::Name("medium".into()))) {
                    show(app, ui);
                }
            },
            app,
        );
        PhotocraftApp::setup_context(&h.ctx, crate::theme::ThemeKind::Studio);
        h.run_steps(4);
        assert!(h.state().studio.rects[0].1.contains_rect(h.get_by_label("Use selection").rect()));
        assert!(h.state().studio.info["controls"]["aspect"].is_array());
        h.get_by_label("Aa").click();
        h.run_steps(2);
        assert!(h.state().ui.gen_studio.workspace.monospace);
        h.get_by_label("{Aa}").click();
        h.run_steps(2);
        assert!(!h.state().ui.gen_studio.workspace.monospace);
        let before = h.state().studio.prompt_rect.unwrap();
        let controls_before = h.state().studio.info["controls"]["generate"].clone();
        h.state_mut().ui.gen_studio.workspace.prompt = "A line of JSON prompt\n".repeat(250);
        h.run_steps(3);
        assert!(
            (before.height() - h.state().studio.prompt_rect.unwrap().height()).abs() < 2.0,
            "long prompts scroll inside their field: {before:?} -> {:?}",
            h.state().studio.prompt_rect
        );
        assert_eq!(controls_before, h.state().studio.info["controls"]["generate"], "scrollbars must not move the controls");
        h.get_by_label("+").click();
        h.run_steps(2);
        assert_eq!(h.state().ui.gen_studio.workspace.batch, 2);
        let image = Image::from_u8(8, 8, photocraft_codecs::ChannelLayout::Rgba, [120, 80, 20, 255].repeat(64)).unwrap();
        h.state_mut().studio.main =
            Some(Reference { id: 1, name: "Fixture".into(), png: core::encoded(&image).unwrap(), image, texture: None, selection: false });
        h.run_steps(3);
        assert!(
            h.state().studio.rects[0].1.contains_rect(h.get_by_label("Use selection").rect()),
            "a loaded reference must still offer the document selection"
        );
        assert!(h.state().studio.info["controls"]["aspect"].is_null());
        assert!(h.state().studio.info["controls"]["resolution"].is_null());
        h.state_mut().studio.main = None;
        h.state_mut().session.execute("select.deselect", json!({})).unwrap();
        h.run_steps(3);
        assert!(h.query_by_label("Use selection").is_none());
        assert!(h.state().studio.info["controls"]["resolution"].is_array());
    }
    #[test]
    fn workspace_layout_scales_in_every_theme_and_accent_label_has_contrast() {
        let directory = std::env::var_os("GENSTUDIO_UI_FIXTURES").map(std::path::PathBuf::from);
        if let Some(dir) = &directory {
            std::fs::create_dir_all(dir).unwrap();
        }
        for theme in crate::theme::ThemeKind::ALL {
            let mut ready = false;
            let mut h = egui_kittest::Harness::builder().with_size(vec2(720.0, 460.0)).wgpu().build_ui_state(
                move |ui, app: &mut PhotocraftApp| {
                    if !ready {
                        PhotocraftApp::setup_context(ui.ctx(), theme);
                        ready = true;
                        return;
                    }
                    show(app, ui);
                },
                fixture(),
            );
            h.run_steps(5);
            let small_main = h.state().studio.rects[0].1;
            let small_prompt = h.state().studio.prompt_rect.unwrap();
            for (name, size) in [("small", vec2(720.0, 460.0)), ("large", vec2(1200.0, 800.0))] {
                h.set_size(size);
                h.run_steps(5);
                let info = &h.state().studio.info;
                let generate = rect(&info["controls"]["generate"]);
                let main = h.state().studio.rects[0].1;
                assert!(generate.max.x <= size.x && generate.max.y <= size.y, "{theme:?} {info}");
                assert!((generate.max.y - main.max.y).abs() < 0.6, "controls align with canvas: {theme:?} {generate:?} {main:?}");
                let prompt = h.state().studio.prompt_rect.unwrap();
                let batch = rect(&info["controls"]["batch"]);
                let resolution = rect(&info["controls"]["resolution"]);
                let horizontal_gap = prompt.left() - main.right();
                let vertical_gap = batch.top() - prompt.bottom();
                assert!(
                    (horizontal_gap - 8.0).abs() < 0.6 && (vertical_gap - horizontal_gap).abs() < 0.6,
                    "same horizontal and vertical gaps: {horizontal_gap} / {vertical_gap}"
                );
                assert!((prompt.right() - generate.right()).abs() < 0.6, "prompt and Generate share the right edge");
                assert!((batch.top() - generate.top()).abs() < 0.6 && (batch.bottom() - generate.bottom()).abs() < 0.6, "batch aligns with all controls");
                assert!((resolution.left() - batch.right() - 8.0).abs() < 0.6, "batch uses the same control gap");
                let parts: Vec<_> = info["controls"]["batchParts"].as_array().unwrap().iter().map(rect).collect();
                assert!(parts.windows(2).all(|p| (p[0].height() - p[1].height()).abs() < 1.0), "equal batch heights: {parts:?}");
                if name == "large" {
                    assert!(main.width() > small_main.width() + 100.0 && main.height() > small_main.height() + 200.0);
                    let prompt = h.state().studio.prompt_rect.unwrap();
                    assert!(prompt.width() > small_prompt.width() + 100.0 && prompt.height() > small_prompt.height() + 200.0);
                }
                if let Some(dir) = &directory {
                    h.render().unwrap().save(dir.join(format!("{}-{name}.png", theme.id()))).unwrap();
                }
                let at = main.center();
                h.input_mut().hovered_files = vec![egui::HoveredFile { path: None, mime: "image/png".into() }];
                h.event(egui::Event::PointerMoved(at));
                h.run_steps(2);
                assert_eq!(h.state().studio.info["fileHover"], "main");
                if name == "large"
                    && let Some(dir) = &directory
                {
                    h.render().unwrap().save(dir.join(format!("{}-drop-hover.png", theme.id()))).unwrap();
                }
                h.input_mut().hovered_files.clear();
                h.run_steps(2);
            }
            let tokens = crate::theme::Tokens::for_kind(theme);
            let (a, b) = (luminance(tokens.accent), luminance(accent_label(&tokens)));
            assert!((a.max(b) + 0.05) / (a.min(b) + 0.05) >= 4.5, "{theme:?}");
        }
    }
}
