//! A separate, themed LoRA browser. Aliases never rename checkpoint files.
use crate::PhotocraftApp;
use photocraft_engine::genstudio_lora::{Entry, Selection, validate};
use serde_json::{Value, json};
use std::collections::BTreeMap;

#[derive(Clone, Debug, Default, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct Metadata { pub name: String, pub triggers: String }
#[derive(Clone, Debug, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct Choice { pub enabled: bool, pub strength: f64 }
impl Default for Choice { fn default() -> Self { Self { enabled: false, strength: 1.0 } } }
#[derive(Clone, Debug, Default, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(default)]
pub struct Library {
    folder: String,
    choices: BTreeMap<String, Choice>,
    metadata: BTreeMap<String, Metadata>,
}
#[derive(Clone, Debug, Default, PartialEq)]
struct Edit { path: String, value: Metadata }
#[derive(Clone, Debug, Default, PartialEq)]
struct Cache { root: String, folder: Option<String>, pending: Option<u64>, entries: Vec<Entry>, error: String, edit: Option<Edit>, info: Value }
#[derive(Clone, Debug, Default, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(default)]
pub struct State {
    pub open: bool,
    libraries: BTreeMap<String, Library>,
    #[serde(skip)] cache: Cache,
}
fn root(app: &PhotocraftApp) -> String { app.session.prefs().gen_studio.models_dir.clone() }
pub fn available(models: &str) -> bool {
    if models.trim().is_empty() { return false; }
    #[cfg(not(target_arch="wasm32"))] { std::path::Path::new(models).join("loras").is_dir() }
    #[cfg(target_arch="wasm32")] { let _ = models; false }
}
pub fn selected(state: &State, models: &str) -> Vec<Selection> {
    state.libraries.get(models).map(|lib| lib.choices.iter().filter(|(_, c)| c.enabled)
        .map(|(path,c)| Selection { path: path.clone(), strength: c.strength }).collect()).unwrap_or_default()
}
pub fn restore(state: &mut State, models: &str, values: &[Selection]) -> Result<(), String> {
    validate(values)?;
    let lib = state.libraries.entry(models.into()).or_default();
    for value in lib.choices.values_mut() { value.enabled = false; }
    for value in values { lib.choices.insert(value.path.clone(), Choice { enabled: true, strength: value.strength }); }
    Ok(())
}
pub fn append(prompt: &mut String, triggers: &str) {
    let triggers = triggers.split(',').map(str::trim).filter(|s| !s.is_empty()).collect::<Vec<_>>().join(", ");
    if triggers.is_empty() || prompt.len().saturating_add(triggers.len()).saturating_add(2) > 12000 { return; }
    if !prompt.is_empty() && !prompt.ends_with(char::is_whitespace) { prompt.push_str(", "); }
    prompt.push_str(&triggers);
}
#[derive(Clone)]
pub struct Drag { pub triggers: String }
pub fn info(app: &PhotocraftApp) -> Value {
    let state = &app.ui.gen_studio.workspace.loras;
    json!({"open": state.open, "available": available(&root(app)), "selected":selected(state,&root(app)), "browser":state.cache.info})
}
pub fn menu(app: &mut PhotocraftApp, id: &str, p: &Value) -> Option<Result<Value, String>> {
    if !id.starts_with("genstudio.loras.") || id == "genstudio.loras.list" { return None; }
    Some((|| {
        let models = root(app);
        let mut state = app.ui.gen_studio.workspace.loras.clone();
        let path = p.get("path").and_then(Value::as_str).unwrap_or("");
        match id {
            "genstudio.loras.open" => {
                if !available(&models) { return Err("The selected Models folder has no loras directory".into()); }
                state.open = p.get("open").map_or(Ok(true), |v| v.as_bool().ok_or("Open must be boolean"))?;
            }
            "genstudio.loras.configure" => {
                let lib = state.libraries.entry(models.clone()).or_default();
                if let Some(v) = p.get("folder") {
                    let folder = v.as_str().filter(|s| s.len()<=2048 && !s.contains(['\0', '\\', ':']) && (s.is_empty() || !s.split('/').any(|s| matches!(s,""|"."|"..")))).ok_or("Stay inside Models/loras")?;
                    lib.folder = folder.into();
                }
                if !path.is_empty() {
                    validate(&[Selection { path: path.into(), strength: 1.0 }])?;
                    let choice = lib.choices.entry(path.into()).or_default();
                    if let Some(v)=p.get("enabled") { choice.enabled=v.as_bool().ok_or("Enabled must be boolean")?; }
                    if let Some(v)=p.get("strength") { choice.strength=v.as_f64().ok_or("Invalid LoRA strength")?; }
                    validate(&[Selection { path:path.into(), strength:choice.strength }])?;
                }
                validate(&selected(&state,&models))?;
            }
            "genstudio.loras.settings" => {
                validate(&[Selection { path: path.into(), strength: 1.0 }])?;
                let value = state.libraries.entry(models.clone()).or_default().metadata.get(path).cloned().unwrap_or_default();
                state.cache.edit=Some(Edit { path:path.into(), value });
            }
            "genstudio.loras.metadata.save" => {
                validate(&[Selection { path: path.into(), strength: 1.0 }])?;
                let name=p["name"].as_str().filter(|s|s.len()<=200 && !s.contains('\0')).ok_or("Name must be at most 200 bytes")?;
                let triggers=p["triggers"].as_str().filter(|s|s.len()<=4000 && !s.contains('\0')).ok_or("Trigger words must be at most 4000 bytes")?;
                let metadata=Metadata { name:name.trim().into(), triggers:triggers.into() };
                state.libraries.entry(models.clone()).or_default().metadata.insert(path.into(),metadata.clone());
                if let Some(edit)=state.cache.edit.as_mut().filter(|e|e.path==path) { edit.value=metadata; }
            }
            "genstudio.loras.append" => {
                let lib=state.libraries.entry(models).or_default();
                if !lib.choices.get(path).is_some_and(|c|c.enabled) { return Err("Enable this LoRA to insert its trigger words".into()); }
                let triggers=lib.metadata.get(path).map_or("",|m|m.triggers.as_str());
                append(&mut app.ui.gen_studio.workspace.prompt,triggers);
            }
            _ => return Err("Unknown LoRA browser action".into()),
        }
        app.ui.gen_studio.workspace.loras=state;
        Ok(info(app))
    })())
}
pub fn show(app: &mut PhotocraftApp, ctx: &egui::Context) {
    let models=root(app);
    let mut state=std::mem::take(&mut app.ui.gen_studio.workspace.loras);
    if state.cache.root!=models { state.cache=Cache { root:models.clone(), ..Default::default() }; }
    if let Some(id)=state.cache.pending
        && let Some(job)=app.session.jobs_with_recent().into_iter().find(|j|j.id.0==id && j.state!="running") {
        state.cache.pending=None;
        if job.state=="done" {
            match serde_json::from_value::<Vec<Entry>>(job.result.unwrap_or(Value::Null)["entries"].clone()) {
                Ok(rows)=>{state.cache.entries=rows;state.cache.error.clear();}
                Err(e)=>state.cache.error=e.to_string(),
            }
        } else { state.cache.error=job.error.unwrap_or("Could not read the LoRA folder".into()); }
    }
    if state.open && available(&models) {
        let lib=state.libraries.entry(models.clone()).or_default();
        if state.cache.folder.as_deref()!=Some(lib.folder.as_str()) && state.cache.pending.is_none() {
            match app.run("genstudio.loras.list",json!({"folder":lib.folder})) {
                Ok(v)=>{state.cache.pending=v["job"].as_u64();state.cache.folder=Some(lib.folder.clone());state.cache.entries.clear();}
                Err(e)=>{state.cache.error=e;state.cache.folder=Some(lib.folder.clone());}
            }
        }
        let mut open=state.open;
        let mut actions=Vec::new();
        egui::Window::new("LoRAs").id(egui::Id::new("gengate-loras")).default_pos(ctx.content_rect().min+egui::vec2(24.0,100.0)).default_size(egui::vec2(570.0,380.0)).min_width(410.0).open(&mut open).show(ctx,|ui|{
            ui.horizontal(|ui|{
                if ui.add_enabled(!lib.folder.is_empty(),egui::Button::new("←")).on_hover_text("Parent folder").clicked() {
                    let parent=lib.folder.rsplit_once('/').map_or("",|(a,_)|a);
                    actions.push(("genstudio.loras.configure",json!({"folder":parent})));
                }
                ui.label(if lib.folder.is_empty(){"loras".into()}else{format!("loras/{}",lib.folder)});
                if ui.button("↻").on_hover_text("Refresh").clicked(){state.cache.folder=None;}
            });
            ui.separator();
            if state.cache.pending.is_some(){ui.spinner();ctx.request_repaint_after(std::time::Duration::from_millis(80));}
            if !state.cache.error.is_empty(){ui.colored_label(crate::theme::Tokens::get(ctx).text_dim,&state.cache.error);}
            let mut rows=Vec::new();
            egui::ScrollArea::vertical().id_salt("gengate-loras-files").show_rows(ui,26.0,state.cache.entries.len(),|ui,range|{
                let name_width=(ui.available_width()-122.0-ui.spacing().item_spacing.x*3.0).max(180.0);
                egui::Grid::new("gengate-loras-table").num_columns(4).striped(true).min_row_height(26.0).show(ui,|ui|{
                    if range.start==0 {ui.label("");ui.small("Strength");ui.small("Name");ui.label("");ui.end_row();}
                    for entry in state.cache.entries.get(range).unwrap_or_default() {
                        let metadata=lib.metadata.get(&entry.path).cloned().unwrap_or_default();
                        if entry.directory {
                            ui.label("");ui.label("");
                            let r=ui.add_sized([name_width,26.0],egui::Label::new(format!("▸ {}",entry.name)).truncate().halign(egui::Align::Min).sense(egui::Sense::click()));
                            rows.push(json!({"path":entry.path,"directory":true,"nameRect":[r.rect.min.x,r.rect.min.y,r.rect.width(),r.rect.height()]}));
                            if r.clicked(){actions.push(("genstudio.loras.configure",json!({"folder":entry.path})));}
                            ui.label("");ui.end_row();continue;
                        }
                        let choice=lib.choices.entry(entry.path.clone()).or_default();
                        let check=ui.checkbox(&mut choice.enabled,"");
                        let strength=ui.add_sized([58.0,26.0],egui::DragValue::new(&mut choice.strength).range(-20.0..=20.0).speed(0.05));
                        let name=if metadata.name.is_empty(){entry.name.as_str()}else{metadata.name.as_str()};
                        let label=egui::Label::new(name).truncate().halign(egui::Align::Min).sense(egui::Sense::click_and_drag());
                        let r=ui.add_sized([name_width,26.0],label).on_hover_text(&entry.path);
                        if r.drag_started(){r.dnd_set_drag_payload(Drag { triggers:metadata.triggers.clone() });}
                        if r.clicked() && choice.enabled {actions.push(("genstudio.loras.append",json!({"path":entry.path})));}
                        let settings=ui.button("⚙").on_hover_text("LoRA settings");
                        if settings.clicked(){actions.push(("genstudio.loras.settings",json!({"path":entry.path})));}
                        rows.push(json!({"path":entry.path,"directory":false,"name":name,"enabled":choice.enabled,"strength":choice.strength,"triggers":metadata.triggers,
                            "checkRect":[check.rect.min.x,check.rect.min.y,check.rect.width(),check.rect.height()],
                            "strengthRect":[strength.rect.min.x,strength.rect.min.y,strength.rect.width(),strength.rect.height()],
                            "nameRect":[r.rect.min.x,r.rect.min.y,r.rect.width(),r.rect.height()],
                            "settingsRect":[settings.rect.min.x,settings.rect.min.y,settings.rect.width(),settings.rect.height()]}));
                        ui.end_row();
                    }
                });
            });
            state.cache.info=json!({"folder":lib.folder,"rows":rows,"loading":state.cache.pending.is_some(),"error":state.cache.error});
        });
        state.open=open;
        app.ui.gen_studio.workspace.loras=state;
        for (id,p) in actions { if let Some(Err(e))=menu(app,id,&p){crate::notices::error(app,e);} }
        state=std::mem::take(&mut app.ui.gen_studio.workspace.loras);
    }
    if let Some(mut edit)=state.cache.edit.take() {
        let saved=state.libraries.entry(models.clone()).or_default().metadata.get(&edit.path).cloned().unwrap_or_default();
        let mut open=true;
        let mut save=false;
        egui::Window::new("LoRA settings").id(egui::Id::new("gengate-lora-settings")).min_width(440.0).collapsible(false).resizable(false).open(&mut open).show(ctx,|ui|{
            let mut fields=Vec::new();
            egui::Grid::new("gengate-lora-metadata").show(ui,|ui|{
                ui.label("Name");let r=ui.add(egui::TextEdit::singleline(&mut edit.value.name).desired_width(310.0).min_size(egui::vec2(310.0,26.0)).char_limit(200));fields.push(r.rect);ui.end_row();
                ui.label("Trigger words");let r=ui.add(egui::TextEdit::singleline(&mut edit.value.triggers).desired_width(310.0).min_size(egui::vec2(310.0,26.0)).char_limit(4000).hint_text("Comma-separated trigger words"));fields.push(r.rect);ui.end_row();
            });
            let button=ui.add_enabled(saved!=edit.value,egui::Button::new("Save"));save=button.clicked();
            state.cache.info["settings"]=json!({"path":edit.path,"dirty":saved!=edit.value,"fields":fields.iter().map(|r|[r.min.x,r.min.y,r.width(),r.height()]).collect::<Vec<_>>(),"saveRect":[button.rect.min.x,button.rect.min.y,button.rect.width(),button.rect.height()]});
        });
        if save {state.libraries.entry(models).or_default().metadata.insert(edit.path.clone(),edit.value.clone());}
        if open{state.cache.edit=Some(edit);}
    }
    app.ui.gen_studio.workspace.loras=state;
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn restored_layers_replace_enabled_adapters_but_preserve_aliases() {
        let mut state=State::default();
        restore(&mut state,"models",&[Selection{path:"old.safetensors".into(),strength:1.0}]).unwrap();
        state.libraries.get_mut("models").unwrap().metadata.insert("new.safetensors".into(),Metadata{name:"My name".into(),triggers:"word".into()});
        restore(&mut state,"models",&[Selection{path:"new.safetensors".into(),strength:0.4}]).unwrap();
        assert_eq!(selected(&state,"models"),vec![Selection{path:"new.safetensors".into(),strength:0.4}]);
        let encoded=serde_json::to_vec(&state).unwrap();let decoded:State=serde_json::from_slice(&encoded).unwrap();
        assert_eq!(decoded.libraries,state.libraries);
        restore(&mut state,"models",&[]).unwrap();assert!(selected(&state,"models").is_empty());
    }
    #[test]
    fn trigger_words_append_without_inserting_an_adapter_command() {
        let mut prompt="A portrait".to_owned();append(&mut prompt," warm light, , detailed ");
        assert_eq!(prompt,"A portrait, warm light, detailed");
    }
}
