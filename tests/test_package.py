import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import package_release


class PackageTests(unittest.TestCase):
    def test_release_only_contains_template_not_private_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name in package_release.FILES:
                target=root/name
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes((package_release.ROOT/name).read_bytes())
            (root/'feishu-config.json').write_text('{"app_secret":"private-test-sentinel"}')
            (root/'.local').mkdir()
            (root/'.local/config.json').write_text('{"app_secret":"private-test-sentinel"}')
            with patch.object(package_release,'ROOT',root): package_release.main()
            with ZipFile(root/'dist/schedule-widget.zip') as archive:
                names=archive.namelist()
                self.assertIn('schedule-widget/examples/feishu-config.example.json',names)
                self.assertFalse(any('/.local/' in n or n.endswith('/feishu-config.json') for n in names))
                self.assertFalse(any(b'private-test-sentinel' in archive.read(n) for n in names if not n.endswith('/test_package.py')))
                sample=json.loads(archive.read('schedule-widget/examples/feishu-config.example.json'))
                self.assertEqual(sample['app_secret'],'REPLACE_ME')


if __name__=='__main__': unittest.main()
