//! Gesture regressions, without starting any model processes.
use crate::PhotocraftApp;
use photocraft_engine::Session;
use serde_json::json;

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
        assert!(title.left() - window.left() < 40.0, "title belongs at the left edge: {title:?} in {window:?}");
    }
}
