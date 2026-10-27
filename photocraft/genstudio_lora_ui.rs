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
struct Cache { root: String, folder: Option<String>, pending: Option<u64>, entries: Vec<Entry>, error: String, edit: Option<Edit>, info: Value, browser_position: crate::genstudio_ui::FloatingPosition, settings_position: crate::genstudio_ui::FloatingPosition }
#[derive(Clone, Debug, Default, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(default)]
pub struct State {
    pub open: bool,
    collapsed: bool,
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
pub struct Drag { pub name: String, pub triggers: String }
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
fn rect_value(rect: egui::Rect) -> Value { json!([rect.min.x,rect.min.y,rect.width(),rect.height()]) }
fn header_info(rect: egui::Rect, header: &crate::genstudio_ui::FloatingHeader) -> Value {
    json!({"rect":rect_value(rect),"headerRect":rect_value(header.band),"titleRect":rect_value(header.drag.rect),"closeRect":rect_value(header.close.rect)})
}

/// The whole cell is a stable drag target, independent of aliases and truncation.
fn name_cell(ui: &mut egui::Ui, width: f32, key: &str, text: &str, sense: egui::Sense) -> egui::Response {
    name_cell_icon(ui,width,key,text,sense,None)
}
fn name_cell_icon(ui: &mut egui::Ui, width:f32, key:&str, text:&str, sense:egui::Sense, icon:Option<&str>) -> egui::Response {
    let (rect, _) = ui.allocate_exact_size(egui::vec2(width,26.0),egui::Sense::hover());
    let response=ui.interact(rect,ui.id().with(("gengate-lora-cell",key)),sense);
    let inset=if icon.is_some(){24.0}else{4.0};
    let galley=egui::WidgetText::from(text).into_galley(ui,Some(egui::TextWrapMode::Truncate),(width-inset-4.0).max(0.0),egui::TextStyle::Body);
    let at=egui::pos2(rect.left()+inset,rect.center().y-galley.size().y*0.5);
    ui.painter().with_clip_rect(rect).galley(at,galley,crate::theme::Tokens::get(ui.ctx()).text);
    if let Some(icon)=icon {
        crate::icons::paint(ui,egui::Rect::from_center_size(egui::pos2(rect.left()+11.0,rect.center().y),egui::Vec2::splat(16.0)),icon,16.0,crate::theme::Tokens::get(ui.ctx()).icon);
    }
    response.widget_info(||egui::WidgetInfo::labeled(egui::WidgetType::Button,true,text));
    response
}

fn files(ui: &mut egui::Ui, lib: &mut Library, entries: &[Entry], actions: &mut Vec<(&'static str,Value)>) -> Vec<Value> {
    let mut rows=Vec::new();
    egui::ScrollArea::vertical().id_salt("gengate-loras-files").min_scrolled_height(92.0).show_rows(ui,28.0,entries.len(),|ui,range|{
        let gap=8.0;
        let settings_width=ui.spacing().interact_size.x.max(40.0);
        let name_width=(ui.available_width()-24.0-64.0-settings_width-3.0*gap).max(180.0);
        let mut divisions=None;
        let table=egui::Grid::new("gengate-loras-table").num_columns(4).striped(true).min_col_width(0.0).min_row_height(26.0).spacing([gap,2.0]).show(ui,|ui|{
            if range.start==0 {
                let a=name_cell(ui,24.0,"heading-enabled","",egui::Sense::hover());
                let b=name_cell(ui,64.0,"heading-strength","Strength",egui::Sense::hover());
                let c=name_cell(ui,name_width,"heading-name","Name",egui::Sense::hover());
                name_cell(ui,settings_width,"heading-settings","",egui::Sense::hover());ui.end_row();
                divisions=Some([a.rect.right()+gap*0.5,b.rect.right()+gap*0.5,c.rect.right()+gap*0.5]);
            }
            for entry in entries.get(range).unwrap_or_default() {
                let metadata=lib.metadata.get(&entry.path).cloned().unwrap_or_default();
                if entry.directory {
                    let a=name_cell(ui,24.0,"folder-enabled","",egui::Sense::hover());
                    let b=name_cell(ui,64.0,"folder-strength","",egui::Sense::hover());
                    let r=name_cell_icon(ui,name_width,&entry.path,&entry.name,egui::Sense::click(),Some("folder")).on_hover_cursor(egui::CursorIcon::PointingHand);
                    name_cell(ui,settings_width,"folder-settings","",egui::Sense::hover());
                    divisions=Some([a.rect.right()+gap*0.5,b.rect.right()+gap*0.5,r.rect.right()+gap*0.5]);
                    rows.push(json!({"path":entry.path,"directory":true,"nameRect":rect_value(r.rect)}));
                    if r.clicked(){actions.push(("genstudio.loras.configure",json!({"folder":entry.path})));}
                    ui.end_row();continue;
                }
                let choice=lib.choices.entry(entry.path.clone()).or_default();
                let cell=ui.allocate_ui_with_layout(egui::vec2(24.0,26.0),egui::Layout::left_to_right(egui::Align::Center),|ui|crate::widgets::checkbox(ui,&mut choice.enabled,""));
                let check=cell.inner;
                let strength=ui.add_sized([64.0,26.0],egui::DragValue::new(&mut choice.strength).range(-20.0..=20.0).speed(0.05));
                let name=if metadata.name.is_empty(){entry.name.as_str()}else{metadata.name.as_str()};
                let r=name_cell(ui,name_width,&entry.path,name,egui::Sense::click_and_drag()).on_hover_text(&entry.path).on_hover_cursor(egui::CursorIcon::Grab);
                r.dnd_set_drag_payload(Drag { name:name.into(), triggers:metadata.triggers.clone() });
                if r.clicked() && choice.enabled {actions.push(("genstudio.loras.append",json!({"path":entry.path})));}
                let settings=ui.add_sized([settings_width,26.0],egui::Button::new("\u{2699}")).on_hover_text("LoRA settings");
                if settings.clicked(){actions.push(("genstudio.loras.settings",json!({"path":entry.path})));}
                divisions=Some([cell.response.rect.right()+gap*0.5,strength.rect.right()+gap*0.5,r.rect.right()+gap*0.5]);
                rows.push(json!({"path":entry.path,"directory":false,"name":name,"enabled":choice.enabled,"strength":choice.strength,"triggers":metadata.triggers,
                    "checkRect":rect_value(check.rect),"strengthRect":rect_value(strength.rect),"nameRect":rect_value(r.rect),"settingsRect":rect_value(settings.rect)}));
                ui.end_row();
            }
        });
        if let Some(divisions)=divisions {
            let stroke=egui::Stroke::new(1.0,crate::theme::Tokens::get(ui.ctx()).field_border);
            for x in divisions {ui.painter().vline(x,table.response.rect.y_range(),stroke);}
        }
    });
    rows
}

fn drag_preview(ctx: &egui::Context) -> Value {
    let Some(payload)=egui::DragAndDrop::payload::<Drag>(ctx) else{return Value::Null};
    let Some(at)=ctx.pointer_interact_pos() else{return Value::Null};
    ctx.set_cursor_icon(egui::CursorIcon::Grabbing);
    egui::Area::new(egui::Id::new("gengate-lora-drag-preview")).order(egui::Order::Tooltip).fixed_pos(at+egui::vec2(14.0,12.0)).interactable(false).constrain(false).show(ctx,|ui|{
        ui.set_opacity(0.92);
        egui::Frame::popup(ui.style()).show(ui,|ui|{
            ui.set_max_width(280.0);
            ui.add(egui::Label::new(&payload.name).truncate().selectable(false));
        });
    });
    json!({"name":payload.name,"position":[at.x,at.y]})
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
        let mut header=None;
        let collapsed=state.collapsed;
        let window=egui::Window::new("LoRAs").id(egui::Id::new(if collapsed {"gengate-loras-chip"}else{"gengate-loras"})).title_bar(false).collapsible(false).movable(false)
            .default_pos(ctx.content_rect().min+egui::vec2(24.0,100.0)).default_size(egui::vec2(570.0,380.0)).min_width(410.0)
            .frame(egui::Frame::window(&ctx.global_style()).inner_margin(egui::Margin::symmetric(8,4)));
        let window=if collapsed {window.auto_sized()}else{window};
        let response=state.cache.browser_position.prepare(ctx,window).show(ctx,|ui|{
            let title=crate::genstudio_ui::floating_title(ui,"LoRAs");
            let h=crate::genstudio_ui::floating_header(ui,"gengate-loras","LoRAs",title,Some(collapsed));
            open= !h.close.clicked();header=Some(h);
            if collapsed {return;}
            egui::Frame::NONE.inner_margin(egui::Margin::symmetric(8,12)).show(ui,|ui|{
                ui.horizontal(|ui|{
                    if ui.add_enabled(!lib.folder.is_empty(),egui::Button::new("\u{2190}")).on_hover_text("Parent folder").clicked() {
                        let parent=lib.folder.rsplit_once('/').map_or("",|(a,_)|a);
                        actions.push(("genstudio.loras.configure",json!({"folder":parent})));
                    }
                    ui.label(if lib.folder.is_empty(){"loras".into()}else{format!("loras/{}",lib.folder)});
                    if ui.button("\u{21bb}").on_hover_text("Refresh").clicked(){state.cache.folder=None;}
                });
                ui.separator();
                if state.cache.pending.is_some(){ui.spinner();ctx.request_repaint_after(std::time::Duration::from_millis(80));}
                if !state.cache.error.is_empty(){ui.colored_label(crate::theme::Tokens::get(ctx).text_dim,&state.cache.error);}
                let rows=files(ui,lib,&state.cache.entries,&mut actions);
                state.cache.info=json!({"folder":lib.folder,"rows":rows,"loading":state.cache.pending.is_some(),"error":state.cache.error});
            });
        });
        if let (Some(response),Some(header))=(response,header) {
            state.cache.browser_position.finish(ctx,response.response.rect,&header);
            state.cache.info["window"]=header_info(response.response.rect,&header);
            state.cache.info["window"]["collapsed"]=json!(collapsed);
            if header.toggle {state.collapsed= !collapsed;}
        }
        state.open=open;
        app.ui.gen_studio.workspace.loras=state;
        for (id,p) in actions { if let Some(Err(e))=menu(app,id,&p){crate::notices::error(app,e);} }
        state=std::mem::take(&mut app.ui.gen_studio.workspace.loras);
    }
    if let Some(mut edit)=state.cache.edit.take() {
        let saved=state.libraries.entry(models.clone()).or_default().metadata.get(&edit.path).cloned().unwrap_or_default();
        let mut open=true;
        let mut save=false;
        let mut header=None;
        let window=egui::Window::new("LoRA settings").id(egui::Id::new("gengate-lora-settings")).title_bar(false).collapsible(false).movable(false).resizable(false).min_width(440.0)
            .frame(egui::Frame::window(&ctx.global_style()).inner_margin(egui::Margin::symmetric(8,4)));
        let response=state.cache.settings_position.prepare(ctx,window).show(ctx,|ui|{
            let title=crate::genstudio_ui::floating_title(ui,"LoRA settings");
            let h=crate::genstudio_ui::floating_header(ui,"gengate-lora-settings","LoRA settings",title,None);
            open= !h.close.clicked();header=Some(h);
            egui::Frame::NONE.inner_margin(egui::Margin::symmetric(8,12)).show(ui,|ui|{
                let mut fields=Vec::new();
                egui::Grid::new("gengate-lora-metadata").show(ui,|ui|{
                    ui.label("Name");let r=ui.add(egui::TextEdit::singleline(&mut edit.value.name).desired_width(310.0).min_size(egui::vec2(310.0,26.0)).char_limit(200));fields.push(r.rect);ui.end_row();
                    ui.label("Trigger words");let r=ui.add(egui::TextEdit::singleline(&mut edit.value.triggers).desired_width(310.0).min_size(egui::vec2(310.0,26.0)).char_limit(4000).hint_text("Comma-separated trigger words"));fields.push(r.rect);ui.end_row();
                });
                let button=ui.add_enabled(saved!=edit.value,egui::Button::new("Save"));save=button.clicked();
                state.cache.info["settings"]=json!({"path":edit.path,"dirty":saved!=edit.value,"fields":fields.iter().map(|r|rect_value(*r)).collect::<Vec<_>>(),"saveRect":rect_value(button.rect)});
            });
        });
        if let (Some(response),Some(header))=(response,header) {
            state.cache.settings_position.finish(ctx,response.response.rect,&header);
            state.cache.info["settings"]["window"]=header_info(response.response.rect,&header);
        }
        if save {state.libraries.entry(models).or_default().metadata.insert(edit.path.clone(),edit.value.clone());}
        if open{state.cache.edit=Some(edit);}
    }
    state.cache.info["drag"]=drag_preview(ctx);
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
    #[test]
    fn an_unrenamed_truncated_file_has_a_real_drag_target_and_visible_preview() {
        struct Fixture { app:PhotocraftApp, lib:Library, entries:Vec<Entry>, rect:egui::Rect, preview:Value }
        let name="an_unmodified_long_lora_filename_with_training_details.safetensors";
        let mut lib=Library::default();
        lib.metadata.insert(name.into(),Metadata{name:String::new(),triggers:"portrait, soft light".into()});
        let mut app=PhotocraftApp::new(photocraft_engine::Session::new(),crate::Services::default());
        app.ui.gen_studio.open=true;app.ui.gen_studio.initialized=true;
        app.ui.gen_studio.workspace.prompt="A portrait".into();
        let fixture=Fixture{app,lib,entries:vec![Entry{path:name.into(),name:name.into(),directory:false}],rect:egui::Rect::NOTHING,preview:Value::Null};
        let mut h=egui_kittest::Harness::builder().with_size(egui::vec2(1500.0,950.0)).with_max_steps(64).build_ui_state(|ui,f:&mut Fixture|{
            if !ui.ctx().fonts(|fonts|fonts.families().contains(&egui::FontFamily::Name("medium".into()))) {return;}
            crate::genstudio_ui::show(&mut f.app,ui.ctx());
            egui::Window::new("Browser drag fixture").title_bar(false).movable(false).fixed_pos(egui::pos2(10.0,90.0)).fixed_size(egui::vec2(410.0,180.0)).show(ui.ctx(),|ui|{
                let rows=files(ui,&mut f.lib,&f.entries,&mut Vec::new());
                let r=&rows[0]["nameRect"];
                f.rect=egui::Rect::from_min_size(egui::pos2(r[0].as_f64().unwrap() as f32,r[1].as_f64().unwrap() as f32),egui::vec2(r[2].as_f64().unwrap() as f32,r[3].as_f64().unwrap() as f32));
            });
            f.preview=drag_preview(ui.ctx());
        },fixture);
        PhotocraftApp::setup_context(&h.ctx,crate::theme::ThemeKind::Studio);h.run_steps(8);
        let from=h.state().rect.left_center()+egui::vec2(12.0,0.0);
        let target=&h.state().app.studio.info["promptRect"];
        let to=egui::pos2((target[0].as_f64().unwrap()+target[2].as_f64().unwrap()*0.5) as f32,(target[1].as_f64().unwrap()+target[3].as_f64().unwrap()*0.5) as f32);
        h.event(egui::Event::PointerMoved(from));h.run_steps(1);
        h.event(egui::Event::PointerButton{pos:from,button:egui::PointerButton::Primary,pressed:true,modifiers:egui::Modifiers::NONE});h.run_steps(1);
        h.event(egui::Event::PointerMoved(from+egui::vec2(24.0,0.0)));h.run_steps(2);
        assert_eq!(h.state().preview["name"],name);
        assert_eq!(egui::DragAndDrop::payload::<Drag>(&h.ctx).unwrap().triggers,"portrait, soft light");
        for i in 1..=8 {h.event(egui::Event::PointerMoved(from+(to-from)*(i as f32/8.0)));h.run_steps(1);}
        h.event(egui::Event::PointerButton{pos:to,button:egui::PointerButton::Primary,pressed:false,modifiers:egui::Modifiers::NONE});h.run_steps(3);
        assert!(h.state().app.ui.gen_studio.workspace.prompt.ends_with("portrait, soft light"));
        assert!(egui::DragAndDrop::payload::<Drag>(&h.ctx).is_none());
        assert!(h.state().lib.metadata[name].name.is_empty());
    }
}
