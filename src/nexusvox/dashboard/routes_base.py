"""Dashboard routes: what the topic modules share."""

from __future__ import annotations

from flask import abort, jsonify, make_response


def _switch(data: dict, default: bool | None) -> bool:
    """A switch's `enabled` from its request body, `default` when it is not sent; anything else is a 400.

    A `default` of None makes the field required.
    """
    value = data.get("enabled", default)
    if not isinstance(value, bool):  # bool("false") would be True
        abort(make_response(jsonify({"error": "enabled must be true or false"}), 400))
    return value
