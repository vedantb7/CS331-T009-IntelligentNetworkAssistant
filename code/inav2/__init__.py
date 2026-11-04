"""Compatibility package shim for inav2.

Allows both 'import inav2.<module>' and 'import <module>' to resolve to
the exact same module objects, ensuring backward compatibility with
deployment scripts, systemd units, and external test invocations without
duplicate module instances.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from importlib.abc import Loader, MetaPathFinder
from importlib.machinery import ModuleSpec


class _InaV2Loader(Loader):
    """Loader that delegates module creation to top-level packages while maintaining

    alias registration in sys.modules.
    """

    def __init__(self, real_name: str, real_spec: ModuleSpec | None = None) -> None:
        self.real_name = real_name
        self.real_spec = real_spec

    def create_module(self, spec: ModuleSpec):
        mod = importlib.import_module(self.real_name)
        sys.modules[spec.name] = mod
        parts = spec.name.split(".")
        parent_name = ".".join(parts[:-1])
        if parent_name in sys.modules:
            setattr(sys.modules[parent_name], parts[-1], mod)
        return mod

    def exec_module(self, module) -> None:
        pass

    def get_code(self, fullname: str):
        if self.real_spec and self.real_spec.loader and hasattr(self.real_spec.loader, "get_code"):
            return self.real_spec.loader.get_code(self.real_name)
        return None

    def get_source(self, fullname: str):
        if self.real_spec and self.real_spec.loader and hasattr(self.real_spec.loader, "get_source"):
            return self.real_spec.loader.get_source(self.real_name)
        return None

    def is_package(self, fullname: str) -> bool:
        if self.real_spec:
            return bool(self.real_spec.submodule_search_locations)
        return False

    def __getattr__(self, name: str):
        if self.real_spec and self.real_spec.loader:
            return getattr(self.real_spec.loader, name)
        raise AttributeError(name)


class _InaV2Finder(MetaPathFinder):
    """Meta-path finder translating `inav2.<submodule>` into top-level imports."""

    def find_spec(self, fullname: str, path=None, target=None):
        if fullname.startswith("inav2."):
            real_name = fullname[len("inav2.") :]
            real_spec = importlib.util.find_spec(real_name)
            if real_spec is None:
                return None
            spec = ModuleSpec(
                fullname,
                _InaV2Loader(real_name, real_spec),
                is_package=bool(real_spec.submodule_search_locations),
            )
            if real_spec.submodule_search_locations is not None:
                spec.submodule_search_locations = list(real_spec.submodule_search_locations)
            return spec
        return None


if not any(isinstance(finder, _InaV2Finder) for finder in sys.meta_path):
    sys.meta_path.insert(0, _InaV2Finder())


def __getattr__(name: str):
    try:
        mod = importlib.import_module(name)
        sys.modules[f"inav2.{name}"] = mod
        globals()[name] = mod
        return mod
    except ImportError as e:
        raise AttributeError(f"module 'inav2' has no attribute '{name}'") from e
