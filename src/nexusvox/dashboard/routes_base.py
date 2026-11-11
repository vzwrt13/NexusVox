"""Dashboard routes: what the topic modules share."""

from __future__ import annotations

from flask import abort, jsonify, make_response, request


def _bad_request(message: str):
    """Answer 400 `{error}` and stop the route."""
    abort(make_response(jsonify({"error": message}), 400))


def _json_object() -> dict:
    """The request's JSON body; valid JSON that is no object (`[]`, `null`, `1`) is a 400."""
    data = request.get_json(force=True)
    if not isinstance(data, dict):
        _bad_request("Expected a JSON object")
    return data


def _typed(data: dict, key: str, kind: type, default, label: str):
    """`data[key]` when it is a `kind`, `default` when it is not sent; anything else is a 400."""
    value = data.get(key, default)
    if not isinstance(value, kind):
        _bad_request(f"{key} must be {label}")
    return value


def _switch(data: dict, default: bool | None) -> bool:
    """A switch's `enabled` from its request body, `default` when it is not sent; anything else is a 400.

    A `default` of None makes the field required.
    """
    return _typed(data, "enabled", bool, default, "true or false")  # bool("false") would be True


def _optional_text(data: dict, key: str) -> str | None:
    """A text field that may be missing or null; any other type is a 400."""
    value = data.get(key)
    if value is not None and not isinstance(value, str):
        _bad_request(f"{key} must be text")
    return value


def _optional_id(data: dict, key: str) -> int | None:
    """A record id that may be missing or null; anything but an integer (a bool is none) is a 400."""
    value = data.get(key)
    if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
        _bad_request(f"{key} must be a whole number")
    return value


def _typed_list(data: dict, key: str, item: type) -> list:
    """A list whose items are all `item`, `[]` when it is not sent; anything else is a 400."""
    value = _typed(data, key, list, [], "a list")
    if not all(isinstance(v, item) for v in value):
        _bad_request(f"{key} must be a list of {item.__name__}")
    return value


def _typed_map(data: dict, key: str, key_type: type, value_type: type) -> dict:
    """An object whose keys and values are all of the given types, `{}` when it is not sent; else a 400."""
    value = _typed(data, key, dict, {}, "an object")
    if not all(isinstance(k, key_type) and isinstance(v, value_type) for k, v in value.items()):
        _bad_request(f"{key} must be an object of {key_type.__name__} to {value_type.__name__}")
    return value
