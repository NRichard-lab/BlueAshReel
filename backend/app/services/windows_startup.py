"""Instance-scoped access to the Agent's existing per-user startup registration."""

from __future__ import annotations

import contextlib
import os

from app.config import AppConfig

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


class WindowsStartupRegistration:
    def __init__(self, config: AppConfig) -> None:
        if os.name != "nt" or config.native_program_dir is None or config.native_data_dir is None:
            raise OSError("Windows startup registration is unavailable")
        self.name = config.windows_service_prefix + "Tray"
        executable = config.native_program_dir / "BlueAshReelAgent.exe"
        self.command = f'"{str(executable).rstrip(chr(92))}" --data-dir "{str(config.native_data_dir).rstrip(chr(92))}"'

    @staticmethod
    def _registry() -> object:
        import winreg

        return winreg

    def enabled(self) -> bool:
        registry = self._registry()
        try:
            with registry.OpenKey(registry.HKEY_CURRENT_USER, RUN_KEY) as key:
                value = registry.QueryValueEx(key, self.name)[0]
            return isinstance(value, str) and value.casefold() == self.command.casefold()
        except FileNotFoundError:
            return False

    def set_enabled(self, enabled: bool) -> None:
        registry = self._registry()
        with registry.CreateKey(registry.HKEY_CURRENT_USER, RUN_KEY) as key:
            if enabled:
                registry.SetValueEx(key, self.name, 0, registry.REG_SZ, self.command)
            else:
                with contextlib.suppress(FileNotFoundError):
                    registry.DeleteValue(key, self.name)


def startup_registration(config: AppConfig) -> WindowsStartupRegistration | None:
    try:
        return WindowsStartupRegistration(config)
    except OSError:
        return None
