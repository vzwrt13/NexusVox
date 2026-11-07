"""The topic mixins of `DashboardAPI` keep to the contract in `DashboardState`.

The mixins call each other through `self`, which neither ruff nor a type checker looks at here, so a renamed method
would otherwise fail only at runtime, on the path that uses it.
"""

from __future__ import annotations

import ast
import inspect
import sys
import textwrap
from collections import Counter

import pytest

from nexusvox.config import Config
from nexusvox.dashboard.api import DashboardAPI
from nexusvox.dashboard.api_base import DashboardState

MIXINS = [cls for cls in DashboardAPI.__mro__ if cls.__name__.endswith("Mixin")]


def _own(cls) -> set[str]:
    return {name for name in vars(cls) if not name.startswith("__")}


def _used(cls) -> set[str]:
    """Every `self.<name>` in the class."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(cls)))
    return {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self"
    }


def _declared() -> set[str]:
    return set(DashboardState.__annotations__) | _own(DashboardState)


def test_every_topic_module_is_a_mixin_of_the_api():
    assert len(MIXINS) == 10
    assert all(issubclass(cls, DashboardState) for cls in MIXINS)


@pytest.mark.parametrize("cls", MIXINS, ids=lambda cls: cls.__name__)
def test_a_mixin_uses_only_what_it_defines_or_the_contract_declares(cls):
    assert _used(cls) - _own(cls) - _declared() == set()


def test_everything_declared_exists_on_a_running_api(session_factory):
    api = DashboardAPI(session_factory, Config())

    assert [name for name in sorted(_declared()) if not hasattr(api, name)] == []


def test_nothing_declared_is_left_over():
    borrowed = set().union(*(_used(cls) - _own(cls) for cls in MIXINS))

    assert _declared() - borrowed == set()


def test_no_two_mixins_define_the_same_name():
    counts = Counter(name for cls in MIXINS for name in _own(cls))

    assert [name for name, count in counts.items() if count > 1] == []


def test_every_module_logs_under_the_one_name():
    for cls in MIXINS:
        module = sys.modules[cls.__module__]
        assert "getLogger" not in inspect.getsource(module), module.__name__
        if hasattr(module, "logger"):
            assert module.logger.name == "nexusvox.dashboard.api"
