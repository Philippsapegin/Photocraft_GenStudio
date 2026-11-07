//! Add-on update notices and the warning before PhotoCraft replaces its overlay.
use photocraft_engine::gengate_updates::{Status, VERSION, RELEASES};
use serde_json::{Value,json};
use crate::PhotocraftApp;

pub fn check(app:&mut PhotocraftApp,params:&Value)->Result<Value,String> {
    let result=app.run("genstudio.updates.check",params.clone());
    if params.as_object().is_some_and(|p|p.is_empty()) && (result.is_ok() || matches!(app.session.gen_studio.updates.status,Status::Failed{..})) {
        app.ui.gen_studio.updates_open=true;
        app.ui.gen_studio.updates_startup_checked=true;
    }
    result
}

pub fn tick(app:&mut PhotocraftApp,ctx:&egui::Context) {
    app.session.gen_studio.updates.poll();
    if let Status::Available{release}=&app.session.gen_studio.updates.status
        && app.ui.gen_studio.updates_notified.as_ref()!=Some(&release.version) {
        app.ui.gen_studio.updates_notified=Some(release.version.clone());
        app.ui.gen_studio.updates_open=true;
    }
    if !app.ui.gen_studio.updates_startup_checked {
        app.ui.gen_studio.updates_startup_checked=true;
        if app.session.prefs().general.automatically_check_updates && app.session.gen_studio.updates.check.is_some() && matches!(app.session.gen_studio.updates.status,Status::Idle) {
            let _=app.run("genstudio.updates.check",json!({}));
        }
    }
    if matches!(app.session.gen_studio.updates.status,Status::Checking) {ctx.request_repaint_after(std::time::Duration::from_millis(100));}
}

fn details(app:&mut PhotocraftApp,ui:&mut egui::Ui) {
    ui.label(format!("Installed GenGate: {VERSION}"));
    let status=app.session.gen_studio.updates.status.clone();
    match status {
        Status::Available{release}=>{
            ui.label(format!("GenGate {} is available.",release.version));
            ui.small("Choose a GenGate build compatible with your PhotoCraft version. Run its installer with PhotoCraft closed.");
            if ui.button("Update GenGate…").clicked(){crate::links::open(app,ui.ctx(),&release.url);}
        }
        Status::Checking=>{ui.horizontal(|ui|{ui.spinner();ui.label("Checking GenGate updates…");});}
        Status::UpToDate=>{ui.label("GenGate is up to date.");}
        Status::Failed{message}=>{ui.label("Could not check GenGate updates.");ui.small(message);}
        Status::Idle=>{ui.label("GenGate updates have not been checked yet.");}
    }
    ui.horizontal(|ui|{
        if ui.add_enabled(app.session.gen_studio.updates.check.is_some() && !matches!(app.session.gen_studio.updates.status,Status::Checking),egui::Button::new("Check GenGate updates")).clicked(){let _=check(app,&json!({}));}
        if ui.button("GenGate releases").clicked(){crate::links::open(app,ui.ctx(),RELEASES);}
    });
}

pub fn photocraft_notice(app:&mut PhotocraftApp,ui:&mut egui::Ui) {
    ui.add_space(8.0);
    ui.label("GenGate will verify the official PhotoCraft update and keep your engine, model folders and settings. Save your work, then close PhotoCraft when the update window asks.");
    details(app,ui);
    ui.separator();
}

#[cfg(any(target_os="windows",test))]
fn updater_arguments(runtime:&std::path::Path,exe:&std::path::Path,version:&str)->Result<Vec<std::ffi::OsString>,String> {
    use std::ffi::OsString;
    if version.len()>128 || !version.bytes().all(|b|b.is_ascii_alphanumeric()||matches!(b,b'.'|b'-'|b'+')) || !version.starts_with(|c:char|c.is_ascii_digit()) {return Err("Invalid PhotoCraft update version".into());}
    let bundle=runtime.parent().ok_or("The GenGate update package is missing")?;
    let host=exe.parent().ok_or("The PhotoCraft folder is missing")?;
    let mut args:Vec<OsString>=["-NoProfile","-ExecutionPolicy","Bypass","-STA","-WindowStyle","Hidden","-File"].into_iter().map(OsString::from).collect();
    args.push(runtime.join("packaging/Setup.ps1").into_os_string());
    args.push("-BundleRoot".into());args.push(bundle.as_os_str().to_owned());
    args.push("-EditorFolder".into());args.push(host.as_os_str().to_owned());
    args.push("-TargetVersion".into());args.push(version.into());args.push("-UpdateEditor".into());
    Ok(args)
}

pub fn open_photocraft_update(app:&mut PhotocraftApp,version:&str) {
    fn launch(runtime:&std::path::Path,version:&str)->Result<(),String> {
        #[cfg(target_os="windows")]
        {
            use std::os::windows::process::CommandExt;
            let exe=std::env::current_exe().map_err(|e|e.to_string())?;
            let args=updater_arguments(runtime,&exe,version)?;
            if !runtime.join("packaging/Setup.ps1").is_file(){return Err("GenGate Setup is missing. Run the latest GenGate installer.".into());}
            if std::env::var_os("GENSTUDIO_NO_PROCESS").is_some_and(|v|v=="1"){return Err("External update launch is disabled for validation".into());}
            std::process::Command::new("powershell.exe").args(args).creation_flags(0x08000000)
                .stdin(std::process::Stdio::null()).stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null())
                .spawn().map_err(|e|format!("Could not open GenGate Setup: {e}"))?;
            Ok(())
        }
        #[cfg(not(target_os="windows"))]
        {let _=(runtime,version);Err("Use the GenGate Windows installer to update this editor.".into())}
    }
    app.ui.status=match launch(std::path::Path::new(&app.session.prefs().gen_studio.runtime_root),version) {
        Ok(())=>"Update window opened. Save your work and close PhotoCraft there to continue.".into(),
        Err(error)=>error,
    };
}

pub fn show(app:&mut PhotocraftApp,ctx:&egui::Context) {
    tick(app,ctx);
    if !app.ui.gen_studio.updates_open{return;}
    let mut open=true;
    egui::Window::new("GenGate Updates").id(egui::Id::new("gengate-updates")).open(&mut open).collapsible(false).resizable(false).default_width(420.0).show(ctx,|ui|details(app,ui));
    app.ui.gen_studio.updates_open=open;
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::Services;
    use photocraft_engine::{Session,gengate_updates::release};
    #[test]
    fn editor_update_uses_the_same_setup_with_literal_paths_and_target() {
        let args=updater_arguments(std::path::Path::new("addon/versions/current/runtime"),std::path::Path::new("editor/photocraft.exe"),"0.21.0").unwrap();
        let strings:Vec<_>=args.iter().map(|s|s.to_string_lossy().into_owned()).collect();
        assert!(strings.windows(2).any(|w|w==["-BundleRoot","addon/versions/current"]));
        assert!(strings.windows(2).any(|w|w==["-EditorFolder","editor"]));
        assert!(strings.windows(2).any(|w|w==["-TargetVersion","0.21.0"]));
        assert_eq!(strings.last().unwrap(),"-UpdateEditor");
        for bad in ["", "0.21.0 -File evil.ps1", "https://evil", "-UpdateEditor"] {assert!(updater_arguments(std::path::Path::new("runtime"),std::path::Path::new("editor/app.exe"),bad).is_err());}
    }
    #[test]
    fn photocraft_warning_offers_the_available_addon_update() {
        use egui_kittest::kittest::Queryable;
        let mut app=PhotocraftApp::new(Session::new(),Services::default());
        app.session.gen_studio.updates.status=Status::Available{release:release("v99.0.0",VERSION).unwrap().unwrap()};
        let mut ready=false;
        let mut h=egui_kittest::Harness::builder().with_size(egui::vec2(800.0,600.0)).build_ui_state(move|ui,app:&mut PhotocraftApp|{
            if !ready {PhotocraftApp::setup_context(ui.ctx(),crate::theme::ThemeKind::Studio);ready=true;return;}
            photocraft_notice(app,ui);
        },app);
        h.run_steps(6);
        assert!(h.query_by_label("GenGate will verify the official PhotoCraft update and keep your engine, model folders and settings. Save your work, then close PhotoCraft when the update window asks.").is_some());
        assert!(h.query_by_label("GenGate 99.0.0 is available.").is_some());
        assert!(h.query_by_label("Update GenGate…").is_some());
    }
    #[test]
    fn startup_obeys_preference_notifies_once_and_manual_check_works() {
        let mut app=PhotocraftApp::new(Session::new(),Services::default());
        app.session.edit_prefs(|p|p.general.automatically_check_updates=false);
        let (tx,rx)=std::sync::mpsc::channel();let mut rx=Some(rx);
        app.session.gen_studio.updates.check=Some(Box::new(move||Ok(rx.take().expect("one check"))));
        let ctx=egui::Context::default();
        tick(&mut app,&ctx);
        assert_eq!(app.session.gen_studio.updates.status,Status::Idle);
        check(&mut app,&json!({})).unwrap();
        tx.send(Ok(release("v99.0.0",VERSION).unwrap())).unwrap();
        app.run("genstudio.updates.status",json!({})).unwrap();
        tick(&mut app,&ctx);assert!(app.ui.gen_studio.updates_open);
        app.ui.gen_studio.updates_open=false;tick(&mut app,&ctx);assert!(!app.ui.gen_studio.updates_open);
        assert!(check(&mut app,&json!({"url":"evil"})).is_err());assert!(!app.ui.gen_studio.updates_open);
    }
    #[test]
    fn automatic_failures_stay_quiet_and_do_not_retry() {
        let mut app=PhotocraftApp::new(Session::new(),Services::default());
        app.session.gen_studio.updates.check=Some(Box::new(||Err("offline".into())));
        let ctx=egui::Context::default();tick(&mut app,&ctx);
        assert!(matches!(app.session.gen_studio.updates.status,Status::Failed{..}));
        assert!(!app.ui.gen_studio.updates_open);
        tick(&mut app,&ctx);assert!(!app.ui.gen_studio.updates_open);
        check(&mut app,&json!({})).unwrap_err();assert!(app.ui.gen_studio.updates_open);
    }
}
