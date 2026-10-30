import sys
import tempfile
from pathlib import Path
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from photocraft_source import GROUPS, SourcePath, source_view


class PhotoCraftSourceTests(unittest.TestCase):
    def test_split_files_round_trip_and_hooks_keep_the_native_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'crates/engine/src').mkdir(parents=True)
            (root/'crates/engine/src/cmd.rs').write_text('commands\n')
            paths=GROUPS['crates/ui-egui/src/lib.rs']
            for index,relative in enumerate(paths):
                path=root/relative;path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text(f'part {index}\n')
            logical=source_view(root)/'crates/ui-egui/src/lib.rs'
            text=logical.read_text()
            logical.write_text(text)
            self.assertEqual([(root/p).read_text() for p in paths],['part 0\n','part 1\n','part 2\n'])
            logical.write_text('#![recursion_limit="256"]\n'+text.replace('part 1','constructor')+'\nmod extension;\n')
            self.assertEqual((root/paths[1]).read_text(),'constructor\n')
            self.assertEqual((root/paths[2]).read_text(),'part 2\n')
            self.assertIn('mod extension;', (root/paths[0]).read_text())
            self.assertTrue((root/paths[0]).read_text().startswith('#![recursion_limit'))
            self.assertNotIn('\0',(root/paths[0]).read_text())

    def test_missing_section_refuses_all_writes_and_old_layout_uses_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            self.assertIsInstance(source_view(root),Path)
            view=SourcePath(root,'crates/ui-egui/src/state.rs')
            for path in view.targets():
                path.parent.mkdir(parents=True,exist_ok=True);path.write_text('original\n')
            text=view.read_text().replace(view.marker(view.targets()[1]),'lost')
            with self.assertRaises(ValueError):view.write_text(text)
            self.assertEqual([p.read_text() for p in view.targets()],['original\n','original\n'])
            self.assertTrue(str(SourcePath(root)/'crates/engine/src/retouch_cmds/remove.rs').endswith(str(Path('cmd/retouch_cmds/remove.rs'))))
