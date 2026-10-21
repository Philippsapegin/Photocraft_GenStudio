//! Gesture regressions, without starting any model processes.
use crate::PhotocraftApp;
use photocraft_engine::Session;
use serde_json::json;

#[test]
fn component_picker_starts_in_models_and_edits_only_the_preset_draft() {
    use crate::file_dialog::{self, FileDialogAnswer, FileDialogRequest};
    for component in ["modelFile", "textEncoder", "promptUpsampler", "vae", "constantLora"] {
        let (service, asked) = file_dialog::fake(vec![Some(FileDialogAnswer::Paths(vec!["D:/Custom Pieces/component.safetensors".into()]))]);
        let mut app = PhotocraftApp::new(Session::new(), crate::Services { file_dialog: Some(service), ..Default::default() });
        app.session.edit_prefs(|p| {
            p.gen_studio.model = "flux-3-image-klein".into();
            p.gen_studio.models_dir = "D:/Shared Models".into();
        });
        let before = app.session.prefs().gen_studio.clone();
        let ctx = egui::Context::default();
        let dialog = crate::prefs_ui::open_preferences(&mut app, "genStudio");
        crate::menus::invoke(&mut app, &ctx, "genstudio.component.browse", json!({"component":component})).unwrap();
        app.poll_file_dialog(&ctx, None);
        app.poll_file_dialog(&ctx, None);
        assert!(matches!(asked.borrow().first(), Some(FileDialogRequest::Open { multiple:false, initial_dir:Some(folder), .. }) if folder=="D:/Shared Models"));
        let values = &app.ui.dialogs.iter().find(|d| d.id == dialog).unwrap().fields["values"]["genStudio"];
        assert_eq!(values["presets"]["flux-3-image-klein"][component], "D:/Custom Pieces/component.safetensors");
        assert!(values["presets"].get("qwen-image-2.1-turbo").is_none());
        assert_eq!(app.session.prefs().gen_studio, before, "Browse must not apply settings or start an engine");
        assert_eq!(photocraft_engine::genstudio::snapshot(&app.session)["pending"], false);
    }
}

#[test]
fn ai_select_ignores_clicks_and_narrow_drags_without_touching_selection() {
    let mut app = PhotocraftApp::new(Session::new(), crate::Services::default());
    app.session.execute("file.new", json!({"width":30,"height":20})).unwrap();
    app.session.execute("select.rect", json!({"x":2,"y":2,"width":8,"height":8})).unwrap();
    app.session.edit_prefs(|p| p.gen_studio.segmentation.enabled = true);
    app.ui.tool_options.object_ai_select = true;
    let before = app.session.active().unwrap().history.past_len();
    let journal = app.session.journal.len();
    for end in [[10.0, 10.0], [10.5, 20.0], [20.0, 10.5]] {
        crate::retouch_ui::finish_object_selection(&mut app, [10.0, 10.0], end, egui::Modifiers::NONE);
    }
    assert_eq!(app.session.active().unwrap().history.past_len(), before);
    assert_eq!(app.session.journal.len(), journal);
    assert!(app.session.active().unwrap().doc.selection.is_some());
}

#[test]
fn resetting_object_tool_resets_ai_select() {
    let mut options = crate::state::ToolOptions { object_ai_select: true, ..Default::default() };
    crate::tool_reset::reset_options(&mut options, crate::state::Tool::ObjectSelection);
    assert!(!options.object_ai_select);
}

#[test]
fn automation_validates_ai_select_before_mutating_any_options() {
    let mut app = PhotocraftApp::new(Session::new(), crate::Services::default());
    let ctx = egui::Context::default();
    let (request, _) = crate::control::ControlRequest::new("ui.set", json!({"objectAiSelect":true}));
    let _ = crate::control::handle(&mut app, &ctx, &request);
    assert!(app.ui.tool_options.object_ai_select);
    let (request, _) = crate::control::ControlRequest::new("ui.set", json!({"objectAiSelect":"wrong","tool":"ObjectSelection"}));
    let tool = app.ui.tool;
    let _ = crate::control::handle(&mut app, &ctx, &request);
    assert_eq!(app.ui.tool, tool);
    assert!(app.ui.tool_options.object_ai_select);
}

#[test]
fn workspace_title_is_left_aligned_and_follows_the_selected_model() {
    use egui_kittest::kittest::Queryable;
    let mut app = PhotocraftApp::new(Session::new(), crate::Services::default());
    app.ui.gen_studio.open = true;
    app.ui.gen_studio.initialized = true;
    let mut ready = false;
    let mut h = egui_kittest::Harness::builder().with_size(egui::vec2(1200.0, 900.0)).build_ui_state(
        move |ui, app: &mut PhotocraftApp| {
            if !ready {
                PhotocraftApp::setup_context(ui.ctx(), crate::theme::ThemeKind::Studio);
                ready = true;
                return;
            }
            crate::genstudio_ui::show(app, ui.ctx());
        },
        app,
    );
    for model in photocraft_engine::genstudio::MODELS {
        h.state_mut().session.edit_prefs(|p| p.gen_studio.model = model.id.into());
        h.run_steps(8);
        let title = h.get_by_role_and_label(egui::accesskit::Role::Label, &format!("GenGate | {}", model.label)).rect();
        let window = h.ctx.memory(|m| m.area_rect(egui::Id::new("genstudio-workspace"))).unwrap();
        assert!(title.left() - window.left() < 65.0, "title belongs beside the collapse triangle at the left edge: {title:?} in {window:?}");
    }
}

#[test]
fn opening_workspace_restores_a_previously_collapsed_window() {
    use egui_kittest::kittest::Queryable;
    let mut app = PhotocraftApp::new(Session::new(), crate::Services::default());
    app.ui.gen_studio.initialized = true;
    let mut ready = false;
    let mut h = egui_kittest::Harness::builder().with_size(egui::vec2(1200.0, 900.0)).build_ui_state(
        move |ui, app: &mut PhotocraftApp| {
            if !ready {
                PhotocraftApp::setup_context(ui.ctx(), crate::theme::ThemeKind::Studio);
                ready = true;
                return;
            }
            crate::genstudio_ui::show(app, ui.ctx());
        },
        app,
    );
    let id = egui::Id::new("genstudio-workspace").with("collapsing");
    let mut collapsed = egui::collapsing_header::CollapsingState::load_with_default_open(&h.ctx, id, false);
    collapsed.set_open(false);
    collapsed.store(&h.ctx);
    let ctx = h.ctx.clone();
    crate::menus::invoke(h.state_mut(), &ctx, "genstudio.open", json!({})).unwrap();
    h.run_steps(10);
    let model = photocraft_engine::genstudio::model_spec(&h.state().session.prefs().gen_studio.model).unwrap();
    assert!(h.get_by_role_and_label(egui::accesskit::Role::Label, &format!("GenGate | {}", model.label)).rect().is_positive());
    assert!(egui::collapsing_header::CollapsingState::load(&h.ctx, id).unwrap().is_open());
    assert!(!h.state().studio.info["slots"].as_array().unwrap().is_empty(), "the workspace body must render");
}

fn workspace_harness() -> egui_kittest::Harness<'static, PhotocraftApp> {
    let mut app = PhotocraftApp::new(Session::new(), crate::Services::default());
    app.ui.gen_studio.open = true;
    app.ui.gen_studio.initialized = true;
    app.ui.gen_studio.window_position = Some([120.0, 100.0]);
    let mut ready = false;
    let mut h = egui_kittest::Harness::builder().with_size(egui::vec2(1600.0, 1100.0)).build_ui_state(
        move |ui, app: &mut PhotocraftApp| {
            if !ready {
                PhotocraftApp::setup_context(ui.ctx(), crate::theme::ThemeKind::Studio);
                ready = true;
                return;
            }
            crate::genstudio_ui::show(app, ui.ctx());
        },
        app,
    );
    h.run_steps(10);
    h
}

#[test]
fn collapsed_workspace_fits_the_title_and_restores_its_size_and_prompt() {
    use egui_kittest::kittest::Queryable;
    let mut h = workspace_harness();
    h.state_mut().ui.gen_studio.workspace.prompt = "Keep this prompt".into();
    let before = h.ctx.memory(|m| m.area_rect(egui::Id::new("genstudio-workspace"))).unwrap();
    h.get_by_role_and_label(egui::accesskit::Role::Button, "Collapse GenGate").click();
    h.run_steps(8);
    assert!(h.state().ui.gen_studio.collapsed);
    for model in photocraft_engine::genstudio::MODELS {
        h.state_mut().session.edit_prefs(|p| p.gen_studio.model = model.id.into());
        h.run_steps(8);
        let text = h.get_by_role_and_label(egui::accesskit::Role::Label, &format!("GenGate | {}", model.label)).rect();
        let chip = h.ctx.memory(|m| m.area_rect(egui::Id::new("gengate-workspace-chip"))).unwrap();
        assert!(chip.height() < 55.0, "compact title height: {chip:?}");
        assert!(chip.width() > text.width() + 40.0 && chip.width() < text.width() + 100.0, "chip fits the title and controls: {text:?} in {chip:?}");
        assert!((chip.min - before.min).length() < 2.0, "collapsing must preserve position");
    }
    h.get_by_role_and_label(egui::accesskit::Role::Button, "Expand GenGate").click();
    h.run_steps(10);
    let restored = h.ctx.memory(|m| m.area_rect(egui::Id::new("genstudio-workspace"))).unwrap();
    assert!((restored.size() - before.size()).length() < 2.0, "expanded size: {before:?} -> {restored:?}");
    assert!((restored.min - before.min).length() < 2.0);
    assert_eq!(h.state().ui.gen_studio.workspace.prompt, "Keep this prompt");
    h.get_by_role_and_label(egui::accesskit::Role::Button, "Collapse GenGate").click();
    h.run_steps(5);
    h.get_by_role_and_label(egui::accesskit::Role::Button, "Close GenGate").click();
    h.run_steps(5);
    assert!(!h.state().ui.gen_studio.open);
    let ctx = h.ctx.clone();
    crate::menus::invoke(h.state_mut(), &ctx, "genstudio.open", json!({})).unwrap();
    h.run_steps(5);
    assert!(h.get_by_role_and_label(egui::accesskit::Role::Button, "Expand GenGate").rect().is_positive());
}

#[test]
fn workspace_and_chip_follow_every_pointer_frame_without_lag_or_release_jump() {
    use egui_kittest::kittest::Queryable;
    let mut h = workspace_harness();
    for collapsed in [false, true] {
        if collapsed {
            h.get_by_role_and_label(egui::accesskit::Role::Button, "Collapse GenGate").click();
            h.run_steps(8);
        }
        let id = egui::Id::new(if collapsed { "gengate-workspace-chip" } else { "genstudio-workspace" });
        let model = photocraft_engine::genstudio::model_spec(&h.state().session.prefs().gen_studio.model).unwrap();
        let at = h.get_by_role_and_label(egui::accesskit::Role::Label, &format!("GenGate | {}", model.label)).rect().center();
        let before = h.ctx.memory(|m| m.area_rect(id)).unwrap();
        h.event(egui::Event::PointerMoved(at));
        h.run_steps(1);
        h.event(egui::Event::PointerButton { pos: at, button: egui::PointerButton::Primary, pressed: true, modifiers: egui::Modifiers::NONE });
        h.run_steps(1);
        assert!(h.state().ui.gen_studio.drag_offset.is_some());
        let mut end = at;
        let mut last = before;
        for delta in [egui::vec2(21.0, 11.0), egui::vec2(53.0, 29.0), egui::vec2(120.0, 64.0), egui::vec2(75.0, 37.0)] {
            end = at + delta;
            h.event(egui::Event::PointerMoved(end));
            h.run_steps(1);
            last = h.ctx.memory(|m| m.area_rect(id)).unwrap();
            assert!(
                (last.min - before.min - delta).length() < 1.5,
                "each frame follows the grab point: collapsed={collapsed}, delta={delta:?}, {before:?} -> {last:?}"
            );
        }
        let release_delta = egui::vec2(6.0, 4.0);
        end += release_delta;
        h.event(egui::Event::PointerMoved(end));
        h.event(egui::Event::PointerButton { pos: end, button: egui::PointerButton::Primary, pressed: false, modifiers: egui::Modifiers::NONE });
        h.run_steps(8);
        let released = h.ctx.memory(|m| m.area_rect(id)).unwrap();
        assert!((released.min - last.min - release_delta).length() < 1.5, "release must follow the final pointer position");
        assert!(h.state().ui.gen_studio.drag_offset.is_none());
        assert_eq!(h.state().ui.gen_studio.collapsed, collapsed);
    }
}
