//! Studio workflow snapshots and bounded result files; no model code in the editor.
use crate::{EngineError, Result, genstudio::Settings, jobs::JobCtx};
use photocraft_codecs::{ChannelLayout, DecodeOptions, Image, Limits, SampleType};
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
    #[serde(default)]
    pub negative_prompt: String,
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

/// Forward the Studio payload and graph; images/masks come from reference slots.
#[cfg(not(target_arch="wasm32"))]
pub fn request(_settings:&Settings, payload:&Value, ctx:&JobCtx)->Result<Value> {
    ctx.check()?;
    // The Python worker requests a fresh snapshot from ComfyUI's active tab.
    Ok(payload.clone())
}

#[cfg(target_arch="wasm32")]
pub fn request(_: &Settings,_:&Value,_:&JobCtx)->Result<Value> {Err(error("Local workflows require the desktop app"))}

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
        let graph=json!({"in":{"class_type":"GenGateIN2","inputs":{}},"out":{"class_type":"GenGateOUT","inputs":inputs}});
        let path=folder.join("workflow.json");std::fs::write(&path,serde_json::to_vec(&graph).map_err(error)?).map_err(error)?;
        Ok(Settings{studio_mode:"workflow".into(),studio_workflow:path.to_string_lossy().into_owned(),..Default::default()})
    }
    #[test]
    fn request_forwards_studio_values_without_hidden_document_sources(){
        let folder=tempfile::tempdir().unwrap();let settings=settings(folder.path(),&[2,3,4]).unwrap();
        let payload=json!({"prompt":"A scene","width":640,"height":480,"references":[],"loras":[]});
        let result=request(&settings,&payload,&JobCtx::default()).unwrap();
        assert_eq!(result["prompt"],payload["prompt"]);
        assert_eq!(result["width"],640);
        assert!(result.get("context").is_none());
        assert!(result.get("workflowGraph").is_none());
        assert_eq!(request(&Settings::default(),&payload,&JobCtx::default()).unwrap(),payload);
    }
    #[test]
    fn recipe_rejects_invalid_hash_and_path(){
        assert!(Recipe{path:"workflow.json".into(),sha256:"f".repeat(64)}.validate().is_ok());
        assert!(Recipe{path:"workflow.json".into(),sha256:"old".into()}.validate().is_err());
        assert!(Recipe{path:"bad\0path".into(),sha256:"f".repeat(64)}.validate().is_err());
    }
}
