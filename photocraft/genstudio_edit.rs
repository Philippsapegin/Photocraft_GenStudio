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
        return Err(err("Gen Studio runtime is missing; check its Advanced settings"));
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
fn transport(settings: &Settings, folder: &std::path::Path, payload: &serde_json::Value, ctx: &JobCtx) -> Result<()> {
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
fn read_png(path: &std::path::Path, size: (u32, u32)) -> Result<Image> {
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
