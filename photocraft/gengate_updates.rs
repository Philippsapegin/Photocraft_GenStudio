//! Independent add-on release checks. Networking is injected by the desktop.
use std::sync::mpsc::{Receiver, TryRecvError};
use semver::Version;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use crate::{EngineError, Result, Session, commands::{CommandSpec, always}};

pub const VERSION: &str = include_str!("gengate-version.txt");
pub const RELEASES: &str = "https://github.com/Philippsapegin/Photocraft_GenStudio/releases";
pub type CheckResult = std::result::Result<Option<Release>, String>;
pub type CheckFn = Box<dyn FnMut() -> std::result::Result<Receiver<CheckResult>, String> + Send>;

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Release { pub version: String, pub url: String }

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(tag="status", rename_all="camelCase")]
pub enum Status {
    #[default]
    Idle,
    Checking,
    UpToDate,
    Available { release: Release },
    Failed { message: String },
}

#[derive(Default)]
pub struct Runtime {
    pub check: Option<CheckFn>,
    pub status: Status,
    pending: Option<Receiver<CheckResult>>,
}

impl Runtime {
    pub fn poll(&mut self) {
        let Some(rx) = &self.pending else { return };
        let result = match rx.try_recv() {
            Ok(result) => result,
            Err(TryRecvError::Empty) => return,
            Err(TryRecvError::Disconnected) => Err("The GenGate update check stopped before returning a result.".into()),
        };
        self.pending = None;
        self.status = match result {
            Ok(Some(release)) => Status::Available { release },
            Ok(None) => Status::UpToDate,
            Err(message) => Status::Failed { message },
        };
    }
}

/// Accept a stable, newer tag; construct our own URL instead of trusting remote links.
pub fn release(tag: &str, current: &str) -> CheckResult {
    if tag.len() > 128 || !tag.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b,b'.'|b'-'|b'+')) {
        return Err("Invalid GenGate release tag.".into());
    }
    let version = Version::parse(tag.strip_prefix(['v','V']).unwrap_or(tag)).map_err(|e| format!("Invalid GenGate release: {e}"))?;
    let installed = Version::parse(current).map_err(|e| format!("Invalid installed GenGate version: {e}"))?;
    Ok((version.pre.is_empty() && version.cmp_precedence(&installed).is_gt()).then(|| Release {
        version: version.to_string(), url: format!("{RELEASES}/tag/{tag}"),
    }))
}

fn empty(params: &Value) -> Result<()> {
    if params.as_object().is_some_and(|p|p.is_empty()) { Ok(()) }
    else { Err(EngineError::Other("GenGate update commands take an empty object.".into())) }
}

fn check(s: &mut Session, params: &Value) -> Result<Value> {
    empty(params)?;
    let updates = &mut s.gen_studio.updates;
    if updates.pending.is_none() {
        let start = updates.check.as_mut().ok_or_else(||EngineError::Other("GenGate update checks are unavailable in this frontend.".into()))?;
        match start() {
            Ok(rx) => { updates.pending = Some(rx); updates.status = Status::Checking; }
            Err(message) => { updates.status = Status::Failed {message:message.clone()}; return Err(EngineError::Other(message)); }
        }
    }
    Ok(json!(updates.status))
}

pub fn specs() -> Vec<CommandSpec> {
    vec![
        CommandSpec { id:"genstudio.updates.check", label:"Check for GenGate Updates", menu:&[], shortcut:None,
            params:"{}", journal:false,
            enabled:|s| if s.gen_studio.updates.check.is_some() {Ok(())} else {Err("GenGate update checks are unavailable in this frontend.".into())}, run:check },
        CommandSpec { id:"genstudio.updates.status", label:"GenGate Update Status", menu:&[], shortcut:None,
            params:"{}", journal:false, enabled:always, run:|s,p| {
                empty(p)?; s.gen_studio.updates.poll();
                Ok(json!({"supported":s.gen_studio.updates.check.is_some(),"currentVersion":VERSION,"result":s.gen_studio.updates.status}))
            } },
    ]
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn release_versions_and_links_are_checked() {
        assert_eq!(release("v0.10.0","0.9.4").unwrap().unwrap().url,format!("{RELEASES}/tag/v0.10.0"));
        for tag in ["v0.9.3","v0.9.4","v0.9.4+build.2","v1.0.0-rc.1"] { assert!(release(tag,"0.9.4").unwrap().is_none()); }
        for tag in ["", "latest", "v1.0.0/../evil", "v1.0.0?next=evil", "v1.0.0\n", "版本1.0.0"] { assert!(release(tag,"0.9.4").is_err()); }
    }
    #[test]
    fn checks_are_deduplicated_and_fail_gracefully() {
        let mut s=Session::new();
        let (tx,rx)=std::sync::mpsc::channel(); let mut rx=Some(rx);
        s.gen_studio.updates.check=Some(Box::new(move||Ok(rx.take().expect("one worker"))));
        for _ in 0..3 {assert_eq!(s.execute("genstudio.updates.check",json!({})).unwrap()["status"],"checking");}
        tx.send(Ok(release("v1.0.0",VERSION).unwrap())).unwrap();
        assert_eq!(s.execute("genstudio.updates.status",json!({})).unwrap()["result"]["status"],"available");
        assert!(s.journal.is_empty());
        for bad in [Value::Null,json!([]),json!({"url":"evil"})] {
            for command in ["genstudio.updates.check","genstudio.updates.status"] {assert!(s.execute(command,bad.clone()).is_err());}
        }
        let mut unavailable=Session::new();
        assert!(unavailable.execute("genstudio.updates.check",json!({})).is_err());
    }
}
