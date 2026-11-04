import importlib.util
import tarfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "scanner_transfer", Path(__file__).resolve().parents[1] / "transfer.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ArchiveTests(unittest.TestCase):
    def test_regular_relative_paths_only(self):
        item = tarfile.TarInfo("data/sast-state/result.json")
        self.assertEqual(list(module.safe_members([item])), [item])

    def test_reject_escape_absolute_symlink_and_device(self):
        for path, kind in (
            ("../etc/passwd", tarfile.REGTYPE),
            ("/etc/passwd", tarfile.REGTYPE),
            ("data/link", tarfile.SYMTYPE),
            ("data/device", tarfile.CHRTYPE),
        ):
            item = tarfile.TarInfo(path)
            item.type = kind
            with self.subTest(path=path), self.assertRaises(ValueError):
                list(module.safe_members([item]))


if __name__ == "__main__":
    unittest.main()
