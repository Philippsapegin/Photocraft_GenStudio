//! Filter > Plug-ins > Gen Studio. Native widgets, shared with the editor's theme.
use crate::PhotocraftApp;
use photocraft_engine::genstudio::{Engine, Settings, Startup};
use serde_json::{Value, json};

#[derive(Clone, Debug, Default, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(default)]
pub struct State {
    pub open: bool,
    pub draft: Option<Settings>,
    pub hide_fill_progress: bool,
    pub hide_ai_bar: bool,
    pub selection_prompt: String,
    #[serde(skip)]
    pub last_probe: Option<std::time::Instant>,
    #[serde(skip)]
    pub initialized: bool,
    #[serde(skip)]
    pub was_pending: bool,
    #[serde(skip)]
    pub remove_start_notice: Option<u64>,
}

pub fn menu(app: &mut PhotocraftApp, id: &str, params: &Value) -> Option<Result<Value, String>> {
    if id=="edit.aiFill" {
        return Some(app.run(id,params.clone()).inspect(|_| {
            app.ui.mask_target=false;app.ui.properties_layer=app.session.active().and_then(|st|st.active_layer).map(|id|id.0);app.ui.panels.properties=true;
            app.ui.dock.collapsed.retain(|group|*group!=crate::dock::Group::Properties);
        }));
    }
    if id=="genstudio.ai.bar.set" {
        return Some(match params.get("enabled").and_then(Value::as_bool) {
            Some(enabled)=>{app.ui.gen_studio.hide_ai_bar=!enabled;Ok(json!({"enabled":enabled}))},
            None=>Err("AI prompt bar enabled must be a boolean".into()),
        });
    }
    if id == "genstudio.progress.set" {
        return Some(match params.get("enabled").and_then(Value::as_bool) {
            Some(enabled) => { app.ui.gen_studio.hide_fill_progress = !enabled; Ok(json!({"enabled":enabled})) },
            None => Err("Mask progress enabled must be a boolean".into()),
        });
    }
    if id == "genstudio.models.browse" {
        let initial_dir = app.ui.gen_studio.draft.as_ref().map(|v| v.models_dir.clone());
        return Some(app.ask_file(crate::file_dialog::FileDialogRequest::Folder { initial_dir }, |app, answer| {
            let crate::file_dialog::FileDialogAnswer::FolderTo(path) = answer else { return Err("Expected a models folder".into()) };
            let mut draft = app.ui.gen_studio.draft.take().unwrap_or_else(|| app.session.prefs().gen_studio.clone());
            draft.models_dir = path;
            app.ui.gen_studio.draft = Some(draft);
            Ok(json!({"modelsFolderSelected":true}))
        }));
    }
    if id != "genstudio.open" {
        return None;
    }
    if params.get("open").is_some_and(|value|!value.is_boolean()) {
        return Some(Err("Gen Studio open must be a boolean".into()));
    }
    app.ui.gen_studio.open = params.get("open").and_then(Value::as_bool).unwrap_or(true);
    if app.ui.gen_studio.open { app.ui.gen_studio.draft = Some(app.session.prefs().gen_studio.clone()); }
    Some(Ok(json!({"window":"genStudio","open":app.ui.gen_studio.open})))
}

fn text(ui: &mut egui::Ui, label: &str, value: &mut String) {
    ui.label(label);
    ui.add(egui::TextEdit::singleline(value).desired_width(350.0));
    ui.end_row();
}

pub fn show(app: &mut PhotocraftApp, ctx: &egui::Context) {
    if let Some(job) = app.session.jobs().into_iter().find(|job|job.message.starts_with("Starting"))
        && (job.command.starts_with("genstudio.fill.") || job.label == "Remove · Ai Fill" || job.label=="Ai Fill")
        && job.message.starts_with("Starting") && app.ui.gen_studio.remove_start_notice != Some(job.id.0)
    {
        app.ui.gen_studio.remove_start_notice = Some(job.id.0);
        crate::notices::post(app, "Gen Studio", vec!["Starting the local AI engine. This may take a moment.".into()], false, None);
    }
    if !app.ui.gen_studio.initialized {
        app.ui.gen_studio.initialized = true;
        if app.session.prefs().gen_studio.startup() == Startup::AppStart
            && let Err(e) = app.run("genstudio.engine.ensure", json!({"event":"appStart"}))
        {
            crate::notices::error(app, e);
        }
    }
    let state = photocraft_engine::genstudio::snapshot(&app.session);
    let pending = state.get("pending").and_then(Value::as_bool).unwrap_or(false);
    let activity = app.session.jobs().into_iter().find(|job|job.command.starts_with("genstudio.fill.") || job.label == "Remove · Ai Fill" || job.label=="Ai Fill");
    let engine_busy = pending || photocraft_engine::genstudio_queue::has_pending(&app.session);
    let start_message = if pending && state["status"]["state"] == "starting" {
        Some("Starting the AI engine…".to_owned())
    } else {
        app.session.jobs().iter().find(|j|(j.command.starts_with("genstudio.fill.") || j.label == "Remove · Ai Fill" || j.label=="Ai Fill") && j.message.starts_with("Starting"))
            .map(|_|"Starting the AI engine…".to_owned())
    };
    if pending && !app.ui.gen_studio.was_pending && state["status"]["message"].as_str().is_some_and(|s| s.starts_with("Starting")) {
        crate::notices::post(app, "Gen Studio", vec!["Starting the local AI engine. This may take a moment.".into()], false, None);
    }
    if pending {
        ctx.request_repaint_after(std::time::Duration::from_millis(100));
    }
    if app.ui.gen_studio.was_pending && !pending && state["status"]["state"] == "error" {
        let error = state["status"]["message"].as_str().unwrap_or("Engine command failed").to_owned();
        crate::notices::error(app, error);
    }
    app.ui.gen_studio.was_pending = pending;
    if !app.ui.gen_studio.open {
        return;
    }
    if !engine_busy && activity.is_none() && app.ui.gen_studio.last_probe.is_none_or(|time|time.elapsed()>std::time::Duration::from_secs(2)) {
        app.ui.gen_studio.last_probe=Some(std::time::Instant::now());
        photocraft_engine::genstudio::probe(&app.session);
    }
    ctx.request_repaint_after(std::time::Duration::from_millis(250));
    let mut draft = app.ui.gen_studio.draft.clone().unwrap_or_else(|| app.session.prefs().gen_studio.clone());
    let mut open = true;
    let mut action = None;
    egui::Window::new("Gen Studio").id(egui::Id::new("genstudio-settings")).open(&mut open).default_width(560.0).resizable(false).show(ctx, |ui| {
        ui.heading("AI Engine");
        ui.add_space(8.0);
        if let Some(message) = &start_message {
            egui::Frame::new().fill(crate::theme::Tokens::get(ui.ctx()).field).inner_margin(10.0).show(ui,|ui| {
                ui.horizontal(|ui| { ui.spinner(); ui.strong(message); });
                ui.small("Preparing the local server. You can keep working while it starts.");
            });
            ui.add_space(8.0);
        }
        ui.add_enabled_ui(!engine_busy, |ui| {
            egui::Grid::new("genstudio-engine-grid").num_columns(2).spacing([18.0, 10.0]).show(ui, |ui| {
                ui.label("Engine");
                egui::ComboBox::from_id_salt("genstudio-engine")
                    .width(350.0)
                    .selected_text(match draft.engine {
                        Engine::Comfy => "Qwen 2.1 Turbo · ComfyUI",
                        Engine::Direct => "Qwen 2.1 Turbo · Direct",
                    })
                    .show_ui(ui, |ui| {
                        ui.selectable_value(&mut draft.engine, Engine::Comfy, "Qwen 2.1 Turbo · ComfyUI");
                        ui.selectable_value(&mut draft.engine, Engine::Direct, "Qwen 2.1 Turbo · Direct");
                    });
                ui.end_row();
                ui.label("Automatic start");
                let policy = draft.startup_mut();
                egui::ComboBox::from_id_salt("genstudio-startup")
                    .width(350.0)
                    .selected_text(match policy {
                        Startup::Off => "Off",
                        Startup::AppStart => "When PhotoCraft starts",
                        Startup::FirstRequest => "On first AI request",
                    })
                    .show_ui(ui, |ui| {
                        ui.selectable_value(policy, Startup::Off, "Off");
                        ui.selectable_value(policy, Startup::AppStart, "When PhotoCraft starts");
                        ui.selectable_value(policy, Startup::FirstRequest, "On first AI request");
                    });
                ui.end_row();
            });
            ui.add_space(10.0);
            ui.horizontal(|ui| {
                ui.label("Models folder");
                ui.add(egui::TextEdit::singleline(&mut draft.models_dir).desired_width(340.0));
                if ui.button("Browse…").clicked() {
                    action = Some("genstudio.models.browse");
                }
            });
            ui.small("Use a standalone model folder or your existing ComfyUI/models. Both engines share these files.");
            ui.add_space(8.0);
            ui.collapsing("Connection & model", |ui| {
                egui::Grid::new("genstudio-paths").num_columns(2).spacing([18.0, 8.0]).show(ui, |ui| match draft.engine {
                    Engine::Comfy => {
                        text(ui, "Server address", &mut draft.comfy_url);
                        text(ui, "ComfyUI folder", &mut draft.comfy_dir);
                        text(ui, "Python executable", &mut draft.comfy_python);
                    }
                    Engine::Direct => {
                        text(ui, "Python executable", &mut draft.direct_python);
                    }
                });
            });
            ui.collapsing("Advanced", |ui| {
                egui::Grid::new("genstudio-advanced").num_columns(2).show(ui, |ui| text(ui, "Runtime folder", &mut draft.runtime_root));
            });
        });
        ui.add_space(12.0);
        let mut progress = !app.ui.gen_studio.hide_fill_progress;
        if crate::widgets::checkbox(ui,&mut progress,"Show progress bars under masks").changed() {
            app.ui.gen_studio.hide_fill_progress = !progress;
        }
        let mut bar=!app.ui.gen_studio.hide_ai_bar;
        if crate::widgets::checkbox(ui,&mut bar,"Show floating AI prompt bar").changed() { app.ui.gen_studio.hide_ai_bar=!bar; }
        ui.separator();
        ui.horizontal(|ui| {
            if pending || activity.is_some() {
                ui.spinner();
            }
            let message = activity.as_ref().map(|j|j.message.as_str()).filter(|m|!m.is_empty())
                .unwrap_or_else(||idle_message(&state));
            ui.label(message);
        });
        let changed = draft != app.session.prefs().gen_studio;
        if changed {
            ui.small("Apply changes before starting the selected engine.");
        }
        ui.add_space(8.0);
        ui.horizontal(|ui| {
            if ui.add_enabled(!engine_busy && changed, egui::Button::new("Apply")).clicked() {
                action = Some("genstudio.engine.configure");
            }
            if ui.add_enabled(!engine_busy && !changed, egui::Button::new("Start")).clicked() {
                action = Some("genstudio.engine.start");
            }
            if ui.add_enabled(!engine_busy && !changed, egui::Button::new("Stop")).clicked() {
                action = Some("genstudio.engine.stop");
            }
            if ui.add_enabled(!engine_busy && !changed, egui::Button::new("Refresh")).clicked() {
                action = Some("genstudio.engine.refresh");
            }
        });
    });
    if let Some(id) = action {
        let params = if id == "genstudio.engine.configure" { serde_json::to_value(&draft).unwrap_or(Value::Null) } else { json!({}) };
        if let Err(e) = crate::menus::invoke(app, ctx, id, params) {
            crate::notices::error(app, e);
        }
    }
    app.ui.gen_studio.draft = Some(draft);
    app.ui.gen_studio.open = open;
}

fn idle_message(state:&Value)->&str {
    if state["status"]["ready"]==true { "Engine ready" }
    else if state["status"]["state"]=="stopped" { "Engine not running" }
    else { state["status"]["message"].as_str().unwrap_or("Checking engine…") }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn ai_fill_startup_banner_is_visible_inside_gen_studio_without_a_model_process() {
        use egui_kittest::kittest::Queryable;
        let mut app=PhotocraftApp::new(photocraft_engine::Session::new(),crate::Services::default());
        app.ui.gen_studio.open=true;
        app.ui.gen_studio.initialized=true;
        {
            let mut state=app.session.gen_studio.state.lock().unwrap();
            state.busy=true;
            state.value=json!({"state":"starting","message":"Starting the local AI engine. This may take a moment."});
        }
        let mut harness=egui_kittest::Harness::builder().with_size(egui::vec2(1000.0,800.0)).build_ui_state(|ui,app:&mut PhotocraftApp| {
            if ui.ctx().fonts(|f|f.families().contains(&egui::FontFamily::Name("medium".into()))) { show(app,ui.ctx()); }
        },app);
        PhotocraftApp::setup_context(&harness.ctx,crate::theme::ThemeKind::Studio);
        harness.run_steps(5);
        assert!(harness.get_by_label("Starting the AI engine…").rect().is_positive());
        assert!(harness.get_by_label("Show progress bars under masks").rect().is_positive());
        assert_eq!(harness.state().ui.notices.len(),1,"startup notice is nonblocking and not repeated every frame");
    }

    #[test]
    fn ai_fill_progress_setting_is_immediate_persistent_and_validated() {
        let mut app=PhotocraftApp::new(photocraft_engine::Session::new(),crate::Services::default());
        assert!(!app.ui.gen_studio.hide_fill_progress);
        let ctx=egui::Context::default();
        crate::menus::invoke(&mut app,&ctx,"genstudio.progress.set",json!({"enabled":false})).unwrap();
        let saved=serde_json::to_value(&app.ui.gen_studio).unwrap();
        let restored:State=serde_json::from_value(saved).unwrap();
        assert!(restored.hide_fill_progress);
        assert!(crate::menus::invoke(&mut app,&ctx,"genstudio.progress.set",json!({"enabled":"yes"})).is_err());
        crate::menus::invoke(&mut app,&ctx,"genstudio.progress.set",json!({"enabled":true})).unwrap();
        assert!(!app.ui.gen_studio.hide_fill_progress);
    }
    #[test]
    fn qwen_checkbox_preserves_the_other_remove_options_and_reset() {
        use egui_kittest::kittest::Queryable;
        use crate::state::Tool;
        let mut app = PhotocraftApp::new(photocraft_engine::Session::new(), crate::Services::default());
        app.run("file.new", json!({"width":100,"height":60})).unwrap();
        app.ui.tool = Tool::Remove;
        app.ui.tool_options.remove_new_layer = true;
        app.ui.tool_options.sample_all_layers = true;
        let mut harness = egui_kittest::Harness::builder().with_size(egui::vec2(1500.0,60.0)).build_ui_state(|ui, app: &mut PhotocraftApp| {
            if ui.ctx().fonts(|f| f.families().contains(&egui::FontFamily::Name("medium".into()))) {
                crate::panels::options_bar(app, ui);
            }
        }, app);
        PhotocraftApp::setup_context(&harness.ctx, crate::theme::ThemeKind::Studio);
        harness.run_steps(4);
        for label in ["Ai Fill", "Sample All Layers", "Create a new layer", "Remove after each stroke"] {
            assert!(harness.get_by_label(label).rect().is_positive(), "{label} stays accessible");
        }
        assert!(harness.get_by_label("Ai Fill").rect().left() > harness.get_by_label("Remove after each stroke").rect().right());
        let at = harness.get_by_label("Ai Fill").rect().center();
        harness.hover_at(at); harness.run(); harness.drag_at(at); harness.run(); harness.drop_at(at); harness.run_steps(2);
        let options = &harness.state().ui.tool_options;
        assert!(options.remove_ai_fill && options.remove_new_layer && options.sample_all_layers && options.remove_after_each_stroke);
        let mut reset = options.clone();
        crate::tool_reset::reset_options(&mut reset,Tool::Remove);
        assert!(!reset.remove_ai_fill && !reset.remove_new_layer && !reset.sample_all_layers && reset.remove_after_each_stroke);
        let saved = serde_json::to_value(options).unwrap();
        let restored: crate::state::ToolOptions = serde_json::from_value(saved).unwrap();
        assert!(restored.remove_ai_fill);
        let migrated: crate::state::ToolOptions = serde_json::from_value(json!({"remove_use_qwen":true})).unwrap();
        assert!(migrated.remove_ai_fill,"the model-specific preference migrates to Ai Fill");
    }

    #[test]
    fn qwen_remove_waits_for_release_or_for_batch_commit_and_does_not_open_a_window() {
        use crate::canvas::{ToolEvent,tool_event};
        use crate::state::Tool;
        for batch in [false,true] {
            let mut app = PhotocraftApp::new(photocraft_engine::Session::new(), crate::Services::default());
            app.run("file.new",json!({"width":300,"height":200})).unwrap();
            // Invalid runtime guarantees this UI-only test cannot start any engine.
            app.run("prefs.set",json!({"path":"genStudio.runtimeRoot","value":""})).unwrap();
            app.ui.tool=Tool::Remove;
            app.ui.tool_options.remove_ai_fill=true;
            app.ui.tool_options.remove_after_each_stroke=!batch;
            let before=app.session.active().unwrap().doc.clone();
            let modifiers=egui::Modifiers::NONE;
            tool_event(&mut app,ToolEvent::Down{x:150.0,y:100.0,pressure:1.0},modifiers);
            tool_event(&mut app,ToolEvent::Move{x:150.0,y:120.0,pressure:1.0},modifiers);
            assert!(!app.ui.status_error,"there is no request while drawing");
            tool_event(&mut app,ToolEvent::Up{x:150.0,y:120.0},modifiers);
            if batch {
                assert!(!app.ui.status_error && app.remove_pending.len()==1);
                assert!(crate::retouch_ui::remove_pending_commit(&mut app));
                assert!(app.remove_pending.is_empty());
            }
            assert!(app.ui.status_error,"the Qwen command reaches runtime validation");
            assert!(std::sync::Arc::ptr_eq(&before,&app.session.active().unwrap().doc));
            assert!(!app.ui.gen_studio.open);
        }
    }
    #[test]
    fn suite_opens_without_a_document_or_starting_inference() {
        let mut app = PhotocraftApp::new(photocraft_engine::Session::new(), crate::Services::default());
        let ctx = egui::Context::default();
        assert!(crate::menus::is_enabled(&app, "genstudio.open"));
        crate::menus::invoke(&mut app, &ctx, "genstudio.open", json!({})).unwrap();
        assert!(app.ui.gen_studio.open);
        let item = crate::menus::menu_items(&app).into_iter().find(|i| i.id == "genstudio.open").unwrap();
        assert_eq!(item.path, ["Filter", "Plug-ins"]);
        assert_eq!(app.run("genstudio.engine.status", json!({})).unwrap()["pending"], false);
    }
    #[test]
    fn folder_picker_changes_draft_without_applying_or_starting_engine() {
        use crate::file_dialog::{self, FileDialogAnswer, FileDialogRequest};
        let (dialog, asked) = file_dialog::fake(vec![Some(FileDialogAnswer::FolderTo("D:/Shared Models".into()))]);
        let mut app = PhotocraftApp::new(photocraft_engine::Session::new(), crate::Services { file_dialog: Some(dialog), ..Default::default() });
        let before = app.session.prefs().gen_studio.clone();
        let ctx = egui::Context::default();
        crate::menus::invoke(&mut app, &ctx, "genstudio.open", json!({})).unwrap();
        crate::menus::invoke(&mut app, &ctx, "genstudio.models.browse", json!({})).unwrap();
        app.poll_file_dialog(&ctx, None);
        app.poll_file_dialog(&ctx, None);
        assert!(matches!(asked.borrow().first(), Some(FileDialogRequest::Folder { .. })));
        assert_eq!(app.ui.gen_studio.draft.unwrap().models_dir, "D:/Shared Models");
        assert_eq!(app.session.prefs().gen_studio, before);
        assert!(!photocraft_engine::genstudio::snapshot(&app.session)["pending"].as_bool().unwrap());
    }
}
