"""Wire the Qwen generation workspace into the pinned native editor."""
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[1]


def prepare(source,replace):
    engine=source/"crates/engine/src"
    ui=source/"crates/ui-egui/src"
    shutil.copyfile(ROOT/"photocraft/genstudio_generate.rs",engine/"genstudio_generate.rs")
    shutil.copyfile(ROOT/"photocraft/genstudio_canvas.rs",ui/"genstudio_canvas.rs")
    for crate in ("engine","ui-egui"):
        manifest=source/"crates"/crate/"Cargo.toml"
        text=manifest.read_text(encoding="utf-8")
        if "base64 =" not in text:
            manifest.write_text(text.replace("[dependencies]","[dependencies]\nbase64 = \"0.22\"",1),encoding="utf-8")
    # base64 is already pinned for the UI in the upstream lockfile. Keep the
    # engine's dependency list in sync so a fresh checkout builds with --locked.
    lock=source/"Cargo.lock"
    text=lock.read_text(encoding="utf-8")
    start=text.index('name = "photocraft-engine"')
    end=text.find("\n[[package]]",start)
    if end<0:end=len(text)
    package=text[start:end]
    if '\n "base64",' not in package:
        package=package.replace('dependencies = [\n','dependencies = [\n "base64",\n',1)
        lock.write_text(text[:start]+package+text[end:],encoding="utf-8")
    def module(path,anchor,addition):
        text=path.read_text(encoding="utf-8")
        seen=False;lines=[]
        for line in text.splitlines(keepends=True):
            if line.strip()==addition:
                if seen:continue
                seen=True
            lines.append(line)
        path.write_text("".join(lines),encoding="utf-8")
        if not seen:replace(path,anchor,anchor+"\n"+addition)
    module(engine/"lib.rs","pub mod genstudio;","pub mod genstudio_generate;")
    replace(engine/"commands.rs","v.extend(crate::genstudio::specs());","v.extend(crate::genstudio::specs());\n    v.extend(crate::genstudio_generate::specs());")
    module(ui/"lib.rs","pub mod genstudio_ui;","pub mod genstudio_canvas;")
    replace(ui/"lib.rs","pub(crate) ai_layers: crate::genstudio_ai_ui::Cache,","pub(crate) ai_layers: crate::genstudio_ai_ui::Cache,\n    pub(crate) studio: crate::genstudio_canvas::Cache,")
    replace(ui/"lib.rs","ai_layers: Default::default(),","ai_layers: Default::default(),\n            studio: Default::default(),")
    # Clean the duplicate hook left by the early pre-format preparer.
    path=ui/"lib.rs"
    text=path.read_text(encoding="utf-8")
    condition='!crate::genstudio_canvas::dropped(self, ctx, &dropped, at)'
    double=f'            if {condition} {{\n                if {condition} {{\n                    self.open_dropped(ctx, dropped, at);\n                }}\n            }}'
    single=f'            if {condition} {{\n                self.open_dropped(ctx, dropped, at);\n            }}'
    if double in text:path.write_text(text.replace(double,single),encoding="utf-8")
    if "genstudio_canvas::dropped" not in (ui/"lib.rs").read_text(encoding="utf-8"):
        replace(ui/"lib.rs","            self.open_dropped(ctx, dropped, at);","            if !crate::genstudio_canvas::dropped(self,ctx,&dropped,at) { self.open_dropped(ctx, dropped, at); }")
    if '"genstudio.studio."' not in (engine/"jobs.rs").read_text(encoding="utf-8"):
        replace(engine/"jobs.rs",'const PREFIXES: &[&str] = &["jobs.",','const PREFIXES: &[&str] = &["genstudio.studio.", "genstudio.reference.", "jobs.",')
    if 'j.command.starts_with("genstudio.studio.")' not in (ui/"jobs_ui.rs").read_text(encoding="utf-8"):
        replace(ui/"jobs_ui.rs",'j.command.starts_with("genstudio.fill.")','(j.command.starts_with("genstudio.fill.") || j.command.starts_with("genstudio.studio.") || j.command.starts_with("genstudio.reference."))')
    commands=("inspect","configure","submit","reference.browse","reference.set","reference.remove","reference.selection")
    # These hidden automation actions still participate in PhotoCraft's command
    # catalog. Reuse the editor's translated action words beside the suite name.
    words=("Properties","Settings","Generate","Open","Add","Remove","Selection")
    for catalog in (ui/"i18n").glob("*.tsv"):
        text=catalog.read_text(encoding="utf-8")
        plain={cells[1]:cells[2] for line in text.splitlines() if len(cells:=line.split("\t"))==3 and not cells[0]}
        for name,word in zip(commands,words):
            label=f"Gen Studio {name}"
            if label not in plain:text+=f"\n\t{label}\tGen Studio · {plain.get(word,word)}\n"
        catalog.write_text(text,encoding="utf-8")
    menu=ui/"menus.rs"
    text=menu.read_text(encoding="utf-8")
    anchor='    ("genstudio.open", "Gen Studio…", &["Filter", "Plug-ins"], None),'
    if '"genstudio.studio.inspect"' not in text:
        entries="\n".join(f'    ("genstudio.studio.{name}", "Gen Studio {name}", &[], None),' for name in commands)
        text=text.replace(anchor,anchor+"\n"+entries)
        target='    match id {'
        # UI command enabled checks are in is_enabled (rather than dispatch).
        pos=text.index('pub fn is_enabled')
        head,tail=text[:pos],text[pos:]
        insert='        '+" | ".join('"genstudio.studio.'+name+'"' for name in commands)+' => true,\n'
        tail=tail.replace(target,target+"\n"+insert,1)
        text=head+tail
        menu.write_text(text,encoding="utf-8")
