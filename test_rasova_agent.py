# test_rasova_agent.py
"""
Tests for rasova_agent.py's Windows auto-start / crash-restart watchdog.

Standalone script, not a Django app, so this uses plain unittest and is
run directly rather than through `manage.py test`:

    python test_rasova_agent.py

Background: the Android/Termux path has always had real crash recovery --
its boot script is a shell loop ("run the agent; if it dies, sleep 3s and
run it again, forever"), restarted by the Termux:Boot watchdog even if
Android kills the whole process. The Windows path only ever had a VBS
launcher that starts the agent ONCE at login with no restart on crash --
if the process died mid-shift (a printer driver hang, an unhandled
exception, a Windows update killing background processes), it stayed dead
until the next login or reboot. Since this is the exact process keeping
every printer in the restaurant reachable, that gap matters.

Fix: install_autostart() now writes a small watchdog .bat (the same
"run it, wait, run it again forever" shape as the Termux shell script) and
points the VBS launcher at the watchdog instead of at the agent directly.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import rasova_agent


class _TempDirsMixin:
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="rasova_agent_test_")
        self._startup_dir = os.path.join(self._tmp, "Startup")
        self._config_dir = os.path.join(self._tmp, "Rasova")
        os.makedirs(self._startup_dir, exist_ok=True)
        os.makedirs(self._config_dir, exist_ok=True)

        self._patches = [
            patch.object(rasova_agent, "_startup_dir", lambda: self._startup_dir),
            patch.object(rasova_agent, "CONFIG_DIR", self._config_dir),
            patch.object(rasova_agent, "IS_WINDOWS", True),
            patch.object(subprocess, "run", return_value=None),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)


class InstallWritesCrashRestartWatchdogTest(_TempDirsMixin, unittest.TestCase):
    def test_install_writes_a_watchdog_bat_with_a_restart_loop(self):
        rasova_agent.install_autostart()

        watchdog_path = rasova_agent._watchdog_bat_path()
        self.assertTrue(os.path.exists(watchdog_path))

        with open(watchdog_path, encoding="utf-8") as f:
            contents = f.read()
        # Structurally a "run it, wait, run it again forever" loop - the
        # same shape as the Termux boot script's `while true; do ...; done`.
        self.assertIn(":loop", contents)
        self.assertIn("goto loop", contents)
        self.assertIn("timeout", contents)
        self.assertIn(os.path.abspath(rasova_agent.__file__), contents)

    def test_vbs_launcher_points_at_the_watchdog_not_the_agent_directly(self):
        # This is the actual regression: before the fix, the VBS ran the
        # agent directly with no restart-on-crash at all.
        rasova_agent.install_autostart()

        vbs_path = rasova_agent._vbs_path()
        self.assertTrue(os.path.exists(vbs_path))
        with open(vbs_path, encoding="utf-8") as f:
            vbs_contents = f.read()

        watchdog_path = rasova_agent._watchdog_bat_path()
        self.assertIn(watchdog_path, vbs_contents)
        self.assertNotIn(os.path.abspath(rasova_agent.__file__), vbs_contents)

    def test_watchdog_bat_launches_pythonw_when_available(self):
        rasova_agent.install_autostart()
        with open(rasova_agent._watchdog_bat_path(), encoding="utf-8") as f:
            contents = f.read()
        # Runs the agent by invoking a real python interpreter, not "start"
        # or some other construct that would detach and defeat the loop's
        # ability to notice the process died.
        self.assertIn(".exe", contents.lower())


class UninstallRemovesWatchdogTest(_TempDirsMixin, unittest.TestCase):
    def test_uninstall_removes_both_vbs_and_watchdog_bat(self):
        rasova_agent.install_autostart()
        self.assertTrue(os.path.exists(rasova_agent._vbs_path()))
        self.assertTrue(os.path.exists(rasova_agent._watchdog_bat_path()))

        rasova_agent.uninstall_autostart()

        self.assertFalse(os.path.exists(rasova_agent._vbs_path()))
        self.assertFalse(os.path.exists(rasova_agent._watchdog_bat_path()))

    def test_uninstall_on_clean_system_does_not_raise(self):
        # Nothing was ever installed - must be a clean no-op, not an error.
        try:
            rasova_agent.uninstall_autostart()
        except Exception as e:
            self.fail(f"uninstall_autostart() raised on a clean system: {e}")

    def test_uninstall_survives_process_kill_failure(self):
        # The best-effort "kill it right now" step must never make uninstall
        # look like it failed just because wmic isn't available/erroring.
        rasova_agent.install_autostart()
        with patch.object(subprocess, "run", side_effect=OSError("wmic not found")):
            try:
                rasova_agent.uninstall_autostart()
            except Exception as e:
                self.fail(f"uninstall_autostart() raised when process-kill failed: {e}")
        # The files must still have been removed despite the kill step failing.
        self.assertFalse(os.path.exists(rasova_agent._vbs_path()))


if __name__ == "__main__":
    unittest.main()
