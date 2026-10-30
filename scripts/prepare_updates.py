"""Add independent GenGate checks beside PhotoCraft's native updater."""
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]


def prepare(source, replace):
    engine, ui = source/'crates/engine/src', source/'crates/ui-egui/src'
    for name, destination in (('gengate_updates.rs',engine),('gengate_updates_ui.rs',ui)):
        shutil.copyfile(ROOT/'photocraft'/name,destination/name)
    sys.path.insert(0,str(ROOT))
    from genstudio import VERSION
    (engine/'gengate-version.txt').write_text(VERSION,encoding='utf-8')
    for path,anchor,addition in (
        (engine/'lib.rs','pub mod genstudio;','pub mod gengate_updates;'),
        (engine/'commands.rs','v.extend(crate::genstudio::specs());','    v.extend(crate::gengate_updates::specs());'),
        (ui/'lib.rs','pub mod genstudio_ui;','pub mod gengate_updates_ui;'),
    ):
        if addition.strip() not in path.read_text(encoding='utf-8'):
            replace(path,anchor,anchor+'\n'+addition)
    # 0.17 exposes the suite updater. Older bases keep a disabled transport.
    desktop=source/'apps/photocraft/src'
    if 'craft_update::' in (desktop/'updates.rs').read_text(encoding='utf-8'):
        shutil.copyfile(ROOT/'photocraft/gengate_updates_transport.rs',desktop/'gengate_updates_transport.rs')
        main=desktop/'main.rs'
        text=main.read_text(encoding='utf-8')
        lines=[line for line in text.splitlines() if line.strip()!='mod gengate_updates_transport;']
        main.write_text('\n'.join(lines)+'\n',encoding='utf-8')
        replace(main,'mod updates;','mod updates;\nmod gengate_updates_transport;')
        services=ui/'services.rs'
        replace(services,'    pub check_updates: Option<photocraft_engine::updates_cmds::CheckFn>,',
                '    pub check_updates: Option<photocraft_engine::updates_cmds::CheckFn>,\n    pub check_gengate_updates: Option<photocraft_engine::gengate_updates::CheckFn>,')
        replace(ui/'lib.rs','        session.updates.check = services.check_updates.take();',
                '        session.updates.check = services.check_updates.take();\n        session.gen_studio.updates.check = services.check_gengate_updates.take();')
        replace(desktop/'services.rs','        check_updates: update_transport(std::env::var_os("FLATPAK_ID").as_deref()),',
                '        check_updates: update_transport(std::env::var_os("FLATPAK_ID").as_deref()),\n        check_gengate_updates: Some(crate::gengate_updates_transport::service()),')
        # Show the version/status window with the add-on warning before offering
        # the suite's download/restart prompt. The user's Update button keeps it.
        path=ui/'updates_ui.rs'
        text=path.read_text(encoding='utf-8')
        text=text.replace('        #[cfg(not(target_arch = "wasm32"))]\n        app.updates_rt.updater.offer(release_of(release));',
                          '        // GenGate: show compatibility notice before the user starts the native updater.')
        anchor='                    ui.label(crate::i18n::fmt(crate::i18n::t("PhotoCraft {version} is available."), &[("version", &release.version)]));'
        if 'gengate_updates_ui::photocraft_notice' not in text:
            if text.count(anchor)!=1:raise RuntimeError('PhotoCraft update notice changed')
            text=text.replace(anchor,anchor+'\n                    crate::gengate_updates_ui::photocraft_notice(app,ui);')
        text=text.replace('assert!(app.updates_rt.updater.prompt_open(), "startup prompt initialized on available release");',
                          'assert!(!app.updates_rt.updater.prompt_open(), "GenGate warning appears before the native update prompt");')
        text=text.replace('// Rendering show() renders the prompt modal', '// Rendering show() displays the compatibility notice first')
        text=text.replace('        assert!(app.updates_rt.updater.prompt_open());', '        assert!(!app.updates_rt.updater.prompt_open());\n        assert!(app.ui.updates_open);')
        path.write_text(text,encoding='utf-8')
        example=source/'crates/ui-egui/examples/snapshot.rs'
        text=example.read_text(encoding='utf-8')
        if 'gengate-update-notice' not in text:
            anchor='            if update_notice {'
            fixture='''            if update_notice || gengate_update_notice {
                app.session.gen_studio.updates.status=photocraft_engine::gengate_updates::Status::Available {
                    release:photocraft_engine::gengate_updates::Release {
                        version:"99.0.0".into(),
                        url:"https://github.com/Philippsapegin/Photocraft_GenStudio/releases/tag/v99.0.0".into(),
                    },
                };
                if update_notice { app.ui.gen_studio.updates_notified=Some("99.0.0".into()); }
            }
'''
            if text.count(anchor)!=1:raise RuntimeError('PhotoCraft update snapshot fixture changed')
            text=text.replace(anchor,fixture+anchor,1)
        text=text.replace('if update_notice || args.iter().any(|arg| arg == "--gengate-update-notice") {','if update_notice || gengate_update_notice {')
        if 'let gengate_update_notice' not in text:
            anchor='    let update_notice = args.iter().any(|a| a == "--update-notice");'
            if text.count(anchor)!=1:raise RuntimeError('PhotoCraft update snapshot arguments changed')
            text=text.replace(anchor,anchor+'\n    let gengate_update_notice = args.iter().any(|a| a == "--gengate-update-notice");')
        example.write_text(text,encoding='utf-8')
    for path in (ui/'i18n').glob('*.tsv'):
        text=path.read_text(encoding='utf-8')
        for label in ('Check for GenGate Updates','GenGate Update Status'):
            if '\t'+label+'\t' not in text:text+='\n\t'+label+'\t'+label+'\n'
        path.write_text(text,encoding='utf-8')
    if (engine/'cmd.rs').exists():
        # PhotoCraft 0.17 uses egui's explicit global IDs and accessibility roles.
        for path in (source/'crates/ui-egui/src').rglob('*.rs'):
            if path.name.startswith(('genstudio','gengate')) or path.name=='layer_transfer.rs':
                text=path.read_text(encoding='utf-8')
                text=text.replace('egui::Id::new(', 'egui::Id::unique(').replace('egui::WidgetType::Button','egui::Role::Button').replace('ui.id()', 'ui.scope_id()').replace('egui::ComboBox::','craft_ui::ComboBox::')
                path.write_text(text,encoding='utf-8')
