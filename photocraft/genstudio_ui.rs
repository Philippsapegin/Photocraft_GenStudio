//! Filter > Plug-ins > GenGate. Native widgets, shared with the editor's theme.
use crate::PhotocraftApp;
use photocraft_engine::genstudio::{Engine, Settings, Startup};
use serde_json::{Value, json};

#[derive(Clone, Debug, Default, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(default)]
pub struct State {
    pub open: bool,
    pub collapsed: bool,
    pub settings_open: bool,
    pub advanced_open: bool,
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
    pub window_position: Option<[f32; 2]>,
    #[serde(skip)]
    pub drag_offset: Option<[f32; 2]>,
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
        let dialog = app
            .ui
            .dialogs
            .iter()
            .rev()
            .find(|d| crate::prefs_ui::is_preferences(&d.fields))
            .map(|d| d.id)
            .unwrap_or_else(|| crate::prefs_ui::open_preferences(app, "genStudio"));
        let initial_dir = app.ui.dialogs.iter().find(|d| d.id == dialog).and_then(|d| d.fields["values"]["genStudio"]["modelsDir"].as_str()).map(str::to_owned);
        return Some(app.ask_file(crate::file_dialog::FileDialogRequest::Folder { initial_dir }, move |app, answer| {
            let crate::file_dialog::FileDialogAnswer::FolderTo(path) = answer else { return Err("Expected a models folder".into()) };
            let d = app.ui.dialog_mut(dialog).ok_or("Preferences was closed")?;
            let value = d.fields.get_mut("values").and_then(|v| v.get_mut("genStudio")).and_then(Value::as_object_mut).ok_or("Preferences draft is missing")?;
            value.insert("modelsDir".into(), json!(path));
            Ok(json!({"modelsFolderSelected":true}))
        }));
    }
    if id == "genstudio.component.browse" {
        let key = match params.get("component").and_then(Value::as_str) {
            Some(key @ ("modelFile" | "textEncoder" | "promptUpsampler" | "vae" | "constantLora")) => key.to_owned(),
            _ => return Some(Err("Choose a valid preset component".into())),
        };
        let Some(dialog) = app.ui.dialogs.iter().rev().find(|d| crate::prefs_ui::is_preferences(&d.fields)).map(|d| d.id) else {
            return Some(Err("Open GenGate Settings before choosing a component".into()));
        };
        let values = app.ui.dialogs.iter().find(|d| d.id == dialog).map(|d| d.fields["values"]["genStudio"].clone()).unwrap_or(Value::Null);
        let preset = values["model"].as_str().unwrap_or("qwen-image-2.1-turbo").to_owned();
        let initial_dir = values["modelsDir"].as_str().map(str::to_owned);
        let request = crate::file_dialog::FileDialogRequest::Open {
            multiple: false,
            initial_dir,
            extensions: Some(vec!["safetensors".into(), "pt".into(), "ckpt".into()]),
        };
        return Some(app.ask_file(request, move |app, answer| {
            let crate::file_dialog::FileDialogAnswer::Paths(paths) = answer else { return Err("Choose a model component file on this computer".into()) };
            let path = paths.first().ok_or("No component file selected")?;
            let d = app.ui.dialog_mut(dialog).ok_or("Preferences was closed")?;
            let value = d.fields.get_mut("values").and_then(|v| v.get_mut("genStudio")).ok_or("Preferences draft is missing")?;
            let mut settings: Settings = serde_json::from_value(value.clone()).map_err(|e| e.to_string())?;
            let original = settings.model.clone();
            settings.model = preset.clone();
            let parts = settings.components_mut();
            match key.as_str() {
                "modelFile" => parts.model_file = path.clone(),
                "textEncoder" => parts.text_encoder = path.clone(),
                "promptUpsampler" => parts.prompt_upsampler = path.clone(),
                "vae" => parts.vae = path.clone(),
                "constantLora" => parts.constant_lora = path.clone(),
                _ => return Err("Unknown component".into()),
            }
            settings.model = original;
            settings.validate()?;
            *value = serde_json::to_value(settings).map_err(|e| e.to_string())?;
            Ok(json!({"componentSelected":key,"preset":preset}))
        }));
    }
    if id != "genstudio.open" && id != "genstudio.settings.open" {
        return None;
    }
    if params.get("open").is_some_and(|value| !value.is_boolean()) {
        return Some(Err("GenGate open must be a boolean".into()));
    }
    let open = params.get("open").and_then(Value::as_bool).unwrap_or(true);
    if id == "genstudio.settings.open" {
        if open {
            let dialog = crate::prefs_ui::open_preferences(app, "genStudio");
            return Some(Ok(json!({"dialog":dialog,"section":"genStudio"})));
        }
        return Some(Ok(json!({"open":false})));
    }
    app.ui.gen_studio.open = open;
    if open {
        crate::genstudio_canvas::opened(app);
    }
    Some(Ok(json!({"window":if id == "genstudio.open" {"genStudio"} else {"genStudioSettings"},"open":open})))
}

fn text(ui: &mut egui::Ui, label: &str, value: &mut String) {
    ui.label(label);
    ui.add(egui::TextEdit::singleline(value).desired_width(350.0));
    ui.end_row();
}

pub(crate) struct FloatingHeader {
    pub drag: egui::Response,
    pub close: egui::Response,
    pub toggle: bool,
    pub band: egui::Rect,
}

/// Shared compact title strip for the GenGate floating windows.
pub(crate) fn floating_header(ui: &mut egui::Ui, key: &str, caption: &str, title: egui::text::LayoutJob, collapse: Option<bool>) -> FloatingHeader {
    let title = ui.fonts_mut(|f| f.layout_job(title));
    let button = ui.spacing().interact_size.y;
    let gap = ui.spacing().item_spacing.x;
    let collapsed = collapse == Some(true);
    let height = button.max(title.size().y);
    let width = if collapsed { title.size().x + 2.0 * (button + gap) } else { ui.available_width() };
    let (header, _) = ui.allocate_exact_size(egui::vec2(width, height), egui::Sense::hover());
    let band = header.expand2(egui::vec2(8.0, 4.0));
    let mut corners = ui.visuals().window_corner_radius;
    if !collapsed { corners.sw = 0; corners.se = 0; }
    ui.painter().rect_filled(band, corners, ui.visuals().widgets.open.weak_bg_fill);
    if !collapsed { ui.painter().hline(band.x_range(), band.bottom() + 0.5, ui.visuals().window_stroke); }
    let mut title_left = header.left();
    let mut toggle = false;
    if let Some(collapsed) = collapse {
        let rect = egui::Rect::from_min_size(header.left_center() - egui::vec2(0.0, button / 2.0), egui::Vec2::splat(button));
        let label = format!("{} {caption}", if collapsed { "Expand" } else { "Collapse" });
        let response = ui.put(rect, egui::Button::new("").frame(false)).on_hover_text(&label);
        response.widget_info(|| egui::WidgetInfo::labeled(egui::WidgetType::Button, true, &label));
        let mut icon = response.clone();
        icon.rect = egui::Rect::from_center_size(rect.center(), egui::Vec2::splat(ui.spacing().icon_width));
        egui::collapsing_header::paint_default_icon(ui, if collapsed { 0.0 } else { 1.0 }, &icon);
        title_left = rect.right() + gap;
        toggle = response.clicked();
    }
    let close_rect = egui::Rect::from_min_size(header.right_center() - egui::vec2(button, button / 2.0), egui::Vec2::splat(button));
    let close_label = format!("Close {caption}");
    let close = ui.interact(close_rect, ui.id().with((key, "close")), egui::Sense::click()).on_hover_text(&close_label);
    close.widget_info(|| egui::WidgetInfo::labeled(egui::WidgetType::Button, true, &close_label));
    let visuals = ui.style().interact(&close);
    let cross = egui::Rect::from_center_size(close_rect.center(), egui::Vec2::splat(12.0)).expand(visuals.expansion);
    ui.painter().line_segment([cross.left_top(), cross.right_bottom()], visuals.fg_stroke);
    ui.painter().line_segment([cross.right_top(), cross.left_bottom()], visuals.fg_stroke);
    let title_rect = header.with_min_x(title_left).with_max_x(close_rect.left() - gap);
    let drag = ui.interact(title_rect, ui.id().with((key, "title-drag")), egui::Sense::click_and_drag());
    ui.scope_builder(egui::UiBuilder::new().max_rect(title_rect).layout(egui::Layout::left_to_right(egui::Align::Center)), |ui| {
        ui.add(egui::Label::new(title).selectable(false));
    });
    toggle |= collapse.is_some() && drag.double_clicked();
    FloatingHeader { drag, close, toggle, band }
}

pub(crate) fn floating_title(ui: &egui::Ui, text: &str) -> egui::text::LayoutJob {
    let mut title = egui::text::LayoutJob::default();
    title.append(text, 0.0, egui::TextFormat {
        font_id: crate::theme::semibold(egui::TextStyle::Heading.resolve(ui.style()).size),
        color: crate::theme::Tokens::get(ui.ctx()).text, ..Default::default()
    });
    title
}

/// Absolute pointer offset keeps custom title-strip dragging smooth.
#[derive(Clone, Debug, Default, PartialEq)]
pub(crate) struct FloatingPosition {
    pub position: Option<[f32; 2]>,
    offset: Option<[f32; 2]>,
}
impl FloatingPosition {
    pub fn prepare<'a>(&mut self, ctx: &egui::Context, mut window: egui::Window<'a>) -> egui::Window<'a> {
        let (at, down) = ctx.input(|i| (i.pointer.interact_pos(), i.pointer.primary_down()));
        if let (Some(at), Some([x, y])) = (at, self.offset) {
            self.position = Some([at.x - x, at.y - y]);
            if down { ctx.request_repaint(); }
        }
        if !down { self.offset = None; }
        if let Some([x, y]) = self.position.take() { window = window.current_pos(egui::pos2(x, y)); }
        window
    }
    pub fn finish(&mut self, ctx: &egui::Context, rect: egui::Rect, header: &FloatingHeader) {
        if ctx.input(|i| i.pointer.primary_down()) && header.drag.is_pointer_button_down_on() && self.offset.is_none()
            && let Some(origin) = ctx.input(|i| i.pointer.press_origin()) {
            self.offset = Some([origin.x - rect.min.x, origin.y - rect.min.y]);
        }
        if header.toggle { self.position = Some([rect.min.x, rect.min.y]); self.offset = None; ctx.request_repaint(); }
    }
}

fn workspace_header(ui: &mut egui::Ui, model: &str, collapsed: bool) -> FloatingHeader {
    let mut title = floating_title(ui, "GenGate");
    title.append(&format!(" | {model}"), 0.0, egui::TextFormat {
        font_id: egui::FontId::proportional(egui::TextStyle::Heading.resolve(ui.style()).size),
        color: crate::theme::Tokens::get(ui.ctx()).text, ..Default::default()
    });
    floating_header(ui, "gengate", "GenGate", title, Some(collapsed))
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
        crate::notices::post(app, "GenGate", vec!["Starting the local AI engine. This may take a moment.".into()], false, None);
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
        crate::notices::post(app, "GenGate", vec!["Starting the local AI engine. This may take a moment.".into()], false, None);
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
        app.ui.gen_studio.drag_offset = None;
        return;
    }
    if !engine_busy && activity.is_none() && app.ui.gen_studio.last_probe.is_none_or(|time| time.elapsed() > std::time::Duration::from_secs(2)) {
        app.ui.gen_studio.last_probe = Some(std::time::Instant::now());
        photocraft_engine::genstudio::probe(&app.session);
    }
    ctx.request_repaint_after(std::time::Duration::from_millis(250));
    let mut open = true;
    let mut workspace_actions = Vec::new();
    if !app.ui.gen_studio.advanced_open {
        app.ui.gen_studio.advanced_were_open = false;
    }
    if app.ui.gen_studio.open {
        let settings=&app.session.prefs().gen_studio;
        let model = if settings.workflow_mode(){std::path::Path::new(&settings.studio_workflow).file_stem().and_then(|v|v.to_str()).unwrap_or("ComfyUI workflow").to_owned()}
            else{photocraft_engine::genstudio::model_spec(&settings.model).map_or("Choose model",|m|m.label).to_owned()};
        let caption = format!("GenGate | {model}");
        // The custom header owns collapse; migrate the old invisible native body.
        let mut expanded = egui::collapsing_header::CollapsingState::load_with_default_open(ctx, egui::Id::new("genstudio-workspace").with("collapsing"), true);
        expanded.set_open(true);
        expanded.store(ctx);
        let collapsed = app.ui.gen_studio.collapsed;
        let (pointer, down) = ctx.input(|i| (i.pointer.interact_pos(), i.pointer.primary_down()));
        if let (Some(at), Some([x, y])) = (pointer, app.ui.gen_studio.drag_offset) {
            // Apply the absolute grab position before drawing this frame. Reading
            // the previous Area rect inside the body dropped every other delta.
            app.ui.gen_studio.window_position = Some([at.x - x, at.y - y]);
            if down {
                ctx.request_repaint();
            }
        }
        if !down {
            app.ui.gen_studio.drag_offset = None;
        }
        let mut window = egui::Window::new(&caption)
            .title_bar(false)
            .movable(false)
            .id(egui::Id::new(if collapsed { "gengate-workspace-chip" } else { "genstudio-workspace" }))
            .default_pos(ctx.content_rect().center() - egui::vec2(470.0, 325.0))
            .default_size(egui::vec2(940.0, 650.0))
            .min_size(egui::vec2(680.0, 460.0))
            .max_height((ctx.content_rect().height() - 32.0).max(360.0))
            .frame(egui::Frame::window(&ctx.global_style()).inner_margin(egui::Margin::symmetric(8, 4)))
            .resizable(true);
        if collapsed {
            window = window.auto_sized();
        }
        if let Some([x, y]) = app.ui.gen_studio.window_position.take() {
            window = window.current_pos(egui::pos2(x, y));
        }
        let mut header = None;
        let response = window.show(ctx, |ui| {
            let title = workspace_header(ui, &model, collapsed);
            open = !title.close.clicked();
            header = Some(title);
            if collapsed {
                return;
            }
            egui::Frame::NONE.inner_margin(egui::Margin::symmetric(8, 12)).show(ui, |ui| {
                let body_height = (ui.available_height() - 38.0).max(340.0);
                ui.allocate_ui_with_layout(egui::vec2(ui.available_width(), body_height), egui::Layout::top_down(egui::Align::Min), |ui| {
                    workspace_actions = crate::genstudio_canvas::workflow_controls(app,ui);
                    workspace_actions.extend(crate::genstudio_canvas::show(app, ui));
                });
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
        });
        if let (Some(response), Some(header)) = (response, header) {
            let drag = &header.drag;
            let toggle = header.toggle;
            let rect = response.response.rect;
            crate::genstudio_canvas::set_window_rect(app,rect);
            if down
                && drag.is_pointer_button_down_on()
                && app.ui.gen_studio.drag_offset.is_none()
                && let Some(origin) = ctx.input(|i| i.pointer.press_origin())
            {
                app.ui.gen_studio.drag_offset = Some([origin.x - rect.min.x, origin.y - rect.min.y]);
            }
            if toggle {
                app.ui.gen_studio.collapsed = !collapsed;
                app.ui.gen_studio.window_position = Some([rect.min.x, rect.min.y]);
                app.ui.gen_studio.drag_offset = None;
                ctx.request_repaint();
            }
            if let Some(info) = app.studio.info.as_object_mut() {
                info.insert("window".into(), json!({"collapsed":collapsed,"rect":[rect.min.x,rect.min.y,rect.width(),rect.height()],"titleRect":[drag.rect.min.x,drag.rect.min.y,drag.rect.width(),drag.rect.height()],"headerRect":[header.band.min.x,header.band.min.y,header.band.width(),header.band.height()],"closeRect":[header.close.rect.min.x,header.close.rect.min.y,header.close.rect.width(),header.close.rect.height()]}));
            }
        }
        app.ui.gen_studio.open = open;
    }
    for (id, params) in workspace_actions {
        if let Err(e) = crate::menus::invoke(app, ctx, id, params) {
            crate::notices::error(app, e);
        }
    }
    crate::genstudio_lora_ui::show(app,ctx);
}

fn component(ui: &mut egui::Ui, label: &str, value: &mut String, catalog: &Value, key: &str, default: &str, strength: Option<&mut f64>) -> bool {
    ui.label(label);
    let automatic = catalog[default]
        .as_str()
        .filter(|s| !s.is_empty())
        .map_or(if strength.is_some() { "None" } else { "Automatic" }.to_owned(), |name| format!("Automatic · {}", name.rsplit('/').next().unwrap_or(name)));
    let mut browse = false;
    ui.horizontal(|ui| {
        egui::ComboBox::from_id_salt(("genstudio-component", key))
            .width(if strength.is_some() { 234.0 } else { 302.0 })
            .truncate()
            .selected_text(if value.is_empty() { automatic.clone() } else { value.replace('\\', "/").rsplit('/').next().unwrap_or(value).to_owned() })
            .show_ui(ui, |ui| {
                ui.selectable_value(value, String::new(), automatic);
                if let Some(items) = catalog[key].as_array() {
                    for item in items {
                        if let Some(name) = item["value"].as_str() {
                            ui.selectable_value(value, name.to_owned(), name);
                        }
                    }
                }
            })
            .response
            .on_hover_text(value.as_str());
        if let Some(strength) = strength {
            ui.add_sized([60.0, 24.0], egui::DragValue::new(strength).speed(0.05).range(-20.0..=20.0).min_decimals(2).max_decimals(2))
                .on_hover_text("LoRA strength — double-click to type");
        }
        browse = ui.button("Browse…").clicked();
    });
    ui.end_row();
    browse
}
fn settings(app: &mut PhotocraftApp, ui: &mut egui::Ui, draft: &mut Settings, state: &Value, busy: bool, view: &mut Value) -> Option<(&'static str, Value)> {
    let mut action = None;
    ui.add_enabled_ui(!busy, |ui| {
        egui::Grid::new("genstudio-model-engine").num_columns(2).spacing([16.0, 8.0]).show(ui, |ui| {
            ui.label("Preset");
            egui::ComboBox::from_id_salt("genstudio-model")
                .width(410.0)
                .selected_text(photocraft_engine::genstudio::model_spec(&draft.model).map_or("Choose preset", |m| m.label))
                .show_ui(ui, |ui| {
                    for model in photocraft_engine::genstudio::MODELS {
                        ui.selectable_value(&mut draft.model, model.id.into(), model.label);
                    }
                });
            draft.components_mut();
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
                ui.add(egui::TextEdit::singleline(&mut draft.models_dir).desired_width(330.0));
                if ui.button("Browse…").clicked() {
                    action = Some(("genstudio.models.browse", json!({})));
                }
            });
            ui.end_row();
        });
        let response =
            egui::CollapsingHeader::new("Advanced").id_salt("genstudio-advanced-section").open(Some(app.ui.gen_studio.advanced_open)).show(ui, |ui| {
                let catalog = &state["models"];
                let flux = draft.model == "flux-3-image-klein";
                let parts = draft.components_mut();
                egui::Grid::new("genstudio-components").num_columns(2).spacing([16.0, 8.0]).show(ui, |ui| {
                    for (label, value, key, default, field) in [
                        ("Model", &mut parts.model_file, "diffusionModels", "defaultModel", "modelFile"),
                        ("Text encoder", &mut parts.text_encoder, "textEncoders", "defaultTextEncoder", "textEncoder"),
                    ] {
                        if component(ui, label, value, catalog, key, default, None) {
                            action = Some(("genstudio.component.browse", json!({"component":field})));
                        }
                    }
                    if flux && component(ui, "Prompt upsampler", &mut parts.prompt_upsampler, catalog, "promptUpsamplers", "defaultPromptUpsampler", None) {
                        action = Some(("genstudio.component.browse", json!({"component":"promptUpsampler"})));
                    }
                    if component(ui, "VAE", &mut parts.vae, catalog, "vaes", "defaultVae", None) {
                        action = Some(("genstudio.component.browse", json!({"component":"vae"})));
                    }
                    if component(ui, "Constant LoRA", &mut parts.constant_lora, &Value::Null, "constantLora", "", Some(&mut parts.constant_lora_strength)) {
                        action = Some(("genstudio.component.browse", json!({"component":"constantLora"})));
                    }
                });
                ui.small("Automatic components follow the selected preset and Models folder.");
                ui.small("Constant LoRA applies only to Ai Fill and Remove Brush.");
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
                if draft.engine==Engine::Comfy {
                    ui.horizontal(|ui|{
                        let current=&app.session.prefs().gen_studio;
                        let applied=!draft.comfy_dir.trim().is_empty() && draft.comfy_dir==current.comfy_dir && draft.runtime_root==current.runtime_root;
                        if ui.add_enabled(applied,egui::Button::new("Install / Update nodes")).on_hover_text("Install widget-free GenGate IN / OUT into this ComfyUI. Apply connection changes first; restart an already running ComfyUI afterwards.").clicked(){
                            action=Some(("genstudio.comfy.install",json!({})));
                        }
                        ui.small("GenGate IN / OUT for Studio workflows");
                    });
                }
            });
        if response.header_response.clicked() {
            app.ui.gen_studio.advanced_open = !app.ui.gen_studio.advanced_open;
        }
        if app.ui.gen_studio.advanced_open && response.fully_open() && !app.ui.gen_studio.advanced_were_open {
            response.header_response.scroll_to_me(Some(egui::Align::TOP));
            app.ui.gen_studio.advanced_were_open = true;
        }
    });
    crate::widgets::checkbox(ui, &mut draft.reduce_edit_resolution, "Reduce AI fill resolution (up to 2\u{00d7})")
        .on_hover_text("Keep crops up to 768 px at full resolution. Larger fills and Remove Tool crops reduce progressively, up to 2×. Off preserves the original pixel size.");
    let mut progress = view["progress"].as_bool().unwrap_or(!app.ui.gen_studio.hide_fill_progress);
    if crate::widgets::checkbox(ui, &mut progress, "Show progress bars under masks").changed() {
        view["progress"] = json!(progress);
    }
    let mut bar = view["bar"].as_bool().unwrap_or(!app.ui.gen_studio.hide_ai_bar);
    if crate::widgets::checkbox(ui, &mut bar, "Show floating AI prompt bar").changed() {
        view["bar"] = json!(bar);
    }
    let mut current = app.session.prefs().gen_studio.clone();
    current.components_mut();
    let changed = *draft != current;
    ui.horizontal(|ui| {
        for (label, id, enabled) in
            [("Start", "genstudio.engine.start", !changed), ("Stop", "genstudio.engine.stop", !changed), ("Refresh", "genstudio.engine.refresh", !changed)]
        {
            if ui.add_enabled(!busy && enabled, egui::Button::new(label)).clicked() {
                action = Some((id, json!({})));
            }
        }
    });
    action
}

pub fn preferences(app: &mut PhotocraftApp, ui: &mut egui::Ui, obj: &mut serde_json::Map<String, Value>, view: &mut Value) {
    let mut draft = serde_json::from_value::<Settings>(Value::Object(obj.clone())).unwrap_or_else(|_| app.session.prefs().gen_studio.clone());
    draft.components_mut();
    photocraft_engine::genstudio::discover(&app.session, draft.clone());
    let state = photocraft_engine::genstudio::snapshot(&app.session);
    let busy = state["pending"] == true
        || photocraft_engine::genstudio_queue::has_pending(&app.session)
        || app.session.jobs().iter().any(|j| j.state == "running" && (j.command.starts_with("genstudio.fill.") || j.command == "genstudio.studio.generate"));
    if !view.is_object() {
        *view = preferences_view(app);
    }
    let action = settings(app, ui, &mut draft, &state, busy, view);
    ui.add_space(12.0);
    ui.separator();
    ui.strong("Segmentation");
    ui.small("Independent Selection Engine");
    crate::widgets::checkbox(ui, &mut draft.segmentation.enabled, "Enable AI segmentation");
    ui.add_enabled_ui(draft.segmentation.enabled, |ui| {
        egui::Grid::new("gengate-segmentation-settings").num_columns(2).spacing([16.0, 8.0]).show(ui, |ui| {
            ui.label("Detector");
            egui::ComboBox::from_id_salt("gengate-detector")
                .width(300.0)
                .selected_text(format!(
                    "YOLO-World v2 {}",
                    if draft.segmentation.detector == "yolov8m-worldv2" {
                        "M"
                    } else if draft.segmentation.detector == "yolov8l-worldv2" {
                        "L"
                    } else {
                        "S"
                    }
                ))
                .show_ui(ui, |ui| {
                    for (id, label) in [("yolov8s-worldv2", "YOLO-World v2 S"), ("yolov8m-worldv2", "YOLO-World v2 M"), ("yolov8l-worldv2", "YOLO-World v2 L")]
                    {
                        ui.selectable_value(&mut draft.segmentation.detector, id.into(), label);
                    }
                });
            ui.end_row();
            ui.label("Segmenter");
            egui::ComboBox::from_id_salt("gengate-segmenter").width(300.0).selected_text("SAM3").show_ui(ui, |ui| {
                ui.selectable_value(&mut draft.segmentation.segmenter, "sam3".into(), "SAM3");
            });
            ui.end_row();
            text(ui, "CUDA Python (optional)", &mut draft.segmentation.python);
        });
        ui.small("Defaults download automatically when enabled (about 3.5 GB). Existing files are reused.");
        ui.small("Leave Python empty to reuse your Direct / ComfyUI Python. ComfyUI itself is never started.");
    });
    if let Ok(Value::Object(values)) = serde_json::to_value(draft) {
        *obj = values;
    }
    ui.add_space(8.0);
    ui.small(idle_message(&state));
    if let Some((id, params)) = action
        && let Err(e) = crate::menus::invoke(app, ui.ctx(), id, params)
    {
        crate::notices::error(app, e);
    }
}
pub fn preferences_view(app: &PhotocraftApp) -> Value {
    json!({"progress":!app.ui.gen_studio.hide_fill_progress,"bar":!app.ui.gen_studio.hide_ai_bar})
}
pub fn confirm_preferences(app: &mut PhotocraftApp, f: &serde_json::Map<String, Value>) -> Result<Value, String> {
    let values = f.get("values").cloned().unwrap_or(Value::Null);
    let studio = values.get("genStudio").map(|v| serde_json::from_value::<Settings>(v.clone()).map_err(|e| e.to_string())).transpose()?;
    if let Some(studio) = &studio {
        studio.validate()?;
    }
    let changed = studio.as_ref().is_some_and(|v| {
        let mut previous = app.session.prefs().gen_studio.clone();
        previous.segmentation = v.segmentation.clone();
        *v != previous
    });
    let prepare_segmentation = studio.as_ref().is_some_and(|v| {
        v.segmentation.enabled && (v.segmentation != app.session.prefs().gen_studio.segmentation || v.models_dir != app.session.prefs().gen_studio.models_dir)
    });
    let state = photocraft_engine::genstudio::snapshot(&app.session);
    if changed
        && (state["pending"] == true
            || app.session.jobs().iter().any(|j| j.state == "running" && j.command.starts_with("genstudio."))
            || photocraft_engine::genstudio_queue::has_pending(&app.session))
    {
        return Err("Wait for AI requests to finish before changing the engine".into());
    }
    let result = app.run("prefs.set", json!({"path":"","value":values}))?;
    if let Some(view) = f.get("__genstudioView") {
        if let Some(v) = view["progress"].as_bool() {
            app.ui.gen_studio.hide_fill_progress = !v;
        }
        if let Some(v) = view["bar"].as_bool() {
            app.ui.gen_studio.hide_ai_bar = !v;
        }
    }
    if changed && let Some(studio) = studio {
        app.run("genstudio.engine.configure", serde_json::to_value(studio).map_err(|e| e.to_string())?)?;
    }
    if prepare_segmentation {
        app.run("gengate.segmentation.prepare", json!({}))?;
    }
    Ok(result)
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
        let window = harness.ctx.memory(|m| m.area_rect(egui::Id::new("genstudio-workspace"))).unwrap();
        assert!(window.height() < 720.0, "the footer must not grow the window every frame: {window:?}");
        assert!(!harness.state().ui.gen_studio.settings_open);
    }

    #[test]
    fn resizing_studio_expands_canvas_and_prompt_without_footer_drift() {
        let mut app = PhotocraftApp::new(photocraft_engine::Session::new(), crate::Services::default());
        app.ui.gen_studio.initialized = true;
        app.ui.gen_studio.open = true;
        let mut ready = false;
        let mut h = egui_kittest::Harness::builder().with_size(egui::vec2(1600.0, 1100.0)).build_ui_state(
            move |ui, app: &mut PhotocraftApp| {
                if !ready {
                    PhotocraftApp::setup_context(ui.ctx(), crate::theme::ThemeKind::Studio);
                    ready = true;
                    return;
                }
                show(app, ui.ctx());
            },
            app,
        );
        h.run_steps(10);
        let before = h.state().studio.info.clone();
        let window = h.ctx.memory(|m| m.area_rect(egui::Id::new("genstudio-workspace"))).unwrap();
        let at = window.right_bottom() - egui::vec2(2.0, 2.0);
        h.hover_at(at);
        h.run_steps(1);
        h.drag_at(at);
        h.run_steps(1);
        let to = at + egui::vec2(230.0, 170.0);
        h.event(egui::Event::PointerMoved(to));
        h.run_steps(3);
        h.drop_at(to);
        h.run_steps(10);
        let after = &h.state().studio.info;
        assert!(after["slots"][0]["rect"][2].as_f64().unwrap() > before["slots"][0]["rect"][2].as_f64().unwrap() + 50.0, "{before} -> {after}");
        assert!(after["promptRect"][3].as_f64().unwrap() > before["promptRect"][3].as_f64().unwrap() + 80.0, "{before} -> {after}");
        let size = h.ctx.memory(|m| m.area_rect(egui::Id::new("genstudio-workspace"))).unwrap().size();
        h.run_steps(50);
        let settled = h.ctx.memory(|m| m.area_rect(egui::Id::new("genstudio-workspace"))).unwrap().size();
        assert!((size - settled).length() < 2.0, "window must stay at the requested size");
    }

    #[test]
    fn preferences_apply_saves_new_component_maps() {
        let mut app = PhotocraftApp::new(photocraft_engine::Session::new(), crate::Services::default());
        let root = std::env::temp_dir()
            .join(format!("gengate-uninstalled-{}", std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()));
        assert!(!root.exists(), "the test must never use an installed runtime");
        let mut settings = app.session.prefs().gen_studio.clone();
        settings.runtime_root = root.to_string_lossy().into_owned();
        settings.segmentation.enabled = false;
        settings.model = "qwen-image-2.1-turbo".into();
        settings.components_mut().constant_lora = "loras/qwen-edit.safetensors".into();
        settings.components_mut().constant_lora_strength = 0.65;
        settings.model = "flux-3-image-klein".into();
        settings.components_mut().prompt_upsampler = "text_encoders/custom-upsampler.safetensors".into();
        let id = crate::prefs_ui::open_preferences(&mut app, "genStudio");
        app.ui.dialog_mut(id).unwrap().fields["values"]["genStudio"] = serde_json::to_value(&settings).unwrap();
        crate::prefs_ui::apply(&mut app, id).unwrap();
        assert_eq!(app.session.prefs().gen_studio, settings);
        let fields = &app.ui.dialog_mut(id).unwrap().fields;
        assert_eq!(fields["values"]["genStudio"], serde_json::to_value(settings).unwrap());
    }

    #[test]
    fn preferences_contains_studio_and_apply_cancel_are_transactional() {
        use egui_kittest::kittest::Queryable;
        let mut app = PhotocraftApp::new(photocraft_engine::Session::new(), crate::Services::default());
        app.ui.gen_studio.initialized = true;
        let id = crate::prefs_ui::open_preferences(&mut app, "genStudio");
        let mut ready = false;
        let mut h = egui_kittest::Harness::builder().with_size(egui::vec2(1200.0, 900.0)).build_ui_state(
            move |ui, app: &mut PhotocraftApp| {
                if !ready {
                    PhotocraftApp::setup_context(ui.ctx(), crate::theme::ThemeKind::Studio);
                    ready = true;
                    return;
                }
                crate::dialogs::show(app, ui.ctx());
            },
            app,
        );
        h.run_steps(6);
        assert!(h.get_by_label("Preset").rect().is_positive());
        assert!(h.get_by_label("Engine").rect().is_positive());
        assert!(h.query_by_label("Text encoder").is_none(), "Advanced starts collapsed");
        assert!(!h.state().ui.gen_studio.open);
        let before = h.state().session.prefs().gen_studio.clone();
        {
            let draft = h.state_mut().ui.dialog_mut(id).unwrap();
            draft.fields["values"]["genStudio"]["modelsDir"] = json!("D:/A different models folder");
            draft.fields.insert("__genstudioView".into(), json!({"progress":false,"bar":false}));
        }
        assert_eq!(h.state().session.prefs().gen_studio, before);
        assert!(!h.state().ui.gen_studio.hide_ai_bar);
        let fields = h.state().ui.dialogs.iter().find(|d| d.id == id).unwrap().fields.clone();
        assert!(crate::prefs_ui::preferences_changed(h.state(), &fields));
        // Display-only Apply does not configure or launch a worker.
        h.state_mut().ui.dialog_mut(id).unwrap().fields["values"]["genStudio"] = serde_json::to_value(before).unwrap();
        crate::prefs_ui::apply(h.state_mut(), id).unwrap();
        assert!(h.state().ui.gen_studio.hide_ai_bar && h.state().ui.gen_studio.hide_fill_progress);
        let fields = &h.state().ui.dialogs.iter().find(|d| d.id == id).unwrap().fields;
        assert!(!crate::prefs_ui::preferences_changed(h.state(), fields));
        h.state_mut().ui.dialogs.clear();
        assert!(h.state().ui.gen_studio.hide_ai_bar);
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
        crate::menus::invoke(&mut app, &ctx, "genstudio.open", json!({"open":false})).unwrap();
        crate::menus::invoke(&mut app, &ctx, "genstudio.settings.open", json!({})).unwrap();
        assert!(!app.ui.gen_studio.open);
        assert!(app.ui.dialogs.iter().any(|d| d.fields["section"] == "genStudio"));
        let item = crate::menus::menu_items(&app).into_iter().find(|i| i.id == "genstudio.settings.open").unwrap();
        assert!(item.path.is_empty());
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
        assert_eq!(
            app.ui.dialogs.iter().find(|d| crate::prefs_ui::is_preferences(&d.fields)).unwrap().fields["values"]["genStudio"]["modelsDir"],
            "D:/Shared Models"
        );
        assert_eq!(app.session.prefs().gen_studio, before);
        assert!(!photocraft_engine::genstudio::snapshot(&app.session)["pending"].as_bool().unwrap());
    }
}
