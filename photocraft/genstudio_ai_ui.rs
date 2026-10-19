//! Prompt bar and AI-layer Properties; all document changes use engine commands.
use crate::{PhotocraftApp,canvas::ViewXform,theme::Tokens};
use photocraft_doc::{DocId,Layer,LayerId};
use photocraft_engine::genstudio_ai::{self,Gallery};
use egui::{TextureHandle,TextureOptions,pos2,vec2};
use serde_json::{Value,json};
use std::sync::Arc;

#[derive(Default)]
pub struct Cache {
    target:Option<(DocId,LayerId)>, blob:Option<Arc<Vec<u8>>>, gallery:Option<Gallery>,
    text:String, thumbnails:Vec<(u8,TextureHandle)>, bar_target:Option<(DocId,LayerId,Option<u8>)>,
    pub properties_info:Value, pub bar_info:Value,
    reuse:Option<(std::sync::Weak<photocraft_doc::Document>,Option<LayerId>,Option<LayerId>)>,
    bar_model:Option<Gallery>,
    bar_saved_prompt:String,
    bar_variant_count:usize,
}
fn rect(rect:egui::Rect)->Value { json!([rect.min.x,rect.min.y,rect.width(),rect.height()]) }
fn failure(app:&mut PhotocraftApp,error:String) { crate::notices::error(app,error); }

pub fn properties(app:&mut PhotocraftApp,ui:&mut egui::Ui,layer:&Layer)->bool {
    if !genstudio_ai::is_layer(layer) { return false; }
    let Some(doc)=app.session.active().map(|st|st.doc.id) else { return false };
    let mut cache=std::mem::take(&mut app.ai_layers);
    let target=(doc,layer.id);
    let Some(blob)=genstudio_ai::block(layer) else { app.ai_layers=cache;return false };
    let changed=cache.target!=Some(target) || cache.blob.as_ref().is_none_or(|old|!Arc::ptr_eq(old,blob));
    if changed {
        match genstudio_ai::gallery(layer) {
            Ok(gallery)=>{
                if cache.target!=Some(target) || cache.gallery.as_ref().is_none_or(|old|
                    (old.variants.len()==gallery.variants.len() && (old.prompt!=gallery.prompt || old.active!=gallery.active))
                    || cache.text==old.prompt || cache.text==gallery.prompt) {
                    cache.text=gallery.prompt.clone();
                }
                cache.target=Some(target);cache.blob=Some(blob.clone());cache.gallery=Some(gallery);
                cache.thumbnails.clear();
            },
            Err(error)=>{ui.label(error.to_string());app.ai_layers=cache;return true;},
        }
    }
    let Some(gallery)=cache.gallery.clone() else { app.ai_layers=cache;return true };
    let prompt=ui.add_sized(vec2(ui.available_width(),62.0),egui::TextEdit::multiline(&mut cache.text).hint_text("Prompt goes here…").id_salt(("ai-layer-prompt",doc,layer.id)));
    let generate=ui.add_enabled(gallery.variants.len()<32 && layer.mask.is_some() && !layer.locks.all && !layer.locks.pixels,egui::Button::new("Generate"));
    let can_select=app.session.is_enabled("genstudio.ai.select") && !layer.locks.all && !layer.locks.pixels;
    let mut selection=None;
    let mut entries=Vec::new();
    if !gallery.variants.is_empty() {
        ui.add_space(8.0);ui.separator();ui.small("Variants");
        ui.horizontal_wrapped(|ui| {
            for variant in &gallery.variants {
                if !cache.thumbnails.iter().any(|(slot,_)|*slot==variant.slot)
                    && let Some(bytes)=genstudio_ai::thumbnail(layer,variant.slot)
                {
                    let limits=photocraft_codecs::Limits {max_width:96,max_height:96,max_pixels:96*96,max_alloc:1<<20};
                    if let Ok(image)=photocraft_codecs::decode_as_with(photocraft_codecs::Format::Png,bytes,&photocraft_codecs::DecodeOptions {limits,..Default::default()}) {
                        let image=image.convert(photocraft_codecs::ChannelLayout::Rgba,photocraft_codecs::SampleType::U8);
                        let pixels=egui::ColorImage::from_rgba_unmultiplied([image.width() as usize,image.height() as usize],image.data());
                        let texture=ui.ctx().load_texture(format!("ai-variant-{}-{}-{}",doc.0,layer.id.0,variant.slot),pixels,TextureOptions::LINEAR);
                        cache.thumbnails.push((variant.slot,texture));
                    }
                }
                let button=if let Some((_,texture))=cache.thumbnails.iter().find(|(slot,_)|*slot==variant.slot) {
                    let size=texture.size_vec2();let scale=(72.0/size.x).min(56.0/size.y);
                    egui::Button::image(egui::Image::new((texture.id(),size*scale)))
                } else { egui::Button::new(format!("Variant {}",u32::from(variant.slot)+1)) };
                let response=ui.add_enabled(can_select,button.min_size(vec2(78.0,62.0)).selected(gallery.active==Some(variant.slot)))
                    .on_hover_text(if variant.prompt.is_empty() {"Restore the background"} else {&variant.prompt});
                entries.push(json!({"id":variant.slot,"rect":rect(response.rect),"prompt":variant.prompt,"selected":gallery.active==Some(variant.slot)}));
                if response.clicked() {selection=Some(variant.slot);}
            }
        });
    }
    cache.properties_info=json!({"layer":layer.id.0,"prompt":cache.text,"promptRect":rect(prompt.rect),"generateRect":rect(generate.rect),"variants":entries});
    let text=cache.text.clone();
    app.ai_layers=cache;
    if generate.clicked() && let Err(error)=app.run("genstudio.ai.generate",json!({"layer":layer.id.0,"prompt":text})) { failure(app,error); }
    if let Some(slot)=selection {
        match app.run("genstudio.ai.select",json!({"layer":layer.id.0,"variant":slot})) {
            Ok(_)=>{
                if let Some(value)=gallery.variants.iter().find(|v|v.slot==slot) {
                    app.ai_layers.text=value.prompt.clone();
                    app.ui.gen_studio.selection_prompt=value.prompt.clone();
                }
            },
            Err(error)=>failure(app,error),
        }
    }
    true
}

fn reusable(app:&mut PhotocraftApp)->Option<LayerId> {
    let st=app.session.active()?;
    if let Some((prior,active,result))=&app.ai_layers.reuse
        && *active==st.active_layer && prior.upgrade().is_some_and(|doc|Arc::ptr_eq(&doc,&st.doc)) { return *result; }
    let doc=st.doc.clone();let active=st.active_layer;
    let result=reusable_inner(app);
    app.ai_layers.reuse=Some((Arc::downgrade(&doc),active,result));
    app.ai_layers.bar_model=result.and_then(|id|doc.layer(id)).and_then(|layer|genstudio_ai::gallery(layer).ok());
    result
}
fn reusable_inner(app:&PhotocraftApp)->Option<LayerId> {
    let st=app.session.active()?;
    let layer=st.doc.layer(st.active_layer?)?;
    if !genstudio_ai::is_layer(layer) { return None; }
    if st.doc.selection.as_ref().is_none_or(|selection|layer.mask.as_ref().is_some_and(|mask|mask.surface==*selection)) {Some(layer.id)} else {None}
}
pub fn show_bar(app:&mut PhotocraftApp,ui:&mut egui::Ui,xf:&ViewXform,index:usize) {
    app.ai_layers.bar_info=Value::Null;
    if app.ui.gen_studio.open || app.ui.gen_studio.hide_ai_bar || app.session.active_index()!=Some(index) {return;}
    let existing=reusable(app);
    let Some(st)=app.session.active() else {return;};
    let doc=st.doc.id;
    let bounds=genstudio_ai::selected_bounds(&app.session);
    let (bounds,model)=if bounds.is_empty() {
        let Some(layer)=existing.and_then(|id|st.doc.layer(id)) else {return;};
        let Some(mask)=&layer.mask else {return;};
        let bounds=if mask.surface.default_pixel().first().copied().unwrap_or(0.0)>0.0 {st.doc.bounds()} else {mask.surface.tile_bounds().intersect(&st.doc.bounds())};
        (bounds,app.ai_layers.bar_model.clone())
    } else {(bounds,app.ai_layers.bar_model.clone())};
    if bounds.is_empty() {return;}
    if let (Some(id),Some(gallery))=(existing,model) {
        let identity=(doc,id,gallery.active);
        if app.ai_layers.bar_target!=Some(identity) {
            let same_layer=app.ai_layers.bar_target.is_some_and(|(d,l,_)|d==doc && l==id);
            if !same_layer || gallery.variants.len()==app.ai_layers.bar_variant_count
                || app.ui.gen_studio.selection_prompt==app.ai_layers.bar_saved_prompt
                || app.ui.gen_studio.selection_prompt==gallery.prompt {
                app.ui.gen_studio.selection_prompt=gallery.prompt.clone();
            }
            app.ai_layers.bar_target=Some(identity);
            app.ai_layers.bar_saved_prompt=gallery.prompt;
            app.ai_layers.bar_variant_count=gallery.variants.len();
        }
    } else {app.ai_layers.bar_target=None;}
    let screen=xf.doc_rect(bounds);
    let width=(xf.rect.width()-24.0).clamp(180.0,420.0);
    let x=(screen.center().x-width/2.0).max(xf.rect.left()+8.0).min(xf.rect.right()-width-8.0);
    let y=(screen.bottom()+44.0).min(xf.rect.bottom()-48.0).max(xf.rect.top()+8.0);
    let enabled=existing.is_some() || app.session.job_on(doc).is_none();
    let mut info=Value::Null;
    let mut generate=false;
    egui::Area::new(egui::Id::new("genstudio-ai-prompt-bar")).order(egui::Order::Foreground).default_pos(pos2(x,y)).constrain_to(xf.rect).movable(true).show(ui.ctx(),|ui| {
        let t=Tokens::get(ui.ctx());
        let frame=egui::Frame::new().fill(t.card).stroke(egui::Stroke::new(1.0,t.card_border)).corner_radius(t.radius_lg).inner_margin(8.0);
        let response=frame.show(ui,|ui| {ui.horizontal(|ui| {
            let prompt=ui.add(egui::TextEdit::singleline(&mut app.ui.gen_studio.selection_prompt).desired_width(width-110.0).hint_text("Prompt goes here…").id_salt("ai-selection-prompt"));
            let button=ui.add_enabled(enabled,egui::Button::new("Generate"));
            generate=button.clicked();
            info=json!({"prompt":app.ui.gen_studio.selection_prompt,"promptRect":rect(prompt.rect),"generateRect":rect(button.rect),"enabled":enabled});
        });});
        if let Some(info)=info.as_object_mut() {info.insert("rect".into(),rect(response.response.rect));}
    });
    app.ai_layers.bar_info=info;
    if generate {
        let text=app.ui.gen_studio.selection_prompt.clone();
        let layer=if let Some(id)=existing {Ok(id.0)} else {
            app.run("edit.aiFill",json!({"prompt":text})).and_then(|value|value["layer"].as_u64().ok_or_else(||"Ai Fill layer was not created".into()))
        };
        match layer.and_then(|layer|app.run("genstudio.ai.generate",json!({"layer":layer,"prompt":text}))) {
            Ok(_)=>{app.ui.mask_target=false;app.ui.properties_layer=app.session.active().and_then(|st|st.active_layer).map(|id|id.0);app.ui.panels.properties=true;},
            Err(error)=>failure(app,error),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use egui::pos2;

    fn app()->PhotocraftApp {
        let mut app=PhotocraftApp::new(photocraft_engine::Session::new(),crate::Services::default());
        app.run("file.new",json!({"width":384,"height":256})).unwrap();
        app
    }
    fn selected(app:&mut PhotocraftApp) {app.run("select.rect",json!({"x":120,"y":80,"width":40,"height":32})).unwrap();}
    fn click(h:&mut egui_kittest::Harness<PhotocraftApp>,rect:&Value) {
        let r=rect.as_array().unwrap();let at=pos2((r[0].as_f64().unwrap()+r[2].as_f64().unwrap()/2.0) as f32,(r[1].as_f64().unwrap()+r[3].as_f64().unwrap()/2.0) as f32);
        h.hover_at(at);h.run_steps(2);h.drag_at(at);h.run_steps(2);h.drop_at(at);h.run_steps(3);
    }
    #[test]
    fn selection_ai_menu_is_disabled_without_a_selection_and_precedes_fill() {
        let mut app=app();let ctx=egui::Context::default();
        assert!(!crate::menus::is_enabled(&app,"edit.aiFill"));
        selected(&mut app);
        assert!(crate::menus::is_enabled(&app,"edit.aiFill"));
        let items=crate::menus::menu_items(&app);
        assert!(items.iter().position(|v|v.id=="edit.aiFill").unwrap()<items.iter().position(|v|v.id=="edit.fill").unwrap());
        crate::menus::invoke(&mut app,&ctx,"edit.aiFill",json!({})).unwrap();
        assert!(app.ui.panels.properties && !app.ui.mask_target);
        assert!(genstudio_ai::is_layer(app.session.active().and_then(|st|st.doc.layer(st.active_layer?)).unwrap()));
        assert!(!photocraft_engine::genstudio_queue::has_pending(&app.session),"menu creates the layer without generating");
    }
    #[test]
    fn selection_ai_bar_is_default_visible_can_be_hidden_and_queues_the_typed_prompt() {
        let mut app=app();selected(&mut app);app.ui.gen_studio.selection_prompt="A red bicycle".into();
        let mut h=egui_kittest::Harness::builder().with_size(vec2(800.0,600.0)).build_ui_state(|ui,app:&mut PhotocraftApp| {
            if ui.ctx().fonts(|f|f.families().contains(&egui::FontFamily::Name("medium".into()))) {
                let xf=ViewXform {rect:ui.max_rect(),zoom:1.0,center:[192.0,128.0],flip:false,rotation:0.0,aspect:1.0};
                show_bar(app,ui,&xf,0);
            }
        },app);
        PhotocraftApp::setup_context(&h.ctx,crate::theme::ThemeKind::Studio);h.run_steps(5);
        assert_eq!(h.state().ai_layers.bar_info["prompt"],"A red bicycle");
        h.state_mut().ui.gen_studio.open=true;h.run_steps(3);
        assert!(h.state().ai_layers.bar_info.is_null(),"the floating selection bar must not cover Gen Studio");
        assert!(!h.state().ui.gen_studio.hide_ai_bar,"opening Studio does not change the display preference");
        h.state_mut().ui.gen_studio.open=false;h.run_steps(3);
        assert_eq!(h.state().ai_layers.bar_info["prompt"],"A red bicycle");
        let ctx=h.ctx.clone();
        crate::menus::invoke(h.state_mut(),&ctx,"genstudio.ai.bar.set",json!({"enabled":false})).unwrap();h.run_steps(3);
        assert!(h.state().ai_layers.bar_info.is_null());
        assert!(serde_json::to_value(&h.state().ui.gen_studio).unwrap()["hide_ai_bar"].as_bool().unwrap());
        crate::menus::invoke(h.state_mut(),&ctx,"genstudio.ai.bar.set",json!({"enabled":true})).unwrap();h.run_steps(3);
        let button=h.state().ai_layers.bar_info["generateRect"].clone();click(&mut h,&button);
        assert_eq!(photocraft_engine::genstudio_queue::views(&h.state().session).len(),1);
        let st=h.state().session.active().unwrap();
        assert_eq!(genstudio_ai::gallery(st.doc.layer(st.active_layer.unwrap()).unwrap()).unwrap().prompt,"A red bicycle");
    }
    #[test]
    fn selection_ai_gallery_click_restores_the_prompt_and_undo_restores_the_variant() {
        use photocraft_codecs::{Image,ChannelLayout,Format,EncodeOptions};
        let mut app=app();selected(&mut app);
        let id=LayerId(app.run("edit.aiFill",json!({})).unwrap()["layer"].as_u64().unwrap());
        let png=|w,h,color:[u8;4]|Arc::new(photocraft_codecs::encode(&Image::from_u8(w,h,ChannelLayout::Rgba,(0..w*h).flat_map(|_|color).collect()).unwrap(),Format::Png,&EncodeOptions::default()).unwrap());
        let value=Gallery {version:1,prompt:"Green boat".into(),active:Some(1),variants:vec![
            genstudio_ai::Variant {slot:0,prompt:"Red boat".into(),rect:[0,0,384,256]},
            genstudio_ai::Variant {slot:1,prompt:"Green boat".into(),rect:[0,0,384,256]}]};
        app.session.edit("Synthetic gallery",|doc,_| {
            let layer=doc.layer_mut(id).unwrap();
            layer.psd_blocks.retain(|(k,_)|*k!=*b"gsAI");
            layer.psd_blocks.extend([
                (*b"gsAI",Arc::new(serde_json::to_vec(&value).unwrap())),
                (*b"gV00",png(384,256,[230,30,40,255])),(*b"gV01",png(384,256,[20,200,40,255])),
                (*b"gT00",png(32,24,[230,30,40,255])),(*b"gT01",png(32,24,[20,200,40,255]))]);
            Ok(())
        }).unwrap();
        let mut h=egui_kittest::Harness::builder().with_size(vec2(320.0,500.0)).build_ui_state(|ui,app:&mut PhotocraftApp| {
            if ui.ctx().fonts(|f|f.families().contains(&egui::FontFamily::Name("medium".into()))) {
                let st=app.session.active().unwrap();let doc=st.doc.clone();let id=st.active_layer.unwrap();
                properties(app,ui,doc.layer(id).unwrap());
            }
        },app);
        PhotocraftApp::setup_context(&h.ctx,crate::theme::ThemeKind::Studio);h.run_steps(5);
        let first=h.state().ai_layers.properties_info["variants"][0]["rect"].clone();click(&mut h,&first);
        assert_eq!(h.state().ai_layers.properties_info["prompt"],"Red boat");
        assert_eq!(genstudio_ai::gallery(h.state().session.active().unwrap().doc.layer(id).unwrap()).unwrap().active,Some(0));
        h.state_mut().run("edit.undo",json!({})).unwrap();h.run_steps(3);
        assert_eq!(h.state().ai_layers.properties_info["prompt"],"Green boat");
        h.state_mut().ai_layers.text="Unfinished draft".into();
        h.state_mut().session.edit("Synthetic completion",|doc,_| {
            let layer=doc.layer_mut(id).unwrap();let mut value=genstudio_ai::gallery(layer).unwrap();
            value.variants.push(genstudio_ai::Variant {slot:2,prompt:"Completed prompt".into(),rect:[0,0,384,256]});
            value.prompt="Completed prompt".into();value.active=Some(2);
            layer.psd_blocks.retain(|(k,_)|*k!=*b"gsAI");layer.psd_blocks.push((*b"gsAI",Arc::new(serde_json::to_vec(&value).unwrap())));Ok(())
        }).unwrap();h.run_steps(3);
        assert_eq!(h.state().ai_layers.text,"Unfinished draft","completion never overwrites a new prompt being typed");
    }
}
