"""Local file publication and strict JSON helpers."""

import contextlib
import json
import os
import stat
import tempfile
from pathlib import Path


class InputError(ValueError):
    """A documented input contract was violated."""


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputError("duplicate JSON object member")
        result[key] = value
    return result


def strict_json(text):
    def invalid_constant(value):
        raise InputError("non-finite JSON constant")

    try:
        return json.loads(text, object_pairs_hook=unique_object, parse_constant=invalid_constant)
    except InputError:
        raise
    except (ValueError, RecursionError) as exc:
        raise InputError("invalid JSON") from exc


def regular(path):
    path = Path(path)
    if not stat.S_ISREG(path.stat().st_mode):
        raise InputError("input must be a regular file")
    return path


def load_config(path):
    with regular(path).open("rb") as source:
        data = source.read(65537)
    if len(data) > 65536:
        raise InputError("configuration exceeds 64 KiB")
    try:
        return strict_json(data.decode("utf-8-sig"))
    except UnicodeError as exc:
        raise InputError("configuration must be UTF-8") from exc


def keys(obj, allowed, required):
    if not isinstance(obj, dict) or set(obj) - set(allowed) or set(required) - set(obj):
        raise InputError("missing or unknown configuration keys")


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise InputError(f"expected integer in {low}..{high}")
    return value


def names(values):
    if (
        not isinstance(values, list)
        or not values
        or len(values) > 1024
        or any(not isinstance(v, str) or not v or any(ord(c) < 32 for c in v) for v in values)
    ):
        raise InputError("expected 1..1024 nonempty column names without controls")
    if len(set(values)) != len(values):
        raise InputError("duplicate column name")
    return values


@contextlib.contextmanager
def publish(path, inputs, force=False):
    """Stage beside destination; link for atomic no-clobber, replace if explicit."""
    path = Path(path)
    if path.is_symlink():
        raise InputError("output must not be a symbolic link")
    for source in inputs:
        if path.resolve() == Path(source).resolve():
            raise InputError("output must differ from every input")
        if path.exists() and os.path.samefile(path, source):
            raise InputError("output must differ from every input")
    if path.exists() and not force:
        raise InputError("output already exists; choose another path or --force")
    fd, temporary = tempfile.mkstemp(prefix=".transit-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as target:
            yield target
            target.flush()
            os.fsync(target.fileno())
        if force:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
