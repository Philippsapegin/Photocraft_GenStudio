//! Portable Studio adapters and a bounded directory view; checkpoint data is never read.
use crate::{EngineError, Result, Session, commands::CommandSpec};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Selection {
    pub path: String,
    pub strength: f64,
}
pub fn validate(selected: &[Selection]) -> std::result::Result<(), String> {
    if selected.len() > 64 { return Err("Choose up to 64 LoRAs".into()); }
    let mut seen = std::collections::BTreeSet::new();
    for item in selected {
        let path = &item.path;
        if path.is_empty() || path.len() > 2048 || path.contains(['\0', '\\', ':'])
            || path.split('/').any(|p| matches!(p, "" | "." | ".."))
            || !path.to_lowercase().ends_with(".safetensors")
            || !item.strength.is_finite() || !(-20.0..=20.0).contains(&item.strength)
            || !seen.insert(path.to_lowercase()) {
            return Err("Choose unique Safetensors LoRAs inside Models/loras, with strength between -20 and 20".into());
        }
    }
    Ok(())
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct Entry {
    pub path: String,
    pub name: String,
    pub directory: bool,
}
#[cfg(not(target_arch = "wasm32"))]
pub fn list(models: &str, folder: &str) -> std::result::Result<Vec<Entry>, String> {
    use std::path::Path;
    if folder.len() > 2048 || folder.contains(['\0', '\\', ':']) || (!folder.is_empty() && folder.split('/').any(|p| matches!(p, "" | "." | ".."))) {
        return Err("Stay inside Models/loras".into());
    }
    let root = Path::new(models).join("loras").canonicalize().map_err(|_| "The Models folder has no loras directory")?;
    let directory = root.join(folder).canonicalize().map_err(|e| e.to_string())?;
    if !directory.starts_with(&root) { return Err("Stay inside Models/loras".into()); }
    let mut entries = Vec::new();
    for entry in std::fs::read_dir(&directory).map_err(|e| e.to_string())? {
        let entry = entry.map_err(|e| e.to_string())?;
        let path = entry.path();
        let Ok(resolved) = path.canonicalize() else { continue };
        if !resolved.starts_with(&root) { continue; }
        let directory = resolved.is_dir();
        if !directory && (!resolved.is_file() || path.extension().is_none_or(|s| !s.eq_ignore_ascii_case("safetensors"))) { continue; }
        if entries.len() >= 4096 { return Err("This folder has more than 4096 entries; organize its LoRAs into subfolders".into()); }
        let name = entry.file_name().to_string_lossy().into_owned();
        if name.len() > 512 { continue; }
        entries.push(Entry { path: if folder.is_empty() { name.clone() } else { format!("{folder}/{name}") }, name, directory });
    }
    entries.sort_by_key(|e| (!e.directory, e.name.to_lowercase()));
    Ok(entries)
}
fn browse(s: &mut Session, p: &Value) -> Result<Value> {
    let folder = p.get("folder").map_or(Ok(""), |v| v.as_str().ok_or_else(|| EngineError::Other("Invalid LoRA folder".into())))?.to_owned();
    let models = s.prefs().gen_studio.models_dir.clone();
    #[cfg(not(target_arch = "wasm32"))]
    return crate::jobs::run(s, "Read LoRA folder", false,
        move |ctx| { ctx.check()?; list(&models, &folder).map_err(EngineError::Other) },
        |_, entries| Ok(json!({"entries": entries})));
    #[cfg(target_arch = "wasm32")]
    { let _ = (models, folder); Err(EngineError::Other("Local LoRA folders require the desktop app".into())) }
}
pub fn specs() -> Vec<CommandSpec> {
    vec![CommandSpec { id: "genstudio.loras.list", label: "LoRA folder", menu: &[], shortcut: None,
        params: "{folder:relative path inside Models/loras}", enabled: crate::commands::always, run: browse, journal: false }]
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn adapter_metadata_rejects_escapes_duplicates_and_bad_strengths() {
        let a = |path: &str, strength| Selection { path: path.into(), strength };
        assert!(validate(&[a("style/日本.safetensors", 0.75)]).is_ok());
        for path in ["../x.safetensors", "/x.safetensors", "C:/x.safetensors", "a\\b.safetensors", "a//b.safetensors", "a.pt"] {
            assert!(validate(&[a(path, 1.0)]).is_err());
        }
        assert!(validate(&[a("a.safetensors", 1.0), a("A.safetensors", 1.0)]).is_err());
        assert!(validate(&[a("a.safetensors", f64::NAN)]).is_err());
    }
    #[cfg(not(target_arch = "wasm32"))]
    #[test]
    fn directory_view_never_leaves_loras_and_does_not_open_weights() {
        let root = std::env::temp_dir().join(format!("gengate-loras-{}-{}", std::process::id(), std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()));
        std::fs::create_dir_all(root.join("loras/nested")).unwrap();
        std::fs::write(root.join("loras/example.safetensors"), b"not weights").unwrap();
        std::fs::write(root.join("loras/readme.txt"), b"skip").unwrap();
        let rows = list(root.to_str().unwrap(), "").unwrap();
        assert_eq!(rows.len(), 2); assert!(rows[0].directory);
        assert!(list(root.to_str().unwrap(), "..").is_err());
        assert!(list(root.to_str().unwrap(), "nested").unwrap().is_empty());
        assert_eq!(std::fs::read(root.join("loras/example.safetensors")).unwrap(), b"not weights");
        std::fs::remove_dir_all(root).unwrap();
    }
}
