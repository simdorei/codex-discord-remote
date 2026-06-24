from __future__ import annotations

import sys
from types import ModuleType
from typing import Protocol

import codex_desktop_bridge_impl_common as _common
import codex_desktop_bridge_impl_chunk01 as _chunk01
import codex_desktop_bridge_impl_chunk02 as _chunk02
import codex_desktop_bridge_impl_chunk03 as _chunk03
import codex_desktop_bridge_impl_chunk04 as _chunk04
import codex_desktop_bridge_impl_chunk05 as _chunk05
import codex_desktop_bridge_impl_chunk06 as _chunk06
import codex_desktop_bridge_impl_chunk07 as _chunk07


class BridgeAttributeValue(Protocol):
    pass


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


_MODULES: tuple[ModuleType, ...] = (
    _common,
    _chunk01,
    _chunk02,
    _chunk03,
    _chunk04,
    _chunk05,
    _chunk06,
    _chunk07,
)


def _export_module_names(module: ModuleType) -> list[str]:
    return [name for name in dir(module) if not _is_dunder(name)]


def _sync_exports() -> None:
    exported: dict[str, BridgeAttributeValue] = {}
    for module in _MODULES:
        for name in _export_module_names(module):
            exported[name] = getattr(module, name)
    globals().update(exported)
    for module in _MODULES:
        module.__dict__.update(exported)


def set_facade_attribute(name: str, value: BridgeAttributeValue) -> None:
    globals()[name] = value
    for module in _MODULES:
        module.__dict__[name] = value


def main() -> int:
    return _chunk06.main()


_sync_exports()

if __name__ == "__main__":
    sys.exit(main())
