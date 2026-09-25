import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent))

from runtime_pointer import PointerError, current_target, switch


class RuntimePointerTests(unittest.TestCase):
    def test_compare_and_swap_and_rollback(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            shim = root / "legacy-shim"
            bundle = root / "bundles" / "pinned"
            shim.mkdir()
            bundle.mkdir(parents=True)
            self.assertEqual(switch(root, shim, None)["status"], "switched")
            self.assertEqual(current_target(root), shim)
            with self.assertRaises(PointerError) as error:
                switch(root, bundle, None)
            self.assertEqual(str(error.exception), "current_mismatch")
            self.assertEqual(switch(root, bundle, shim)["status"], "switched")
            self.assertEqual(current_target(root), bundle)
            self.assertEqual(switch(root, shim, bundle)["status"], "switched")
            self.assertEqual(current_target(root), shim)

    def test_external_target_and_real_directory_are_refused(self):
        with TemporaryDirectory() as tmp, TemporaryDirectory() as outside:
            root = Path(tmp).resolve()
            inner = root / "bundle"
            inner.mkdir()
            with self.assertRaises(PointerError):
                switch(root, Path(outside), None)
            (root / "current").mkdir()
            with self.assertRaises(PointerError):
                current_target(root)


if __name__ == "__main__":
    unittest.main()
