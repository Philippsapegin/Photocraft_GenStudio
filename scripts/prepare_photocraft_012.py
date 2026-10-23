"""Compatibility with PhotoCraft 0.12's shared shell-command table."""
import re

ALIAS = 'pub const UI_COMMANDS: &[(&str, &str, &[&str], Option<&str>)] = photocraft_engine::shell_commands::SHELL_COMMANDS;'


def table(text, name):
    match = re.search(r'pub const ' + name + r':[^\n]+ = &\[\n.*?\n\];', text, re.S)
    if match is None:
        raise RuntimeError("Expected one shell-command table: " + name)
    return match


def anchors(path, text, old, new):
    if path.name == 'jobs_ui.rs' and '|| app.jobs.saves.iter().any(|s| s.job == j.id)' in text:
        suffix = ' || app.jobs.saves.iter().any(|s| s.job == j.id)'
        if old == '    if j.elapsed_ms < DIALOG_DELAY_MS {':
            return old.replace(' {', suffix + ' {'), new.replace(' {', suffix + ' {')
    if path.name == 'lib.rs' and 'remove_pending_target:' in text:
        return old.replace('remove_pending_doc:', 'remove_pending_target:'), new.replace('remove_pending_doc:', 'remove_pending_target:')
    if path.name == 'retouch_ui.rs' and 'o.sample_all.remove' in text and 'json!({"sampleAllLayers": o.sample_all_layers' in old:
        return old.replace('o.sample_all_layers', 'o.sample_all.remove'), new.replace('o.sample_all_layers', 'o.sample_all.remove')
    if path.name == 'state.rs' and 'sample_all: SampleAllLayers::default(),' in text and 'sample_all_layers: false,' in old:
        return old.replace('sample_all_layers: false,', 'sample_all: SampleAllLayers::default(),'), new.replace('sample_all_layers: false,', 'sample_all: SampleAllLayers::default(),')
    if path.name == 'tool_reset.rs' and 'o.sample_all.slot(tool)' in text:
        new = new.replace('reset!(sample_all_layers, remove_new_layer', 'reset!(remove_new_layer')
    return old, new


def before(source):
    shell = source / 'crates/engine/src/shell_commands.rs'
    menu = source / 'crates/ui-egui/src/menus.rs'
    if shell.exists() and ALIAS in (text := menu.read_text(encoding='utf-8')):
        shared = table(shell.read_text(encoding='utf-8'), 'SHELL_COMMANDS').group()
        menu.write_text(text.replace(ALIAS, shared.replace('SHELL_COMMANDS', 'UI_COMMANDS', 1)), encoding='utf-8')


def after(source):
    prepare_windows_input(source)
    shell = source / 'crates/engine/src/shell_commands.rs'
    menu = source / 'crates/ui-egui/src/menus.rs'
    if not shell.exists():
        return
    text = menu.read_text(encoding='utf-8')
    if ALIAS in text:
        return
    generated = table(text, 'UI_COMMANDS')
    engine = shell.read_text(encoding='utf-8')
    original = table(engine, 'SHELL_COMMANDS')
    shell.write_text(engine[:original.start()] + generated.group().replace('UI_COMMANDS', 'SHELL_COMMANDS', 1) + engine[original.end():], encoding='utf-8')
    menu.write_text(text[:generated.start()] + ALIAS + text[generated.end():], encoding='utf-8')
    ui = source / 'crates/ui-egui/src'
    prefs = ui / 'prefs_ui.rs'
    if prefs.exists():
        text = prefs.read_text(encoding='utf-8')
        # Migrate the first 0.12 layout, which let the scroll area's viewport
        # consume the entire horizontal row instead of the section column.
        text = re.sub(
            r'// Section list stays scrollable when another extension section is present\.\s*'
            r'let sections_height = settings_height\(ui.available_height\(\), ui.spacing\(\).item_spacing.y, crate::widgets::button_height\(ui.ctx\(\)\)\);\s*'
            r'egui::ScrollArea::vertical\(\).id_salt\("prefs-sections"\).max_height\(sections_height\).show\(ui, \|ui\| \{',
            '// Section list.\n        ui.vertical(|ui| {',
            text)
        if 'id_salt("prefs-sections")' not in text:
            begin = text.index('// Section list.')
            end = text.index('// The settings fill', begin)
            block = text[begin:end]
            block = block.replace('ui.set_width(170.0);',
                'ui.set_width(170.0);\n'
                '            let sections_height = settings_height(ui.available_height(), ui.spacing().item_spacing.y, crate::widgets::button_height(ui.ctx()));\n'
                '            egui::ScrollArea::vertical().id_salt("prefs-sections").max_height(sections_height).show(ui, |ui| {\n'
                '                ui.set_width(170.0);', 1)
            head, tail = block.rsplit('        });', 1)
            block = head + '            });\n        });' + tail
            text = text[:begin] + block + text[end:]
        prefs.write_text(text, encoding='utf-8')
    serbian = ui / 'i18n/sr.tsv'
    if serbian.exists():
        text = serbian.read_text(encoding='utf-8')
        for label, translation in (
            ('AI Prompt Bar', 'Traka za AI upite'),
            ('Ai Fill', 'AI popuna'),
            ('Choose Models Folder', 'Izaberi fasciklu modela'),
            ('GenGate…', 'GenGate…'),
            ('Mask Progress Bars', 'Trake napretka maski'),
        ):
            if '\t' + label + '\t' not in text:
                text += '\n\t' + label + '\t' + translation + '\n'
        serbian.write_text(text, encoding='utf-8')
    reset = ui / 'tool_reset.rs'
    text = reset.read_text(encoding='utf-8').replace(
        'Tool::ObjectSelection => reset!(enhance_edge, object_finder),',
        'Tool::ObjectSelection => reset!(enhance_edge, object_finder, object_ai_select),')
    reset.write_text(text, encoding='utf-8')
    retouch = ui / 'retouch_ui.rs'
    text = retouch.read_text(encoding='utf-8').replace(
        'if (w < 2.0 || h < 2.0) && app.ui.tool_options.object_finder {',
        'if (w < 2.0 || h < 2.0) && !ai_select && app.ui.tool_options.object_finder {')
    retouch.write_text(text, encoding='utf-8')
    tests = ui / 'genstudio_ui.rs'
    text = tests.read_text(encoding='utf-8')
    for value in ('app.ui.tool_options', 'options', 'reset'):
        text = text.replace(value + '.sample_all_layers', value + '.sample_all.remove')
    tests.write_text(text, encoding='utf-8')
    remove = source / 'crates/engine/src/retouch_cmds/remove'
    qwen = remove / 'qwen.rs'
    text = qwen.read_text(encoding='utf-8').replace('let hole = hole(plan, None);', 'let hole = hole(plan, None)?;')
    qwen.write_text(text, encoding='utf-8')


def prepare_windows_input(source):
    """Avoid starting an unused, non-pumping WGL window on Windows."""
    path = source / 'apps/photocraft/src/gpu_startup.rs'
    if not path.exists():
        return
    text = path.read_text(encoding='utf-8')
    marker = '// GenGate: unused WGL windows must not block Windows input-language broadcasts.'
    if marker in text:
        return
    old = '''    let auto = plan.env.is_none() && plan.backend == GpuBackend::Auto && os == Os::Windows;
    (auto && dx12_vendors().contains(&INTEL)).then_some(wgpu::Backends::DX12 | wgpu::Backends::GL)'''
    new = '''    // GenGate: unused WGL windows must not block Windows input-language broadcasts.
    // wgpu-hal 30's WGL helper waits on a channel without pumping its window.
    // DefWindowProc(WM_INPUTLANGCHANGEREQUEST) then waits forever for that window.
    // Do not initialize GL merely to enumerate a fallback when native adapters
    // already exist. Keep GL available for GL-only machines and explicit choices.
    let auto = plan.env.is_none() && plan.backend == GpuBackend::Auto && os == Os::Windows;
    if !auto {
        return None;
    }
    let vendors = dx12_vendors();
    if vendors.is_empty() {
        return None;
    }
    Some(if vendors.contains(&INTEL) { wgpu::Backends::DX12 } else { wgpu::Backends::DX12 | wgpu::Backends::VULKAN })'''
    if text.count(old) != 1:
        raise RuntimeError('PhotoCraft Windows graphics initialization changed; input-language fix needs review.')
    text = text.replace(old, new)
    text = text.replace(
        '/// ids of the DX12 adapters, includes Intel: DX12, with GL as the last resort (see [`rank`]). An',
        '/// ids of the DX12 adapters: native backends only when DX12 is available. An')
    text = text.replace(
        '/// crash is in that driver. `dx12_vendors` is called only for that plan; `None` keeps [`backends`]\'\n/// choice.',
        '/// crash is in that driver, so Intel remains DX12-only. `dx12_vendors` is called only for\n/// that plan; `None` keeps [`backends`]\' choice for GL-only hardware and explicit overrides.')
    text = text.replace('let dx12_gl = Some(wgpu::Backends::DX12 | wgpu::Backends::GL);',
                        'let dx12_gl = Some(wgpu::Backends::DX12);')
    text = text.replace('// No Intel adapter: egui\'s default backends, Vulkan included.',
                        '// No Intel adapter: native backends, Vulkan included; no unused WGL window.')
    for vendors in ('vec![NVIDIA, WARP]', 'vec![AMD]'):
        text = text.replace(
            'windows_auto_backends(&auto, Os::Windows, || ' + vendors + '), None)',
            'windows_auto_backends(&auto, Os::Windows, || ' + vendors + '), Some(wgpu::Backends::DX12 | wgpu::Backends::VULKAN))')
    text = text.replace('let vulkan_left_out = without_vulkan.is_some();',
                        'let vulkan_left_out = without_vulkan.is_some_and(|backends| !backends.contains(wgpu::Backends::VULKAN));')
    anchor = '    #[test]\n    fn cpu_prefers_software_adapters() {'
    regression = '''    #[test]
    fn windows_native_adapters_do_not_create_an_unused_wgl_input_language_deadlock() {
        let auto = plan(Auto, None, None, false, Os::Windows);
        for vendors in [vec![INTEL], vec![0x10de], vec![0x1002], vec![0x1414], vec![0x10de, INTEL]] {
            let backends = windows_auto_backends(&auto, Os::Windows, || vendors).unwrap();
            assert!(!backends.contains(wgpu::Backends::GL));
            assert!(backends.contains(wgpu::Backends::DX12));
        }
        // Never enumerate DX12 or override the user's explicit OpenGL choice.
        for explicit in [
            plan(Gl, None, None, false, Os::Windows),
            plan(Auto, None, Some("gl"), false, Os::Windows),
        ] {
            assert_eq!(windows_auto_backends(&explicit, Os::Windows, || panic!("explicit backend was probed")), None);
        }
        assert_eq!(windows_auto_backends(&auto, Os::Windows, Vec::new), None);
    }

'''
    if text.count(anchor) != 1:
        raise RuntimeError('PhotoCraft graphics regression test anchor changed.')
    path.write_text(text.replace(anchor, regression + anchor), encoding='utf-8')
