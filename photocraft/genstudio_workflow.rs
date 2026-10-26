//! Studio workflow snapshots and bounded result files; no model code in the editor.
use crate::{EngineError, Result, genstudio::Settings, jobs::JobCtx};
use photocraft_cms::{Builtin, Intent, Transform};
use photocraft_codecs::{ChannelLayout, DecodeOptions, Image, Limits, SampleType};
use photocraft_doc::{Document, LayerId};
use serde_json::{Value, json};

fn error(e: impl std::fmt::Display) -> EngineError { EngineError::Other(format!("GenGate workflow: {e}")) }

#[derive(Clone, Debug, serde::Serialize, serde::Deserialize)]
pub struct Recipe { pub path: String, pub sha256: String }
impl Recipe {
    pub fn validate(&self) -> Result<()> {
        if self.path.len() > 4096 || self.path.contains('\0') || self.sha256.len() != 64 || !self.sha256.bytes().all(|v| v.is_ascii_hexdigit()) {
            return Err(error("Invalid workflow recipe"));
        }
        Ok(())
    }
}

#[derive(serde::Deserialize)]
#[serde(rename_all="camelCase")]
pub struct ImageRecord {
    pub width: u32,
    pub height: u32,
    pub prompt: String,
    pub name: String,
    pub seed: u64,
    pub loras: Vec<crate::genstudio_lora::Selection>,
    pub has_mask: bool,
}
#[derive(serde::Deserialize)]
pub struct ResultRecords { pub count: usize, pub images: Vec<ImageRecord>, pub workflow: Recipe }
pub struct OutputImage { pub image: Image, pub mask: Option<Image>, pub record: ImageRecord }
pub struct Output { pub images: Vec<OutputImage>, pub workflow: Recipe }

#[cfg(not(target_arch="wasm32"))]
fn bytes(path: &std::path::Path, limit: u64) -> Result<Vec<u8>> {
    use std::io::Read;
    let mut value=Vec::new();
    std::fs::File::open(path).map_err(error)?.take(limit+1).read_to_end(&mut value).map_err(error)?;
    if value.len() as u64 > limit { return Err(error("Workflow file exceeds its size limit")); }
    Ok(value)
}

fn render_reference(doc: &Document, ctx: &JobCtx) -> Result<String> {
    let (w,h)=(doc.size.width,doc.size.height);
    if w==0 || h==0 || w.max(h)>32768 || u64::from(w)*u64::from(h)>32<<20 { return Err(error("Workflow sources support up to 32 million pixels")); }
    let transform=Transform::new(&crate::color_cmds::composite_profile(doc),Builtin::Srgb.profile(),Intent::RelativeColorimetric,true).map_err(error)?;
    let mut data=Vec::with_capacity((u64::from(w)*u64::from(h)*4) as usize);
    // Strip conversion bounds temporary ICC/render buffers even for a 24 MP document.
    for y in (0..h).step_by(32) {
        ctx.check()?;
        let rect=photocraft_geom::Rect::new(0,y as i32,w as i32,(y+32).min(h) as i32);
        let source:Vec<f32>=photocraft_compose::render(doc,rect).px.into_iter().flatten().collect();
        let mut rgba=vec![0.0;source.len()];
        transform.convert_f32(&source,4,&mut rgba,4,true);
        data.extend(rgba.into_iter().map(|v|(v.clamp(0.0,1.0)*255.0).round() as u8));
    }
    crate::genstudio_generate::encoded(&Image::from_u8(w,h,ChannelLayout::Rgba,data).map_err(error)?)
}

/// Only capture the document/layer/selection sockets actually wired in the graph.
#[cfg(not(target_arch="wasm32"))]
pub fn request(settings:&Settings, doc:Option<&Document>, active:Option<LayerId>, payload:&Value, ctx:&JobCtx)->Result<Value> {
    let raw=bytes(std::path::Path::new(&settings.studio_workflow),8<<20)?;
    let value:Value=serde_json::from_slice(raw.strip_prefix(&[0xef,0xbb,0xbf]).unwrap_or(&raw)).map_err(error)?;
    let graph=value.get("prompt").filter(|v|v.is_object() && v.get("class_type").is_none()).unwrap_or(&value);
    if graph.get("nodes").is_some_and(Value::is_array) { return Err(error("Export (API) from ComfyUI, then choose that JSON file")); }
    let graph=graph.as_object().filter(|v|v.len()<=2048).ok_or_else(||error("Invalid API workflow"))?;
    let inputs:Vec<_>=graph.iter().filter(|(_,v)|v.get("class_type").and_then(Value::as_str)==Some("GenGateIN")).map(|(k,_)|k.as_str()).collect();
    let mut used=[false;3];
    for node in graph.values() {
        if let Some(args)=node.get("inputs").and_then(Value::as_object) {
            for arg in args.values() {
                if arg.as_array().is_some_and(|v|v.len()==2) && arg.get(0).and_then(Value::as_str).is_some_and(|id|inputs.contains(&id))
                    && let Some(index)=arg.get(1).and_then(Value::as_u64).and_then(|n|n.checked_sub(2)).filter(|n|*n<3)
                    && let Some(slot)=used.get_mut(index as usize) { *slot=true; }
            }
        }
    }
    let mut context=json!({"metadata":{}});
    if let Some(doc)=doc {
        context["metadata"]=json!({"documentId":doc.id.0,"name":doc.name,"width":doc.size.width,"height":doc.size.height,
            "colorMode":format!("{:?}",doc.mode),"depth":format!("{:?}",doc.depth),"resolutionDpi":doc.resolution_dpi,
            "layerId":active.map(|id|id.0),"hasSelection":doc.selection.is_some()});
        if used.first()==Some(&true) {
            ctx.progress(0.0,"Preparing document for ComfyUI…");
            context["document"]=json!(render_reference(doc,ctx)?);
        }
        if used.get(1)==Some(&true) && let Some(layer)=active.and_then(|id|doc.layer(id)) {
            ctx.progress(0.0,"Preparing layer for ComfyUI…");
            let mut source=doc.clone();let mut layer=layer.clone();layer.visible=true;source.layers=vec![layer];
            context["layer"]=json!(render_reference(&source,ctx)?);
        }
        if used.get(2)==Some(&true) && let Some(selection)=&doc.selection {
            let (w,h)=(doc.size.width,doc.size.height);
            if u64::from(w)*u64::from(h)>32<<20 {return Err(error("Workflow selections support up to 32 million pixels"));}
            let mut data=Vec::with_capacity((u64::from(w)*u64::from(h)) as usize);
            for y in (0..h).step_by(32) {
                ctx.check()?;
                let rect=photocraft_geom::Rect::new(0,y as i32,w as i32,(y+32).min(h) as i32);
                data.extend(selection.read_region(rect).chunks_exact(selection.channels().max(1)).map(|p|(p.first().copied().unwrap_or(0.0).clamp(0.0,1.0)*255.0).round() as u8));
            }
            context["selection"]=json!(crate::genstudio_generate::encoded(&Image::from_u8(w,h,ChannelLayout::Gray,data).map_err(error)?)?);
        }
    }
    let mut result=payload.clone();
    result["context"]=context;
    result["workflowGraph"]=Value::Object(graph.clone());
    Ok(result)
}

#[cfg(target_arch="wasm32")]
pub fn request(_: &Settings,_:Option<&Document>,_:Option<LayerId>,_:&Value,_:&JobCtx)->Result<Value> {Err(error("Local workflows require the desktop app"))}

#[cfg(not(target_arch="wasm32"))]
pub fn run(settings:&Settings,payload:&Value,ctx:&JobCtx)->Result<Output> {
    if std::env::var_os("GENSTUDIO_NO_PROCESS").is_some_and(|v|v=="1") {return Err(error("Process launch is disabled for validation"));}
    let folder=tempfile::Builder::new().prefix("gengate-workflow-").tempdir().map_err(error)?;
    crate::genstudio_edit::transport(settings,folder.path(),&json!({"settings":settings,"studio":payload}),ctx)?;
    let records:ResultRecords=serde_json::from_slice(&bytes(&folder.path().join("results.json"),256<<10)?).map_err(error)?;
    records.workflow.validate()?;
    if records.count==0 || records.count>8 || records.count!=records.images.len() {return Err(error("Invalid workflow batch"));}
    let mut images=Vec::new();let mut pixels=0u64;
    for (index,record) in records.images.into_iter().enumerate() {
        ctx.check()?;
        let area=u64::from(record.width)*u64::from(record.height);pixels=pixels.saturating_add(area);
        if record.width==0 || record.height==0 || record.width.max(record.height)>8192 || area>32<<20 || pixels>64<<20
            || record.prompt.len()>12000 || record.prompt.contains('\0') || record.name.chars().count()>120 || record.name.contains('\0') || record.seed>=1<<53 {
            return Err(error("Invalid workflow image metadata"));
        }
        crate::genstudio_lora::validate(&record.loras).map_err(error)?;
        let read=|name:&str|->Result<Image>{
            let image=photocraft_codecs::decode_with(&bytes(&folder.path().join(name),64<<20)?,&DecodeOptions{limits:Limits{max_width:8192,max_height:8192,max_pixels:32<<20,max_alloc:256<<20},..Default::default()}).map_err(error)?;
            if image.dimensions()!=(record.width,record.height) {return Err(error("Workflow image dimensions do not match its result"));}
            Ok(image)
        };
        let image=read(&format!("result-{index}.png"))?;
        let mask=if record.has_mask {Some(read(&format!("result-{index}-mask.png"))?.convert(ChannelLayout::Gray,SampleType::F32))} else {None};
        images.push(OutputImage{image,mask,record});
    }
    Ok(Output{images,workflow:records.workflow})
}

#[cfg(target_arch="wasm32")]
pub fn run(_: &Settings,_:&Value,_:&JobCtx)->Result<Output>{Err(error("Local workflows require the desktop app"))}

#[cfg(all(test,not(target_arch="wasm32")))]
mod tests {
    use super::*;
    fn settings(folder:&std::path::Path, ports:&[u64])->Result<Settings> {
        let mut inputs=serde_json::Map::new();
        for port in ports {inputs.insert(format!("port{port}"),json!(["in",port]));}
        let graph=json!({"in":{"class_type":"GenGateIN","inputs":{}},"out":{"class_type":"GenGateOUT","inputs":inputs}});
        let path=folder.join("workflow.json");std::fs::write(&path,serde_json::to_vec(&graph).map_err(error)?).map_err(error)?;
        Ok(Settings{studio_mode:"workflow".into(),studio_workflow:path.to_string_lossy().into_owned(),..Default::default()})
    }
    #[test]
    fn unused_document_ports_do_not_render_or_reduce_large_documents(){
        let folder=tempfile::tempdir().unwrap();let settings=settings(folder.path(),&[5,6,7]).unwrap();
        let doc=Document::new("Large metadata only",photocraft_geom::Size::new(50000,50000),photocraft_color::ColorMode::Rgb,photocraft_color::SampleType::U8);
        let result=request(&settings,Some(&doc),None,&json!({"prompt":"text only"}),&JobCtx::default()).unwrap();
        assert_eq!(result["context"]["metadata"]["width"],50000);
        assert!(result["context"].get("document").is_none());
        assert!(result["context"].get("layer").is_none());
        assert!(result["workflowGraph"].is_object());
    }
    #[test]
    fn connected_sources_keep_coordinates_depth_conversion_and_selection_polarity(){
        use base64::Engine as _;
        for depth in [8,16,32] {
            let folder=tempfile::tempdir().unwrap();let settings=settings(folder.path(),&[2,3,4]).unwrap();
            let mut session=crate::Session::new();session.execute("file.new",json!({"width":8,"height":6,"depth":depth})).unwrap();
            let st=session.active().unwrap();let mut doc=(*st.doc).clone();
            doc.selection=Some(photocraft_raster::Surface::with_default(photocraft_color::PixelFormat::GRAY8,&[0.5]));
            let result=request(&settings,Some(&doc),st.active_layer,&json!({"prompt":"source"}),&JobCtx::default()).unwrap();
            for name in ["document","layer","selection"] {
                let raw=base64::engine::general_purpose::STANDARD.decode(result["context"][name].as_str().unwrap()).unwrap();
                let image=photocraft_codecs::decode(&raw).unwrap();
                assert_eq!(image.dimensions(),(8,6));
                if name=="selection" {assert!((image.get(0,0,0)-0.5).abs()<0.01);}
            }
        }
    }
    #[test]
    fn recipe_rejects_invalid_hash_and_path(){
        assert!(Recipe{path:"workflow.json".into(),sha256:"f".repeat(64)}.validate().is_ok());
        assert!(Recipe{path:"workflow.json".into(),sha256:"old".into()}.validate().is_err());
        assert!(Recipe{path:"bad\0path".into(),sha256:"f".repeat(64)}.validate().is_err());
    }
}
