//! Filter > Plug-ins > Gen Studio. Native widgets, shared with the editor's theme.
use crate::PhotocraftApp;
use photocraft_engine::genstudio::{Engine, Settings, Startup};
use serde_json::{Value, json};

#[derive(Clone, Debug, Default, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(default)]
pub struct State {
    pub open: bool,
    pub settings_open: bool,
    pub advanced_open: bool,
    #[serde(skip)]
    pub settings_were_open: bool,
    #[serde(skip)]
    pub advanced_were_open: bool,
    pub workspace: crate::genstudio_canvas::Workspace,
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
    if let Some(result) = crate::genstudio_canvas::menu(app, id, params) {
        return Some(result);
    }
    if id == "edit.aiFill" {
        return Some(app.run(id, params.clone()).inspect(|_| {
            app.ui.mask_target = false;
            app.ui.properties_layer = app.session.active().and_then(|st| st.active_layer).map(|id| id.0);
            app.ui.panels.properties = true;
            app.ui.dock.collapsed.retain(|group| *group != crate::dock::Group::Properties);
        }));
    }
    if id == "genstudio.ai.bar.set" {
        return Some(match params.get("enabled").and_then(Value::as_bool) {
            Some(enabled) => {
                app.ui.gen_studio.hide_ai_bar = !enabled;
                Ok(json!({"enabled":enabled}))
            }
            None => Err("AI prompt bar enabled must be a boolean".into()),
        });
    }
    if id == "genstudio.progress.set" {
        return Some(match params.get("enabled").and_then(Value::as_bool) {
            Some(enabled) => {
                app.ui.gen_studio.hide_fill_progress = !enabled;
                Ok(json!({"enabled":enabled}))
            }
            None => Err("Mask progress enabled must be a boolean".into()),
        });
    }
    if id == "genstudio.models.browse" {
        let initial_dir = app.ui.gen_studio.draft.as_ref().map(|v| v.models_dir.clone());
        return Some(app.ask_file(crate::file_dialog::FileDialogRequest::Folder { initial_dir }, |app, answer| {
            let crate::file_dialog::FileDialogAnswer::FolderTo(path) = answer else { return Err("Expected a models folder".into()) };
            let mut draft = app.ui.gen_studio.draft.take().unwrap_or_else(|| app.session.prefs().gen_studio.clone());
            draft.models_dir = path;
            draft.text_encoder.clear();
            draft.vae.clear();
            app.ui.gen_studio.draft = Some(draft);
            Ok(json!({"modelsFolderSelected":true}))
        }));
    }
    if id != "genstudio.open" {
        return None;
    }
    if params.get("open").is_some_and(|value| !value.is_boolean()) {
        return Some(Err("Gen Studio open must be a boolean".into()));
    }
    app.ui.gen_studio.open = params.get("open").and_then(Value::as_bool).unwrap_or(true);
    if app.ui.gen_studio.open {
        app.ui.gen_studio.draft = Some(app.session.prefs().gen_studio.clone());
        crate::genstudio_canvas::opened(app);
    }
    Some(Ok(json!({"window":"genStudio","open":app.ui.gen_studio.open})))
}

fn text(ui: &mut egui::Ui, label: &str, value: &mut String) {
    ui.label(label);
    ui.add(egui::TextEdit::singleline(value).desired_width(350.0));
    ui.end_row();
}

pub fn show(app: &mut PhotocraftApp, ctx: &egui::Context) {
    if let Some(job) = app.session.jobs().into_iter().find(|job| job.state == "running" && job.message.starts_with("Starting"))
        && ((job.command.starts_with("genstudio.fill.") || job.command == "genstudio.studio.generate")
            || job.label == "Remove · Ai Fill"
            || job.label == "Ai Fill")
        && job.message.starts_with("Starting")
        && app.ui.gen_studio.remove_start_notice != Some(job.id.0)
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
    crate::genstudio_canvas::tick(app, ctx);
    let state = photocraft_engine::genstudio::snapshot(&app.session);
    let pending = state.get("pending").and_then(Value::as_bool).unwrap_or(false);
    let activity = app.session.jobs().into_iter().find(|job| {
        job.state == "running"
            && ((job.command.starts_with("genstudio.fill.") || job.command == "genstudio.studio.generate")
                || job.label == "Remove · Ai Fill"
                || job.label == "Ai Fill")
    });
    let engine_busy = pending || activity.is_some() || photocraft_engine::genstudio_queue::has_pending(&app.session);
    let start_message = if pending && state["status"]["state"] == "starting" {
        Some("Starting the AI engine…".to_owned())
    } else {
        app.session
            .jobs()
            .iter()
            .find(|j| {
                j.state == "running"
                    && ((j.command.starts_with("genstudio.fill.") || j.command == "genstudio.studio.generate")
                        || j.label == "Remove · Ai Fill"
                        || j.label == "Ai Fill")
                    && j.message.starts_with("Starting")
            })
            .map(|_| "Starting the AI engine…".to_owned())
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
    if !engine_busy && activity.is_none() && app.ui.gen_studio.last_probe.is_none_or(|time| time.elapsed() > std::time::Duration::from_secs(2)) {
        app.ui.gen_studio.last_probe = Some(std::time::Instant::now());
        photocraft_engine::genstudio::probe(&app.session);
    }
    ctx.request_repaint_after(std::time::Duration::from_millis(250));
    let mut draft = app.ui.gen_studio.draft.clone().unwrap_or_else(|| app.session.prefs().gen_studio.clone());
    if app.ui.gen_studio.settings_open {
        photocraft_engine::genstudio::discover(&app.session, draft.clone());
    }
    let mut open = true;
    let mut action = None;
    let mut workspace_actions = Vec::new();
    if !app.ui.gen_studio.settings_open {
        app.ui.gen_studio.settings_were_open = false;
        app.ui.gen_studio.advanced_were_open = false;
    }
    if !app.ui.gen_studio.advanced_open {
        app.ui.gen_studio.advanced_were_open = false;
    }
    egui::Window::new("Gen Studio")
        .id(egui::Id::new("genstudio-settings"))
        .open(&mut open)
        .default_pos(ctx.content_rect().center() - egui::vec2(470.0, 325.0))
        .default_size(egui::vec2(940.0, 650.0))
        .min_size(egui::vec2(680.0, 460.0))
        .max_height((ctx.content_rect().height() - 32.0).max(360.0))
        .resizable(true)
        .show(ctx, |ui| {
            egui::ScrollArea::vertical().id_salt("genstudio-content").auto_shrink([false, false]).max_height((ui.available_height() - 48.0).max(340.0)).show(
                ui,
                |ui| {
                    ui.label(egui::RichText::new("Qwen Image 2.1 Turbo").color(crate::theme::Tokens::get(ui.ctx()).text_dim));
                    ui.add_space(8.0);
                    workspace_actions = crate::genstudio_canvas::show(app, ui);
                    ui.add_space(10.0);
                    let response = egui::CollapsingHeader::new("Settings")
                        .id_salt("genstudio-settings-section")
                        .open(Some(app.ui.gen_studio.settings_open))
                        .show(ui, |ui| {
                            action = settings(app, ui, &mut draft, &state, engine_busy);
                        });
                    if response.header_response.clicked() {
                        app.ui.gen_studio.settings_open = !app.ui.gen_studio.settings_open;
                    }
                    if app.ui.gen_studio.settings_open && response.fully_open() && !app.ui.gen_studio.settings_were_open {
                        response.header_response.scroll_to_me(Some(egui::Align::TOP));
                        app.ui.gen_studio.settings_were_open = true;
                    }
                },
            );
            ui.separator();
            ui.horizontal(|ui| {
                ui.spacing_mut().item_spacing.x = 6.0;
                if pending || activity.is_some() {
                    ui.add(egui::Spinner::new().size(12.0));
                }
                let message = start_message
                    .as_deref()
                    .or_else(|| activity.as_ref().map(|j| j.message.as_str()).filter(|s| !s.is_empty()))
                    .unwrap_or_else(|| idle_message(&state));
                ui.add(egui::Label::new(egui::RichText::new(message).size(11.0).color(crate::theme::Tokens::get(ui.ctx()).text_dim)).truncate());
            });
        });
    app.ui.gen_studio.draft = Some(draft.clone());
    app.ui.gen_studio.open = open;
    if let Some(id) = action {
        let params = if id == "genstudio.engine.configure" { serde_json::to_value(&draft).unwrap_or(Value::Null) } else { json!({}) };
        if let Err(e) = crate::menus::invoke(app, ctx, id, params) {
            crate::notices::error(app, e);
        }
    }
    for (id, params) in workspace_actions {
        if let Err(e) = crate::menus::invoke(app, ctx, id, params) {
            crate::notices::error(app, e);
        }
    }
}

fn component(ui: &mut egui::Ui, label: &str, value: &mut String, catalog: &Value, key: &str, default: &str) {
    ui.label(label);
    let automatic = catalog[default]
        .as_str()
        .filter(|s| !s.is_empty())
        .map_or("Automatic".to_owned(), |name| format!("Automatic · {}", name.rsplit('/').next().unwrap_or(name)));
    egui::ComboBox::from_id_salt(("genstudio-component", key))
        .width(410.0)
        .selected_text(if value.is_empty() { automatic.clone() } else { value.clone() })
        .show_ui(ui, |ui| {
            ui.selectable_value(value, String::new(), automatic);
            if let Some(items) = catalog[key].as_array() {
                for item in items {
                    if let Some(name) = item["value"].as_str() {
                        ui.selectable_value(value, name.to_owned(), name);
                    }
                }
            }
        });
    ui.end_row();
}
fn settings(app: &mut PhotocraftApp, ui: &mut egui::Ui, draft: &mut Settings, state: &Value, busy: bool) -> Option<&'static str> {
    let mut action = None;
    ui.add_enabled_ui(!busy, |ui| {
        egui::Grid::new("genstudio-model-engine").num_columns(2).spacing([16.0, 8.0]).show(ui, |ui| {
            ui.label("Model");
            egui::ComboBox::from_id_salt("genstudio-model").width(410.0).selected_text("Qwen Image 2.1 Turbo").show_ui(ui, |ui| {
                ui.selectable_value(&mut draft.model, "qwen-image-2.1-turbo".into(), "Qwen Image 2.1 Turbo");
            });
            ui.end_row();
            ui.label("Engine");
            egui::ComboBox::from_id_salt("genstudio-engine")
                .width(410.0)
                .selected_text(match draft.engine {
                    Engine::Direct => "Direct",
                    Engine::Comfy => "ComfyUI",
                })
                .show_ui(ui, |ui| {
                    ui.selectable_value(&mut draft.engine, Engine::Direct, "Direct");
                    ui.selectable_value(&mut draft.engine, Engine::Comfy, "ComfyUI");
                });
            ui.end_row();
            ui.label("Automatic start");
            let policy = draft.startup_mut();
            egui::ComboBox::from_id_salt("genstudio-startup")
                .width(410.0)
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
            ui.label("Models folder");
            ui.horizontal(|ui| {
                if ui.add(egui::TextEdit::singleline(&mut draft.models_dir).desired_width(330.0)).changed() {
                    draft.text_encoder.clear();
                    draft.vae.clear();
                }
                if ui.button("Browse…").clicked() {
                    action = Some("genstudio.models.browse");
                }
            });
            ui.end_row();
        });
        let response =
            egui::CollapsingHeader::new("Advanced").id_salt("genstudio-advanced-section").open(Some(app.ui.gen_studio.advanced_open)).show(ui, |ui| {
                let catalog = &state["models"];
                egui::Grid::new("genstudio-components").num_columns(2).spacing([16.0, 8.0]).show(ui, |ui| {
                    component(ui, "Text encoder", &mut draft.text_encoder, catalog, "textEncoders", "defaultTextEncoder");
                    component(ui, "VAE", &mut draft.vae, catalog, "vaes", "defaultVae");
                });
                ui.small("Automatic components follow the selected model and Models folder.");
                if catalog["pending"] == true {
                    ui.small("Finding model components…");
                }
                if let Some(message) = catalog["message"].as_str() {
                    ui.small(message);
                }
                ui.add_space(8.0);
                egui::Grid::new("genstudio-connection").num_columns(2).spacing([16.0, 8.0]).show(ui, |ui| {
                    match draft.engine {
                        Engine::Comfy => {
                            text(ui, "Server address", &mut draft.comfy_url);
                            text(ui, "ComfyUI folder", &mut draft.comfy_dir);
                            text(ui, "Python executable", &mut draft.comfy_python);
                        }
                        Engine::Direct => text(ui, "Python executable", &mut draft.direct_python),
                    }
                    text(ui, "Runtime folder", &mut draft.runtime_root);
                });
            });
        if response.header_response.clicked() {
            app.ui.gen_studio.advanced_open = !app.ui.gen_studio.advanced_open;
        }
        if app.ui.gen_studio.advanced_open && response.fully_open() && !app.ui.gen_studio.advanced_were_open {
            response.header_response.scroll_to_me(Some(egui::Align::TOP));
            app.ui.gen_studio.advanced_were_open = true;
        }
    });
    let mut progress = !app.ui.gen_studio.hide_fill_progress;
    if crate::widgets::checkbox(ui, &mut progress, "Show progress bars under masks").changed() {
        app.ui.gen_studio.hide_fill_progress = !progress;
    }
    let mut bar = !app.ui.gen_studio.hide_ai_bar;
    if crate::widgets::checkbox(ui, &mut bar, "Show floating AI prompt bar").changed() {
        app.ui.gen_studio.hide_ai_bar = !bar;
    }
    let changed = *draft != app.session.prefs().gen_studio;
    ui.horizontal(|ui| {
        for (label, id, enabled) in [
            ("Apply", "genstudio.engine.configure", changed),
            ("Start", "genstudio.engine.start", !changed),
            ("Stop", "genstudio.engine.stop", !changed),
            ("Refresh", "genstudio.engine.refresh", !changed),
        ] {
            if ui.add_enabled(!busy && enabled, egui::Button::new(label)).clicked() {
                action = Some(id);
            }
        }
    });
    action
}

fn idle_message(state: &Value) -> &str {
    if state["status"]["ready"] == true {
        "Engine ready"
    } else if state["status"]["state"] == "stopped" {
        "Engine not running"
    } else {
        state["status"]["message"].as_str().unwrap_or("Checking engine…")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn startup_status_is_visible_with_settings_collapsed_without_a_model_process() {
        use egui_kittest::kittest::Queryable;
        let mut app = PhotocraftApp::new(photocraft_engine::Session::new(), crate::Services::default());
        app.ui.gen_studio.open = true;
        app.ui.gen_studio.settings_open = false;
        app.ui.gen_studio.initialized = true;
        {
            let mut state = app.session.gen_studio.state.lock().unwrap();
            state.busy = true;
            state.value = json!({"state":"starting","message":"Starting the local AI engine. This may take a moment."});
        }
        let mut harness = egui_kittest::Harness::builder().with_size(egui::vec2(1000.0, 800.0)).build_ui_state(
            |ui, app: &mut PhotocraftApp| {
                if ui.ctx().fonts(|f| f.families().contains(&egui::FontFamily::Name("medium".into()))) {
                    show(app, ui.ctx());
                }
            },
            app,
        );
        PhotocraftApp::setup_context(&harness.ctx, crate::theme::ThemeKind::Studio);
        harness.run_steps(5);
        assert!(harness.get_by_label("Starting the AI engine…").rect().is_positive());
        assert!(!harness.state().ui.gen_studio.settings_open);
        assert!(!harness.state().ui.gen_studio.advanced_open);
        assert_eq!(harness.state().ui.notices.len(), 1, "startup notice is nonblocking and not repeated every frame");
        harness.run_steps(40);
        let window = harness.ctx.memory(|m| m.area_rect(egui::Id::new("genstudio-settings"))).unwrap();
        assert!(window.height() < 720.0, "the footer must not grow the window every frame: {window:?}");
        harness.state_mut().ui.gen_studio.settings_open = true;
        harness.state_mut().ui.gen_studio.advanced_open = true;
        harness.run_steps(20);
        let encoder = harness.get_by_label("Text encoder").rect();
        let footer = harness.get_by_label("Starting the AI engine…").rect();
        assert!(window.contains_rect(encoder) && encoder.max.y < footer.min.y, "opening settings must bring the components into view");
    }

    #[test]
    fn ai_fill_progress_setting_is_immediate_persistent_and_validated() {
        let mut app = PhotocraftApp::new(photocraft_engine::Session::new(), crate::Services::default());
        assert!(!app.ui.gen_studio.hide_fill_progress);
        let ctx = egui::Context::default();
        crate::menus::invoke(&mut app, &ctx, "genstudio.progress.set", json!({"enabled":false})).unwrap();
        let saved = serde_json::to_value(&app.ui.gen_studio).unwrap();
        let restored: State = serde_json::from_value(saved).unwrap();
        assert!(restored.hide_fill_progress);
        assert!(crate::menus::invoke(&mut app, &ctx, "genstudio.progress.set", json!({"enabled":"yes"})).is_err());
        crate::menus::invoke(&mut app, &ctx, "genstudio.progress.set", json!({"enabled":true})).unwrap();
        assert!(!app.ui.gen_studio.hide_fill_progress);
    }
    #[test]
    fn qwen_checkbox_preserves_the_other_remove_options_and_reset() {
        use crate::state::Tool;
        use egui_kittest::kittest::Queryable;
        let mut app = PhotocraftApp::new(photocraft_engine::Session::new(), crate::Services::default());
        app.run("file.new", json!({"width":100,"height":60})).unwrap();
        app.ui.tool = Tool::Remove;
        app.ui.tool_options.remove_new_layer = true;
        app.ui.tool_options.sample_all_layers = true;
        let mut harness = egui_kittest::Harness::builder().with_size(egui::vec2(1500.0, 60.0)).build_ui_state(
            |ui, app: &mut PhotocraftApp| {
                if ui.ctx().fonts(|f| f.families().contains(&egui::FontFamily::Name("medium".into()))) {
                    crate::panels::options_bar(app, ui);
                }
            },
            app,
        );
        PhotocraftApp::setup_context(&harness.ctx, crate::theme::ThemeKind::Studio);
        harness.run_steps(4);
        for label in ["Ai Fill", "Sample All Layers", "Create a new layer", "Remove after each stroke"] {
            assert!(harness.get_by_label(label).rect().is_positive(), "{label} stays accessible");
        }
        assert!(harness.get_by_label("Ai Fill").rect().left() > harness.get_by_label("Remove after each stroke").rect().right());
        let at = harness.get_by_label("Ai Fill").rect().center();
        harness.hover_at(at);
        harness.run();
        harness.drag_at(at);
        harness.run();
        harness.drop_at(at);
        harness.run_steps(2);
        let options = &harness.state().ui.tool_options;
        assert!(options.remove_ai_fill && options.remove_new_layer && options.sample_all_layers && options.remove_after_each_stroke);
        let mut reset = options.clone();
        crate::tool_reset::reset_options(&mut reset, Tool::Remove);
        assert!(!reset.remove_ai_fill && !reset.remove_new_layer && !reset.sample_all_layers && reset.remove_after_each_stroke);
        let saved = serde_json::to_value(options).unwrap();
        let restored: crate::state::ToolOptions = serde_json::from_value(saved).unwrap();
        assert!(restored.remove_ai_fill);
        let migrated: crate::state::ToolOptions = serde_json::from_value(json!({"remove_use_qwen":true})).unwrap();
        assert!(migrated.remove_ai_fill, "the model-specific preference migrates to Ai Fill");
    }

    #[test]
    fn qwen_remove_waits_for_release_or_for_batch_commit_and_does_not_open_a_window() {
        use crate::canvas::{ToolEvent, tool_event};
        use crate::state::Tool;
        for batch in [false, true] {
            let mut app = PhotocraftApp::new(photocraft_engine::Session::new(), crate::Services::default());
            app.run("file.new", json!({"width":300,"height":200})).unwrap();
            // Invalid runtime guarantees this UI-only test cannot start any engine.
            app.run("prefs.set", json!({"path":"genStudio.runtimeRoot","value":""})).unwrap();
            app.ui.tool = Tool::Remove;
            app.ui.tool_options.remove_ai_fill = true;
            app.ui.tool_options.remove_after_each_stroke = !batch;
            let before = app.session.active().unwrap().doc.clone();
            let modifiers = egui::Modifiers::NONE;
            tool_event(&mut app, ToolEvent::Down { x: 150.0, y: 100.0, pressure: 1.0 }, modifiers);
            tool_event(&mut app, ToolEvent::Move { x: 150.0, y: 120.0, pressure: 1.0 }, modifiers);
            assert!(!app.ui.status_error, "there is no request while drawing");
            tool_event(&mut app, ToolEvent::Up { x: 150.0, y: 120.0 }, modifiers);
            if batch {
                assert!(!app.ui.status_error && app.remove_pending.len() == 1);
                assert!(crate::retouch_ui::remove_pending_commit(&mut app));
                assert!(app.remove_pending.is_empty());
            }
            assert!(app.ui.status_error, "the Qwen command reaches runtime validation");
            assert!(std::sync::Arc::ptr_eq(&before, &app.session.active().unwrap().doc));
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
