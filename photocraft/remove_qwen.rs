//! Qwen backend of the existing Remove command. Shares its brush, loops and selection semantics.
use super::*;
use photocraft_cms::{Builtin, Intent, Transform};
use photocraft_codecs::{ChannelLayout, Image};
use photocraft_doc::LayerMask;
use crate::jobs::JobCtx;

pub(crate) const LABEL: &str = "Remove · Ai Fill";

/// A queued stroke freezes its footprint, brush, target and engine settings, but
/// samples the latest document only when its turn starts.
pub(crate) struct AiRequest {
    pub doc: photocraft_doc::DocId,
    pub mask: std::sync::Arc<crate::genstudio_queue::FillMask>,
    plan: Plan,
    params: Value,
    settings: crate::genstudio::Settings,
    canvas: (u32, u32),
    anchor: Option<LayerId>,
    restriction: Option<usize>,
}

impl AiRequest {
    pub fn target(&self) -> Option<LayerId> { self.plan.id }
    pub fn set_anchor(&mut self, anchor: Option<LayerId>) {
        if self.plan.new_layer { self.anchor = anchor.or(self.plan.id); }
    }
}

struct Input { image: Image, mask: Image, coverage: Vec<f32>, format: PixelFormat }
struct Filled { out: Region, mask: Vec<f32>, format: PixelFormat }

fn other(e: impl std::fmt::Display) -> EngineError { EngineError::Other(format!("Ai Fill: {e}")) }

fn footprint(doc: &Document, plan: &Plan) -> Result<std::sync::Arc<crate::genstudio_queue::FillMask>> {
    let hole = hole(plan, None);
    let selection = doc.selection.as_ref().map(|s| coverage(s, plan.window));
    let coverage: Vec<f32> = hole.iter().enumerate().map(|(i, on)| {
        if *on { selection.as_ref().and_then(|m| m.get(i)).copied().unwrap_or(1.0).clamp(0.0, 1.0) } else { 0.0 }
    }).collect();
    if !coverage.iter().any(|m| *m > 0.0) { return Err(other("the stroke doesn't cover anything that can change; check the selection")); }
    if coverage.iter().all(|m| *m >= 1.0) { return Err(other("leave some surrounding image outside the mask")); }
    let mut bounds = Rect::EMPTY;
    let width = plan.window.width().max(1) as usize;
    for (i, value) in coverage.iter().enumerate() {
        if *value > 0.0 {
            let x = plan.window.x0 + (i % width) as i32;
            let y = plan.window.y0 + (i / width) as i32;
            bounds = bounds.union(&Rect::new(x, y, x + 1, y + 1));
        }
    }
    Ok(std::sync::Arc::new(crate::genstudio_queue::FillMask { rect: plan.window, bounds, coverage }))
}

#[cfg(test)]
fn prepare(doc: &mut Document, plan: &Plan, p: &Value, ctx: &JobCtx) -> Result<Input> {
    let mask = footprint(doc, plan)?;
    prepare_mask(doc, plan, p, ctx, &mask)
}

fn prepare_mask(doc: &mut Document, plan: &Plan, p: &Value, ctx: &JobCtx, mask: &crate::genstudio_queue::FillMask) -> Result<Input> {
    ctx.check()?;
    let coverage = mask.coverage.clone();
    let (surf, _) = crate::channel_cmds::target_surface(doc, plan.id, p)?;
    let format = surf.format();
    let (pixels, stride, profile) = if plan.all_layers {
        let buf = photocraft_compose::render(doc, plan.window);
        (buf.px.into_iter().flatten().collect::<Vec<_>>(), 4, crate::color_cmds::composite_profile(doc))
    } else {
        (surf.read_region(plan.window), format.channels(), if targets_pixels(p) { crate::color_cmds::document_profile(doc) } else { std::sync::Arc::new(Builtin::SGray.profile().clone()) })
    };
    let t = Transform::new(&profile, Builtin::Srgb.profile(), Intent::RelativeColorimetric, true).map_err(other)?;
    let colours = if plan.all_layers { 3 } else { format.mode.color_channels() };
    if t.inputs() != colours { return Err(other("the source profile does not match the layer's color channels")); }
    let mut rgba = vec![0.0; coverage.len() * 4];
    t.convert_f32(&pixels, stride, &mut rgba, 4, false);
    for (i, px) in rgba.as_chunks_mut::<4>().0.iter_mut().enumerate() {
        let alpha = if plan.all_layers || format.alpha { pixels.get(i * stride + stride - 1).copied().unwrap_or(1.0) } else { 1.0 };
        if let Some(a) = px.last_mut() { *a = alpha; }
    }
    let win = plan.window;
    let image = Image::from_normalized(win.width(), win.height(), ChannelLayout::Rgba, photocraft_codecs::SampleType::U8, &rgba).map_err(other)?;
    let bytes = coverage.iter().map(|v| (v * 255.0).round() as u8).collect();
    let mask = Image::from_u8(win.width(), win.height(), ChannelLayout::Gray, bytes).map_err(other)?;
    ctx.progress(0.1, "Preparing the masked image…");
    Ok(Input { image, mask, coverage, format })
}

fn decode(doc: &Document, plan: &Plan, p: &Value, input: &Input, image: Image, mask: Image) -> Result<Filled> {
    if image.dimensions() != input.image.dimensions() || mask.dimensions() != image.dimensions() { return Err(other("the engine returned the wrong rectangle dimensions")); }
    let image = crate::genstudio_edit::rgba(&image);
    let rgba: Vec<f32> = image.data().iter().map(|v| f32::from(*v) / 255.0).collect();
    let profile = if targets_pixels(p) { crate::color_cmds::document_profile(doc) } else { std::sync::Arc::new(Builtin::SGray.profile().clone()) };
    let t = Transform::new(Builtin::Srgb.profile(), &profile, Intent::RelativeColorimetric, true).map_err(other)?;
    let format = PixelFormat::new(input.format.mode, input.format.sample, true);
    if t.outputs() != format.mode.color_channels() { return Err(other("the output profile does not match the layer's color channels")); }
    let mut out = Region::new(plan.window, format.channels());
    t.convert_f32(&rgba, 4, &mut out.data, out.ch, true);
    let mask = mask.convert(ChannelLayout::Gray, photocraft_codecs::SampleType::U8);
    // An engine cannot broaden the user's footprint, even if it returns a wrong mask.
    let mask = mask.data().iter().zip(&input.coverage).map(|(v, cap)| (f32::from(*v) / 255.0).min(*cap)).collect();
    Ok(Filled { out, mask, format })
}

fn apply(s: &mut Session, id: Option<LayerId>, new_layer: bool, p: &Value, filled: Filled, restriction: Option<usize>) -> Result<Value> {
    let rect = filled.out.rect;
    let layer = s.edit(LABEL, |doc, active| {
        if new_layer {
            let mut layer = Layer::raster(doc.next_layer_name(LABEL), filled.format);
            crate::pixels_mut(&mut layer)?.write_region(rect, &filled.out.data);
            let mut mask = LayerMask::hide_all();
            mask.surface.write_region(rect, &filled.mask);
            layer.mask = Some(mask);
            let id = doc.insert_above(id, layer);
            *active = Some(id);
            return Ok(Some(id));
        }
        let (surf, locked) = crate::channel_cmds::target_surface(doc, id, p)?;
        let fmt = surf.format();
        let mut pixels = surf.read_region(rect);
        let colours = fmt.mode.color_channels();
        for ((dst, src), mask) in pixels.chunks_exact_mut(fmt.channels()).zip(filled.out.data.chunks_exact(filled.out.ch)).zip(&filled.mask) {
            let old_alpha = if fmt.alpha { dst.last().copied().unwrap_or(1.0) } else { 1.0 };
            let amount = mask * src.last().copied().unwrap_or(1.0).clamp(0.0, 1.0);
            if amount <= 0.0 || locked && old_alpha <= 0.0 { continue; }
            let alpha = amount + old_alpha * (1.0 - amount);
            for ((channel,dst), src) in dst.iter_mut().take(colours).enumerate().zip(src) {
                if restriction.is_some_and(|selected|channel != selected) { continue; }
                *dst = if locked { *dst + (*src - *dst) * amount } else { (*src * amount + *dst * old_alpha * (1.0 - amount)) / alpha.max(f32::EPSILON) };
            }
            if fmt.alpha && !locked && restriction.is_none() && let Some(a) = dst.last_mut() { *a = alpha; }
        }
        surf.write_region(rect, &pixels);
        Ok(None)
    })?;
    if let Some(st) = s.active_mut() { st.last_damage = Some(rect); }
    Ok(json!({"damage":damage_json(rect), "layer":layer.map(|l| l.0), "engine":"qwen"}))
}

pub(crate) fn capture_ai(s: &Session, p: &Value) -> Result<AiRequest> {
    let mut plan = plan(s, p)?;
    let doc = s.active().ok_or(EngineError::NoDocument)?.doc.clone();
    let bounds = plan.strokes.iter().map(stroke_bounds).reduce(|a,b| a.union(&b)).ok_or_else(|| other("empty stroke"))?.intersect(&doc.bounds());
    plan.window = bounds.inflate(sampling_margin(bounds).clamp(96, MAX_MARGIN)).intersect(&doc.bounds());
    if u64::from(plan.window.width()) * u64::from(plan.window.height()) > MAX_PIXELS { return Err(other("the masked rectangle is too large; use a smaller stroke")); }
    let settings = s.prefs().gen_studio.clone();
    settings.validate().map_err(other)?;
    let mask = footprint(&doc, &plan)?;
    let anchor = plan.id;
    let restriction = crate::channel_cmds::color_restriction(s,"paint.remove",p);
    Ok(AiRequest { doc: doc.id, mask, plan, params: p.clone(), settings, canvas: (doc.size.width, doc.size.height), anchor, restriction })
}

pub(crate) fn run_ai(s: &mut Session, request: AiRequest) -> Result<Value> {
    run_ai_with(s, request, crate::genstudio_edit::generate)
}

fn run_with(
    s: &mut Session, p: &Value,
    exchange: impl FnOnce(&crate::genstudio::Settings, &Image, &Image, &JobCtx) -> Result<(Image, Image)> + Send + 'static,
) -> Result<Value> {
    let request = capture_ai(s, p)?;
    run_ai_with(s, request, exchange)
}

pub(crate) fn run_ai_with(
    s: &mut Session, request: AiRequest,
    exchange: impl FnOnce(&crate::genstudio::Settings, &Image, &Image, &JobCtx) -> Result<(Image, Image)> + Send + 'static,
) -> Result<Value> {
    let doc = s.active().ok_or(EngineError::NoDocument)?.doc.clone();
    if doc.id != request.doc || (doc.size.width, doc.size.height) != request.canvas {
        return Err(other("the document dimensions changed while the fill was queued; draw the mask again"));
    }
    let AiRequest { plan, params, settings, mask, anchor, restriction, .. } = request;
    let (id, new_layer) = (if plan.new_layer { anchor } else { plan.id }, plan.new_layer);
    let worker_params = params.clone();
    crate::jobs::run(s, LABEL, true, move |ctx| {
        let mut doc = (*doc).clone();
        let input = prepare_mask(&mut doc, &plan, &worker_params, ctx, &mask)?;
        let (image, mask) = exchange(&settings, &input.image, &input.mask, ctx)?;
        ctx.check()?;
        ctx.progress(0.95, "Applying the generated content…");
        decode(&doc, &plan, &worker_params, &input, image, mask)
    }, move |s, filled| apply(s, id, new_layer, &params, filled, restriction))
}

pub(super) fn remove(s: &mut Session, p: &Value) -> Result<Value> { run_with(s, p, crate::genstudio_edit::generate) }

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;

    fn scene(sample: SampleType) -> Session {
        let mut s = Session::new();
        s.execute("file.new", json!({"width":384,"height":256})).unwrap();
        s.edit("Synthetic fixture", |doc, active| {
            doc.depth = sample;
            let rect = doc.bounds();
            let id = active.ok_or_else(|| other("no fixture layer"))?;
            let layer = doc.layer_mut(id).ok_or(EngineError::NoLayer(id))?;
            let mut surf = Surface::new(PixelFormat::new(photocraft_color::ColorMode::Rgb, sample, true));
            let data: Vec<f32> = (0..rect.width()*rect.height()).flat_map(|_| [0.45, 0.2, 0.1, 1.0]).collect();
            surf.write_region(rect, &data);
            layer.content = LayerContent::Raster(surf);
            Ok(())
        }).unwrap();
        s
    }

    fn green(_: &crate::genstudio::Settings, input: &Image, _: &Image, _: &JobCtx) -> Result<(Image, Image)> {
        let data: Vec<u8> = (0..input.pixel_count()).flat_map(|_| [20, 180, 40, 255]).collect();
        Ok((Image::from_u8(input.width(), input.height(), ChannelLayout::Rgba, data).map_err(other)?,
            Image::from_u8(input.width(), input.height(), ChannelLayout::Gray, vec![255; input.pixel_count()]).map_err(other)?))
    }

    fn pixels(s: &Session) -> &Surface {
        let st = s.active().unwrap();
        st.doc.layer(st.active_layer.unwrap()).unwrap().surface().unwrap()
    }

    #[test]
    fn flatten_changes_only_the_mask_at_all_document_depths_and_undo_restores_it() {
        for sample in [SampleType::U8, SampleType::U16, SampleType::F32] {
            let mut s = scene(sample);
            let outside = pixels(&s).rgba(50, 100);
            let inside = pixels(&s).rgba(180, 100);
            let count = s.active().unwrap().doc.walk().len();
            let result = run_with(&mut s, &json!({"points":[[180,100],[180,140]],"size":12}), green).unwrap();
            assert_eq!(result["engine"], "qwen");
            assert_eq!(pixels(&s).format().sample, sample);
            assert_eq!(pixels(&s).rgba(50,100), outside, "context pixels must be bit-for-bit unchanged");
            assert!(pixels(&s).rgba(180,100)[1] > 0.65);
            assert_eq!(s.active().unwrap().doc.walk().len(), count);
            s.execute("edit.undo", json!({})).unwrap();
            assert_eq!(pixels(&s).rgba(180,100), inside);
        }
    }

    #[test]
    fn new_layer_keeps_full_rectangle_behind_a_real_editable_mask() {
        let mut s = scene(SampleType::U16);
        let base = s.active().unwrap().active_layer.unwrap();
        let original = pixels(&s).rgba(180,100);
        let count = s.active().unwrap().doc.walk().len();
        let result = run_with(&mut s, &json!({"points":[[180,100]],"size":12,"newLayer":true}), green).unwrap();
        let st = s.active().unwrap();
        assert_eq!(st.doc.walk().len(), count+1);
        assert_eq!(st.doc.layer(base).unwrap().surface().unwrap().rgba(180,100), original);
        let layer = st.doc.layer(st.active_layer.unwrap()).unwrap();
        let mask = layer.mask.as_ref().expect("a separate layer mask");
        assert!(mask.value(180,100) > 0.99);
        assert_eq!(mask.value(160,100), 0.0);
        assert!(layer.surface().unwrap().rgba(160,100)[1] > 0.65, "full generated context exists behind the mask");
        assert_eq!(layer.surface().unwrap().rgba(0,0)[3], 0.0, "pixels outside the rectangle stay empty");
        assert!(result["layer"].as_u64().is_some());
        s.execute("edit.undo", json!({})).unwrap();
        assert_eq!(s.active().unwrap().doc.walk().len(), count);
    }

    #[test]
    fn sample_all_layers_reads_the_composite_instead_of_an_empty_active_layer() {
        let mut s = scene(SampleType::U8);
        s.execute("layer.new.layer", json!({})).unwrap();
        for all in [false,true] {
            let p = json!({"points":[[180,100]],"size":12,"sampleAllLayers":all});
            let plan = plan(&s,&p).unwrap();
            let mut doc = (*s.active().unwrap().doc).clone();
            let input = prepare(&mut doc,&plan,&p,&JobCtx::new()).unwrap();
            assert_eq!(input.image.data().get(3).copied(), Some(if all {255} else {0}));
        }
    }

    #[test]
    fn multiple_strokes_soft_selection_and_an_untrusted_mask_stay_within_the_selection() {
        let mut s = scene(SampleType::F32);
        s.edit("Synthetic selection", |doc,_| {
            let mut sel=Surface::new(PixelFormat::GRAY8);
            sel.write_region(Rect::new(175,80,190,160), &vec![0.5;15*80]);
            doc.selection=Some(sel);
            Ok(())
        }).unwrap();
        let outside = pixels(&s).rgba(200,100);
        let before = pixels(&s).rgba(180,100);
        run_with(&mut s,&json!({"strokes":[[[180,100]],[[200,100]]],"size":12}),green).unwrap();
        assert_eq!(pixels(&s).rgba(200,100), outside, "a generated mask cannot escape the selection");
        let after=pixels(&s).rgba(180,100);
        assert!(after[1] > before[1] && after[1] < 0.6, "soft selection is a blend, not a hard overwrite: {after:?}");
    }

    #[test]
    fn cancelled_or_invalid_results_leave_the_document_unchanged() {
        let mut s=scene(SampleType::U8);
        let before=s.active().unwrap().doc.clone();
        let p=json!({"points":[[180,100]],"size":12});
        assert!(run_with(&mut s,&p,|settings,image,mask,ctx| {
            ctx.cancel(); green(settings,image,mask,ctx)
        }).is_err());
        assert!(Arc::ptr_eq(&before,&s.active().unwrap().doc));
        assert!(run_with(&mut s,&p,|_,_,_,_| {
            let tiny=Image::from_u8(1,1,ChannelLayout::Rgba,vec![0;4]).map_err(other)?;
            Ok((tiny.clone(),tiny))
        }).is_err());
        assert!(Arc::ptr_eq(&before,&s.active().unwrap().doc));
        assert!(s.execute("paint.remove",json!({"useQwen":"yes"})).is_err());
    }

    #[test]
    fn srgb_exchange_round_trips_a_wide_gamut_document_without_retagging_it() {
        let mut s=scene(SampleType::U16);
        let profile=Builtin::DisplayP3.profile().to_bytes();
        s.edit("Synthetic P3",|doc,_| {doc.icc_profile=Some(profile.clone());Ok(())}).unwrap();
        let original=pixels(&s).rgba(180,100);
        run_with(&mut s,&json!({"points":[[180,100]],"size":12}),|_,image,mask,_| Ok((image.clone(),mask.clone()))).unwrap();
        assert_eq!(s.active().unwrap().doc.icc_profile.as_ref(),Some(&profile));
        let after=pixels(&s).rgba(180,100);
        for (a,b) in original.iter().zip(after) {assert!((a-b).abs()<0.015,"{original:?} vs {after:?}");}
    }

    #[test]
    fn locked_transparency_is_preserved_when_painting_into_the_current_layer() {
        let mut s=scene(SampleType::F32);
        s.edit("Synthetic alpha lock",|doc,active| {
            let id=active.ok_or_else(||other("no layer"))?;
            let layer=doc.layer_mut(id).ok_or(EngineError::NoLayer(id))?;
            layer.locks.transparency=true;
            let data:Vec<f32>=(0..40*40).flat_map(|_|[0.4,0.2,0.1,0.4]).collect();
            crate::pixels_mut(layer)?.write_region(Rect::new(160,80,200,120),&data);
            Ok(())
        }).unwrap();
        run_with(&mut s,&json!({"points":[[180,100]],"size":12}),green).unwrap();
        assert_eq!(pixels(&s).rgba(180,100)[3],0.4);
        assert!(pixels(&s).rgba(180,100)[1]>0.65);
    }

    #[test]
    fn a_stroke_on_a_24mp_canvas_sends_only_a_small_context_rectangle() {
        let mut s=Session::new();
        s.execute("file.new",json!({"width":6000,"height":4000,"background":"transparent"})).unwrap();
        let start=std::time::Instant::now();
        let result=run_with(&mut s,&json!({"points":[[3000,2000],[3020,2040]],"size":24}),|settings,image,mask,ctx| {
            assert!(image.width()<512 && image.height()<512, "the full 24MP document must not be sent");
            green(settings,image,mask,ctx)
        }).unwrap();
        eprintln!("24MP canvas / ROI bridge + apply, synthetic pixels: {:?}; {:?}",start.elapsed(),result["damage"]);
    }
}
