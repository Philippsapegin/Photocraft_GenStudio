"""Pinned translation overlays; no network or native build."""
from pathlib import Path
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_craft_i18n as catalogs
from photocraft_source import SourcePath


class CatalogOverlayTests(unittest.TestCase):
    def test_logical_catalogs_resolve_external_case_and_serbian_alias(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for code in ('ru', 'pt-BR', 'sr-Latn'):
                file = root / '.gengate-build/craft-i18n/locales' / code / 'photocraft.tsv'
                file.parent.mkdir(parents=True)
                file.write_text(code, encoding='utf-8')
            view = SourcePath(root) / 'crates/ui-egui/src/i18n'
            self.assertEqual((view / 'pt-br.tsv').read_text(), 'pt-BR')
            self.assertEqual((view / 'sr.tsv').read_text(), 'sr-Latn')
            (view / 'ru.tsv').write_text('native rows plus GenGate')
            self.assertEqual(catalogs.catalog(root, 'ru').read_text(), 'native rows plus GenGate')
            self.assertEqual(len(list(view.glob('*.tsv'))), 3)

    def test_repeated_preparation_preserves_overlay_and_pristine_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder);source = project / 'source';source.mkdir()
            revision = 'a' * 40
            (source / 'craft-i18n.rev').write_text(revision)
            cache = project / '.runtime' / ('craft-i18n-' + revision[:12])
            (cache / '.git').mkdir(parents=True)
            file = cache / 'locales/ru/photocraft.tsv';file.parent.mkdir(parents=True)
            file.write_text('native rows')
            for name in ('LICENSE-MIT', 'LICENSE-APACHE'):(cache / name).write_text('fixture')
            with patch.object(catalogs, 'ROOT', project), patch.object(catalogs.subprocess, 'check_output', side_effect=lambda args, **kw: '' if 'status' in args else revision), patch.object(catalogs.subprocess, 'run') as run:
                catalogs.prepare(source)
                overlay = catalogs.catalog(source, 'ru');overlay.write_text('native rows plus GenGate')
                catalogs.prepare(source)
                self.assertEqual(overlay.read_text(), 'native rows plus GenGate')
                self.assertEqual(file.read_text(), 'native rows')
                self.assertEqual(tomllib.loads((source / '.cargo/config.toml').read_text())['env']['CRAFT_I18N_DIR']['value'], '.gengate-build/craft-i18n')
                run.assert_not_called()

    def test_legacy_catalogs_stay_in_place_and_bad_pin_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder);file = root / 'crates/ui-egui/src/i18n/ru.tsv'
            file.parent.mkdir(parents=True);file.write_text('legacy')
            self.assertIsNone(catalogs.prepare(root))
            self.assertEqual((SourcePath(root) / 'crates/ui-egui/src/i18n/ru.tsv').read_text(), 'legacy')
            (root / 'craft-i18n.rev').write_text('../wrong')
            with self.assertRaisesRegex(ValueError, 'Invalid pinned'):catalogs.prepare(root)
