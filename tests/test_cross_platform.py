# -*- coding: utf-8 -*-
"""Cross-platform check: the module must import and run without `winreg`.

`winreg` only exists on Windows, but CI runs the pure half of this project on
ubuntu and macos. Importing `midas_wizard` there must not explode just because
the registry probe cannot run.

`winreg` is a *built-in* C module on Windows, so blocking it needs
`sys.modules["winreg"] = None` plus a meta-path finder — and it has to happen in
a FRESH interpreter, because an already-imported module stays cached.
"""
import os
import re
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PROBE = r'''
import sys


class BlockWinreg:
    """Refuse `import winreg`, the way a non-Windows interpreter does."""
    def find_spec(self, name, path=None, target=None):
        if name == "winreg":
            raise ImportError("No module named 'winreg' (simulated non-Windows)")
        return None


sys.meta_path.insert(0, BlockWinreg())
sys.modules["winreg"] = None          # built-in modules are cached; force a miss

try:
    import winreg                       # noqa: F401
except ImportError:
    pass
else:
    print("SETUP-FAILED")
    raise SystemExit(9)

import importlib.util

path = sys.argv[1]
spec = importlib.util.spec_from_file_location("mw", path)
mw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mw)             # must not raise

print("import OK", mw.__version__)
print("find_midas_exe", repr(mw.find_midas_exe()))
print("midas_status", mw.midas_status())
print("read_api_conn", mw.read_api_conn())
print("selftest", mw.selftest(verbose=False))
print("build_mct", bool(mw.build_mct(mw.default_model())))
'''


class TestRunsWithoutWinreg(unittest.TestCase):
    def test_import_and_core_paths_without_winreg(self):
        target = os.path.join(ROOT, "midas_wizard.py")
        proc = subprocess.run([sys.executable, "-c", PROBE, target],
                              capture_output=True, text=True)
        self.assertNotIn("SETUP-FAILED", proc.stdout,
                         "the winreg blocker never activated, so this test proves nothing")
        self.assertEqual(proc.returncode, 0,
                         "module failed without winreg:\n%s\n%s" % (proc.stdout, proc.stderr))
        self.assertIn("import OK", proc.stdout)
        self.assertIn("find_midas_exe", proc.stdout)
        self.assertIn("selftest 0", proc.stdout)
        # No winreg means the API connection info is simply unavailable. This must
        # be a clean None, not an exception. Do NOT assert the running flag: it
        # comes from tasklist and depends on whether MIDAS happens to be open, so
        # pinning it makes the test flap on a developer machine.
        self.assertIn("read_api_conn None", proc.stdout)
        m = re.search(r"midas_status \(([^)]*)\)", proc.stdout)
        self.assertIsNotNone(m, "midas_status did not report a tuple: %r" % proc.stdout)
        running, info = [p.strip() for p in m.group(1).split(",", 1)]
        self.assertIn(running, ("True", "False"), "running flag must be a bool")
        self.assertEqual(info, "None", "without winreg there must be no connection info")


if __name__ == "__main__":
    unittest.main(verbosity=2)
