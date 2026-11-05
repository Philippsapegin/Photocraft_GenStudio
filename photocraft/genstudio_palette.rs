//! One session, with Studio rendered in a movable native tool palette.
use crate::PhotocraftApp;
use egui::{Pos2, Rect, Vec2, ViewportId};
use serde_json::json;

#[derive(Default)]
pub(crate) struct Runtime {
    geometry_loaded: bool,
    pub move_pending: bool,
    pub size_pending: bool,
    pub input: std::collections::VecDeque<Vec<egui::Event>>,
    pub capture_requested: bool,
    capture: Option<(craft_ui::capture::CaptureSlot, web_time::Instant)>,
    result: serde_json::Value,
    settle: u8,
}
pub(crate) struct Request {
    position: Option<[f32; 2]>,
    size: Option<[f32; 2]>,
    input: std::collections::VecDeque<Vec<egui::Event>>,
    capture: bool,
}
/// Bounded, viewport-specific automation through the existing Studio configure
/// command. It targets only this palette and never injects OS-wide input.
pub(crate) fn parse_request(value: &serde_json::Value) -> Result<Request, String> {
    let map = value
        .as_object()
        .ok_or("Palette settings must be an object")?;
    if map
        .keys()
        .any(|k| !matches!(k.as_str(), "positionPixels" | "size" | "input" | "capture"))
    {
        return Err("Unknown palette setting".into());
    }
    let pair = |key: &str| -> Result<Option<[f32; 2]>, String> {
        let Some(value) = map.get(key) else {
            return Ok(None);
        };
        let rows = value
            .as_array()
            .filter(|v| v.len() == 2)
            .ok_or("Use two numeric coordinates")?;
        let a = rows
            .first()
            .and_then(|v| v.as_f64())
            .ok_or("Invalid coordinate")?;
        let b = rows
            .get(1)
            .and_then(|v| v.as_f64())
            .ok_or("Invalid coordinate")?;
        if !a.is_finite() || !b.is_finite() || a.abs() > 1_000_000.0 || b.abs() > 1_000_000.0 {
            return Err("Invalid coordinate range".into());
        }
        Ok(Some([a as f32, b as f32]))
    };
    let position = pair("positionPixels")?;
    let size = pair("size")?;
    if size.is_some_and(|s| !(680.0..=4096.0).contains(&s[0]) || !(460.0..=4096.0).contains(&s[1]))
    {
        return Err("Palette size must be 680..4096 by 460..4096 points".into());
    }
    let capture = map
        .get("capture")
        .map_or(Ok(false), |v| v.as_bool().ok_or("Capture must be boolean"))?;
    let mut input = std::collections::VecDeque::new();
    if let Some(value) = map.get("input") {
        let rows = value
            .as_array()
            .filter(|r| r.len() <= 32)
            .ok_or("Use at most 32 input steps")?;
        for row in rows {
            let item = row.as_object().ok_or("Input must be an object")?;
            if item.keys().any(|k| {
                !matches!(
                    k.as_str(),
                    "kind" | "x" | "y" | "text" | "key" | "ctrl" | "shift"
                )
            }) {
                return Err("Unknown input field".into());
            }
            let boolean = |key: &str| {
                item.get(key)
                    .map_or(Ok(false), |v| v.as_bool().ok_or("Modifier must be boolean"))
            };
            let ctrl = boolean("ctrl")?;
            let modifiers = egui::Modifiers {
                ctrl,
                command: ctrl,
                shift: boolean("shift")?,
                ..Default::default()
            };
            let mut events = Vec::new();
            match row["kind"].as_str() {
                Some(kind @ ("move" | "down" | "up" | "click")) => {
                    let number = |key: &str| {
                        row[key]
                            .as_f64()
                            .filter(|n| n.is_finite() && n.abs() <= 32768.0)
                            .map(|n| n as f32)
                            .ok_or("Invalid pointer coordinate")
                    };
                    let pos = egui::pos2(number("x")?, number("y")?);
                    events.push(egui::Event::PointerMoved(pos));
                    if kind != "move" {
                        events.push(egui::Event::PointerButton {
                            pos,
                            button: egui::PointerButton::Primary,
                            pressed: kind != "up",
                            modifiers,
                        });
                    }
                    if kind == "click" {
                        input.push_back(events);
                        events = vec![egui::Event::PointerMoved(pos),egui::Event::PointerButton {
                            pos,
                            button: egui::PointerButton::Primary,
                            pressed: false,
                            modifiers,
                        }];
                    }
                }
                Some("text") => {
                    let text = row["text"]
                        .as_str()
                        .filter(|s| s.len() <= 12000 && !s.contains('\0'))
                        .ok_or("Invalid text")?;
                    events.push(egui::Event::Text(text.into()));
                }
                Some("key") => {
                    let key = match row["key"].as_str() {
                        Some("A") => egui::Key::A,
                        Some("Enter") => egui::Key::Enter,
                        Some("Escape") => egui::Key::Escape,
                        Some("Backspace") => egui::Key::Backspace,
                        Some("Delete") => egui::Key::Delete,
                        Some("Tab") => egui::Key::Tab,
                        Some("ArrowDown") => egui::Key::ArrowDown,
                        Some("ArrowUp") => egui::Key::ArrowUp,
                        _ => return Err("Unsupported palette key".into()),
                    };
                    events.push(egui::Event::Key {
                        key,
                        physical_key: None,
                        pressed: true,
                        repeat: false,
                        modifiers,
                    });
                    events.push(egui::Event::Key {
                        key,
                        physical_key: None,
                        pressed: false,
                        repeat: false,
                        modifiers,
                    });
                }
                _ => return Err("Unsupported palette input".into()),
            }
            input.push_back(events);
        }
    }
    Ok(Request {
        position,
        size,
        input,
        capture,
    })
}
pub(crate) fn configure(app: &mut PhotocraftApp, request: Request) -> Result<(), String> {
    if !app.ui.gen_studio.open {
        return Err("Open GenGate Studio first".into());
    }
    if (!request.input.is_empty() || request.capture)
        && (!crate::menus::modal_allows(app, "genstudio.open") || app.ui.gen_studio.updates_open
            || app.studio.info["palette"]["hidden"] == true)
    {
        return Err("Bring GenGate Studio to the foreground first".into());
    }
    let runtime = &mut app.studio.palette;
    if runtime.input.len() + request.input.len() > 64 {
        return Err("Wait for pending palette input".into());
    }
    if request.capture && (runtime.capture_requested || runtime.capture.is_some()) {
        return Err("A palette capture is already pending".into());
    }
    if let Some(position) = request.position {
        app.ui.gen_studio.palette_position = Some(position);
        runtime.move_pending = true;
    }
    if let Some(size) = request.size {
        app.ui.gen_studio.palette_size = Some(size);
        runtime.size_pending = true;
    }
    runtime.input.extend(request.input);
    if request.capture {
        runtime.result = serde_json::Value::Null;
        runtime.capture_requested = true;
        runtime.settle = 3;
    }
    Ok(())
}
pub(crate) fn capture_info(app: &PhotocraftApp) -> serde_json::Value {
    let runtime = &app.studio.palette;
    json!({"pending":runtime.capture_requested || runtime.capture.is_some(),"capture":runtime.result})
}
fn input_key() -> egui::Id {
    egui::Id::new("gengate-palette-input")
}
#[derive(Clone, Default, PartialEq, serde::Serialize, serde::Deserialize)]
struct Geometry {
    position: Option<[f32; 2]>,
    size: Option<[f32; 2]>,
    collapsed: bool,
}
fn saved_geometry_key() -> egui::Id {
    egui::Id::new("gengate-palette-geometry")
}
fn restore_geometry(app: &mut PhotocraftApp, ctx: &egui::Context) {
    if app.studio.palette.geometry_loaded {
        return;
    }
    app.studio.palette.geometry_loaded = true;
    if let Some(saved) = ctx.data_mut(|d| d.get_persisted::<Geometry>(saved_geometry_key())) {
        let state = &mut app.ui.gen_studio;
        state.palette_position = saved.position.filter(|p| valid_position(*p).is_some());
        let size = valid_size(saved.size);
        state.palette_size = Some([size.x, size.y]);
        state.collapsed = saved.collapsed;
    }
}
fn save_geometry(app: &PhotocraftApp, ctx: &egui::Context) {
    let state = &app.ui.gen_studio;
    let saved = Geometry {
        position: state.palette_position,
        size: state.palette_size,
        collapsed: state.collapsed,
    };
    ctx.data_mut(|d| d.insert_persisted(saved_geometry_key(), saved));
}

pub fn id() -> ViewportId {
    ViewportId::from_hash_of("gengate-studio-palette")
}
fn geometry_key() -> egui::Id {
    egui::Id::new("gengate-palette-restore")
}
pub fn requested_position(ctx: &egui::Context) -> Option<Pos2> {
    ctx.data(|d| d.get_temp::<Pos2>(geometry_key()))
}

/// Convert local coordinates through physical desktop pixels. Each viewport may
/// have its own native DPI; egui's zoom is shared by the whole context.
pub(crate) fn convert_point(
    point: Pos2,
    from: Rect,
    from_scale: f32,
    to: Rect,
    to_scale: f32,
) -> Option<Pos2> {
    if !point.is_finite()
        || !from.is_finite()
        || !to.is_finite()
        || !from_scale.is_finite()
        || !to_scale.is_finite()
        || from_scale <= 0.0
        || to_scale <= 0.0
    {
        return None;
    }
    Some(
        Pos2::ZERO
            + ((from.min.to_vec2() + point.to_vec2()) * from_scale / to_scale - to.min.to_vec2()),
    )
}
pub(crate) fn pointer_in(ctx: &egui::Context, target: ViewportId) -> Option<Pos2> {
    let point = ctx.pointer_latest_pos()?;
    point_in(ctx, point, target)
}
pub(crate) fn point_in(ctx: &egui::Context, point: Pos2, target: ViewportId) -> Option<Pos2> {
    let source = ctx.viewport_id();
    if source == target {
        return Some(point);
    }
    let zoom = ctx.zoom_factor();
    ctx.input(|i| {
        let from = i.raw.viewports.get(&source)?;
        let to = i.raw.viewports.get(&target)?;
        convert_point(
            point,
            from.inner_rect?,
            from.native_pixels_per_point? * zoom,
            to.inner_rect?,
            to.native_pixels_per_point? * zoom,
        )
    })
}
pub fn raw_input(ctx: &egui::Context, input: &mut egui::RawInput) {
    if input.viewport_id != id() {
        return;
    }
    if let Some(events) = ctx.data_mut(|d| d.remove_temp::<Vec<egui::Event>>(input_key())) {
        input.focused = true;
        let modifiers = events
            .iter()
            .find_map(|e| match e {
                egui::Event::Key { modifiers, .. }
                | egui::Event::PointerButton { modifiers, .. } => Some(*modifiers),
                _ => None,
            })
            .unwrap_or_default();
        input.events.push(egui::Event::ModifiersChanged(modifiers));
        input.events.extend(events);
    }
    // Hooks run before egui enters this viewport, so ctx may still name the
    // owner. Explicitly target paste at the child, never the editor canvas.
    input.events.retain(|event| {
        if matches!(event,egui::Event::Key{key:egui::Key::V,pressed:true,modifiers,..} if modifiers.command && !modifiers.alt && !modifiers.shift) {
            ctx.send_viewport_cmd_to(id(),egui::ViewportCommand::RequestPaste);
            false
        } else {true}
    });
    crate::shortcuts::clipboard_keys(ctx, true, input);
    crate::widgets::cap_text_events(&mut input.events);
}
fn valid_position(position: [f32; 2]) -> Option<Pos2> {
    position
        .iter()
        .all(|p| p.is_finite() && p.abs() < 10_000_000.0)
        .then(|| Pos2::new(position[0], position[1]))
}
fn valid_size(size: Option<[f32; 2]>) -> Vec2 {
    size.filter(|s| {
        s.iter()
            .all(|n| n.is_finite() && (100.0..=16384.0).contains(n))
    })
    .map_or(egui::vec2(940.0, 650.0), |s| egui::vec2(s[0], s[1]))
    .max(egui::vec2(680.0, 460.0))
    .min(egui::vec2(4096.0, 4096.0))
}
fn capture_size_allowed(size: Vec2, scale: f32) -> bool {
    size.is_finite() && size.x>0.0 && size.y>0.0 && scale.is_finite() && scale>0.0
        && (f64::from(size.x)*f64::from(scale)).ceil()*(f64::from(size.y)*f64::from(scale)).ceil()*4.0 <= (64u64<<20) as f64
}
fn collapsed_width(info:&serde_json::Value)->f32 {
    if info["window"]["collapsed"]==true {
        info["window"]["headerRect"].get(2).and_then(|v|v.as_f64()).map_or(400.0,|width|(width as f32+2.0).clamp(180.0,400.0))
    } else {400.0}
}
pub fn show(app: &mut PhotocraftApp, ctx: &egui::Context) {
    restore_geometry(app, ctx);
    if ctx.embed_viewports() || !app.ui.gen_studio.open {
        if !app.ui.gen_studio.open {
            app.studio.palette = Runtime {
                geometry_loaded: true,
                ..Default::default()
            };
            ctx.data_mut(|d| d.remove::<Vec<egui::Event>>(input_key()));
            app.studio.info["palette"]=json!({"native":false,"hidden":true,"pendingInput":0,
                "positionPixels":app.ui.gen_studio.palette_position,"size":app.ui.gen_studio.palette_size});
        }
        crate::genstudio_ui::show_in_viewport(app, ctx, false);
        app.ui.gen_studio.palette_live = false;
        return;
    }
    let opening = !app.ui.gen_studio.palette_live;
    let collapsed = app.ui.gen_studio.collapsed;
    let expanded = valid_size(app.ui.gen_studio.palette_size);
    let size = if collapsed {
        egui::vec2(collapsed_width(&app.studio.info), 34.0)
    } else {
        expanded
    };
    let position = app.ui.gen_studio.palette_position.and_then(valid_position);
    let hidden = ctx.input(|i| {
        i.raw
            .viewports
            .get(&ViewportId::ROOT)
            .is_some_and(|v| v.minimized == Some(true))
    }) || !crate::menus::modal_allows(app, "genstudio.open") || app.ui.gen_studio.updates_open;
    let mut injected=false;
    if !hidden && let Some(events) = app.studio.palette.input.pop_front() {
        injected=true;
        ctx.data_mut(|d| d.insert_temp(input_key(), events));
        ctx.request_repaint_of(id());
    }
    let ready = app
        .studio
        .palette
        .capture
        .as_ref()
        .and_then(|(slot, _)| slot.take());
    if let Some(result) = ready {
        app.studio.palette.capture = None;
        app.studio.palette.result = match result {
            Ok(image) => crate::control::save_screenshot(app, &image, None),
            Err(error) => json!({"ok":false,"error":error.to_string()}),
        };
    } else if app
        .studio
        .palette
        .capture
        .as_ref()
        .is_some_and(|(_, start)| start.elapsed().as_secs() > 8)
    {
        app.studio.palette.capture = None;
        app.studio.palette.result = json!({"ok":false,"error":"Palette capture timed out"});
    }
    ctx.data_mut(|d| {
        d.remove::<Pos2>(geometry_key());
        if let Some(p) = position {
            d.insert_temp(geometry_key(), p);
        }
    });
    let mut builder = crate::os_window::builder()
        .with_title("GenGate Studio")
        .with_decorations(false)
        .with_taskbar(false)
        .with_minimize_button(false)
        .with_maximize_button(false)
        .with_resizable(!collapsed)
        .with_min_inner_size(if collapsed {
            egui::vec2(180.0, 34.0)
        } else {
            egui::vec2(680.0, 460.0)
        })
        .with_visible(!hidden)
        .with_drag_and_drop(true);
    // Observed geometry is persistence data, not a new size request each frame.
    // Feeding it back while a native resize is in flight oscillates between the
    // old and requested sizes. Later resizes use explicit viewport commands.
    if opening {builder=builder.with_inner_size(size);}
    ctx.show_viewport_immediate(id(),builder,|ui,class| {
        let native=class!=egui::ViewportClass::EmbeddedWindow;
        let context=ui.ctx().clone();
        let ctx=&context;
        if ctx.input(|i|i.viewport().close_requested()) {app.ui.gen_studio.open=false;return;}
        let automation_before=app.automation_input;
        app.automation_input|=injected;
        if opening && !hidden {ctx.send_viewport_cmd(egui::ViewportCommand::Focus);}
        if app.studio.palette.move_pending {
            app.studio.palette.move_pending=false;
            if let Some(position)=position {ctx.send_viewport_cmd(egui::ViewportCommand::OuterPosition(position/ctx.pixels_per_point()));}
        }
        if app.studio.palette.size_pending {app.studio.palette.size_pending=false;if !collapsed {ctx.send_viewport_cmd(egui::ViewportCommand::InnerSize(expanded));}}
        if let Some(info)=ctx.input(|i|i.raw.viewports.get(&id()).cloned()) {
            if let (Some(rect),Some(scale))=(info.outer_rect,info.native_pixels_per_point) {
                let pixels=rect.min.to_vec2()*scale*ctx.zoom_factor();
                if pixels.is_finite() {app.ui.gen_studio.palette_position=Some([pixels.x,pixels.y]);}
            }
            if !collapsed && let Some(rect)=info.inner_rect && rect.size().is_finite() {
                app.ui.gen_studio.palette_size=Some([rect.width(),rect.height()]);
            }
        }
        crate::genstudio_ui::show_in_viewport(app,ctx,native);
        if native && !hidden {
            let files=ctx.input(|i|i.raw.dropped_files.clone());
            if !files.is_empty() {
                let at=app.services.cursor_pos.as_mut().and_then(|f|f(ctx));
                // A palette point is not a point on the document canvas.
                if !crate::genstudio_canvas::dropped(app,ctx,&files,at) {app.open_dropped(ctx,files,None);}
            }
            crate::layer_transfer::finish_palette(app,ctx);
            crate::shortcuts::handle(app,ctx);
            if !collapsed {crate::titlebar::resize_zones(ui);}
        }
        app.studio.info["palette"]=json!({"native":native,"viewport":format!("{:?}",id()),"positionPixels":app.ui.gen_studio.palette_position,
            "size":app.ui.gen_studio.palette_size,"focused":ctx.input(|i|i.viewport().focused),"hidden":hidden,"pendingInput":app.studio.palette.input.len()});
        if collapsed && (ctx.content_rect().width()-collapsed_width(&app.studio.info)).abs()>0.5 {
            ctx.send_viewport_cmd(egui::ViewportCommand::InnerSize(egui::vec2(collapsed_width(&app.studio.info),34.0)));
        }
        if app.studio.palette.capture_requested && app.studio.palette.input.is_empty() {
            if app.studio.palette.settle>0 {app.studio.palette.settle-=1;ctx.request_repaint();}
            else {
                app.studio.palette.capture_requested=false;
                if capture_size_allowed(ctx.content_rect().size(),ctx.pixels_per_point()) {
                    let (slot,callback)=craft_ui::capture::CaptureSlot::new(ctx,id(),64<<20);
                    app.studio.palette.capture=Some((slot,web_time::Instant::now()));
                    ctx.send_viewport_cmd(egui::ViewportCommand::Screenshot(callback));
                } else {app.studio.palette.result=json!({"ok":false,"error":"Palette capture exceeds the 64 MiB image budget"});}
            }
        }
        if app.ui.gen_studio.collapsed!=collapsed {
            let next=if app.ui.gen_studio.collapsed {egui::vec2(400.0,34.0)} else {expanded};
            ctx.send_viewport_cmd(egui::ViewportCommand::MinInnerSize(if app.ui.gen_studio.collapsed {egui::vec2(180.0,34.0)} else {egui::vec2(680.0,460.0)}));
            ctx.send_viewport_cmd(egui::ViewportCommand::Resizable(!app.ui.gen_studio.collapsed));
            ctx.send_viewport_cmd(egui::ViewportCommand::InnerSize(next));
        }
        app.automation_input=automation_before;
    });
    app.ui.gen_studio.palette_live = app.ui.gen_studio.open;
    save_geometry(app, ctx);
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn native_surface_is_filled_in_expanded_and_compact_views() {
        for (collapsed,size) in [(false,egui::vec2(940.0,650.0)),(true,egui::vec2(400.0,34.0))] {
            let mut app=PhotocraftApp::new(photocraft_engine::Session::new(),crate::Services::default());
            app.ui.gen_studio.open=true;app.ui.gen_studio.initialized=true;app.ui.gen_studio.collapsed=collapsed;
            app.studio.info=json!({});
            let mut h=egui_kittest::Harness::builder().with_size(size).build_ui_state(|ui,app:&mut PhotocraftApp| {
                if ui.ctx().fonts(|f|f.families().contains(&egui::FontFamily::Name("medium".into()))) {
                    crate::genstudio_ui::show_in_viewport(app,ui.ctx(),true);
                }
            },app);
            PhotocraftApp::setup_context(&h.ctx,crate::theme::ThemeKind::Studio);
            h.run_steps(5);
            let rect=&h.state().studio.info["window"]["rect"];
            assert!((rect[2].as_f64().unwrap() as f32-size.x).abs()<0.6,"{rect}");
            assert!((rect[3].as_f64().unwrap() as f32-size.y).abs()<0.6,"{rect}");
        }
    }
    #[test]
    fn capture_budget_counts_physical_pixels_before_requesting_gpu_readback() {
        assert!(capture_size_allowed(egui::vec2(940.0,650.0),2.0));
        assert!(capture_size_allowed(egui::vec2(4096.0,4096.0),1.0));
        assert!(!capture_size_allowed(egui::vec2(4096.0,4096.0),1.5));
        assert!(!capture_size_allowed(egui::vec2(940.0,650.0),f32::INFINITY));
        assert!(!capture_size_allowed(egui::Vec2::ZERO,1.0));
        assert_eq!(collapsed_width(&json!({"window":{"collapsed":true,"headerRect":[0,0,268,32]}})),270.0);
    }
    #[test]
    fn a_long_workflow_title_keeps_the_collapsed_close_button_inside_the_palette() {
        use egui_kittest::kittest::Queryable;
        let mut h=egui_kittest::Harness::builder().with_size(egui::vec2(400.0,60.0)).build_ui(|ui| {
            let title=egui::text::LayoutJob::simple_singleline("GenGate | ".to_owned()+&"Long workflow name ".repeat(20),egui::FontId::proportional(16.0),egui::Color32::WHITE);
            crate::genstudio_ui::floating_header(ui,"test-palette","GenGate",title,Some(true));
        });
        h.run_steps(3);
        let close=h.get_by_role_and_label(egui::accesskit::Role::Button,"Close GenGate").rect();
        assert!(close.left()>=0.0 && close.right()<=400.0,"{close:?}");
    }
    #[test]
    fn cross_window_pointer_uses_each_monitors_dpi_and_negative_desktop_coordinates() {
        let root = Rect::from_min_size(egui::pos2(100.0, 100.0), egui::vec2(800.0, 600.0));
        let palette = Rect::from_min_size(egui::pos2(800.0, 100.0), egui::vec2(700.0, 500.0));
        assert!(
            convert_point(egui::pos2(1100.0, 150.0), root, 1.0, palette, 1.5)
                .is_some_and(|p| p.distance(egui::pos2(0.0, 66.66667)) < 0.01)
        );
        let left = Rect::from_min_size(egui::pos2(-960.0, 0.0), egui::vec2(900.0, 600.0));
        assert_eq!(
            convert_point(egui::pos2(-1500.0, 100.0), root, 1.0, left, 2.0),
            Some(egui::pos2(260.0, 100.0))
        );
        assert!(convert_point(Pos2::ZERO, root, 0.0, palette, 1.0).is_none());
        assert!(valid_position([f32::NAN, 0.0]).is_none());
        assert_eq!(valid_size(Some([f32::NAN, 1.0])), egui::vec2(940.0, 650.0));
    }
    #[test]
    fn a_paste_request_targets_the_child_even_before_its_input_pass() {
        let ctx=egui::Context::default();
        ctx.set_embed_viewports(false);
        let output=ctx.run_ui(egui::RawInput::default(),|ui| {
            ui.ctx().show_viewport_deferred(id(),egui::ViewportBuilder::default(),|_,_|{});
            let mut input=egui::RawInput{viewport_id:id(),events:vec![egui::Event::Key{key:egui::Key::V,physical_key:None,pressed:true,repeat:false,modifiers:egui::Modifiers::COMMAND}],..Default::default()};
            raw_input(ui.ctx(),&mut input);
            assert!(input.events.is_empty());
        });
        assert!(output.viewport_output.get(&id()).is_some_and(|v|v.commands.iter().any(|c|matches!(c,egui::ViewportCommand::RequestPaste))));
        assert!(!output.viewport_output.get(&ViewportId::ROOT).is_some_and(|v|v.commands.iter().any(|c|matches!(c,egui::ViewportCommand::RequestPaste))));
    }
    #[test]
    fn automation_rejects_invalid_or_unbounded_requests() {
        for value in [
            json!({"size":[100,300]}),
            json!({"positionPixels":[0]}),
            json!({"capture":"true"}),
            json!({"other":true}),
            json!({"input":[{"kind":"key","key":"F4"}]}),
            json!({"input":[{"kind":"click","x":1e20,"y":0}]}),
            json!({"input":[{"kind":"text","text":"\u{0000}"}]}),
        ] {
            assert!(parse_request(&value).is_err(), "{value}");
        }
        let mut app = PhotocraftApp::new(
            photocraft_engine::Session::new(),
            crate::Services::default(),
        );
        app.ui.gen_studio.open = true;
        app.ui.gen_studio.palette_size = Some([940.0, 650.0]);
        app.studio.palette.capture_requested = true;
        let request = parse_request(&json!({"size":[900,600],"capture":true})).unwrap();
        assert!(configure(&mut app, request).is_err());
        assert_eq!(app.ui.gen_studio.palette_size, Some([940.0, 650.0]));
    }
    #[test]
    fn closing_releases_pending_input_and_capture_without_losing_workspace() {
        let mut app = PhotocraftApp::new(
            photocraft_engine::Session::new(),
            crate::Services::default(),
        );
        app.ui.gen_studio.workspace.prompt = "Keep this prompt".into();
        app.studio.palette.capture_requested = true;
        app.studio
            .palette
            .input
            .push_back(vec![egui::Event::Text("stale input".into())]);
        let ctx = egui::Context::default();
        let _ = ctx.run_ui(egui::RawInput::default(), |ui| show(&mut app, ui.ctx()));
        assert!(app.studio.palette.input.is_empty());
        assert!(!app.studio.palette.capture_requested);
        assert_eq!(app.studio.info["palette"]["native"],false);
        assert_eq!(app.ui.gen_studio.workspace.prompt, "Keep this prompt");
    }
    #[test]
    fn geometry_survives_a_new_app_and_sanitizes_corrupt_sizes() {
        let ctx = egui::Context::default();
        let mut first = PhotocraftApp::new(
            photocraft_engine::Session::new(),
            crate::Services::default(),
        );
        first.ui.gen_studio.palette_position = Some([-1200.0, 80.0]);
        first.ui.gen_studio.palette_size = Some([960.0, 680.0]);
        first.ui.gen_studio.collapsed = true;
        save_geometry(&first, &ctx);
        let mut next = PhotocraftApp::new(
            photocraft_engine::Session::new(),
            crate::Services::default(),
        );
        restore_geometry(&mut next, &ctx);
        assert_eq!(next.ui.gen_studio.palette_position, Some([-1200.0, 80.0]));
        assert_eq!(next.ui.gen_studio.palette_size, Some([960.0, 680.0]));
        assert!(next.ui.gen_studio.collapsed);
        ctx.data_mut(|d| {
            d.insert_persisted(
                saved_geometry_key(),
                Geometry {
                    position: Some([f32::INFINITY, 0.0]),
                    size: Some([16000.0, 16000.0]),
                    collapsed: false,
                },
            )
        });
        next.studio.palette.geometry_loaded = false;
        restore_geometry(&mut next, &ctx);
        assert_eq!(next.ui.gen_studio.palette_position, None);
        assert_eq!(next.ui.gen_studio.palette_size, Some([4096.0, 4096.0]));
    }
}
