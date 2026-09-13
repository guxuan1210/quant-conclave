"""Deprecated compatibility shim for the ``capitalradar`` package.

CapitalRadar was renamed to QuantConclave. This module keeps
``import capitalradar...`` working for one release by aliasing every
``capitalradar`` submodule to its ``quantconclave`` equivalent while emitting
a :class:`DeprecationWarning` on first import. It will be removed in the next
major release.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import sys
import warnings

_DEPRECATED = "capitalradar"
_REPLACEMENT = "quantconclave"

warnings.warn(
    "The `capitalradar` package is deprecated and will be removed in a future "
    "release. Import `quantconclave` instead.",
    DeprecationWarning,
    stacklevel=2,
)


class _AliasLoader(importlib.abc.Loader):
    """Loads a ``capitalradar.*`` module by delegating to ``quantconclave.*``."""

    def __init__(self, replacement: str) -> None:
        self._replacement = replacement

    def create_module(self, spec):
        # Fresh module — populated in exec_module so the real module's identity
        # (``__name__``, ``__spec__``) is never clobbered.
        return None

    def exec_module(self, module) -> None:
        real = importlib.import_module(self._replacement)
        spec = module.__spec__
        module.__dict__.update(real.__dict__)
        # The dict copy above brought in the real module's identity; restore
        # the alias identity the import system set on this module.
        module.__name__ = spec.name
        module.__loader__ = spec.loader
        module.__spec__ = spec
        module.__package__ = spec.name.rpartition(".")[0]
        if spec.submodule_search_locations is not None:
            module.__path__ = list(real.__path__)


class _AliasFinder(importlib.abc.MetaPathFinder):
    """Routes ``capitalradar.*`` imports to ``quantconclave.*``."""

    def find_spec(self, fullname, path=None, target=None):
        if not fullname.startswith(_DEPRECATED + "."):
            return None
        replacement = _REPLACEMENT + fullname[len(_DEPRECATED):]
        try:
            real_spec = importlib.util.find_spec(replacement)
        except (ImportError, ValueError):
            return None
        if real_spec is None:
            return None
        return importlib.util.spec_from_loader(
            fullname,
            _AliasLoader(replacement),
            is_package=real_spec.submodule_search_locations is not None,
        )


if not any(isinstance(f, _AliasFinder) for f in sys.meta_path):
    sys.meta_path.insert(0, _AliasFinder())
