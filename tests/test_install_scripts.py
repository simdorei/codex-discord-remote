from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class InstallScriptTests(unittest.TestCase):
    def test_windows_install_bootstraps_python_312(self) -> None:
        text = (ROOT / "install.ps1").read_text(encoding="utf-8")

        self.assertIn("$RequiredPythonMajor = 3", text)
        self.assertIn("$RequiredPythonMinor = 12", text)
        self.assertIn("$PortablePythonVersion = '3.12.1'", text)
        self.assertIn(".python-portable", text)
        self.assertIn("python-${PortablePythonVersion}-embed-amd64.zip", text)
        self.assertIn("python.org/ftp/python/${PortablePythonVersion}", text)
        self.assertIn("get-pip.py", text)
        self.assertIn("get-pip.log", text)
        self.assertIn("$updatedLines.Add('..')", text)
        self.assertIn("Set-EnvFileValue -Name 'PYTHON_EXE'", text)
        self.assertNotIn("winget", text)
        self.assertNotIn("Install Python 3.11+", text)

    def test_windows_launcher_uses_python_312_only(self) -> None:
        text = (ROOT / "codex-discord-bot.cmd").read_text(encoding="utf-8")

        self.assertIn(r".python-portable\python.exe", text)
        self.assertIn("Portable Python 3.12 was not found", text)
        self.assertNotIn("Python313", text)
        self.assertNotIn('py -3 "%SCRIPT%"', text)
        self.assertNotIn("py -3.12", text)
        self.assertNotIn('python "%SCRIPT%"', text)

    def test_setup_wrappers_require_python_312(self) -> None:
        powershell = (ROOT / "setup-discord-bot.ps1").read_text(encoding="utf-8")
        shell = (ROOT / "setup-discord-bot.sh").read_text(encoding="utf-8")

        self.assertIn(r".python-portable\python.exe", powershell)
        self.assertIn("Portable Python 3.12 was not found", powershell)
        self.assertIn("python3.12", shell)
        self.assertIn("Python 3.12 was not found", shell)

    def test_macos_install_requires_python_312_without_auto_install(self) -> None:
        text = (ROOT / "install.sh").read_text(encoding="utf-8")

        self.assertIn("required_python_minor=12", text)
        self.assertIn("Would require Python 3.12 on PATH or --python-exe", text)
        self.assertIn("set_env_value PYTHON_EXE", text)
        self.assertNotIn("brew install", text)
        self.assertNotIn("Install Python 3.11+", text)


if __name__ == "__main__":
    unittest.main()
