"""Display-name migration. Stable command IDs, profiles and document metadata survive."""


def before(source):
    # Earlier integration hooks use these literal menu anchors. Normalise only
    # their generated files, then apply the public branding after all hooks.
    for rel in ("crates/engine/src/prefs.rs", "crates/ui-egui/src/menus.rs", "crates/ui-egui/src/menu_catalog.rs"):
        path = source / rel
        text = path.read_text(encoding="utf-8")
        text = text.replace('("genStudio", "GenGate Settings")', '("genStudio", "Gen Studio")')
        text = "\n".join(row.replace('"GenGate Settings…"', '"Gen Studio…"') if '"edit.preferences.genStudio"' in row else row for row in text.splitlines())+"\n"
        seen = False
        rows = []
        for row in text.splitlines():
            if '"edit.preferences.genStudio"' in row:
                if seen:
                    continue
                seen = True
            rows.append(row)
        text = "\n".join(rows)+"\n"
        path.write_text(text.replace("GenGate", "Gen Studio"), encoding="utf-8")


def after(source):
    # Independent preparers share module/registry anchors. Repeated preparation
    # must never register a command twice after another hook inserts between them.
    for rel in ("crates/engine/src/lib.rs", "crates/engine/src/commands.rs", "crates/ui-egui/src/lib.rs"):
        path=source/rel
        seen=set()
        rows=[]
        for row in path.read_text(encoding="utf-8").splitlines():
            key=row.strip()
            generated=("genstudio" in key or "gengate" in key) and (key.startswith(("pub mod ","mod ")) or key.startswith("v.extend(crate::"))
            if generated:
                if key in seen:
                    continue
                seen.add(key)
            rows.append(row)
        path.write_text("\n".join(rows)+"\n",encoding="utf-8")
    for crate in ("engine", "ui-egui"):
        for path in (source / "crates" / crate / "src").rglob("*.rs"):
            text = path.read_text(encoding="utf-8")
            new = text.replace("Gen Studio", "GenGate")
            new = new.replace('("genStudio", "GenGate")', '("genStudio", "GenGate Settings")')
            new = new.replace('"edit.preferences.genStudio", "GenGate…"', '"edit.preferences.genStudio", "GenGate Settings…"')
            new = new.replace('"GenGate…", None, "edit.preferences.genStudio"', '"GenGate Settings…", None, "edit.preferences.genStudio"')
            if text != new:
                path.write_text(new, encoding="utf-8")
    for path in (source / "crates/ui-egui/src/i18n").glob("*.tsv"):
        text = path.read_text(encoding="utf-8").replace("Gen Studio", "GenGate")
        for label in ("GenGate Settings", "GenGate Settings…"):
            if "\t" + label + "\t" not in text:
                text += "\n\t" + label + "\t" + label + "\n"
        seen = set()
        lines = []
        for row in text.splitlines():
            if not row.strip():
                continue
            parts = row.split("\t")
            if len(parts) == 3 and "GenGate" in parts[1]:
                key = tuple(parts[:2])
                if key in seen:
                    continue
                seen.add(key)
            lines.append(row)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
