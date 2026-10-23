//! Full-image generation and bounded reference preparation, independent of documents.
use crate::{EngineError, Result, Session, commands::CommandSpec};
use base64::Engine as _;
use photocraft_cms::{Builtin, Intent, Transform};
use photocraft_codecs::{ChannelLayout, DecodeOptions, EncodeOptions, Format, Image, Limits, SampleType};
use serde_json::{Value, json};

fn error(e: impl std::fmt::Display) -> EngineError {
    EngineError::Other(format!("GenGate: {e}"))
}
pub fn png(image: &Image) -> Result<Vec<u8>> {
    photocraft_codecs::encode(image, Format::Png, &EncodeOptions::default()).map_err(error)
}
pub fn encoded(image: &Image) -> Result<String> {
    Ok(base64::engine::general_purpose::STANDARD.encode(png(image)?))
}

/// A bilinear preview/reference resize. Work is bounded to the chosen output.
pub fn resized(image: &Image, side: u32) -> Result<Image> {
    let image = image.convert(ChannelLayout::Rgba, SampleType::U8);
    if image.width().max(image.height()) <= side {
        return Ok(image);
    }
    let scale = side as f64 / f64::from(image.width().max(image.height()));
    let w = (f64::from(image.width()) * scale).round().max(1.0) as u32;
    let h = (f64::from(image.height()) * scale).round().max(1.0) as u32;
    let mut data = Vec::with_capacity((w * h * 4) as usize);
    for y in 0..h {
        for x in 0..w {
            let sx = ((f64::from(x) + 0.5) / scale - 0.5).max(0.0);
            let sy = ((f64::from(y) + 0.5) / scale - 0.5).max(0.0);
            let x0 = (sx as u32).min(image.width() - 1);
            let y0 = (sy as u32).min(image.height() - 1);
            let x1 = (x0 + 1).min(image.width() - 1);
            let y1 = (y0 + 1).min(image.height() - 1);
            let fx = (sx - f64::from(x0)) as f32;
            let fy = (sy - f64::from(y0)) as f32;
            for channel in 0..4 {
                let a = image.get(x0, y0, channel) * (1.0 - fx) + image.get(x1, y0, channel) * fx;
                let b = image.get(x0, y1, channel) * (1.0 - fx) + image.get(x1, y1, channel) * fx;
                data.push(((a * (1.0 - fy) + b * fy).clamp(0.0, 1.0) * 255.0).round() as u8);
            }
        }
    }
    Image::from_u8(w, h, ChannelLayout::Rgba, data).map_err(error)
}

pub fn prepare_reference(bytes: &[u8]) -> Result<Image> {
    if bytes.len() > 64 << 20 {
        return Err(error("Reference files must be smaller than 64 MB"));
    }
    let image = photocraft_codecs::decode_with(
        bytes,
        &DecodeOptions { limits: Limits { max_width: 16384, max_height: 16384, max_pixels: 32 << 20, max_alloc: 512 << 20 }, ..Default::default() },
    )
    .map_err(error)?;
    if image.width() == 0 || image.height() == 0 {
        return Err(error("The reference is empty"));
    }
    // Reduce in the original channel model before allocating float ICC buffers.
    // A large CMYK TIFF must not create several full-resolution float copies.
    let image = if image.width().max(image.height()) > 1024 {
        let scale = 1024.0 / f64::from(image.width().max(image.height()));
        let w = (f64::from(image.width()) * scale).round().max(1.0) as u32;
        let h = (f64::from(image.height()) * scale).round().max(1.0) as u32;
        let mut data = Vec::with_capacity((w * h) as usize * image.layout().channels());
        for y in 0..h {
            for x in 0..w {
                let sx = ((f64::from(x) + 0.5) / scale - 0.5).max(0.0);
                let sy = ((f64::from(y) + 0.5) / scale - 0.5).max(0.0);
                let x0 = (sx as u32).min(image.width() - 1);
                let y0 = (sy as u32).min(image.height() - 1);
                let fx = (sx - f64::from(x0)) as f32;
                let fy = (sy - f64::from(y0)) as f32;
                for c in 0..image.layout().channels() {
                    let a = image.get(x0, y0, c) * (1.0 - fx) + image.get((x0 + 1).min(image.width() - 1), y0, c) * fx;
                    let b = image.get(x0, (y0 + 1).min(image.height() - 1), c) * (1.0 - fx)
                        + image.get((x0 + 1).min(image.width() - 1), (y0 + 1).min(image.height() - 1), c) * fx;
                    data.push(a * (1.0 - fy) + b * fy);
                }
            }
        }
        Image::from_normalized(w, h, image.layout(), SampleType::F32, &data).map_err(error)?.with_icc(image.icc.clone())
    } else {
        image
    };
    // Image conversion alone does not convert an embedded RGB profile.
    let image = if let Some(profile) = &image.icc {
        let profile = photocraft_cms::Profile::parse(profile).map_err(error)?;
        let transform = Transform::new(&profile, Builtin::Srgb.profile(), Intent::RelativeColorimetric, true).map_err(error)?;
        if transform.inputs() != image.layout().color_channels() {
            return Err(error("The image profile does not match its color channels"));
        }
        let source = image.to_normalized();
        let stride = image.layout().channels();
        let mut pixels = vec![0.0; image.pixel_count() * 4];
        transform.convert_f32(&source, stride, &mut pixels, 4, image.layout().has_alpha());
        if !image.layout().has_alpha() {
            for pixel in pixels.as_chunks_mut::<4>().0 {
                if let Some(alpha) = pixel.last_mut() {
                    *alpha = 1.0;
                }
            }
        }
        Image::from_normalized(image.width(), image.height(), ChannelLayout::Rgba, SampleType::U8, &pixels).map_err(error)?
    } else {
        image.convert(ChannelLayout::Rgba, SampleType::U8)
    };
    resized(&image, 1024)
}

fn capture(s: &mut Session, _: &Value) -> Result<Value> {
    let doc = s.active().ok_or(EngineError::NoDocument)?.doc.clone();
    let bounds = crate::genstudio_ai::selected_bounds(s);
    if bounds.is_empty() {
        return Err(error("Make a selection first"));
    }
    if u64::from(bounds.width()) * u64::from(bounds.height()) > 16 << 20 {
        return Err(error("Use a selection smaller than 16 million pixels"));
    }
    crate::jobs::run(
        s,
        "Capture reference",
        false,
        move |ctx| {
            ctx.check()?;
            let pixels: Vec<f32> = photocraft_compose::render(&doc, bounds).px.into_iter().flatten().collect();
            let transform =
                Transform::new(&crate::color_cmds::composite_profile(&doc), Builtin::Srgb.profile(), Intent::RelativeColorimetric, true).map_err(error)?;
            let mut rgba = vec![0.0; pixels.len()];
            transform.convert_f32(&pixels, 4, &mut rgba, 4, true);
            let selection = doc.selection.as_ref().ok_or_else(|| error("The selection is missing"))?;
            let coverage = selection.read_region(bounds);
            let channels = selection.channels();
            for (pixel, mask) in rgba.as_chunks_mut::<4>().0.iter_mut().zip(coverage.chunks_exact(channels)) {
                if let Some(alpha) = pixel.last_mut() {
                    *alpha *= mask.first().copied().unwrap_or(0.0).clamp(0.0, 1.0);
                }
            }
            ctx.check()?;
            let image = Image::from_normalized(bounds.width(), bounds.height(), ChannelLayout::Rgba, SampleType::U8, &rgba).map_err(error)?;
            encoded(&resized(&image, 1024)?)
        },
        |_, image| Ok(json!({"image":image,"name":"Selection"})),
    )
}

fn validate(p: &Value) -> Result<()> {
    let prompt = p["prompt"].as_str().ok_or_else(|| error("Enter a prompt"))?;
    if prompt.trim().is_empty() || prompt.len() > 12000 || prompt.contains('\0') {
        return Err(error("Enter a prompt up to 12000 UTF-8 bytes"));
    }
    let refs = p["references"].as_array().ok_or_else(|| error("References must be an array"))?;
    if refs.len() > 10
        || refs.iter().any(|v| v.as_str().is_none_or(|s| s.len() > 24 << 20))
        || refs.iter().map(|v| v.as_str().map_or(0, str::len)).sum::<usize>() > 64 << 20
    {
        return Err(error("Too many or oversized reference images"));
    }
    let width = p["width"].as_u64().ok_or_else(|| error("Invalid width"))?;
    let height = p["height"].as_u64().ok_or_else(|| error("Invalid height"))?;
    if !(256..=2048).contains(&width) || !(256..=2048).contains(&height) || width % 32 != 0 || height % 32 != 0 {
        return Err(error("Use dimensions from 256 to 2048, in multiples of 32"));
    }
    if !p["batch"].as_u64().is_some_and(|n| (1..=8).contains(&n)) {
        return Err(error("Batch size must be between 1 and 8"));
    }
    Ok(())
}

const PROMPT_BLOCK: [u8; 4] = *b"gsST";
#[derive(Clone, Debug, serde::Serialize, serde::Deserialize)]
pub struct Generation {
    pub version: u8,
    pub prompt: String,
    pub compiled_prompt: String,
    pub model: String,
    pub width: u32,
    pub height: u32,
    #[serde(default)]
    pub loras: Vec<crate::genstudio_lora::Selection>,
}
pub fn layer_generation(layer: &photocraft_doc::Layer) -> Result<Option<Generation>> {
    let Some((_, bytes)) = layer.psd_blocks.iter().find(|(key, _)| *key == PROMPT_BLOCK) else { return Ok(None) };
    if bytes.len() > 256 << 10 {
        return Err(error("Generation metadata is too large"));
    }
    let mut bytes = bytes.as_slice();
    for _ in 0..3 {
        if let Some(tail) = bytes.strip_suffix(&[0]) {
            bytes = tail;
        } else {
            break;
        }
    }
    let value: Generation = serde_json::from_slice(bytes).map_err(error)?;
    crate::genstudio_lora::validate(&value.loras).map_err(error)?;
    if value.version != 1
        || value.prompt.len() > 12000
        || value.compiled_prompt.len() > 12000
        || value.width > 2048
        || value.height > 2048
        || value.width == 0
        || value.height == 0
    {
        return Err(error("Invalid generation metadata"));
    }
    Ok(Some(value))
}
struct Prepared {
    layers: Vec<photocraft_doc::Layer>,
    template: photocraft_doc::Document,
}
fn prepare_results(images: Vec<Vec<u8>>, template: photocraft_doc::Document, metadata: &Generation) -> Result<Prepared> {
    use photocraft_color::PixelFormat;
    use photocraft_doc::{Layer, LayerContent};
    use std::sync::Arc;
    let mut layers = Vec::new();
    let block = Arc::new(serde_json::to_vec(metadata).map_err(error)?);
    for bytes in images {
        let image = photocraft_codecs::decode_with(
            &bytes,
            &DecodeOptions { limits: Limits { max_width: 2048, max_height: 2048, max_pixels: 2048 * 2048, max_alloc: 64 << 20 }, ..Default::default() },
        )
        .map_err(error)?;
        if image.dimensions() != (metadata.width, metadata.height) {
            return Err(error("The engine returned the wrong image dimensions"));
        }
        let rect = photocraft_geom::Rect::new(0, 0, metadata.width as i32, metadata.height as i32);
        let surface = crate::genstudio_ai::native(&template, &image, rect)?;
        let fmt = template.pixel_format();
        let mut layer = Layer::raster("GenGate", PixelFormat::new(fmt.mode, fmt.sample, true));
        layer.content = LayerContent::Raster(surface);
        layer.psd_blocks.push((PROMPT_BLOCK, block.clone()));
        layers.push(layer);
    }
    if layers.is_empty() || layers.len() > 8 {
        return Err(error("The engine returned an invalid batch"));
    }
    Ok(Prepared { layers, template })
}
fn generate(s: &mut Session, p: &Value) -> Result<Value> {
    validate(p)?;
    let settings = s.prefs().gen_studio.clone();
    settings.validate().map_err(error)?;
    let loras: Vec<crate::genstudio_lora::Selection> = serde_json::from_value(p.get("loras").cloned().unwrap_or_else(|| json!([]))).map_err(error)?;
    crate::genstudio_lora::validate(&loras).map_err(error)?;
    let spec = crate::genstudio::model_spec(&settings.model).ok_or_else(|| error("Choose a supported preset"))?;
    let reference_count = p["references"].as_array().map_or(0, Vec::len);
    if reference_count > spec.reference_limit || (settings.model == "fibo-2-edit-turbo" && reference_count == 0) {
        return Err(error("FIBO Edit Turbo needs 1 to 4 references; use a compatible reference count for the selected preset"));
    }
    let source_prompt = p
        .get("sourcePrompt")
        .unwrap_or(&p["prompt"])
        .as_str()
        .filter(|v| v.len() <= 12000 && !v.contains('\0'))
        .ok_or_else(|| error("Invalid source prompt"))?
        .to_owned();
    let metadata = Generation {
        version: 1,
        prompt: source_prompt,
        compiled_prompt: p["prompt"].as_str().unwrap_or_default().to_owned(),
        model: settings.model.clone(),
        width: p["width"].as_u64().unwrap_or(1024) as u32,
        height: p["height"].as_u64().unwrap_or(1024) as u32,
        loras,
    };
    let target = s.active().map(|st| st.doc.id);
    let template = s.active().map(|st| (*st.doc).clone()).unwrap_or_else(|| {
        photocraft_doc::Document::new(
            "GenGate",
            photocraft_geom::Size::new(metadata.width, metadata.height),
            photocraft_color::ColorMode::Rgb,
            photocraft_color::SampleType::U8,
        )
    });
    let payload = p.clone();
    crate::jobs::run(
        s,
        "GenGate",
        false,
        move |ctx| {
            let images = crate::genstudio_edit::studio(&settings, &payload, ctx)?;
            ctx.check()?;
            prepare_results(images, template, &metadata)
        },
        move |s, prepared| import_results(s, target, prepared),
    )
}
fn import_results(s: &mut Session, target: Option<photocraft_doc::DocId>, prepared: Prepared) -> Result<Value> {
    let Prepared { layers, template } = prepared;
    let count = layers.len();
    let index = target.and_then(|id| s.documents().iter().position(|st| st.doc.id == id));
    let previous = s.active().map(|st| st.doc.id);
    let (index, ids, created) = if let Some(index) = index {
        let current = s.documents().get(index).ok_or(crate::EngineError::NoDocument)?;
        if current.doc.mode != template.mode || current.doc.depth != template.depth || current.doc.icc_profile != template.icc_profile {
            return Err(error("The document colour format changed during generation"));
        }
        s.set_active(index);
        let applied = s.edit("GenGate", move |doc, active| {
            let mut ids = Vec::new();
            for mut layer in layers {
                layer.name = doc.next_layer_name("GenGate");
                let id = doc.insert_above(*active, layer);
                *active = Some(id);
                ids.push(id.0);
            }
            Ok(ids)
        });
        if let Some(previous) = previous
            && let Some(i) = s.documents().iter().position(|st| st.doc.id == previous)
        {
            s.set_active(i);
        }
        (index, applied?, false)
    } else {
        let metadata = layers.first().map(layer_generation).transpose()?.flatten().ok_or_else(|| error("Generation metadata is missing"))?;
        let mut doc = photocraft_doc::Document::new("GenGate", photocraft_geom::Size::new(metadata.width, metadata.height), template.mode, template.depth);
        doc.icc_profile = template.icc_profile;
        let ids = layers.iter().map(|layer| layer.id.0).collect::<Vec<_>>();
        for mut layer in layers {
            layer.name = doc.next_layer_name("GenGate");
            doc.layers.push(layer);
        }
        let index = s.add_document(doc, None);
        if let Some(st) = s.active_mut() {
            st.saved_revision = st.revision.saturating_sub(1);
        }
        (index, ids, true)
    };
    let id = s.documents().get(index).ok_or(crate::EngineError::NoDocument)?.doc.id.0;
    Ok(json!({"document":index,"documentId":id,"layers":ids,"count":count,"createdDocument":created}))
}
fn layer_source(s: &mut Session, p: &Value) -> Result<Value> {
    let st = if let Some(id) = p.get("documentId") { s.documents().iter().find(|st| id.as_u64() == Some(st.doc.id.0)) } else { s.active() }
        .ok_or(EngineError::NoDocument)?;
    let id = p.get("layer").and_then(Value::as_u64).map(photocraft_doc::LayerId).or(st.active_layer).ok_or_else(|| error("Choose a layer"))?;
    let layer = st.doc.layer(id).ok_or(EngineError::NoLayer(id))?;
    let generation = layer_generation(layer)?;
    let prompt = if let Some(ref meta) = generation { meta.prompt.clone() }
        else if crate::genstudio_ai::is_layer(layer) { crate::genstudio_ai::gallery(layer)?.prompt }
        else { String::new() };
    let name = layer.name.clone();
    let mut source = (*st.doc).clone();
    let mut layer = layer.clone();
    layer.visible = true;
    source.layers = vec![layer];
    let rect = photocraft_compose::layer_bounds(source.layers.first().ok_or_else(|| error("Layer is missing"))?, source.bounds()).intersect(&source.bounds());
    let rect = if rect.is_empty() { source.bounds() } else { rect };
    if u64::from(rect.width()) * u64::from(rect.height()) > 32 << 20 { return Err(error("Use a layer smaller than 32 million pixels")); }
    crate::jobs::run(s, "Prepare layer for GenGate", false,
        move |ctx| {
            ctx.check()?;
            let image = photocraft_compose::render(&source, rect);
            let pixels: Vec<f32> = image.px.into_iter().flatten().collect();
            let transform = Transform::new(&crate::color_cmds::composite_profile(&source), Builtin::Srgb.profile(), Intent::RelativeColorimetric, true).map_err(error)?;
            let mut rgba = vec![0.0; pixels.len()];
            transform.convert_f32(&pixels, 4, &mut rgba, 4, true);
            ctx.check()?;
            let image = Image::from_normalized(rect.width(), rect.height(), ChannelLayout::Rgba, SampleType::U8, &rgba).map_err(error)?;
            encoded(&resized(&image, 1024)?)
        }, move |_, image| Ok(json!({"image":image,"name":name,"source":"layer","prompt":prompt,"generation":generation})))
}

fn generated_layer<'a>(s: &'a Session, p: &Value) -> Result<(&'a photocraft_doc::Document, &'a photocraft_doc::Layer, Generation)> {
    let st = if let Some(id) = p.get("documentId") { s.documents().iter().find(|st| id.as_u64() == Some(st.doc.id.0)) } else { s.active() }
        .ok_or(crate::EngineError::NoDocument)?;
    let id = p.get("layer").and_then(Value::as_u64).map(photocraft_doc::LayerId).or(st.active_layer).ok_or_else(|| error("Choose a generated layer"))?;
    let layer = st.doc.layer(id).ok_or(crate::EngineError::NoLayer(id))?;
    let metadata = layer_generation(layer)?.ok_or_else(|| error("Choose a GenGate layer"))?;
    Ok((&st.doc, layer, metadata))
}
fn layer_info(s: &mut Session, p: &Value) -> Result<Value> {
    let (_, layer, metadata) = generated_layer(s, p)?;
    Ok(json!({"layer":layer.id.0,"generation":metadata}))
}
fn result_reference(s: &mut Session, p: &Value) -> Result<Value> {
    let (doc, layer, metadata) = generated_layer(s, p)?;
    let mut source = doc.clone();
    source.layers = vec![layer.clone()];
    let rect = photocraft_geom::Rect::new(0, 0, metadata.width as i32, metadata.height as i32);
    crate::jobs::run(
        s,
        "Prepare generated reference",
        false,
        move |ctx| {
            ctx.check()?;
            let pixels: Vec<f32> = photocraft_compose::render(&source, rect).px.into_iter().flatten().collect();
            let transform =
                Transform::new(&crate::color_cmds::composite_profile(&source), Builtin::Srgb.profile(), Intent::RelativeColorimetric, true).map_err(error)?;
            let mut rgba = vec![0.0; pixels.len()];
            transform.convert_f32(&pixels, 4, &mut rgba, 4, true);
            let image = Image::from_normalized(rect.width(), rect.height(), ChannelLayout::Rgba, SampleType::U8, &rgba).map_err(error)?;
            encoded(&resized(&image, 1024)?)
        },
        |_, image| Ok(json!({"image":image,"name":"Generated image","source":"generated"})),
    )
}

pub fn specs() -> Vec<CommandSpec> {
    vec![
        CommandSpec { id: "genstudio.studio.layer.source", label: "Layer to GenGate", menu: &[], shortcut: None,
            params: "{documentId?,layer?}", enabled: crate::commands::always, run: layer_source, journal: false },
        CommandSpec {
            id: "genstudio.studio.layer.inspect",
            label: "Generation prompt",
            menu: &[],
            shortcut: None,
            params: "{documentId?,layer?}",
            enabled: crate::commands::always,
            run: layer_info,
            journal: false,
        },
        CommandSpec {
            id: "genstudio.studio.result.reference",
            label: "Generated reference",
            menu: &[],
            shortcut: None,
            params: "{documentId,layer}",
            enabled: crate::commands::always,
            run: result_reference,
            journal: false,
        },
        CommandSpec {
            id: "genstudio.reference.capture",
            label: "Capture reference",
            menu: &[],
            shortcut: None,
            params: "{}",
            enabled: crate::commands::always,
            run: capture,
            journal: false,
        },
        CommandSpec {
            id: "genstudio.studio.generate",
            label: "GenGate",
            menu: &[],
            shortcut: None,
            params: "{prompt,references:[PNG base64]=[],width:1024,height:1024,batch:1..8}",
            enabled: crate::commands::always,
            run: generate,
            journal: false,
        },
    ]
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn invalid_full_generation_requests_do_not_touch_the_document() {
        let mut s = Session::new();
        for p in [
            json!({}),
            json!({"prompt":"x","references":[],"width":2049,"height":1024,"batch":1}),
            json!({"prompt":"x","references":[],"width":1024,"height":1024,"batch":0}),
        ] {
            assert!(s.execute("genstudio.studio.generate", p).is_err());
        }
        assert!(s.active().is_none());
    }
    #[test]
    fn references_are_bounded_and_alpha_is_preserved() {
        let image = Image::from_u8(2048, 32, ChannelLayout::Rgba, [20, 80, 120, 128].repeat(2048 * 32)).unwrap();
        let prepared = prepare_reference(&png(&image).unwrap()).unwrap();
        assert_eq!(prepared.dimensions(), (1024, 16));
        assert_eq!(prepared.data()[3], 128);
        assert!(prepare_reference(b"not an image").is_err());
    }
    #[test]
    fn generated_layers_keep_prompts_undo_and_round_trip() {
        let mut s = Session::new();
        let meta = Generation {
            version: 1,
            prompt: "A quiet forest".into(),
            compiled_prompt: "A quiet forest".into(),
            model: "qwen-image-2.1-turbo".into(),
            width: 8,
            height: 8,
            loras: vec![crate::genstudio_lora::Selection { path: "style/example.safetensors".into(), strength: 0.65 }],
        };
        let image = Image::from_u8(8, 8, ChannelLayout::Rgba, [20, 80, 120, 255].repeat(64)).unwrap();
        let template =
            photocraft_doc::Document::new("GenGate", photocraft_geom::Size::new(8, 8), photocraft_color::ColorMode::Rgb, photocraft_color::SampleType::U8);
        let ready = prepare_results(vec![png(&image).unwrap()], template, &meta).unwrap();
        let result = import_results(&mut s, None, ready).unwrap();
        assert_eq!(s.documents().len(), 1);
        assert_eq!(s.active().unwrap().doc.layers.len(), 1);
        assert!(s.active().unwrap().is_dirty());
        let id = s.active().unwrap().doc.id;
        let template = (*s.active().unwrap().doc).clone();
        let ready = prepare_results(vec![png(&image).unwrap(), png(&image).unwrap()], template, &meta).unwrap();
        import_results(&mut s, Some(id), ready).unwrap();
        assert_eq!(s.documents().len(), 1);
        assert_eq!(s.active().unwrap().doc.layers.len(), 3);
        assert_eq!(layer_info(&mut s, &json!({})).unwrap()["generation"]["prompt"], meta.prompt);
        s.execute("edit.undo", json!({})).unwrap();
        assert_eq!(s.active().unwrap().doc.layers.len(), 1);
        s.execute("edit.redo", json!({})).unwrap();
        assert_eq!(s.active().unwrap().doc.layers.len(), 3);
        let doc = &s.active().unwrap().doc;
        let bytes = photocraft_format::save_to_bytes(doc, &Default::default()).unwrap();
        let loaded = photocraft_format::load_from_bytes(&bytes).unwrap();
        assert_eq!(layer_generation(loaded.layers.last().unwrap()).unwrap().unwrap().prompt, meta.prompt);
        assert_eq!(layer_generation(loaded.layers.last().unwrap()).unwrap().unwrap().loras, meta.loras);
        let exported = photocraft_io::export(doc, "psd", &Default::default()).unwrap();
        let psd = photocraft_io::import("generated.psd", &exported.bytes).unwrap().document;
        assert_eq!(layer_generation(psd.layers.last().unwrap()).unwrap().unwrap().prompt, meta.prompt);
        assert_eq!(layer_generation(psd.layers.last().unwrap()).unwrap().unwrap().loras, meta.loras);
        assert_eq!(result["createdDocument"], true);
    }
    #[test]
    fn completion_targets_the_original_document_after_a_tab_switch() {
        let mut s = Session::new();
        s.execute("file.new", json!({"width":16,"height":16})).unwrap();
        let target = s.active().unwrap().doc.id;
        let template = (*s.active().unwrap().doc).clone();
        let meta = Generation {
            version: 1,
            prompt: "Original tab".into(),
            compiled_prompt: "Original tab".into(),
            model: "qwen-image-2.1-turbo".into(),
            width: 8,
            height: 8,
            loras: Vec::new(),
        };
        let image = Image::from_u8(8, 8, ChannelLayout::Rgba, [20, 80, 120, 255].repeat(64)).unwrap();
        let prepared = prepare_results(vec![png(&image).unwrap()], template, &meta).unwrap();
        s.execute("file.new", json!({"width":16,"height":16})).unwrap();
        let other = s.active().unwrap().doc.id;
        import_results(&mut s, Some(target), prepared).unwrap();
        assert_eq!(s.active().unwrap().doc.id, other);
        let original = s.documents().iter().find(|st| st.doc.id == target).unwrap();
        assert_eq!(original.doc.layers.len(), 2);
        assert_eq!(layer_generation(original.doc.layers.last().unwrap()).unwrap().unwrap().prompt, "Original tab");
    }
}
