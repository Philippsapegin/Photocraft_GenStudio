//! Canvas feedback for the shared AI fill queue. No edits to the user's selection.
use crate::{PhotocraftApp, canvas::ViewXform, theme::Tokens};
use egui::{Rect, Sense, Stroke, TextureHandle, TextureOptions, pos2, vec2};
use photocraft_engine::genstudio_queue::{FillMask, FillView};
use serde_json::{Value, json};

#[derive(Default)]
pub struct Cache {
    masks: Vec<Overlay>,
    pub info: Vec<Value>,
}

struct Overlay {
    id: u64,
    texture: TextureHandle,
    edges: Vec<([f32;2],[f32;2])>,
}

fn build(ctx: &egui::Context, id: u64, mask: &FillMask) -> Overlay {
    let bounds = mask.bounds;
    let k = bounds.width().max(bounds.height()).div_ceil(512).max(1);
    let (w,h) = (bounds.width().div_ceil(k).max(1) as usize,bounds.height().div_ceil(k).max(1) as usize);
    let mut grid = vec![0u8;w*h];
    let stride = mask.rect.width().max(1) as usize;
    for y in bounds.y0..bounds.y1 {
        for x in bounds.x0..bounds.x1 {
            let index = (y-mask.rect.y0) as usize*stride+(x-mask.rect.x0) as usize;
            let value = mask.coverage.get(index).copied().unwrap_or(0.0);
            let index = ((y-bounds.y0) as u32/k) as usize*w+((x-bounds.x0) as u32/k) as usize;
            if let Some(pixel) = grid.get_mut(index) { *pixel = (*pixel).max((value.clamp(0.0,1.0)*255.0).round() as u8); }
        }
    }
    let mut rgba = Vec::with_capacity(grid.len()*4);
    for value in &grid { rgba.extend_from_slice(&[255,255,255,*value]); }
    let image = egui::ColorImage::from_rgba_unmultiplied([w,h],&rgba);
    let texture = ctx.load_texture(format!("ai-fill-mask-{id}"),image,TextureOptions::NEAREST);
    let mut edges = Vec::new();
    for y in 0..h {
        for x in 0..w {
            if grid.get(y*w+x).copied().unwrap_or(0) == 0 { continue; }
            let x0 = bounds.x0 as f32 + x as f32*k as f32;
            let y0 = bounds.y0 as f32 + y as f32*k as f32;
            let x1 = (x0+k as f32).min(bounds.x1 as f32);
            let y1 = (y0+k as f32).min(bounds.y1 as f32);
            let mut edge = |empty:bool,a,b| { if empty && edges.len()<8192 { edges.push((a,b)); } };
            edge(y==0 || grid.get((y-1)*w+x).copied().unwrap_or(0)==0,[x0,y0],[x1,y0]);
            edge(x+1==w || grid.get(y*w+x+1).copied().unwrap_or(0)==0,[x1,y0],[x1,y1]);
            edge(y+1==h || grid.get((y+1)*w+x).copied().unwrap_or(0)==0,[x1,y1],[x0,y1]);
            edge(x==0 || grid.get(y*w+x-1).copied().unwrap_or(0)==0,[x0,y1],[x0,y0]);
        }
    }
    Overlay { id,texture,edges }
}

fn draw_mask(ui: &egui::Ui, overlay: &Overlay, view: &FillView, xf: &ViewXform) {
    let b = view.mask.bounds;
    let tint = Tokens::get(ui.ctx()).accent.gamma_multiply(if view.job.is_some() {0.32} else {0.20});
    let corners = [(b.x0,b.y0),(b.x1,b.y0),(b.x1,b.y1),(b.x0,b.y1)];
    let uv = [pos2(0.0,0.0),pos2(1.0,0.0),pos2(1.0,1.0),pos2(0.0,1.0)];
    let mut mesh = egui::Mesh::with_texture(overlay.texture.id());
    for ((x,y),uv) in corners.into_iter().zip(uv) {
        mesh.vertices.push(egui::epaint::Vertex {pos:xf.to_screen(x as f32,y as f32),uv,color:tint});
    }
    mesh.indices.extend_from_slice(&[0,1,2,0,2,3]);
    let painter = ui.painter().with_clip_rect(xf.rect);
    painter.add(mesh);
    let [dark,light] = Tokens::cursor_outline();
    for (a,b) in &overlay.edges {
        let points = [xf.to_screen(a[0],a[1]),xf.to_screen(b[0],b[1])];
        painter.line_segment(points,Stroke::new(2.0,dark));
        painter.line_segment(points,Stroke::new(0.8,light));
    }
}

fn control_rect(bounds: Rect, viewport: Rect, used: &[Rect], width:f32) -> Rect {
    let x = (bounds.center().x-width/2.0).min(viewport.right()-width-3.0).max(viewport.left()+3.0);
    let base = bounds.bottom()+5.0;
    let mut result = Rect::from_min_size(pos2(x,base),vec2(width,16.0));
    for n in 0usize..65 {
        // Prefer space below all masks; move upward only at the viewport edge.
        let shift = if n<=32 {n as f32*18.0} else {-((n-32) as f32)*18.0};
        let y = (base+shift).min(viewport.bottom()-18.0).max(viewport.top()+3.0);
        result = Rect::from_min_size(pos2(x,y),vec2(width,16.0));
        if !used.iter().any(|r|r.expand(1.0).intersects(result)) { break; }
    }
    result
}

fn rect_json(rect:Rect) -> Value { json!([rect.min.x,rect.min.y,rect.width(),rect.height()]) }

pub fn show(app: &mut PhotocraftApp, ui: &mut egui::Ui, xf: &ViewXform, index:usize) {
    let Some(doc) = app.session.documents().get(index).map(|st|st.doc.id) else { return };
    let views = photocraft_engine::genstudio_queue::views(&app.session);
    let mut cache = std::mem::take(&mut app.ai_fill_overlays);
    cache.masks.retain(|overlay|views.iter().any(|v|v.id==overlay.id));
    cache.info.clear();
    let mut used = Vec::new();
    let mut cancel = Vec::new();
    let show_progress = !app.ui.gen_studio.hide_fill_progress;
    for view in views.iter().filter(|v|v.document==doc) {
        let bounds = xf.doc_rect(view.mask.bounds);
        if !bounds.is_finite() || !bounds.intersects(xf.rect) { continue; }
        if !cache.masks.iter().any(|m|m.id==view.id) { cache.masks.push(build(ui.ctx(),view.id,&view.mask)); }
        if let Some(mask) = cache.masks.iter().find(|m|m.id==view.id) { draw_mask(ui,mask,view,xf); }
        let queued = view.job.is_none();
        let bar_width = bounds.width().clamp(48.0,100.0);
        let width = if show_progress {bar_width} else {0.0} + if queued {18.0} else {0.0};
        let controls = control_rect(bounds,xf.rect,&used,width.max(1.0));
        used.push(controls);
        let mut bar = None;
        let mut cross = None;
        if show_progress || queued {
            egui::Area::new(egui::Id::new(("ai-fill-controls",view.id))).order(egui::Order::Foreground)
                .fixed_pos(controls.min).movable(false).sense(Sense::hover()).show(ui.ctx(),|ui| {
                    ui.set_clip_rect(xf.rect);
                    let (rect,_) = ui.allocate_exact_size(vec2(width,16.0),Sense::hover());
                    let tokens = Tokens::get(ui.ctx());
                    if show_progress {
                        let track = Rect::from_min_size(rect.min+vec2(0.0,6.0),vec2(bar_width,3.0));
                        bar = Some(track);
                        ui.painter().rect_filled(track,1.0,tokens.field);
                        ui.painter().rect_stroke(track,1.0,Stroke::new(1.0,tokens.card_border),egui::StrokeKind::Outside);
                        if view.job.is_some() {
                            let progress = view.progress.clamp(0.0,1.0);
                            let fill = if progress>0.2 {
                                Rect::from_min_size(track.min,vec2(track.width()*progress,track.height()))
                            } else {
                                let phase = ui.ctx().input(|i|i.time) as f32;
                                let x = (phase*0.75).fract()*(track.width()-16.0);
                                Rect::from_min_size(track.min+vec2(x,0.0),vec2(16.0,track.height()))
                            };
                            ui.painter().rect_filled(fill,1.0,tokens.accent);
                        }
                    }
                    if queued {
                        let button = Rect::from_min_size(rect.min+vec2(if show_progress {bar_width+3.0} else {0.0},0.0),vec2(15.0,15.0));
                        cross = Some(button);
                        let response=ui.scope(|ui| {
                            ui.spacing_mut().button_padding=vec2(2.0,0.0);
                            ui.spacing_mut().interact_size=vec2(15.0,15.0);
                            ui.put(button,egui::Button::new("×").small())
                        }).inner;
                        if response.on_hover_text("Cancel queued fill").clicked() { cancel.push(view.id); }
                    }
                });
        }
        cache.info.push(json!({"id":view.id,"state":if queued {"queued"} else {"running"},
            "maskBounds":rect_json(bounds),"progressBar":bar.map(rect_json),"cancelButton":cross.map(rect_json),
            "progress":view.progress}));
    }
    app.ai_fill_overlays = cache;
    for id in cancel { let _ = app.run("genstudio.fill.cancel",json!({"id":id})); }
    if !views.is_empty() { ui.ctx().request_repaint_after(std::time::Duration::from_millis(33)); }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;

    #[test]
    fn ai_fill_mask_stays_visible_with_progress_disabled_and_its_cross_cancels_only_the_queue() {
        let mut app=PhotocraftApp::new(photocraft_engine::Session::new(),crate::Services::default());
        app.run("file.new",json!({"width":384,"height":256})).unwrap();
        app.run("genstudio.fill.enqueue",json!({"command":"paint.remove","params":{"points":[[180,100]],"size":16}})).unwrap();
        let document=app.session.active().unwrap().doc.clone();
        let mut harness=egui_kittest::Harness::builder().with_size(vec2(800.0,600.0)).build_ui_state(|ui,app:&mut PhotocraftApp| {
            if ui.ctx().fonts(|f|f.families().contains(&egui::FontFamily::Name("medium".into()))) {
                let xf=ViewXform {rect:ui.max_rect(),zoom:1.0,center:[192.0,128.0],flip:false,rotation:0.0,aspect:1.0};
                show(app,ui,&xf,0);
            }
        },app);
        PhotocraftApp::setup_context(&harness.ctx,crate::theme::ThemeKind::Studio);
        harness.run_steps(5);
        let info=harness.state().ai_fill_overlays.info.first().unwrap();
        assert!(!info["progressBar"].is_null() && !info["cancelButton"].is_null());
        let bounds=info["maskBounds"].clone();
        harness.state_mut().ui.gen_studio.hide_fill_progress=true;
        harness.run_steps(3);
        let info=harness.state().ai_fill_overlays.info.first().unwrap();
        assert!(info["progressBar"].is_null() && !info["cancelButton"].is_null());
        assert_eq!(info["maskBounds"],bounds);
        let rect=info["cancelButton"].as_array().unwrap();
        let at=pos2((rect[0].as_f64().unwrap()+rect[2].as_f64().unwrap()/2.0) as f32,
                    (rect[1].as_f64().unwrap()+rect[3].as_f64().unwrap()/2.0) as f32);
        harness.hover_at(at);harness.run_steps(2);harness.drag_at(at);harness.run_steps(2);harness.drop_at(at);harness.run_steps(3);
        assert!(!photocraft_engine::genstudio_queue::has_pending(&harness.state().session));
        assert!(harness.state().ai_fill_overlays.info.is_empty());
        assert!(Arc::ptr_eq(&document,&harness.state().session.active().unwrap().doc));
    }

    #[test]
    fn ai_fill_controls_for_overlapping_masks_do_not_cover_each_other() {
        let viewport=Rect::from_min_size(pos2(0.0,0.0),vec2(1000.0,700.0));
        for bottom in [200.0,690.0] {
            let bounds=Rect::from_min_size(pos2(400.0,bottom-50.0),vec2(100.0,50.0));
            let mut used=Vec::new();
            for _ in 0..32 {
                let rect=control_rect(bounds,viewport,&used,118.0);
                assert!(viewport.contains_rect(rect));
                assert!(!used.iter().any(|old:&Rect|old.intersects(rect)));
                if bottom==200.0 && used.len()<3 { assert!(rect.top()>bounds.bottom(),"nearby masks keep controls below while there is room"); }
                used.push(rect);
            }
        }
    }
}
