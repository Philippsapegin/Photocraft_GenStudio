//! Local GenGate engine commands. Inference runs outside the editor process.
use crate::{EngineError, Result, Session, commands::CommandSpec};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::sync::{Arc, Mutex};

#[derive(Clone, Copy)]
pub struct ModelSpec {
    pub id: &'static str,
    pub label: &'static str,
    pub reference_limit: usize,
    pub zero_based: bool,
}
impl ModelSpec {
    pub fn chip(self, ordinal: usize) -> String {
        if self.zero_based { format!("<ref_image_{}>", ordinal.saturating_sub(1)) } else { format!("[Image {ordinal}]") }
    }
    pub fn reference(self, ordinal: usize) -> String {
        if self.zero_based { self.chip(ordinal) } else { format!("image {ordinal}") }
    }
}
pub const MODELS: [ModelSpec; 2] = [
    ModelSpec { id: "qwen-image-2.1-turbo", label: "Qwen Image 2.1 Turbo", reference_limit: 10, zero_based: false },
    ModelSpec { id: "flux-3-image-klein", label: "FLUX 3 Image Klein", reference_limit: 10, zero_based: true },
];
pub fn model_spec(id: &str) -> Option<ModelSpec> {
    MODELS.into_iter().find(|m| m.id == id)
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub enum Engine {
    #[default]
    #[serde(rename = "comfy")]
    Comfy,
    #[serde(rename = "direct")]
    Direct,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub enum Startup {
    #[serde(rename = "off")]
    Off,
    #[serde(rename = "appStart")]
    AppStart,
    #[default]
    #[serde(rename = "firstRequest")]
    FirstRequest,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(default, rename_all = "camelCase", deny_unknown_fields)]
pub struct Settings {
    pub segmentation: crate::gengate_segmentation::Settings,
    pub model: String,
    pub text_encoder: String,
    pub vae: String,
    pub engine: Engine,
    pub comfy_startup: Startup,
    pub direct_startup: Startup,
    pub comfy_url: String,
    pub comfy_dir: String,
    pub comfy_python: String,
    pub direct_python: String,
    #[serde(alias = "directModel")]
    pub models_dir: String,
    pub runtime_root: String,
}

impl Default for Settings {
    fn default() -> Self {
        let compiled: Value = serde_json::from_str(include_str!("genstudio-defaults.json")).unwrap_or(Value::Null);
        // Release installs keep their runtime beside the editor. Never bake a
        // developer's filesystem paths into a distributed executable.
        #[cfg(not(target_arch = "wasm32"))]
        let installed =
            std::env::current_exe().ok().and_then(|exe| exe.parent().and_then(|dir| read_installed_defaults(&dir.join("runtime/config.photocraft.json"))));
        #[cfg(target_arch = "wasm32")]
        let installed: Option<Value> = None;
        let v = installed.unwrap_or(compiled);
        let string = |key: &str| v.get(key).and_then(Value::as_str).unwrap_or_default().to_owned();
        Self {
            segmentation: Default::default(),
            model: v.get("model").and_then(Value::as_str).unwrap_or("qwen-image-2.1-turbo").to_owned(),
            text_encoder: string("textEncoder"),
            vae: string("vae"),
            engine: if v.get("engine").and_then(Value::as_str) == Some("direct") { Engine::Direct } else { Engine::Comfy },
            comfy_startup: Startup::FirstRequest,
            direct_startup: Startup::FirstRequest,
            comfy_url: string("comfyUrl"),
            comfy_dir: string("comfyDir"),
            comfy_python: string("comfyPython"),
            direct_python: string("directPython"),
            models_dir: string("modelsDir"),
            runtime_root: string("runtimeRoot"),
        }
    }
}

#[cfg(not(target_arch = "wasm32"))]
fn read_installed_defaults(path: &std::path::Path) -> Option<Value> {
    // A corrupt/oversized install file must never crash the editor. Settings
    // remain editable in the suite when setup has not been completed.
    use std::io::Read;
    let file = std::fs::File::open(path).ok()?;
    let mut text = String::new();
    file.take(64 * 1024 + 1).read_to_string(&mut text).ok()?;
    if text.len() > 64 * 1024 {
        return None;
    }
    let value: Value = serde_json::from_str(text.trim_start_matches('\u{feff}')).ok()?;
    value.as_object()?;
    Some(value)
}

impl Settings {
    pub fn startup(&self) -> Startup {
        match self.engine {
            Engine::Comfy => self.comfy_startup,
            Engine::Direct => self.direct_startup,
        }
    }
    pub fn startup_mut(&mut self) -> &mut Startup {
        match self.engine {
            Engine::Comfy => &mut self.comfy_startup,
            Engine::Direct => &mut self.direct_startup,
        }
    }
    pub fn validate(&self) -> std::result::Result<(), String> {
        self.segmentation.validate()?;
        for s in [
            &self.model,
            &self.text_encoder,
            &self.vae,
            &self.comfy_url,
            &self.comfy_dir,
            &self.comfy_python,
            &self.direct_python,
            &self.models_dir,
            &self.runtime_root,
        ] {
            if s.len() > 4096 || s.contains('\0') {
                return Err("Invalid GenGate path or address".into());
            }
        }
        if model_spec(&self.model).is_none() {
            return Err("Unsupported model".into());
        }
        if self.runtime_root.trim().is_empty() {
            return Err("GenGate runtime folder is required".into());
        }
        Ok(())
    }
}

#[derive(Default)]
pub struct Runtime {
    pub catalog: Arc<Mutex<Value>>,
    pub state: Arc<Mutex<Snapshot>>,
    pub queue: crate::genstudio_queue::Queue,
    pub selection_cache: Mutex<Option<(std::sync::Weak<photocraft_doc::Document>, photocraft_geom::Rect)>>,
}

#[derive(Default)]
pub struct Snapshot {
    pub busy: bool,
    pub value: Value,
    epoch: u64,
    probing: bool,
}

/// Refresh idle status without blocking controls or starting an engine.
pub fn probe(s: &Session) {
    #[cfg(not(target_arch = "wasm32"))]
    {
        if std::env::var_os("GENSTUDIO_NO_PROCESS").is_some_and(|v| v == "1") {
            return;
        }
        let settings = s.prefs().gen_studio.clone();
        let shared = s.gen_studio.state.clone();
        let epoch = {
            let mut state = shared.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
            if state.busy || state.probing {
                return;
            }
            state.probing = true;
            state.epoch
        };
        let worker = shared.clone();
        if std::thread::Builder::new()
            .name("genstudio-status".into())
            .spawn(move || {
                let result = control(&settings, "status", "manual");
                let mut state = worker.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
                state.probing = false;
                if state.epoch == epoch && !state.busy {
                    state.value = result.unwrap_or_else(|e| json!({"state":"error","ready":false,"message":e}));
                }
            })
            .is_err()
        {
            shared.lock().unwrap_or_else(std::sync::PoisonError::into_inner).probing = false;
        }
    }
    #[cfg(target_arch = "wasm32")]
    let _ = s;
}

pub fn snapshot(s: &Session) -> Value {
    let state = s.gen_studio.state.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
    json!({"pending": state.busy, "status": state.value, "settings": s.prefs().gen_studio,
        "models":s.gen_studio.catalog.lock().unwrap_or_else(std::sync::PoisonError::into_inner).clone()})
}

/// Scan components in a worker, independently of engine startup and model loading.
pub fn discover(s: &Session, settings: Settings) {
    #[cfg(not(target_arch = "wasm32"))]
    {
        if std::env::var_os("GENSTUDIO_NO_PROCESS").is_some_and(|v| v == "1") {
            return;
        }
        let key = format!("{}\n{}\n{}", settings.runtime_root, settings.models_dir, settings.model);
        let shared = s.gen_studio.catalog.clone();
        {
            let mut catalog = shared.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
            if catalog["source"].as_str() == Some(&key) {
                return;
            }
            *catalog = json!({"source":key,"pending":true});
        }
        let worker = shared.clone();
        let source = key.clone();
        if std::thread::Builder::new()
            .name("genstudio-models".into())
            .spawn(move || {
                let mut result = control(&settings, "models", "manual").unwrap_or_else(|e| json!({"message":e}));
                if !result.is_object() {
                    result = json!({"message":"Invalid models catalog"});
                }
                result["source"] = json!(source);
                result["pending"] = json!(false);
                let mut catalog = worker.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
                if catalog["source"].as_str() == Some(&source) {
                    *catalog = result;
                }
            })
            .is_err()
        {
            *shared.lock().unwrap_or_else(std::sync::PoisonError::into_inner) = json!({"source":key,"message":"Could not scan models folder"});
        }
    }
    #[cfg(target_arch = "wasm32")]
    let _ = (s, settings);
}

#[cfg(not(target_arch = "wasm32"))]
fn control(settings: &Settings, action: &str, event: &str) -> std::result::Result<Value, String> {
    use std::{
        io::Write,
        path::Path,
        process::{Command, Stdio},
    };
    if std::env::var_os("GENSTUDIO_NO_PROCESS").is_some_and(|v| v == "1") {
        return Err("Engine process launch is disabled for validation".into());
    }
    let root = Path::new(&settings.runtime_root);
    let python = root.join(".venv").join(if cfg!(windows) { "Scripts/python.exe" } else { "bin/python" });
    if !python.is_file() || !root.join("genstudio/control.py").is_file() {
        return Err("GenGate runtime is missing. Check the runtime folder in Advanced settings.".into());
    }
    let mut cmd = Command::new(python);
    cmd.args(["-X", "utf8", "-m", "genstudio.control"])
        .current_dir(root)
        .env_remove("PYTHONHOME")
        .env_remove("PYTHONPATH")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x08000000);
    }
    let mut child = cmd.spawn().map_err(|e| e.to_string())?;
    let data = serde_json::to_vec(&json!({"action": action, "event": event, "settings": settings})).map_err(|e| e.to_string())?;
    if let Some(mut input) = child.stdin.take() {
        input.write_all(&data).map_err(|e| e.to_string())?;
    }
    let output = child.wait_with_output().map_err(|e| e.to_string())?;
    serde_json::from_slice(&output.stdout).map_err(|_| format!("GenGate control failed: {}", String::from_utf8_lossy(&output.stderr)))
}

fn begin(s: &mut Session, action: &'static str, event: &'static str) -> Result<Value> {
    let settings = s.prefs().gen_studio.clone();
    settings.validate().map_err(EngineError::Other)?;
    if action == "ensure" {
        let allowed = settings.startup() == Startup::AppStart || event == "request" && settings.startup() == Startup::FirstRequest;
        // A running engine remains usable even with automatic startup off: the host decides.
        if event == "appStart" && !allowed {
            return Ok(snapshot(s));
        }
    }
    #[cfg(target_arch = "wasm32")]
    {
        let _ = (action, event);
        Err(EngineError::Other("Local GenGate engines require the desktop app".into()))
    }
    #[cfg(not(target_arch = "wasm32"))]
    {
        let shared = s.gen_studio.state.clone();
        {
            let mut state = shared.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
            if state.busy {
                return Err(EngineError::Other("GenGate is already processing an engine command".into()));
            }
            state.busy = true;
            state.epoch = state.epoch.wrapping_add(1);
            let starting = matches!(action, "start" | "ensure");
            state.value = json!({"state":if starting {"starting"} else {"working"}, "message": if starting { "Starting the local AI engine. This may take a moment." } else { "Checking engine…" }});
        }
        let worker = shared.clone();
        if let Err(e) = std::thread::Builder::new().name("genstudio-control".into()).spawn(move || {
            let value = control(&settings, action, event).unwrap_or_else(|e| json!({"state":"error", "ready":false, "message":e}));
            let mut state = worker.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
            state.value = value;
            state.busy = false;
        }) {
            shared.lock().unwrap_or_else(std::sync::PoisonError::into_inner).busy = false;
            return Err(EngineError::Other(e.to_string()));
        }
        Ok(snapshot(s))
    }
}

fn set(s: &mut Session, p: &Value) -> Result<Value> {
    if s.gen_studio.state.lock().unwrap_or_else(std::sync::PoisonError::into_inner).busy {
        return Err(EngineError::Other("Wait for the engine command to finish before applying settings".into()));
    }
    let settings: Settings = serde_json::from_value(p.clone()).map_err(|e| EngineError::Other(e.to_string()))?;
    settings.validate().map_err(EngineError::Other)?;
    s.execute("prefs.set", json!({"path":"genStudio", "value":settings}))?;
    begin(s, "configure", "manual")
}

fn ensure(s: &mut Session, p: &Value) -> Result<Value> {
    match p.get("event").and_then(Value::as_str).unwrap_or("request") {
        "request" => begin(s, "ensure", "request"),
        "appStart" => begin(s, "ensure", "appStart"),
        _ => Err(EngineError::Other("Unknown engine start event".into())),
    }
}

pub fn specs() -> Vec<CommandSpec> {
    let make = |id, label, params, run| CommandSpec { id, label, menu: &[], shortcut: None, params, enabled: crate::commands::always, run, journal: false };
    vec![
        make("genstudio.engine.status", "GenGate Engine Status", "{}", |s, _| Ok(snapshot(s))),
        make("genstudio.engine.refresh", "Refresh GenGate Engine", "{}", |s, _| begin(s, "status", "manual")),
        make("genstudio.engine.start", "Start GenGate Engine", "{}", |s, _| begin(s, "start", "manual")),
        make("genstudio.engine.stop", "Stop GenGate Engine", "{}", |s, _| begin(s, "stop", "manual")),
        make("genstudio.engine.ensure", "Ensure GenGate Engine", "{\"event\":\"request|appStart\"}", ensure),
        make(
            "genstudio.engine.configure",
            "Configure GenGate Engine",
            "{model, engine, textEncoder, vae, comfyStartup, directStartup, comfyUrl, comfyDir, comfyPython, directPython, modelsDir, runtimeRoot}",
            set,
        ),
    ]
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    #[cfg(not(target_arch = "wasm32"))]
    fn installed_defaults_are_bounded_and_tolerate_bom() {
        let dir = std::env::temp_dir().join(format!("genstudio-defaults-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let file = dir.join("config.json");
        assert!(read_installed_defaults(&file).is_none());
        for invalid in ["null".to_string(), "[1]".into(), "{".into(), " ".repeat(65537)] {
            std::fs::write(&file, invalid).unwrap();
            assert!(read_installed_defaults(&file).is_none());
        }
        std::fs::write(&file, "\u{feff}{\"runtimeRoot\":\"D:/Apps/GenGate/runtime\"}").unwrap();
        assert_eq!(read_installed_defaults(&file).unwrap()["runtimeRoot"], "D:/Apps/GenGate/runtime");
        std::fs::remove_file(file).unwrap();
        std::fs::remove_dir(dir).unwrap();
    }
    #[test]
    fn policies_are_independent_and_persist() {
        let mut s = Session::new();
        s.execute("prefs.set", json!({"values":{"genStudio.comfyStartup":"appStart", "genStudio.directStartup":"off", "genStudio.engine":"direct"}})).unwrap();
        let mut next = Session::new();
        next.load_prefs_json(&s.prefs_to_json()).unwrap();
        assert_eq!(next.prefs().gen_studio.startup(), Startup::Off);
        assert_eq!(next.prefs().gen_studio.comfy_startup, Startup::AppStart);
    }
    #[test]
    fn invalid_settings_fail_atomically_without_starting_processes() {
        let mut s = Session::new();
        let before = s.prefs_to_json();
        for p in [json!({"engine":"bad"}), json!(null), json!({"unknown":true})] {
            assert!(s.execute("genstudio.engine.configure", p).is_err());
        }
        assert_eq!(s.prefs_to_json(), before);
        assert!(!snapshot(&s)["pending"].as_bool().unwrap());
    }
    #[test]
    fn first_request_policy_does_nothing_at_app_start() {
        let mut s = Session::new();
        s.execute("genstudio.engine.ensure", json!({"event":"appStart"})).unwrap();
        assert_eq!(snapshot(&s)["pending"], false);
        assert!(s.execute("genstudio.engine.ensure", json!({"event":"wrong"})).is_err());
    }
}
