//! Independent Direct segmentation; existing selection commands keep their IDs.
use crate::{EngineError, Result, Session, commands::CommandSpec, jobs::JobCtx};
use photocraft_algo::selection::{Region, SelectionMode, combine_region};
use photocraft_cms::{Builtin, Intent, Transform};
use photocraft_codecs::{ChannelLayout, Image, SampleType};
use photocraft_doc::{Document, LayerId, LayerMask};
use photocraft_geom::Rect;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(default, rename_all = "camelCase", deny_unknown_fields)]
pub struct Settings {
    pub enabled: bool,
    pub detector: String,
    pub segmenter: String,
    pub python: String,
}
impl Default for Settings {
    fn default() -> Self {
        Self { enabled: false, detector: "yolov8s-worldv2".into(), segmenter: "sam3".into(), python: String::new() }
    }
}
impl Settings {
    pub fn validate(&self) -> std::result::Result<(), String> {
        if !matches!(self.detector.as_str(), "yolov8s-worldv2" | "yolov8m-worldv2" | "yolov8l-worldv2") || self.segmenter != "sam3" {
            return Err("Unsupported segmentation model".into());
        }
        if self.python.len() > 4096 || self.python.contains('\0') {
            return Err("Invalid segmentation Python path".into());
        }
        Ok(())
    }
}
fn error(e: impl std::fmt::Display) -> EngineError {
    EngineError::Other(format!("GenGate segmentation: {e}"))
}

fn area(p: &Value, bounds: Rect, kind: &str) -> Result<Rect> {
    if kind != "object" {
        return Ok(bounds);
    }
    let values = p["rect"].as_array().filter(|a| a.len() == 4).ok_or_else(|| error("Drag a rectangle around the object"))?;
    let v = values
        .iter()
        .map(|v| v.as_f64().filter(|n| n.is_finite() && n.abs() <= 1e7))
        .collect::<Option<Vec<_>>>()
        .ok_or_else(|| error("Rectangle coordinates must be finite numbers"))?;
    let (Some(&x), Some(&y), Some(&w), Some(&h)) = (v.first(), v.get(1), v.get(2), v.get(3)) else { return Err(error("Invalid rectangle")) };
    if w.abs() < 2.0 || h.abs() < 2.0 {
        return Err(error("Ai Select needs a rectangle at least 2 pixels wide and high"));
    }
    Ok(Rect::new(x.min(x + w).floor() as i32, y.min(y + h).floor() as i32, x.max(x + w).ceil() as i32, y.max(y + h).ceil() as i32).intersect(&bounds))
}

/// Capture in strips, then reduce to a bounded model input before any PNG/float copies.
fn input(doc: &Document, layer: Option<LayerId>, all: bool, area: Rect, ctx: &JobCtx) -> Result<Image> {
    let step = area.width().max(area.height()).div_ceil(2048).max(1);
    let (w, h) = (area.width().div_ceil(step), area.height().div_ceil(step));
    let mut rgba = vec![0.0f32; (w * h * 4) as usize];
    let surface = layer.filter(|_| !all).and_then(|id| doc.layer(id)).and_then(|l| l.surface());
    let profile = if surface.is_some() { crate::color_cmds::document_profile(doc) } else { crate::color_cmds::composite_profile(doc) };
    let transform = Transform::new(&profile, Builtin::Srgb.profile(), Intent::RelativeColorimetric, true).map_err(error)?;
    for y in 0..h {
        ctx.check()?;
        let sy = area.y0 + (y * step + step / 2).min(area.height() - 1) as i32;
        let row = Rect::new(area.x0, sy, area.x1, sy + 1);
        let (pixels, stride) = match surface {
            Some(surface) => (surface.read_region(row), surface.channels()),
            None => (photocraft_compose::render(doc, row).px.into_iter().flatten().collect(), 4),
        };
        if stride < transform.inputs() || stride > 5 {
            return Err(error("Invalid source color channels"));
        }
        let mut rgb = vec![0.0f32; area.width() as usize * 4];
        transform.convert_f32(&pixels, stride, &mut rgb, 4, stride > transform.inputs());
        for x in 0..w {
            let sx = (x * step + step / 2).min(area.width() - 1) as usize;
            let at = ((y * w + x) * 4) as usize;
            if let (Some(source), Some(dest)) = (rgb.get(sx * 4..sx * 4 + 4), rgba.get_mut(at..at + 4)) {
                dest.copy_from_slice(source);
                if stride == transform.inputs()
                    && let Some(alpha) = dest.last_mut()
                {
                    *alpha = 1.0;
                }
            }
        }
    }
    Image::from_normalized(w, h, ChannelLayout::Rgba, SampleType::U8, &rgba).map_err(error)
}

fn region(mask: &Image, bounds: Rect) -> Result<Option<Region>> {
    if bounds.is_empty() || u64::from(bounds.width()) * u64::from(bounds.height()) > 64 << 20 || mask.width() == 0 || mask.height() == 0 {
        return Err(error("Invalid segmentation mask dimensions"));
    }
    if mask.layout() != ChannelLayout::Gray {
        return Err(error("The segmenter must return a grayscale mask"));
    }
    let mut pixels = Vec::with_capacity(bounds.width() as usize * bounds.height() as usize);
    let mut nonempty = false;
    for y in 0..bounds.height() {
        let sy = (f64::from(y) + 0.5) * f64::from(mask.height()) / f64::from(bounds.height()) - 0.5;
        let y0 = sy.max(0.0).floor() as u32;
        let y1 = (y0 + 1).min(mask.height() - 1);
        let fy = sy.max(0.0).fract() as f32;
        for x in 0..bounds.width() {
            let sx = (f64::from(x) + 0.5) * f64::from(mask.width()) / f64::from(bounds.width()) - 0.5;
            let x0 = sx.max(0.0).floor() as u32;
            let x1 = (x0 + 1).min(mask.width() - 1);
            let fx = sx.max(0.0).fract() as f32;
            let a = mask.get(x0, y0, 0) * (1.0 - fx) + mask.get(x1, y0, 0) * fx;
            let b = mask.get(x0, y1, 0) * (1.0 - fx) + mask.get(x1, y1, 0) * fx;
            let value = ((a * (1.0 - fy) + b * fy).clamp(0.0, 1.0) * 255.0).round() as u8;
            nonempty |= value > 0;
            pixels.push(value);
        }
    }
    Ok(nonempty.then_some(Region { bbox: bounds, mask: pixels }))
}

#[cfg(not(target_arch = "wasm32"))]
fn predict(settings: &crate::genstudio::Settings, image: Option<&Image>, kind: &str, ctx: &JobCtx) -> Result<Option<Image>> {
    if std::env::var_os("GENSTUDIO_NO_PROCESS").is_some_and(|v| v == "1") {
        return Err(error("Model launch is disabled for validation"));
    }
    let folder = tempfile::Builder::new().prefix("photocraft-segmentation-").tempdir().map_err(error)?;
    if let Some(image) = image {
        std::fs::write(folder.path().join("input.png"), crate::genstudio_generate::png(image)?).map_err(error)?;
    }
    crate::genstudio_edit::transport(settings, folder.path(), &json!({"settings":settings,"segmentation":{"kind":kind}}), ctx)?;
    image.map(|image| crate::genstudio_edit::read_png(&folder.path().join("result-mask.png"), image.dimensions())).transpose()
}
#[cfg(target_arch = "wasm32")]
fn predict(_: &crate::genstudio::Settings, _: Option<&Image>, _: &str, _: &JobCtx) -> Result<Option<Image>> {
    Err(error("Local segmentation requires the desktop app"))
}

pub fn run(s: &mut Session, p: &Value, kind: &'static str) -> Result<Value> {
    let settings = s.prefs().gen_studio.clone();
    settings.segmentation.validate().map_err(error)?;
    if !settings.segmentation.enabled {
        return Err(error("Enable AI segmentation in GenGate Settings first"));
    }
    let st = s.active().ok_or(EngineError::NoDocument)?;
    let area = area(p, st.doc.bounds(), kind)?;
    if area.is_empty() || area.width().max(area.height()) > 32768 || u64::from(area.width()) * u64::from(area.height()) > 64 << 20 {
        return Err(error("Choose a nonempty region up to 64 megapixels"));
    }
    let target = if kind == "background" { Some(crate::commands::layer_param(s, p)?) } else { st.active_layer };
    let all = p.get("sampleAllLayers").and_then(Value::as_bool).unwrap_or(kind != "background" && kind != "object");
    let m = p.get("mode").and_then(Value::as_str).unwrap_or("replace");
    if !matches!(m, "replace" | "new" | "add" | "subtract" | "intersect") {
        return Err(error("Unknown selection mode"));
    }
    let mode = SelectionMode::parse(m);
    let label = match kind {
        "sky" => "AI Select Sky",
        "background" => "AI Remove Background",
        "object" => "AI Object Selection",
        _ => "AI Select Subject",
    };
    let doc = st.doc.clone();
    crate::jobs::run(
        s,
        label,
        true,
        move |ctx| {
            ctx.progress(0.01, "Starting Direct segmentation...");
            let image = input(&doc, target, all, area, ctx)?;
            let mask = predict(&settings, Some(&image), kind, ctx)?.ok_or_else(|| error("The segmenter returned no mask"))?;
            ctx.check()?;
            region(&mask, area)
        },
        move |s, found| apply(s, found.as_ref(), target, kind, mode, label),
    )
}
fn apply(s: &mut Session, found: Option<&Region>, target: Option<LayerId>, kind: &str, mode: SelectionMode, label: &str) -> Result<Value> {
    let Some(found) = found else {
        if kind == "background" {
            return Err(error("No subject found; the layer was left unchanged"));
        }
        return Ok(json!({"changed":false,"selected":s.active().is_some_and(|d|d.doc.selection.is_some())}));
    };
    let selected = s.edit(label, |doc, _| {
        if kind == "background" {
            let id = target.ok_or_else(|| error("No layer selected"))?;
            crate::extra_cmds::background_to_layer_for_mask(doc, id);
            doc.layer_mut(id).ok_or(EngineError::NoLayer(id))?.mask =
                Some(LayerMask { surface: photocraft_algo::matting::region_surface(found), ..LayerMask::reveal_all() });
            doc.selection = None;
        } else {
            doc.selection = combine_region(doc.selection.as_ref(), Some(found), mode);
        }
        Ok(doc.selection.is_some())
    })?;
    let area = found.bbox;
    Ok(json!({"changed":true,"selected":selected,"bounds":[area.x0,area.y0,area.width(),area.height()],"layer":target.map(|id|id.0)}))
}

fn prepare(s: &mut Session, _: &Value) -> Result<Value> {
    let settings = s.prefs().gen_studio.clone();
    settings.segmentation.validate().map_err(error)?;
    crate::jobs::run(
        s,
        "Preparing segmentation models",
        false,
        move |ctx| {
            predict(&settings, None, "prepare", ctx)?;
            Ok(())
        },
        |_, _| Ok(json!({"ready":true})),
    )
}
pub fn specs() -> Vec<CommandSpec> {
    vec![CommandSpec {
        id: "gengate.segmentation.prepare",
        label: "Prepare segmentation models",
        menu: &[],
        shortcut: None,
        params: "{} -> {ready}",
        enabled: crate::commands::always,
        run: prepare,
        journal: false,
    }]
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn defaults_are_independent_and_backward_compatible() {
        let settings: crate::genstudio::Settings = serde_json::from_value(json!({})).unwrap();
        assert!(!settings.segmentation.enabled);
        assert_eq!(settings.segmentation.segmenter, "sam3");
        assert!(settings.segmentation.validate().is_ok());
    }
    #[test]
    fn rectangle_validation_and_clipping() {
        let bounds = Rect::new(0, 0, 20, 20);
        assert_eq!(area(&json!({"rect":[18,15,-8,12]}), bounds, "object").unwrap(), Rect::new(10, 15, 18, 20));
        for p in [json!({}), json!({"rect":[1,1,0,0]}), json!({"rect":[0,0,"bad",10]}), json!({"rect":[1e99,0,10,10]})] {
            assert!(area(&p, bounds, "object").is_err());
        }
    }
    #[test]
    fn masks_restore_roi_coordinates_and_keep_empty_results() {
        let mask = Image::from_u8(2, 2, ChannelLayout::Gray, vec![255, 0, 0, 0]).unwrap();
        let r = region(&mask, Rect::new(7, 9, 11, 13)).unwrap().unwrap();
        assert_eq!(r.bbox, Rect::new(7, 9, 11, 13));
        assert_eq!(r.mask.len(), 16);
        assert_eq!(r.mask[0], 255);
        assert_eq!(r.mask[15], 0);
        let empty = Image::from_u8(1, 1, ChannelLayout::Gray, vec![0]).unwrap();
        assert!(region(&empty, r.bbox).unwrap().is_none());
    }
    #[test]
    fn source_capture_is_color_and_depth_aware() {
        for depth in [8, 16, 32] {
            let mut s = Session::new();
            s.execute("file.new", json!({"width":12,"height":10,"depth":depth})).unwrap();
            let st = s.active().unwrap();
            let image = input(&st.doc, st.active_layer, false, st.doc.bounds(), &JobCtx::default()).unwrap();
            assert_eq!(image.dimensions(), (12, 10));
            assert_eq!(image.get(0, 0, 3), 1.0);
        }
    }
    #[test]
    fn masks_apply_with_modifiers_and_background_is_nondestructive() {
        for depth in [8, 16, 32] {
            let mut s = Session::new();
            s.execute("file.new", json!({"width":12,"height":10,"depth":depth})).unwrap();
            let id = s.active().unwrap().active_layer;
            let a = Region { bbox: Rect::new(1, 2, 5, 6), mask: vec![255; 16] };
            let b = Region { bbox: Rect::new(3, 2, 7, 6), mask: vec![255; 16] };
            apply(&mut s, Some(&a), id, "object", SelectionMode::Replace, "AI Object Selection").unwrap();
            apply(&mut s, Some(&b), id, "object", SelectionMode::Subtract, "AI Object Selection").unwrap();
            let selected = s.active().unwrap().doc.selection.as_ref().unwrap();
            assert_eq!(selected.rgba(1, 3)[0], 1.0);
            assert_eq!(selected.rgba(4, 3)[0], 0.0);
            let before = s.active().unwrap().history.past_len();
            apply(&mut s, None, id, "object", SelectionMode::Replace, "AI Object Selection").unwrap();
            assert_eq!(s.active().unwrap().history.past_len(), before, "nothing found creates no undo step");
            let pixels = s.active().unwrap().doc.layer(id.unwrap()).unwrap().surface().unwrap().read_region(Rect::new(0, 0, 12, 10));
            apply(&mut s, Some(&a), id, "background", SelectionMode::Replace, "AI Remove Background").unwrap();
            let layer = s.active().unwrap().doc.layer(id.unwrap()).unwrap();
            assert_eq!(layer.surface().unwrap().read_region(Rect::new(0, 0, 12, 10)), pixels);
            assert_eq!(layer.mask.as_ref().unwrap().value(1, 3), 1.0);
            assert_eq!(layer.mask.as_ref().unwrap().value(0, 0), 0.0);
            s.execute("edit.undo", json!({})).unwrap();
            assert!(s.active().unwrap().doc.layer(id.unwrap()).unwrap().mask.is_none());
            s.execute("edit.redo", json!({})).unwrap();
            assert!(s.active().unwrap().doc.layer(id.unwrap()).unwrap().mask.is_some());
        }
    }
}
