"""Dashboard routes: what installed extensions add to the dashboard."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from flask import Flask, abort, jsonify, send_from_directory

if TYPE_CHECKING:
    from .api import DashboardAPI

# A panel that cannot be mounted logs under the name it has always carried.
logger = logging.getLogger("nexusvox.dashboard")


def register(app: Flask, api: DashboardAPI) -> None:
    """The extension list, the panels' static files and their mounted blueprints."""

    @app.route("/api/extensions")
    def get_extensions():
        return jsonify(api.extensions.describe())

    @app.route("/ext/<panel_id>/<path:filename>")
    def extension_static(panel_id, filename):
        panel = api.extensions.panel(panel_id)
        if panel is None:
            abort(404)
        return send_from_directory(panel.static_dir, filename)

    for panel in api.extensions.panels():
        if panel.blueprint is None:
            continue
        try:
            app.register_blueprint(panel.blueprint, url_prefix=f"/api/ext/{panel.id}")
        except Exception:  # a clashing blueprint name or a bad route must not take the dashboard down
            logger.exception("Could not mount the API of dashboard panel %r; the panel stays without it", panel.id)
