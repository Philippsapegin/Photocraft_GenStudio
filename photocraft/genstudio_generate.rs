//! Full-image generation and bounded reference preparation, independent of documents.
use crate::{EngineError, Result, Session, commands::CommandSpec};
use base64::Engine as _;
use photocraft_cms::{Builtin, Intent, Transform};
use photocraft_codecs::{ChannelLayout, DecodeOptions, EncodeOptions, Format, Image, Limits, SampleType};
use serde_json::{Value, json};

fn error(e: impl std::fmt::Display) -> EngineError {
    EngineError::Other(format!("Gen Studio: {e}"))
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

fn generate(s: &mut Session, p: &Value) -> Result<Value> {
    validate(p)?;
    let settings = s.prefs().gen_studio.clone();
    settings.validate().map_err(error)?;
    let payload = p.clone();
    crate::jobs::run(s, "Gen Studio", false, move |ctx| crate::genstudio_edit::studio(&settings, &payload, ctx), |s, images| import_results(s, &images))
}

fn import_results(s: &mut Session, images: &[Vec<u8>]) -> Result<Value> {
    let mut documents = Vec::new();
    for (index, image) in images.iter().enumerate() {
        documents.push(crate::file_cmds::open_bytes_as(s, &format!("Qwen generation {}.png", index + 1), image, None, None)?);
        // These pixels have no backing file. Closing them must offer Save.
        if let Some(document) = s.active_mut() {
            document.saved_revision = document.revision.saturating_sub(1);
        }
    }
    Ok(json!({"documents":documents,"count":images.len()}))
}

pub fn specs() -> Vec<CommandSpec> {
    vec![
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
            label: "Gen Studio",
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
    fn generated_documents_are_unsaved_and_keep_each_batch_result() {
        let mut s = Session::new();
        let image = Image::from_u8(8, 8, ChannelLayout::Rgba, [20, 80, 120, 255].repeat(64)).unwrap();
        let result = import_results(&mut s, &[png(&image).unwrap(), png(&image).unwrap()]).unwrap();
        assert_eq!(result["count"], 2);
        assert_eq!(s.documents().len(), 2);
        assert!(s.documents().iter().all(|doc| doc.path.is_none() && doc.is_dirty()));
    }
}
