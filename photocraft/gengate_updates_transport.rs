//! Reuse PhotoCraft's release-check library, with a separate add-on repository.
use photocraft_engine::gengate_updates::{CheckFn, CheckResult, VERSION, release};
const APP: craft_update::App = craft_update::App::new("gengate", "GenGate", "PhotoCraft generative tools", "Philippsapegin/Photocraft_GenStudio");

pub fn service() -> CheckFn {
    Box::new(|| {
        let (tx,rx)=std::sync::mpsc::channel();
        std::thread::Builder::new().name("GenGate update check".into()).spawn(move|| {
            let _=tx.send(check());
        }).map_err(|e|format!("Could not start GenGate update check: {e}"))?;
        Ok(rx)
    })
}

fn check() -> CheckResult {
    let http=craft_update::Http::new("GenGate/Update")?;
    match craft_update::check_for_update(&APP,&http,VERSION)? {
        Some(found) => release(&found.tag,VERSION),
        None => Ok(None),
    }
}
