//! Shared, bounded AI fill queue. Pending requests hold masks, never image copies.
use crate::{EngineError, Result, Session, commands::CommandSpec, jobs::{JobId, Started}};
use photocraft_doc::{DocId, LayerId};
use photocraft_geom::Rect;
use serde_json::{Value, json};
use std::{collections::VecDeque, sync::Arc};

pub const FILL_LABEL: &str = "Remove · Ai Fill";
const LIMIT: usize = 32;
const PIXELS: usize = 32 << 20;

pub struct FillMask {
    pub rect: Rect,
    pub bounds: Rect,
    pub coverage: Vec<f32>,
}

struct Pending {
    id: u64,
    request: Request,
}

enum Request { Remove(crate::retouch_cmds::AiRequest), Selection(crate::genstudio_ai::Request) }
impl Request {
    fn doc(&self)->DocId { match self { Self::Remove(r)=>r.doc,Self::Selection(r)=>r.doc } }
    fn mask(&self)->&Arc<FillMask> { match self { Self::Remove(r)=>&r.mask,Self::Selection(r)=>&r.mask } }
    fn target(&self)->Option<LayerId> { match self { Self::Remove(r)=>r.target(),Self::Selection(r)=>Some(r.layer) } }
    fn set_anchor(&mut self,anchor:Option<LayerId>) { if let Self::Remove(r)=self { r.set_anchor(anchor); } }
}

struct Active {
    id: u64,
    doc: DocId,
    mask: Arc<FillMask>,
    job: JobId,
    target: Option<LayerId>,
}

#[derive(Default)]
pub struct Queue {
    next: u64,
    pending: VecDeque<Pending>,
    active: Option<Active>,
    dispatch: Option<Pending>,
    last_layer: Vec<(DocId, Option<LayerId>, LayerId)>,
    errors: Vec<String>,
    #[cfg(test)]
    exchange: Option<TestExchange>,
}

#[cfg(test)]
type TestExchange = Arc<dyn Fn(&crate::genstudio::Settings, &photocraft_codecs::Image, &photocraft_codecs::Image, &crate::jobs::JobCtx) -> Result<(photocraft_codecs::Image,photocraft_codecs::Image)> + Send + Sync>;

#[derive(Clone)]
pub struct FillView {
    pub id: u64,
    pub document: DocId,
    pub mask: Arc<FillMask>,
    pub job: Option<JobId>,
    pub progress: f32,
    pub message: String,
}

fn failure(message: impl Into<String>) -> EngineError { EngineError::Other(message.into()) }

pub fn views(s: &Session) -> Vec<FillView> {
    let q = &s.gen_studio.queue;
    let mut views = Vec::new();
    if let Some(active) = &q.active
        && let Some(job) = s.job(active.job)
    {
        views.push(FillView { id: active.id, document: active.doc, mask: active.mask.clone(), job: Some(active.job), progress: job.progress, message: job.message });
    }
    views.extend(q.pending.iter().map(|p| FillView {
        id: p.id, document: p.request.doc(), mask: p.request.mask().clone(), job: None,
        progress: 0.0, message: "Queued".into(),
    }));
    views
}

pub fn snapshot(s: &Session) -> Value {
    json!({"fills": views(s).iter().map(|v| json!({
        "id":v.id,"document":v.document.0,"state":if v.job.is_some() {"running"} else {"queued"},
        "job":v.job.map(|j|j.0),"progress":v.progress,"message":v.message,
        "maskBounds":[v.mask.bounds.x0,v.mask.bounds.y0,v.mask.bounds.width(),v.mask.bounds.height()],
    })).collect::<Vec<_>>()})
}

pub fn take_errors(s: &mut Session) -> Vec<String> { std::mem::take(&mut s.gen_studio.queue.errors) }

pub fn has_pending(s: &Session) -> bool { !s.gen_studio.queue.pending.is_empty() || s.gen_studio.queue.active.is_some() }

pub(crate) fn enqueue(s: &mut Session, p: &Value) -> Result<Value> {
    let command=p.get("command").and_then(Value::as_str).ok_or_else(||failure("An AI fill command is required"))?;
    let params = p.get("params").filter(|p| p.is_object()).ok_or_else(|| failure("AI fill params must be an object"))?;
    if let Some(authorize) = s.authorize { authorize(command,params)?; }
    let request=match command {
        "paint.remove"=>Request::Remove(crate::retouch_cmds::capture_ai(s,&crate::channel_cmds::inject_target(s,command,params.clone()))?),
        "genstudio.ai.generate"=>Request::Selection(crate::genstudio_ai::capture(s,params)?),
        _=>return Err(failure("This AI fill tool is not supported")),
    };
    let q = &mut s.gen_studio.queue;
    let pixels = q.pending.iter().map(|p| p.request.mask().coverage.len()).sum::<usize>()
        + q.active.as_ref().map_or(0, |a| a.mask.coverage.len());
    if q.pending.len() + usize::from(q.active.is_some()) >= LIMIT
        || pixels.saturating_add(request.mask().coverage.len()) > PIXELS
    {
        return Err(failure("The AI fill queue is full. Wait for a fill or cancel a queued mask"));
    }
    q.next = q.next.checked_add(1).ok_or_else(|| failure("AI fill request counter exhausted"))?;
    let id = q.next;
    q.pending.push_back(Pending { id, request });
    Ok(json!({"queued":id}))
}

fn cancel(s: &mut Session, p: &Value) -> Result<Value> {
    let id = p.get("id").and_then(Value::as_u64).ok_or_else(|| failure("A queued fill id is required"))?;
    let q = &mut s.gen_studio.queue;
    let Some(index) = q.pending.iter().position(|item| item.id == id) else {
        return Err(failure("This fill has already started or is no longer queued"));
    };
    q.pending.remove(index);
    Ok(json!({"cancelled":id}))
}

fn execute(s: &mut Session, p: &Value) -> Result<Value> {
    let id = p.get("id").and_then(Value::as_u64).ok_or_else(|| failure("Invalid AI fill dispatch"))?;
    if s.gen_studio.queue.dispatch.as_ref().is_none_or(|request| request.id != id) {
        return Err(failure("AI fills must be submitted through the queue"));
    }
    let request = s.gen_studio.queue.dispatch.take().ok_or_else(|| failure("Missing AI fill request"))?;
    #[cfg(test)]
    if let Some(exchange) = s.gen_studio.queue.exchange.clone() {
        return match request.request {
            Request::Remove(r)=>crate::retouch_cmds::run_ai_with(s,r,move |settings,image,mask,ctx|exchange(settings,image,mask,ctx)),
            Request::Selection(r)=>crate::genstudio_ai::run_with(s,r,move |settings,image,mask,ctx|exchange(settings,image,mask,ctx)),
        };
    }
    match request.request {
        Request::Remove(r)=>crate::retouch_cmds::run_ai(s,r),
        Request::Selection(r)=>crate::genstudio_ai::run(s,r),
    }
}

/// Called after ordinary jobs are applied. Only one AI fill runs across all documents.
pub fn tick(s: &mut Session) {
    if s.gen_studio.queue.active.is_none() && s.gen_studio.queue.pending.is_empty() {
        s.gen_studio.queue.last_layer.clear();
        return;
    }
    if let Some(active) = &s.gen_studio.queue.active {
        if s.job(active.job).is_some() { return; }
        let (doc, job, target) = (active.doc, active.job, active.target);
        let layer = s.jobs_with_recent().iter().find(|j|j.id == job && j.state == "done")
            .and_then(|j|j.result.as_ref()).and_then(|r|r.get("layer")).and_then(Value::as_u64).map(LayerId);
        if let Some(layer) = layer {
            s.gen_studio.queue.last_layer.retain(|(d,t,_)|*d != doc || *t != target);
            s.gen_studio.queue.last_layer.push((doc, target, layer));
        }
        s.gen_studio.queue.active = None;
        if s.gen_studio.queue.pending.is_empty() { s.gen_studio.queue.last_layer.clear(); }
    }
    let docs: Vec<DocId> = s.documents().iter().map(|st|st.doc.id).collect();
    s.gen_studio.queue.pending.retain(|item| docs.contains(&item.request.doc()));
    s.gen_studio.queue.last_layer.retain(|(doc,_,_)|docs.contains(doc));
    if s.gen_studio.state.lock().unwrap_or_else(std::sync::PoisonError::into_inner).busy
        || s.jobs().iter().any(|job| job.label == FILL_LABEL || job.label == crate::genstudio_ai::LABEL || job.label == "Remove · Qwen")
    { return; }
    let Some(first) = s.gen_studio.queue.pending.front() else { return };
    if s.job_on(first.request.doc()).is_some() { return; }
    let doc_id = first.request.doc();
    let Some(index) = s.documents().iter().position(|st|st.doc.id == doc_id) else { return };
    let Some(mut first) = s.gen_studio.queue.pending.pop_front() else { return };
    let target = first.request.target();
    let anchor = s.gen_studio.queue.last_layer.iter().find(|(doc,t,_)|*doc == doc_id && *t == target).map(|(_,_,id)|*id)
        .filter(|id|s.documents().get(index).is_some_and(|st|st.doc.layer(*id).is_some()));
    first.request.set_anchor(anchor);
    let (id, doc, mask) = (first.id, first.request.doc(), first.request.mask().clone());
    s.gen_studio.queue.dispatch = Some(first);
    let prior = s.active;
    s.active = Some(index);
    let result = s.start("genstudio.fill.execute", json!({"id":id}));
    s.active = prior;
    s.gen_studio.queue.dispatch = None;
    match result {
        Ok(Started::Job(job)) => s.gen_studio.queue.active = Some(Active { id, doc, mask, job, target }),
        Ok(Started::Done(_)) => {}
        Err(error) => {
            if s.gen_studio.queue.errors.len() < LIMIT { s.gen_studio.queue.errors.push(error.to_string()); }
        }
    }
}

pub fn specs() -> Vec<CommandSpec> {
    let make = |id, label, params, run| CommandSpec { id, label, menu:&[], shortcut:None, params, enabled:crate::commands::always, run, journal:false };
    vec![
        make("genstudio.fill.enqueue", "Queue AI Fill", r#"{"command":"paint.remove","params":{...}}"#, enqueue),
        make("genstudio.fill.cancel", "Cancel Queued AI Fill", r#"{"id":u64}"#, cancel),
        make("genstudio.fill.status", "AI Fill Queue", "{}", |s,_|Ok(snapshot(s))),
        make("genstudio.fill.execute", "Run Next AI Fill", r#"{"id":u64} (internal)"#, execute),
    ]
}

#[cfg(test)]
mod tests {
    use super::*;
    use photocraft_codecs::{ChannelLayout, Image};
    use std::{sync::atomic::{AtomicBool,AtomicUsize,Ordering},time::{Duration,Instant}};

    fn scene() -> Session {
        let mut s = Session::new();
        s.execute("file.new",json!({"width":384,"height":256})).unwrap();
        s.execute("edit.fill",json!({"color":"#73331a"})).unwrap();
        s
    }

    fn green(image: &Image, mask: &Image) -> Result<(Image,Image)> {
        Ok((Image::from_u8(image.width(),image.height(),ChannelLayout::Rgba,
            (0..image.pixel_count()).flat_map(|_|[20,180,40,255]).collect()).map_err(|e|failure(e.to_string()))?,mask.clone()))
    }

    fn add(s: &mut Session, x:i32, new_layer:bool) -> u64 {
        s.execute("genstudio.fill.enqueue",json!({"command":"paint.remove","params":{
            "points":[[x,100]],"size":16,"newLayer":new_layer}})).unwrap()["queued"].as_u64().unwrap()
    }

    fn until(s: &mut Session, ready:impl Fn(&Session)->bool) {
        let start=Instant::now();
        while !ready(s) && start.elapsed()<Duration::from_secs(10) {
            s.poll_jobs(); std::thread::sleep(Duration::from_millis(4));
        }
        assert!(ready(s),"bounded synthetic queue did not finish");
    }

    #[test]
    fn ai_fill_queue_is_fifo_and_cancelled_waiting_masks_never_reach_the_engine() {
        let mut s=scene();
        let calls=Arc::new(AtomicUsize::new(0));
        let release=Arc::new(AtomicBool::new(false));
        let source_green=Arc::new(AtomicBool::new(false));
        let sample_index=Arc::new(AtomicUsize::new(0));
        let (c,r,g,px)=(calls.clone(),release.clone(),source_green.clone(),sample_index.clone());
        s.gen_studio.queue.exchange=Some(Arc::new(move |_,image,mask,ctx| {
            let index=c.fetch_add(1,Ordering::SeqCst);
            if index==0 {
                ctx.progress(0.5,"Generating synthetic pixels");
                while !r.load(Ordering::SeqCst) { ctx.check()?; std::thread::sleep(Duration::from_millis(4)); }
            } else { g.store(image.data().get(px.load(Ordering::SeqCst)).copied().unwrap_or(0)>150,Ordering::SeqCst); }
            green(image,mask)
        }));
        let original=s.active().unwrap().doc.clone();
        let first=add(&mut s,180,false);
        assert_eq!(calls.load(Ordering::SeqCst),0,"enqueue alone sends no model request");
        until(&mut s, |s|calls.load(Ordering::SeqCst)==1 && views(s).first().is_some_and(|v|v.progress>=0.5));
        let second=add(&mut s,160,false);
        let third=add(&mut s,230,false);
        let views=views(&s);
        assert_eq!(views.iter().map(|v|v.id).collect::<Vec<_>>(),[first,second,third]);
        assert!(views.first().unwrap().job.is_some() && views.iter().skip(1).all(|v|v.job.is_none()));
        assert_eq!(views.first().unwrap().progress,0.5);
        assert!(s.execute("genstudio.fill.cancel",json!({"id":first})).is_err(),"a queued cancel cannot cancel an active fill");
        let rect=views.last().unwrap().mask.rect;
        sample_index.store(((100-rect.y0) as usize*rect.width() as usize+(180-rect.x0) as usize)*4+1,Ordering::SeqCst);
        s.execute("genstudio.fill.cancel",json!({"id":second})).unwrap();
        s.tools.brush.size=120.0;
        assert!(Arc::ptr_eq(&original,&s.active().unwrap().doc),"collecting masks never edits document pixels or selection");
        release.store(true,Ordering::SeqCst);
        until(&mut s,|s|!has_pending(s));
        assert_eq!(calls.load(Ordering::SeqCst),2);
        assert!(source_green.load(Ordering::SeqCst),"later fills sample the already applied earlier result");
        let st=s.active().unwrap();
        let surface=st.doc.layer(st.active_layer.unwrap()).unwrap().surface().unwrap();
        assert!(surface.rgba(180,100)[1]>0.65 && surface.rgba(230,100)[1]>0.65);
        assert!(surface.rgba(160,100)[1]<0.4,"the cancelled mask never paints");
        assert!(surface.rgba(230,120)[1]<0.4,"changing brush size after enqueue cannot broaden its frozen mask");
        s.execute("edit.undo",json!({})).unwrap();
        let st=s.active().unwrap();
        assert!(st.doc.layer(st.active_layer.unwrap()).unwrap().surface().unwrap().rgba(230,100)[1]<0.4);
    }

    #[test]
    fn selection_and_remove_share_one_queue_and_keep_their_own_masks() {
        let mut s=scene();
        let background=s.active().unwrap().active_layer.unwrap();
        s.execute("select.rect",json!({"x":240,"y":80,"width":24,"height":30})).unwrap();
        let layer=s.execute("edit.aiFill",json!({})).unwrap()["layer"].as_u64().unwrap();
        s.execute("select.deselect",json!({})).unwrap();
        let calls=Arc::new(AtomicUsize::new(0));let c=calls.clone();
        s.gen_studio.queue.exchange=Some(Arc::new(move |_,image,mask,_|{c.fetch_add(1,Ordering::SeqCst);green(image,mask)}));
        let first=s.execute("genstudio.fill.enqueue",json!({"command":"paint.remove","params":{"layer":background.0,"points":[[180,100]],"size":16}})).unwrap()["queued"].as_u64().unwrap();
        let second=s.execute("genstudio.ai.generate",json!({"layer":layer,"prompt":"A small tree"})).unwrap()["queued"].as_u64().unwrap();
        assert_eq!(views(&s).iter().map(|v|v.id).collect::<Vec<_>>(),[first,second]);
        until(&mut s,|s|!has_pending(s));
        assert_eq!(calls.load(Ordering::SeqCst),2);
        let layer=s.active().unwrap().doc.layer(LayerId(layer)).unwrap();
        assert_eq!(crate::genstudio_ai::gallery(layer).unwrap().variants[0].prompt,"A small tree");
        assert!(layer.mask.as_ref().unwrap().surface.sample_channel(250,90,0)>0.9);
        assert_eq!(layer.mask.as_ref().unwrap().surface.sample_channel(180,100,0),0.0);
    }

    #[test]
    fn ai_fill_queue_freezes_the_selected_color_channel() {
        let mut s=scene();
        s.gen_studio.queue.exchange=Some(Arc::new(|_,image,mask,_|green(image,mask)));
        let original={
            let st=s.active().unwrap();
            st.doc.layer(st.active_layer.unwrap()).unwrap().surface().unwrap().rgba(180,100)
        };
        s.active_mut().unwrap().channel_view.target=crate::channel_cmds::ChannelTarget::Color(0);
        add(&mut s,180,false);
        s.active_mut().unwrap().channel_view.target=crate::channel_cmds::ChannelTarget::Composite;
        until(&mut s,|s|!has_pending(s));
        let st=s.active().unwrap();
        let result=st.doc.layer(st.active_layer.unwrap()).unwrap().surface().unwrap().rgba(180,100);
        assert!(result[0]<original[0]);
        assert_eq!(&result[1..],&original[1..],"queued fills retain their original channel target");
    }

    #[test]
    fn ai_fill_queue_new_layers_are_in_completion_order_with_real_masks() {
        let mut s=scene();
        s.gen_studio.queue.exchange=Some(Arc::new(|_,image,mask,_|green(image,mask)));
        add(&mut s,180,true); add(&mut s,230,true);
        until(&mut s,|s|!has_pending(s));
        let doc=s.execute("document.inspect",json!({})).unwrap();
        let layers=doc["layers"].as_array().unwrap();
        assert_eq!(layers.len(),3);
        assert!(layers[0]["hasMask"].as_bool().unwrap() && layers[1]["hasMask"].as_bool().unwrap());
        assert!(layers[0]["id"].as_u64().unwrap()>layers[1]["id"].as_u64().unwrap(),"the latest queued layer is on top");
    }

    #[test]
    fn ai_fill_queue_is_bounded_and_closed_documents_drop_their_pending_masks() {
        let mut s=scene();
        for _ in 0..LIMIT { add(&mut s,180,false); }
        assert!(s.execute("genstudio.fill.enqueue",json!({"command":"paint.remove","params":{"points":[[180,100]]}})).is_err());
        assert_eq!(views(&s).len(),LIMIT);
        for malformed in [json!({}),json!({"command":"file.close","params":{}}),json!({"command":"paint.remove","params":[]})] {
            assert!(s.execute("genstudio.fill.enqueue",malformed).is_err());
        }
        s.execute("file.close",json!({"discard":true})).unwrap();
        s.poll_jobs();
        assert!(!has_pending(&s));
        assert!(s.execute("genstudio.fill.execute",json!({"id":1})).is_err());
    }

    #[test]
    fn ai_fill_queue_survives_a_failed_fill_and_keeps_its_next_mask() {
        let mut s=scene();
        let calls=Arc::new(AtomicUsize::new(0));
        let c=calls.clone();
        s.gen_studio.queue.exchange=Some(Arc::new(move |_,image,mask,_| {
            if c.fetch_add(1,Ordering::SeqCst)==0 {return Err(failure("Synthetic engine failure"));}
            green(image,mask)
        }));
        add(&mut s,180,false);add(&mut s,230,false);
        until(&mut s,|s|!has_pending(s));
        assert_eq!(calls.load(Ordering::SeqCst),2);
        assert!(s.jobs_with_recent().iter().any(|job|job.state=="failed"));
        let st=s.active().unwrap();
        let surface=st.doc.layer(st.active_layer.unwrap()).unwrap().surface().unwrap();
        assert!(surface.rgba(180,100)[1]<0.4 && surface.rgba(230,100)[1]>0.65);
    }
}
