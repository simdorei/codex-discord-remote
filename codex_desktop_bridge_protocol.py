from __future__ import annotations

try:
    import winreg
except ImportError:
    winreg = None


def is_protocol_registered(protocol: str) -> bool:
    if not protocol or winreg is None:
        return False

    candidates = [
        (winreg.HKEY_CLASSES_ROOT, protocol),
        (winreg.HKEY_CURRENT_USER, rf"Software\Classes\{protocol}"),
    ]
    for hive, subkey in candidates:
        try:
            with winreg.OpenKey(hive, subkey):
                return True
        except FileNotFoundError:
            continue
        except OSError:
            continue
    return False
