"""Start at login: a LaunchAgent on Mac, a Run registry entry on Windows."""

import plistlib
import sys
from pathlib import Path, PureWindowsPath

LABEL = "com.optionsjourney.app"


def launch_command(executable: str | None = None, platform: str | None = None) -> list[str]:
    """How the login item (and Restart) start the menu bar app. On Windows, pythonw.exe:
    python.exe would open a console window that stops the app when closed (decision 0094)."""
    python = executable or sys.executable
    if (platform or sys.platform) == "win32":
        path = PureWindowsPath(python)
        if path.name.lower() == "python.exe":
            python = str(path.with_name("pythonw.exe"))
    return [python, "-m", "optionsjourney.launcher"]


class MacLoginItem:
    def __init__(
        self,
        home: Path | None = None,
        command: list[str] | None = None,
        working_dir: Path | None = None,
    ):
        self.path = (home or Path.home()) / "Library" / "LaunchAgents" / f"{LABEL}.plist"
        self.command = command or launch_command()
        # Where it starts, so a developer's .env is found; harmless otherwise
        self.working_dir = working_dir or Path.cwd()

    def is_enabled(self) -> bool:
        return self.path.exists()

    def enable(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(
            plistlib.dumps(
                {
                    "Label": LABEL,
                    "ProgramArguments": self.command,
                    "RunAtLoad": True,
                    "ProcessType": "Interactive",
                    "WorkingDirectory": str(self.working_dir),
                }
            )
        )

    def disable(self) -> None:
        self.path.unlink(missing_ok=True)


class WindowsLoginItem:
    """HKCU\\...\\Run: starts the app when the user logs in to Windows."""

    KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
    NAME = "OptionsJourney"

    def __init__(self, command: list[str] | None = None):
        import winreg  # noqa: PLC0415 -- Windows only

        self._winreg = winreg
        self.command = command or launch_command()

    def is_enabled(self) -> bool:
        try:
            with self._winreg.OpenKey(self._winreg.HKEY_CURRENT_USER, self.KEY) as key:
                self._winreg.QueryValueEx(key, self.NAME)
            return True
        except OSError:
            return False

    def enable(self) -> None:
        value = " ".join(f'"{part}"' for part in self.command)
        with self._winreg.OpenKey(
            self._winreg.HKEY_CURRENT_USER, self.KEY, 0, self._winreg.KEY_SET_VALUE
        ) as key:
            self._winreg.SetValueEx(key, self.NAME, 0, self._winreg.REG_SZ, value)

    def disable(self) -> None:
        try:
            with self._winreg.OpenKey(
                self._winreg.HKEY_CURRENT_USER, self.KEY, 0, self._winreg.KEY_SET_VALUE
            ) as key:
                self._winreg.DeleteValue(key, self.NAME)
        except OSError:
            pass


def login_item():
    return WindowsLoginItem() if sys.platform == "win32" else MacLoginItem()
