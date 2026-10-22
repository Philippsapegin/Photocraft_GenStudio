//! Bounded file/JSON bridge for background image jobs. No model work on the UI thread.
use crate::genstudio::Settings;
use crate::jobs::JobCtx;
use crate::{EngineError, Result};
use photocraft_codecs::{ChannelLayout, Image, SampleType};
#[cfg(not(target_arch = "wasm32"))]
use photocraft_codecs::{DecodeOptions, EncodeOptions, Format, Limits};

fn err(e: impl std::fmt::Display) -> EngineError {
    EngineError::Other(format!("Ai Fill: {e}"))
}

/// Extend context to the model's pixel grid without moving or resampling it.
/// At canvas edges the Python transport pads the remaining outside border.
pub(crate) fn aligned_context(bounds: photocraft_geom::Rect, canvas: photocraft_geom::Rect, margin: i32) -> Result<photocraft_geom::Rect> {
    let rect = bounds.inflate(margin).intersect(&canvas);
    if rect.is_empty() { return Err(err("the edit rectangle is empty")); }
    let step = 32_i64;
    let axis = |start: i32, end: i32, lo: i32, hi: i32| -> Result<(i32, i32)> {
        let (start, end, lo, hi) = (i64::from(start), i64::from(end), i64::from(lo), i64::from(hi));
        let size = end - start;
        let target = (size + step - 1) / step * step;
        if target > hi - lo {
            return Ok((i32::try_from(lo).map_err(err)?, i32::try_from(hi).map_err(err)?));
        }
        let left = (start - (target - size) / 2).clamp(lo, hi - target);
        Ok((i32::try_from(left).map_err(err)?, i32::try_from(left + target).map_err(err)?))
    };
    let (x0, x1) = axis(rect.x0, rect.x1, canvas.x0, canvas.x1)?;
    let (y0, y1) = axis(rect.y0, rect.y1, canvas.y0, canvas.y1)?;
    Ok(photocraft_geom::Rect::new(x0, y0, x1, y1))
}

#[cfg(target_arch = "wasm32")]
pub(crate) fn generate(_: &Settings, _: &Image, _: &Image, _: &JobCtx) -> Result<(Image, Image)> {
    Err(err("the local AI engine requires the desktop app"))
}

#[cfg(target_arch = "wasm32")]
pub(crate) fn generate_prompt(_: &Settings, _: &Image, _: &Image, _: &JobCtx, _: &str) -> Result<(Image, Image)> {
    Err(err("the local AI engine requires the desktop app"))
}

#[cfg(not(target_arch = "wasm32"))]
pub(crate) fn generate(settings: &Settings, image: &Image, mask: &Image, ctx: &JobCtx) -> Result<(Image, Image)> {
    generate_request(settings, image, mask, ctx, None)
}

#[cfg(not(target_arch = "wasm32"))]
pub(crate) fn generate_prompt(settings: &Settings, image: &Image, mask: &Image, ctx: &JobCtx, prompt: &str) -> Result<(Image, Image)> {
    generate_request(settings, image, mask, ctx, Some(prompt))
}

#[cfg(not(target_arch = "wasm32"))]
fn generate_request(settings: &Settings, image: &Image, mask: &Image, ctx: &JobCtx, prompt: Option<&str>) -> Result<(Image, Image)> {
    use std::{fs, path::Path};
    if std::env::var_os("GENSTUDIO_NO_PROCESS").is_some_and(|v| v == "1") {
        return Err(err("engine process launch is disabled for validation"));
    }
    ctx.check()?;
    let root = Path::new(&settings.runtime_root);
    let python = root.join(".venv").join(if cfg!(windows) { "Scripts/python.exe" } else { "bin/python" });
    if !python.is_file() || !root.join("genstudio/edit.py").is_file() {
        return Err(err("GenGate runtime is missing; check its Advanced settings"));
    }
    let folder = tempfile::Builder::new().prefix("photocraft-qwen-").tempdir().map_err(err)?;
    for (name, data) in [("input.png", image), ("mask.png", mask)] {
        let bytes = photocraft_codecs::encode(data, Format::Png, &EncodeOptions::default()).map_err(err)?;
        fs::write(folder.path().join(name), bytes).map_err(err)?;
        ctx.check()?;
    }
    transport(settings, folder.path(), &serde_json::json!({"settings":settings,"prompt":prompt}), ctx)?;
    ctx.check()?;
    Ok((read_png(&folder.path().join("result.png"), image.dimensions())?, read_png(&folder.path().join("result-mask.png"), image.dimensions())?))
}

#[cfg(not(target_arch = "wasm32"))]
pub(crate) fn transport(settings: &Settings, folder: &std::path::Path, payload: &serde_json::Value, ctx: &JobCtx) -> Result<()> {
    use std::{
        fs,
        io::{BufRead, BufReader, Write},
        process::{Command, Stdio},
        sync::mpsc,
        time::Duration,
    };
    let errors = fs::File::create(folder.join("errors.log")).map_err(err)?;
    let root = std::path::Path::new(&settings.runtime_root);
    let python = root.join(".venv").join(if cfg!(windows) { "Scripts/python.exe" } else { "bin/python" });
    let mut command = Command::new(python);
    command
        .args(["-X", "utf8", "-m", "genstudio.edit", "--folder"])
        .arg(folder)
        .current_dir(root)
        .env_remove("PYTHONHOME")
        .env_remove("PYTHONPATH")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::from(errors));
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    let mut running = Child { child: command.spawn().map_err(err)?, folder: folder.to_owned() };
    let data = serde_json::to_vec(payload).map_err(err)?;
    running.child.stdin.take().ok_or_else(|| err("missing control input"))?.write_all(&data).map_err(err)?;
    let output = running.child.stdout.take().ok_or_else(|| err("missing control output"))?;
    let (tx, rx) = mpsc::channel();
    let _reader = std::thread::Builder::new()
        .name("qwen-remove-progress".into())
        .spawn(move || {
            for line in BufReader::new(output).lines() {
                if tx.send(line).is_err() {
                    break;
                }
            }
        })
        .map_err(err)?;
    let mut complete = false;
    let started = std::time::Instant::now();
    loop {
        ctx.check()?;
        if started.elapsed() > Duration::from_secs(8 * 1800 + 600) {
            return Err(err("generation timed out; the document is unchanged"));
        }
        match rx.recv_timeout(Duration::from_millis(100)) {
            Ok(line) => {
                let line = line.map_err(err)?;
                let event: serde_json::Value = serde_json::from_str(&line).map_err(err)?;
                let fraction = event
                    .get("progress")
                    .and_then(serde_json::Value::as_f64)
                    .filter(|v| v.is_finite())
                    .map_or(0.15, |v| 0.15 + 0.75 * v.clamp(0.0, 1.0) as f32);
                if let Some(message) = event.get("message").and_then(serde_json::Value::as_str) {
                    ctx.progress(fraction, message);
                }
                if let Some(message) = event.get("error").and_then(serde_json::Value::as_str) {
                    return Err(err(message));
                }
                complete |= event.get("done").and_then(serde_json::Value::as_bool).unwrap_or(false);
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {}
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                let status = running.child.try_wait().map_err(err)?;
                if let Some(status) = status {
                    if !status.success() || !complete {
                        let detail = fs::read_to_string(folder.join("errors.log")).unwrap_or_default();
                        return Err(err(format!("image transport ended unexpectedly: {}", detail.chars().take(2000).collect::<String>())));
                    }
                    break;
                }
                std::thread::sleep(Duration::from_millis(50));
            }
        }
    }
    ctx.check()?;
    Ok(())
}

#[cfg(target_arch = "wasm32")]
pub(crate) fn studio(_: &Settings, _: &serde_json::Value, _: &JobCtx) -> Result<Vec<Vec<u8>>> {
    Err(err("Local generation requires the desktop app"))
}

#[cfg(not(target_arch = "wasm32"))]
pub(crate) fn studio(settings: &Settings, payload: &serde_json::Value, ctx: &JobCtx) -> Result<Vec<Vec<u8>>> {
    use std::fs;
    if std::env::var_os("GENSTUDIO_NO_PROCESS").is_some_and(|v| v == "1") {
        return Err(err("Engine process launch is disabled for validation"));
    }
    let root = std::path::Path::new(&settings.runtime_root);
    if !root.join("genstudio/studio_edit.py").is_file() {
        return Err(err("The Studio runtime needs updating"));
    }
    let folder = tempfile::Builder::new().prefix("photocraft-studio-").tempdir().map_err(err)?;
    transport(settings, folder.path(), &serde_json::json!({"settings":settings,"studio":payload}), ctx)?;
    let count = payload["batch"].as_u64().unwrap_or(1).clamp(1, 8);
    let size = (payload["width"].as_u64().unwrap_or(1024) as u32, payload["height"].as_u64().unwrap_or(1024) as u32);
    let mut images = Vec::new();
    for index in 0..count {
        ctx.check()?;
        let path = folder.path().join(format!("result-{index}.png"));
        let image = read_png(&path, size)?;
        images.push(photocraft_codecs::encode(&image, Format::Png, &EncodeOptions::default()).map_err(err)?);
    }
    // Retain tempdir ownership until every image was validated and copied.
    let _ = fs::metadata(folder.path()).map_err(err)?;
    Ok(images)
}

#[cfg(not(target_arch = "wasm32"))]
struct Child {
    child: std::process::Child,
    folder: std::path::PathBuf,
}

#[cfg(not(target_arch = "wasm32"))]
impl Drop for Child {
    fn drop(&mut self) {
        if self.child.try_wait().ok().flatten().is_some() {
            return;
        }
        // Let Python cancel this host job before killing only its transport process.
        let _ = std::fs::write(self.folder.join("cancel"), []);
        for _ in 0..60 {
            if self.child.try_wait().ok().flatten().is_some() {
                return;
            }
            std::thread::sleep(std::time::Duration::from_millis(100));
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

#[cfg(not(target_arch = "wasm32"))]
pub(crate) fn read_png(path: &std::path::Path, size: (u32, u32)) -> Result<Image> {
    use std::io::Read;
    const MAX: u64 = 128 << 20;
    let file = std::fs::File::open(path).map_err(err)?;
    let mut bytes = Vec::new();
    file.take(MAX + 1).read_to_end(&mut bytes).map_err(err)?;
    if bytes.len() as u64 > MAX {
        return Err(err("result file is too large"));
    }
    let limits = Limits { max_width: size.0, max_height: size.1, max_pixels: u64::from(size.0) * u64::from(size.1), max_alloc: MAX };
    let image = photocraft_codecs::decode_as_with(Format::Png, &bytes, &DecodeOptions { limits, ..Default::default() }).map_err(err)?;
    if image.dimensions() != size {
        return Err(err("the result dimensions differ from the requested rectangle"));
    }
    Ok(image)
}

pub(crate) fn rgba(image: &Image) -> Image {
    image.convert(ChannelLayout::Rgba, SampleType::U8)
}

#[cfg(test)]
mod geometry_tests {
    use super::*;
    use photocraft_geom::Rect;

    #[test]
    fn context_grows_to_the_grid_without_moving_the_original_mask() {
        let canvas = Rect::new(0, 0, 4000, 3000);
        let bounds = Rect::new(101, 201, 864, 710);
        let aligned = aligned_context(bounds, canvas, 0).unwrap();
        assert_eq!((aligned.width(), aligned.height()), (768, 512));
        assert!(aligned.x0 <= bounds.x0 && aligned.y0 <= bounds.y0);
        assert!(aligned.x1 >= bounds.x1 && aligned.y1 >= bounds.y1);
    }

    #[test]
    fn edge_context_uses_existing_pixels_and_leaves_outside_padding_to_transport() {
        let canvas = Rect::new(0, 0, 4000, 3000);
        let bounds = Rect::new(3237, 2491, 4000, 3000);
        let aligned = aligned_context(bounds, canvas, 0).unwrap();
        assert_eq!(aligned, Rect::new(3232, 2488, 4000, 3000));
        let small = Rect::new(0, 0, 763, 509);
        assert_eq!(aligned_context(small, small, 256).unwrap(), small);
        assert!(aligned_context(Rect::EMPTY, canvas, 0).is_err());
    }
}
