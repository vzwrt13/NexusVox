"""The route table is put together from `routes_*.py`: a module left out would drop its routes without an error."""

from pathlib import Path

import nexusvox.dashboard as dashboard
from nexusvox.config import Config
from nexusvox.dashboard import _create_app
from nexusvox.dashboard.api import DashboardAPI


def test_every_routes_module_puts_its_routes_on_the_app(session_factory):
    package = Path(dashboard.__file__).parent
    expected = {path.stem for path in package.glob("routes_*.py")} - {"routes_base"}  # helpers, no routes

    app = _create_app(DashboardAPI(session_factory, Config()))

    served = {view.__module__.rpartition(".")[2] for view in app.view_functions.values()}
    assert expected - served == set()
