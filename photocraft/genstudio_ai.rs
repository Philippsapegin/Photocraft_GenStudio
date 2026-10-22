//! Selection-based AI layers. Custom layer blocks preserve galleries in PCraft/PSD.
use crate::{EngineError, Result, Session, commands::CommandSpec, genstudio_queue::FillMask, jobs::JobCtx};
use photocraft_cms::{Builtin, Intent, Transform};
use photocraft_codecs::{ChannelLayout, DecodeOptions, EncodeOptions, Format, Image, Limits};
use photocraft_color::PixelFormat;
use photocraft_doc::{Document, Layer, LayerContent, LayerId, LayerMask};
use photocraft_geom::Rect;
use photocraft_raster::Surface;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::sync::Arc;

const META: [u8;4] = *b"gsAI";
const MAX_PIXELS: u64 = 16 << 20;
const MAX_GALLERY: usize = 128 << 20;
const MAX_VARIANTS: usize = 32;
pub const LABEL: &str = "Ai Fill";

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Variant { pub slot:u8, pub prompt:String, pub rect:[i32;4] }
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Gallery { pub version:u8, pub prompt:String, pub active:Option<u8>, pub variants:Vec<Variant> }
impl Default for Gallery {
    fn default()->Self { Self {version:1,prompt:String::new(),active:None,variants:Vec::new()} }
}
fn error(message:impl std::fmt::Display)->EngineError { EngineError::Other(format!("Ai Fill: {message}")) }
pub fn is_layer(layer:&Layer)->bool { layer.psd_blocks.iter().any(|(key,_)|*key==META) }
pub fn block(layer:&Layer)->Option<&Arc<Vec<u8>>> { layer.psd_blocks.iter().find(|(key,_)|*key==META).map(|(_,bytes)|bytes) }
pub fn gallery(layer:&Layer)->Result<Gallery> {
    let bytes=block(layer).ok_or_else(||error("select an Ai Fill layer"))?;
    if bytes.len()>2*1024*1024 { return Err(error("gallery metadata exceeds the size limit")); }
    // PSD additional-layer blocks may include their 4-byte alignment padding.
    let mut data=bytes.as_slice();
    for _ in 0..3 {if let Some(next)=data.strip_suffix(&[0]) {data=next;} else {break;}}
    let value:Gallery=serde_json::from_slice(data).map_err(error)?;
    if value.version!=1 || value.variants.len()>MAX_VARIANTS || value.prompt.len()>6000 {
        return Err(error("unsupported or invalid gallery"));
    }
    let mut slots=std::collections::BTreeSet::new();
    for v in &value.variants {
        let w=i64::from(v.rect[2])-i64::from(v.rect[0]);
        let h=i64::from(v.rect[3])-i64::from(v.rect[1]);
        if usize::from(v.slot)>=MAX_VARIANTS || v.prompt.len()>6000 || !slots.insert(v.slot)
            || v.rect[0]<0 || v.rect[1]<0 || v.rect[2]>1<<20 || v.rect[3]>1<<20 || w<=0 || h<=0 || w>i64::from(i32::MAX) || h>i64::from(i32::MAX)
            || w.saturating_mul(h)>MAX_PIXELS as i64 {
            return Err(error("invalid gallery variant"));
        }
    }
    if value.active.is_some_and(|slot|!slots.contains(&slot)) { return Err(error("invalid active variant")); }
    Ok(value)
}
fn key(kind:u8,slot:u8)->[u8;4] {
    let hex=b"0123456789abcdef";
    [b'g',kind,hex.get(usize::from(slot/16)).copied().unwrap_or(b'0'),hex.get(usize::from(slot%16)).copied().unwrap_or(b'0')]
}
pub fn thumbnail(layer:&Layer,slot:u8)->Option<&Arc<Vec<u8>>> {
    layer.psd_blocks.iter().find(|(k,_)|*k==key(b'T',slot)).map(|(_,v)|v)
}
fn save_gallery(layer:&mut Layer,value:&Gallery)->Result<()> {
    let bytes=Arc::new(serde_json::to_vec(value).map_err(error)?);
    layer.psd_blocks.retain(|(k,_)|*k!=META);
    layer.psd_blocks.push((META,bytes));
    Ok(())
}
pub(crate) struct SelectionCache {
    document:std::sync::Weak<Document>,
    canvas:Rect,
    mask:Option<Surface>,
    bounds:Rect,
}
/// A COW mask snapshot can be compared without reading every pixel. Tile
/// references change when a mask is edited, including after Undo/Redo.
pub fn shared_mask(a:&Surface,b:&Surface)->bool {
    a.format()==b.format() && a.default_pixel()==b.default_pixel() && a.tile_count()==b.tile_count()
        && a.tiles().zip(b.tiles()).all(|((ac,at),(bc,bt))|ac==bc && Arc::ptr_eq(at,bt))
}
fn shared_selection(a:Option<&Surface>,b:Option<&Surface>)->bool {
    match (a,b) {(Some(a),Some(b))=>shared_mask(a,b),(None,None)=>true,_=>false}
}
pub fn selected_bounds(s:&Session)->Rect {
    let Some(st)=s.active() else { return Rect::EMPTY };
    let mut cache=s.gen_studio.selection_cache.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
    if let Some(prior)=cache.as_mut() {
        if prior.document.upgrade().is_some_and(|doc|Arc::ptr_eq(&doc,&st.doc)) {return prior.bounds;}
        if prior.canvas==st.doc.bounds() && shared_selection(prior.mask.as_ref(),st.doc.selection.as_ref()) {
            prior.document=Arc::downgrade(&st.doc);
            return prior.bounds;
        }
    }
    let bounds=st.doc.selection.as_ref().map_or(Rect::EMPTY,|mask|mask_bounds(mask,st.doc.bounds()));
    *cache=Some(SelectionCache {document:Arc::downgrade(&st.doc),canvas:st.doc.bounds(),mask:st.doc.selection.clone(),bounds});
    bounds
}
fn mask_bounds(mask:&Surface,canvas:Rect)->Rect {
    if mask.default_pixel().first().copied().unwrap_or(0.0)>0.0 { canvas } else { mask.content_bounds().intersect(&canvas) }
}
fn enabled(s:&Session)->std::result::Result<(),String> {
    if selected_bounds(s).is_empty() { Err("Make a selection first".into()) } else { Ok(()) }
}
fn layer_id(s:&Session,p:&Value)->Result<LayerId> {
    match p.get("layer") {
        Some(value)=>value.as_u64().map(LayerId).ok_or_else(||error("layer must be an id")),
        None=>s.active().and_then(|st|st.active_layer).ok_or(EngineError::NoDocument),
    }
}
fn prompt(p:&Value)->Result<String> {
    let value=p.get("prompt").map_or(Ok(""),|v|v.as_str().ok_or_else(||error("prompt must be text")))?;
    if value.len()>6000 || value.contains('\0') { return Err(error("prompt must contain at most 6000 UTF-8 bytes")); }
    Ok(value.to_owned())
}
pub fn create(s:&mut Session,p:&Value)->Result<Value> {
    enabled(s).map_err(error)?;
    let text=prompt(p)?;
    let doc=s.active().ok_or(EngineError::NoDocument)?.doc.clone();
    let selection=doc.selection.as_ref().ok_or_else(||error("Make a selection first"))?.clone();
    // Validate the bounded context before creating a layer or changing history.
    let _=capture_mask(&selection,&doc)?;
    let id=s.edit(LABEL,move |doc,active| {
        let fmt=doc.pixel_format();
        let mut layer=Layer::raster(doc.next_layer_name(LABEL),PixelFormat::new(fmt.mode,fmt.sample,true));
        layer.mask=Some(LayerMask {surface:selection,..LayerMask::hide_all()});
        save_gallery(&mut layer,&Gallery {prompt:text,..Default::default()})?;
        let id=doc.insert_above(*active,layer);
        *active=Some(id);
        Ok(id)
    })?;
    Ok(json!({"layer":id.0}))
}
fn capture_mask(surface:&Surface,doc:&Document)->Result<Arc<FillMask>> {
    let bounds=mask_bounds(surface,doc.bounds());
    if bounds.is_empty() { return Err(error("the mask is empty")); }
    let margin=i32::try_from(bounds.width().max(bounds.height())).unwrap_or(1024).clamp(256,1024);
    let rect=crate::genstudio_edit::aligned_context(bounds,doc.bounds(),margin)?;
    if u64::from(rect.width())*u64::from(rect.height())>MAX_PIXELS { return Err(error("the selected area and context are too large; use a smaller selection")); }
    let channels=surface.channels().max(1);
    let coverage:Vec<f32>=surface.read_region(rect).chunks_exact(channels).map(|p|p.first().copied().unwrap_or(0.0).clamp(0.0,1.0)).collect();
    if !coverage.iter().any(|v|*v>0.0) { return Err(error("the mask is empty")); }
    Ok(Arc::new(FillMask {rect,bounds,coverage}))
}
pub(crate) struct Request {
    pub doc:photocraft_doc::DocId, pub mask:Arc<FillMask>, pub layer:LayerId,
    text:String, settings:crate::genstudio::Settings, size:(u32,u32),
}
pub(crate) fn capture(s:&Session,p:&Value)->Result<Request> {
    let st=s.active().ok_or(EngineError::NoDocument)?;
    let layer=layer_id(s,p)?;
    let target=st.doc.layer(layer).ok_or(EngineError::NoLayer(layer))?;
    let existing=gallery(target)?;
    if target.locks.all || target.locks.pixels { return Err(error("unlock the Ai Fill layer first")); }
    if existing.variants.len()>=MAX_VARIANTS { return Err(error("this layer already has 32 variants; create another Ai Fill layer")); }
    let text=prompt(p)?;
    let mask=capture_mask(&target.mask.as_ref().ok_or_else(||error("the Ai Fill layer has no mask"))?.surface,&st.doc)?;
    let settings=s.prefs().gen_studio.clone();
    settings.validate().map_err(error)?;
    Ok(Request {doc:st.doc.id,mask,layer,text,settings,size:(st.doc.size.width,st.doc.size.height)})
}
fn input(doc:&Document,mask:&FillMask)->Result<(Image,Image)> {
    let rect=mask.rect;
    let pixels:Vec<f32>=photocraft_compose::render(doc,rect).px.into_iter().flatten().collect();
    let profile=crate::color_cmds::composite_profile(doc);
    let transform=Transform::new(&profile,Builtin::Srgb.profile(),Intent::RelativeColorimetric,true).map_err(error)?;
    if transform.inputs()!=3 { return Err(error("the composite color profile is invalid")); }
    let mut rgba=vec![0.0;pixels.len()];
    transform.convert_f32(&pixels,4,&mut rgba,4,true);
    let image=Image::from_normalized(rect.width(),rect.height(),ChannelLayout::Rgba,photocraft_codecs::SampleType::U8,&rgba).map_err(error)?;
    let coverage=Image::from_u8(rect.width(),rect.height(),ChannelLayout::Gray,mask.coverage.iter().map(|p|(p*255.0).round() as u8).collect()).map_err(error)?;
    Ok((image,coverage))
}
pub(crate) fn native(doc:&Document,image:&Image,rect:Rect)->Result<Surface> {
    if image.dimensions()!=(rect.width(),rect.height()) { return Err(error("the engine returned the wrong rectangle dimensions")); }
    let rgba=crate::genstudio_edit::rgba(image);
    let values:Vec<f32>=rgba.data().iter().map(|p|f32::from(*p)/255.0).collect();
    let fmt=doc.pixel_format();
    let fmt=PixelFormat::new(fmt.mode,fmt.sample,true);
    let transform=Transform::new(Builtin::Srgb.profile(),&crate::color_cmds::document_profile(doc),Intent::RelativeColorimetric,true).map_err(error)?;
    if transform.outputs()!=fmt.mode.color_channels() { return Err(error("the document profile does not match its color channels")); }
    let mut pixels=vec![0.0;image.pixel_count()*fmt.channels()];
    transform.convert_f32(&values,4,&mut pixels,fmt.channels(),true);
    let mut surface=Surface::new(fmt);
    surface.write_region(rect,&pixels);
    Ok(surface)
}
fn preview(image:&Image)->Result<Vec<u8>> {
    let rgba=crate::genstudio_edit::rgba(image);
    let scale=(rgba.width().max(rgba.height()) as f32/96.0).max(1.0);
    let (w,h)=((rgba.width() as f32/scale).ceil() as u32,(rgba.height() as f32/scale).ceil() as u32);
    let mut bytes=Vec::with_capacity((w*h*4) as usize);
    for y in 0..h { for x in 0..w {
        let sx=((x as f32*scale) as u32).min(rgba.width().saturating_sub(1));
        let sy=((y as f32*scale) as u32).min(rgba.height().saturating_sub(1));
        let offset=(sy as usize*rgba.width() as usize+sx as usize)*4;
        bytes.extend_from_slice(rgba.data().get(offset..offset+4).ok_or_else(||error("invalid preview pixels"))?);
    }}
    let thumb=Image::from_u8(w,h,ChannelLayout::Rgba,bytes).map_err(error)?;
    photocraft_codecs::encode(&thumb,Format::Png,&EncodeOptions::default()).map_err(error)
}
pub(crate) fn run(s:&mut Session,request:Request)->Result<Value> {
    let text=request.text.clone();
    run_with(s,request,move |settings,image,mask,ctx|crate::genstudio_edit::generate_prompt(settings,image,mask,ctx,&text))
}
pub(crate) fn run_with(s:&mut Session,request:Request,exchange:impl FnOnce(&crate::genstudio::Settings,&Image,&Image,&JobCtx)->Result<(Image,Image)>+Send+'static)->Result<Value> {
    let doc=s.active().ok_or(EngineError::NoDocument)?.doc.clone();
    if doc.id!=request.doc || (doc.size.width,doc.size.height)!=request.size { return Err(error("the canvas dimensions changed; generate again")); }
    let target=doc.layer(request.layer).ok_or(EngineError::NoLayer(request.layer))?;
    if target.locks.all || target.locks.pixels { return Err(error("unlock the Ai Fill layer first")); }
    if gallery(target)?.variants.len()>=MAX_VARIANTS { return Err(error("the gallery is full")); }
    let Request {layer,mask,text,settings,..}=request;
    let rect=mask.rect;
    let source=doc.clone();
    crate::jobs::run(s,LABEL,true,move |ctx| {
        ctx.check()?;
        ctx.progress(0.1,"Preparing the selected area…");
        let (image,hole)=input(&source,&mask)?;
        let (image,_)=exchange(&settings,&image,&hole,ctx)?;
        ctx.check()?;
        let surface=native(&source,&image,rect)?;
        let thumb=Arc::new(preview(&image)?);
        let png=Arc::new(photocraft_codecs::encode(&image,Format::Png,&EncodeOptions::default()).map_err(error)?);
        if png.len()>MAX_GALLERY { return Err(error("the generated variant exceeds the gallery size limit")); }
        ctx.progress(0.95,"Adding the generated variant…");
        Ok((surface,png,thumb))
    },move |s,(surface,png,thumb)| {
        let slot=s.edit(LABEL,move |doc,_| {
            let target=doc.layer_mut(layer).ok_or(EngineError::NoLayer(layer))?;
            let mut value=gallery(target)?;
            if value.variants.len()>=MAX_VARIANTS { return Err(error("the gallery is full")); }
            let bytes=target.psd_blocks.iter().filter(|(k,_)|k.get(..2)==Some(&b"gV"[..])).map(|(_,b)|b.len()).sum::<usize>();
            if bytes.saturating_add(png.len())>MAX_GALLERY { return Err(error("the gallery is full; create another Ai Fill layer")); }
            let slot=u8::try_from(value.variants.len()).map_err(error)?;
            target.content=LayerContent::Raster(surface);
            target.psd_blocks.push((key(b'V',slot),png));
            target.psd_blocks.push((key(b'T',slot),thumb));
            value.variants.push(Variant {slot,prompt:text.clone(),rect:[rect.x0,rect.y0,rect.x1,rect.y1]});
            value.prompt=text;value.active=Some(slot);
            save_gallery(target,&value)?;
            Ok(slot)
        })?;
        if let Some(st)=s.active_mut() { st.last_damage=Some(rect); }
        Ok(json!({"layer":layer.0,"variant":slot,"damage":[rect.x0,rect.y0,rect.width(),rect.height()]}))
    })
}
fn select(s:&mut Session,p:&Value)->Result<Value> {
    let id=layer_id(s,p)?;
    let slot=p.get("variant").and_then(Value::as_u64).and_then(|n|u8::try_from(n).ok()).ok_or_else(||error("variant must be an id"))?;
    let doc=s.active().ok_or(EngineError::NoDocument)?.doc.clone();
    let layer=doc.layer(id).ok_or(EngineError::NoLayer(id))?;
    if layer.locks.all || layer.locks.pixels { return Err(error("unlock the Ai Fill layer first")); }
    let mut value=gallery(layer)?;
    let variant=value.variants.iter().find(|v|v.slot==slot).ok_or_else(||error("variant not found"))?;
    let rect=Rect::new(variant.rect[0],variant.rect[1],variant.rect[2],variant.rect[3]);
    let png=layer.psd_blocks.iter().find(|(k,_)|*k==key(b'V',slot)).map(|(_,b)|b).ok_or_else(||error("variant pixels are missing"))?;
    if png.len()>MAX_GALLERY { return Err(error("variant exceeds the size limit")); }
    let limits=Limits {max_width:rect.width(),max_height:rect.height(),max_pixels:MAX_PIXELS,max_alloc:MAX_PIXELS*4};
    let image=photocraft_codecs::decode_as_with(Format::Png,png,&DecodeOptions {limits,..Default::default()}).map_err(error)?;
    let surface=native(&doc,&image,rect)?;
    value.prompt=variant.prompt.clone();value.active=Some(slot);
    s.edit("Select Ai Fill Variant",move |doc,_| {
        let layer=doc.layer_mut(id).ok_or(EngineError::NoLayer(id))?;
        layer.content=LayerContent::Raster(surface);
        save_gallery(layer,&value)
    })?;
    if let Some(st)=s.active_mut() { st.last_damage=Some(doc.bounds()); }
    Ok(json!({"layer":id.0,"variant":slot}))
}
fn inspect(s:&mut Session,p:&Value)->Result<Value> {
    let id=layer_id(s,p)?;
    let layer=s.active().and_then(|st|st.doc.layer(id)).ok_or(EngineError::NoLayer(id))?;
    Ok(json!({"layer":id.0,"gallery":gallery(layer)?}))
}
pub fn specs()->Vec<CommandSpec> {
    vec![
        CommandSpec {id:"edit.aiFill",label:LABEL,menu:&["Edit"],shortcut:None,params:r#"{"prompt"?:string}"#,enabled,run:create,journal:true},
        CommandSpec {id:"genstudio.ai.generate",label:"Generate AI Fill",menu:&[],shortcut:None,params:r#"{"layer"?:id,"prompt":string}"#,enabled:crate::commands::always,run:|s,p|crate::genstudio_queue::enqueue(s,&json!({"command":"genstudio.ai.generate","params":p})),journal:false},
        CommandSpec {id:"genstudio.ai.select",label:"Select AI Fill Variant",menu:&[],shortcut:None,params:r#"{"layer"?:id,"variant":u8}"#,enabled:crate::commands::always,run:select,journal:true},
        CommandSpec {id:"genstudio.ai.inspect",label:"AI Fill Variants",menu:&[],shortcut:None,params:r#"{"layer"?:id}"#,enabled:crate::commands::always,run:inspect,journal:false},
    ]
}

#[cfg(test)]
mod tests {
    use super::*;
    use photocraft_color::{ColorMode,SampleType};

    fn scene(sample:SampleType)->Session {
        let mut s=Session::new();
        s.execute("file.new",json!({"width":384,"height":256})).unwrap();
        s.edit("Synthetic background",|doc,active| {
            doc.depth=sample;
            let bounds=doc.bounds();
            let layer=doc.layer_mut(active.ok_or(EngineError::NoDocument)?).ok_or(EngineError::NoDocument)?;
            let mut surface=Surface::new(PixelFormat::new(ColorMode::Rgb,sample,true));
            surface.write_region(bounds,&(0..bounds.width()*bounds.height()).flat_map(|_|[0.1,0.2,0.8,1.0]).collect::<Vec<_>>());
            layer.content=LayerContent::Raster(surface);
            Ok(())
        }).unwrap();
        s
    }
    fn selection(s:&mut Session) {s.execute("select.rect",json!({"x":120,"y":80,"width":40,"height":32})).unwrap();}
    fn fill(s:&mut Session,id:LayerId,text:&str,color:[u8;4])->Result<Value> {
        let request=capture(s,&json!({"layer":id.0,"prompt":text}))?;
        run_with(s,request,move |_,image,mask,_|Ok((Image::from_u8(image.width(),image.height(),ChannelLayout::Rgba,(0..image.pixel_count()).flat_map(|_|color).collect()).map_err(error)?,mask.clone())))
    }
    fn pixel(s:&Session,id:LayerId)->[f32;4] {s.active().unwrap().doc.layer(id).unwrap().surface().unwrap().rgba(130,90)}

    #[test]
    fn selection_ai_layer_requires_a_selection_and_creates_only_a_masked_empty_layer() {
        let mut s=scene(SampleType::U8);
        assert!(!s.is_enabled("edit.aiFill"));
        assert!(s.execute("edit.aiFill",json!({})).is_err());
        selection(&mut s);
        s.edit("Soft selection",|doc,_|{doc.selection.as_mut().unwrap().write_region(Rect::new(120,80,121,81),&[0.25]);Ok(())}).unwrap();
        assert!(s.is_enabled("edit.aiFill"));
        let original=s.active().unwrap().doc.clone();
        assert!(s.execute("edit.aiFill",json!({"prompt":true})).is_err());
        assert!(Arc::ptr_eq(&original,&s.active().unwrap().doc));
        let id=LayerId(s.execute("edit.aiFill",json!({"prompt":"A small red boat"})).unwrap()["layer"].as_u64().unwrap());
        let doc=&s.active().unwrap().doc;
        let layer=doc.layer(id).unwrap();
        assert_eq!(layer.mask.as_ref().unwrap().surface,*original.selection.as_ref().unwrap());
        assert_eq!(pixel(&s,id)[3],0.0);
        assert!(gallery(layer).unwrap().variants.is_empty());
        assert_eq!(gallery(layer).unwrap().prompt,"A small red boat");
        assert_eq!(doc.selection,original.selection);
        s.execute("edit.undo",json!({})).unwrap();
        assert_eq!(*s.active().unwrap().doc,*original);
    }

    #[test]
    fn selection_ai_variants_switch_pixels_and_prompts_at_all_depths_with_undo() {
        for sample in SampleType::ALL {
            let mut s=scene(sample);selection(&mut s);
            let background=s.active().unwrap().doc.clone();
            let id=LayerId(create(&mut s,&json!({})).unwrap()["layer"].as_u64().unwrap());
            fill(&mut s,id,"Red boat",[230,30,40,255]).unwrap();
            fill(&mut s,id,"Green boat",[20,200,40,255]).unwrap();
            assert_eq!(s.active().unwrap().doc.layer(id).unwrap().surface().unwrap().format().sample,sample);
            assert!(pixel(&s,id)[1]>0.7);
            assert_eq!(gallery(s.active().unwrap().doc.layer(id).unwrap()).unwrap().variants.len(),2);
            s.execute("genstudio.ai.select",json!({"layer":id.0,"variant":0})).unwrap();
            assert!(pixel(&s,id)[0]>0.8);
            let value=gallery(s.active().unwrap().doc.layer(id).unwrap()).unwrap();
            assert_eq!(value.prompt,"Red boat");assert_eq!(value.active,Some(0));
            let composed=photocraft_compose::render(&s.active().unwrap().doc,Rect::new(0,0,1,1));
            let original=photocraft_compose::render(&background,Rect::new(0,0,1,1));
            assert_eq!(composed.px,original.px,"the layer mask preserves all unselected content");
            s.execute("edit.undo",json!({})).unwrap();
            assert!(pixel(&s,id)[1]>0.7);
            assert_eq!(gallery(s.active().unwrap().doc.layer(id).unwrap()).unwrap().prompt,"Green boat");
        }
    }

    #[test]
    fn selection_ai_gallery_survives_native_and_psd_roundtrips() {
        let mut s=scene(SampleType::U16);selection(&mut s);
        let id=LayerId(create(&mut s,&json!({})).unwrap()["layer"].as_u64().unwrap());
        fill(&mut s,id,"A red boat",[230,30,40,255]).unwrap();
        fill(&mut s,id,"A green boat",[20,200,40,255]).unwrap();
        let doc=&s.active().unwrap().doc;
        let bytes=photocraft_format::save_to_bytes(doc,&Default::default()).unwrap();
        let native=photocraft_format::load_from_bytes(&bytes).unwrap();
        assert_eq!(&native,doc.as_ref());
        let psd=photocraft_io::export(doc,"psd",&Default::default()).unwrap();
        let loaded=photocraft_io::import("gallery.psd",&psd.bytes).unwrap().document;
        let layer=loaded.layers.iter().find(|l|is_layer(l)).unwrap();
        let value=gallery(layer).unwrap();
        assert_eq!(value.variants.len(),2);assert_eq!(value.prompt,"A green boat");
        for v in &value.variants {assert!(thumbnail(layer,v.slot).is_some());assert!(layer.psd_blocks.iter().any(|(k,_)|*k==key(b'V',v.slot)));}
        s.active_mut().unwrap().doc=Arc::new(native);
        s.execute("genstudio.ai.select",json!({"layer":id.0,"variant":0})).unwrap();
        assert!(pixel(&s,id)[0]>0.8);
    }

    #[test]
    fn selection_ai_cancelled_invalid_and_failed_results_leave_the_layer_unchanged() {
        let mut s=scene(SampleType::U8);selection(&mut s);
        let id=LayerId(create(&mut s,&json!({})).unwrap()["layer"].as_u64().unwrap());
        let original=s.active().unwrap().doc.clone();
        let request=capture(&s,&json!({"layer":id.0,"prompt":"x"})).unwrap();
        assert!(run_with(&mut s,request,|_,_,_,ctx|{ctx.cancel();Err(EngineError::Cancelled)}).is_err());
        assert!(Arc::ptr_eq(&original,&s.active().unwrap().doc));
        let request=capture(&s,&json!({"layer":id.0,"prompt":"x"})).unwrap();
        assert!(run_with(&mut s,request,|_,_,_,_|{let tiny=Image::from_u8(1,1,ChannelLayout::Rgba,vec![255;4]).map_err(error)?;Ok((tiny.clone(),tiny))}).is_err());
        for params in [json!({"layer":id.0,"variant":9000}),json!({"layer":id.0,"variant":0})] {assert!(select(&mut s,&params).is_err());}
        assert!(capture(&s,&json!({"layer":id.0,"prompt":"x".repeat(6001)})).is_err());
        assert!(Arc::ptr_eq(&original,&s.active().unwrap().doc));
        let mut hostile=original.layer(id).unwrap().clone();
        let mut value=Gallery::default();
        value.variants.push(Variant {slot:0,prompt:String::new(),rect:[i32::MIN,0,i32::MAX,1]});
        save_gallery(&mut hostile,&value).unwrap();
        assert!(gallery(&hostile).is_err());
    }

    #[test]
    fn selection_ai_context_is_larger_and_large_masks_are_bounded_before_allocation() {
        let mut s=Session::new();s.execute("file.new",json!({"width":6000,"height":4000})).unwrap();
        s.execute("select.rect",json!({"x":3000,"y":2000,"width":20,"height":30})).unwrap();
        let id=LayerId(create(&mut s,&json!({})).unwrap()["layer"].as_u64().unwrap());
        let request=capture(&s,&json!({"layer":id.0})).unwrap();
        assert!(request.mask.rect.width()>=532 && request.mask.rect.height()>=542);
        s.execute("select.all",json!({})).unwrap();
        let original=s.active().unwrap().doc.clone();
        assert!(create(&mut s,&json!({})).is_err());
        assert!(Arc::ptr_eq(&original,&s.active().unwrap().doc));
    }
    #[test]
    fn selection_cache_survives_pixel_edits_and_tracks_mask_edits_and_undo() {
        let mut s=scene(SampleType::U8);selection(&mut s);
        let original=selected_bounds(&s);
        let snapshot=s.gen_studio.selection_cache.lock().unwrap().as_ref().unwrap().mask.clone().unwrap();
        s.edit("Pixels only",|doc,active| {crate::pixels_mut(doc.layer_mut(active.unwrap()).unwrap())?.write_region(Rect::new(1,1,2,2),&[1.0,0.0,0.0,1.0]);Ok(())}).unwrap();
        assert_eq!(selected_bounds(&s),original);
        assert!(shared_mask(&snapshot,s.active().unwrap().doc.selection.as_ref().unwrap()));
        s.execute("select.rect",json!({"x":2,"y":3,"width":11,"height":13})).unwrap();
        assert_eq!(selected_bounds(&s),Rect::new(2,3,13,16));
        s.execute("edit.undo",json!({})).unwrap();
        assert_eq!(selected_bounds(&s),original);
        s.execute("select.deselect",json!({})).unwrap();
        assert!(selected_bounds(&s).is_empty());
    }
}
