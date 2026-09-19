"""A small, stdlib-only JSON Schema checker for the FloofyCrew schemas (Requirement 1.9).

This is deliberately not a complete JSON Schema implementation. It covers the
draft 2020-12 keywords the two shipped schemas use — ``type``, ``enum``,
``const``, ``pattern``, ``minLength``/``maxLength``, ``minimum``/``maximum``,
``required``, ``properties``, ``patternProperties``, ``additionalProperties``,
``propertyNames``, ``minProperties``/``maxProperties``, ``items``, ``minItems``/
``maxItems``, ``uniqueItems``, ``allOf``/``anyOf``/``oneOf``/``not`` and ``$ref``
to any JSON pointer inside the same document. ``format`` and annotation keywords
are ignored. Every keyword that appears in ``floofy.schema.json`` or
``patch.schema.json`` is exercised by the tests against the example mods.

Error paths are JSON pointers into the *instance* (``/parts/0/kind``); the
empty string denotes the root.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

__all__ = ["SchemaError", "validate_instance"]

_TYPE_NAMES = ("object", "array", "string", "integer", "number", "boolean", "null")

#: Keywords that carry no validation semantics for our purposes.
_IGNORED = frozenset(
    {"$schema", "$id", "$comment", "$defs", "definitions", "title", "description", "default", "examples", "format", "deprecated"}
)


@dataclass(frozen=True)
class SchemaError:
    """One violation. ``path`` is a JSON pointer into the instance ("" = root)."""

    path: str
    message: str
    keyword: str
    details: tuple[str, ...] = field(default_factory=tuple)

    def __str__(self) -> str:
        where = self.path or "/"
        return f"{where}: {self.message}"


def validate_instance(instance: Any, schema: dict[str, Any]) -> list[SchemaError]:
    """Check ``instance`` against ``schema``; return every violation found (empty = valid)."""
    errors: list[SchemaError] = []
    _check(instance, schema, "", schema, errors)
    return errors


# --- helpers -----------------------------------------------------------------------


def _escape(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _join(path: str, token: str | int) -> str:
    return f"{path}/{_escape(str(token))}"


def _resolve_ref(ref: str, root: dict[str, Any]) -> Any:
    if not ref.startswith("#"):
        raise ValueError(f"only same-document $ref values are supported, got {ref!r}")
    node: Any = root
    pointer = ref[1:]
    if pointer.startswith("/"):
        for raw in pointer[1:].split("/"):
            token = raw.replace("~1", "/").replace("~0", "~")
            if isinstance(node, list):
                node = node[int(token)]
            else:
                node = node[token]
    elif pointer:
        raise ValueError(f"unsupported $ref {ref!r}")
    return node


def _is_type(value: Any, name: str) -> bool:
    if name == "object":
        return isinstance(value, dict)
    if name == "array":
        return isinstance(value, list)
    if name == "string":
        return isinstance(value, str)
    if name == "boolean":
        return isinstance(value, bool)
    if name == "null":
        return value is None
    if name == "integer":
        return (isinstance(value, int) and not isinstance(value, bool)) or (isinstance(value, float) and value.is_integer())
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    raise ValueError(f"unknown type {name!r}")


def _type_name(value: Any) -> str:
    for name in ("null", "boolean", "integer", "number", "string", "array", "object"):
        if _is_type(value, name):
            return name
    return type(value).__name__


def _json_equal(a: Any, b: Any) -> bool:
    """JSON equality: booleans never equal numbers, 1 == 1.0."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_json_equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_json_equal(x, y) for x, y in zip(a, b))
    return type(a) is type(b) and a == b


_END_ANCHOR = re.compile(r"(?<!\\)\$")


@lru_cache(maxsize=256)
def _compiled(pattern: str) -> re.Pattern[str]:
    """Compile with ECMA-262 semantics for ``$``: end of input only, not before a trailing newline."""
    return re.compile(_END_ANCHOR.sub(r"(?!\\n)$", pattern))


def _display(value: Any, limit: int = 60) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


# --- the checker -------------------------------------------------------------------


def _check(instance: Any, schema: Any, path: str, root: dict[str, Any], errors: list[SchemaError]) -> None:
    if schema is True or schema == {}:
        return
    if schema is False:
        errors.append(SchemaError(path, "no value is allowed here", "false"))
        return
    if not isinstance(schema, dict):
        raise ValueError(f"schema at {path or '/'} must be an object or boolean")

    if "$ref" in schema:
        _check(instance, _resolve_ref(schema["$ref"], root), path, root, errors)

    for keyword, argument in schema.items():
        if keyword in _IGNORED or keyword == "$ref":
            continue
        handler = _HANDLERS.get(keyword)
        if handler is None:
            raise ValueError(f"unsupported schema keyword {keyword!r} at instance path {path or '/'}")
        handler(instance, argument, schema, path, root, errors)


def _kw_type(instance, argument, schema, path, root, errors) -> None:
    names = [argument] if isinstance(argument, str) else list(argument)
    if not any(_is_type(instance, name) for name in names):
        expected = " or ".join(names)
        errors.append(SchemaError(path, f"expected {expected}, got {_type_name(instance)}", "type"))


def _kw_enum(instance, argument, schema, path, root, errors) -> None:
    if not any(_json_equal(instance, option) for option in argument):
        options = ", ".join(_display(o) for o in argument)
        errors.append(SchemaError(path, f"value {_display(instance)} is not one of: {options}", "enum"))


def _kw_const(instance, argument, schema, path, root, errors) -> None:
    if not _json_equal(instance, argument):
        errors.append(SchemaError(path, f"value {_display(instance)} must be {_display(argument)}", "const"))


def _kw_pattern(instance, argument, schema, path, root, errors) -> None:
    if isinstance(instance, str) and not _compiled(argument).search(instance):
        errors.append(SchemaError(path, f"value {_display(instance)} does not match pattern {argument}", "pattern"))


def _kw_min_length(instance, argument, schema, path, root, errors) -> None:
    if isinstance(instance, str) and len(instance) < argument:
        errors.append(SchemaError(path, f"string is shorter than {argument} character(s)", "minLength"))


def _kw_max_length(instance, argument, schema, path, root, errors) -> None:
    if isinstance(instance, str) and len(instance) > argument:
        errors.append(SchemaError(path, f"string is longer than {argument} character(s)", "maxLength"))


def _kw_minimum(instance, argument, schema, path, root, errors) -> None:
    if _is_type(instance, "number") and instance < argument:
        errors.append(SchemaError(path, f"{instance} is less than the minimum {argument}", "minimum"))


def _kw_maximum(instance, argument, schema, path, root, errors) -> None:
    if _is_type(instance, "number") and instance > argument:
        errors.append(SchemaError(path, f"{instance} is greater than the maximum {argument}", "maximum"))


def _kw_required(instance, argument, schema, path, root, errors) -> None:
    if isinstance(instance, dict):
        for name in argument:
            if name not in instance:
                errors.append(SchemaError(path, f"missing required property {name!r}", "required", (name,)))


def _kw_properties(instance, argument, schema, path, root, errors) -> None:
    if isinstance(instance, dict):
        for name, subschema in argument.items():
            if name in instance:
                _check(instance[name], subschema, _join(path, name), root, errors)


def _kw_pattern_properties(instance, argument, schema, path, root, errors) -> None:
    if isinstance(instance, dict):
        for pattern, subschema in argument.items():
            for name, value in instance.items():
                if _compiled(pattern).search(name):
                    _check(value, subschema, _join(path, name), root, errors)


def _kw_additional_properties(instance, argument, schema, path, root, errors) -> None:
    if not isinstance(instance, dict):
        return
    declared = schema.get("properties", {})
    patterns = list(schema.get("patternProperties", {}))
    for name, value in instance.items():
        if name in declared or any(_compiled(p).search(name) for p in patterns):
            continue
        if argument is False:
            errors.append(SchemaError(_join(path, name), f"unexpected property {name!r}", "additionalProperties", (name,)))
        else:
            _check(value, argument, _join(path, name), root, errors)


def _kw_property_names(instance, argument, schema, path, root, errors) -> None:
    if isinstance(instance, dict):
        for name in instance:
            name_errors: list[SchemaError] = []
            _check(name, argument, _join(path, name), root, name_errors)
            if name_errors:
                errors.append(SchemaError(_join(path, name), f"property name {name!r} is not allowed", "propertyNames"))


def _kw_min_properties(instance, argument, schema, path, root, errors) -> None:
    if isinstance(instance, dict) and len(instance) < argument:
        errors.append(SchemaError(path, f"object has fewer than {argument} propert(y/ies)", "minProperties"))


def _kw_max_properties(instance, argument, schema, path, root, errors) -> None:
    if isinstance(instance, dict) and len(instance) > argument:
        errors.append(SchemaError(path, f"object has more than {argument} properties", "maxProperties"))


def _kw_items(instance, argument, schema, path, root, errors) -> None:
    if isinstance(instance, list):
        for index, item in enumerate(instance):
            _check(item, argument, _join(path, index), root, errors)


def _kw_min_items(instance, argument, schema, path, root, errors) -> None:
    if isinstance(instance, list) and len(instance) < argument:
        errors.append(SchemaError(path, f"array has fewer than {argument} item(s)", "minItems"))


def _kw_max_items(instance, argument, schema, path, root, errors) -> None:
    if isinstance(instance, list) and len(instance) > argument:
        errors.append(SchemaError(path, f"array has more than {argument} item(s)", "maxItems"))


def _kw_unique_items(instance, argument, schema, path, root, errors) -> None:
    if argument and isinstance(instance, list):
        for i, a in enumerate(instance):
            if any(_json_equal(a, b) for b in instance[:i]):
                errors.append(SchemaError(_join(path, i), f"duplicate item {_display(a)}", "uniqueItems"))
                return


def _kw_all_of(instance, argument, schema, path, root, errors) -> None:
    for subschema in argument:
        _check(instance, subschema, path, root, errors)


def _branch_errors(instance, branches, path, root) -> list[list[SchemaError]]:
    results = []
    for branch in branches:
        branch_errors: list[SchemaError] = []
        _check(instance, branch, path, root, branch_errors)
        results.append(branch_errors)
    return results


def _discriminate(instance, branches, root) -> tuple[str | None, list[int], list[Any]]:
    """Find a property every branch pins with ``const``/``enum``; return (name, matching branch indexes, allowed values)."""
    if not isinstance(instance, dict):
        return None, [], []
    resolved = [_resolve_ref(b["$ref"], root) if isinstance(b, dict) and "$ref" in b else b for b in branches]
    candidates: set[str] | None = None
    for branch in resolved:
        pinned = {
            name
            for name, sub in (branch.get("properties", {}) if isinstance(branch, dict) else {}).items()
            if isinstance(sub, dict) and ("const" in sub or "enum" in sub)
        }
        candidates = pinned if candidates is None else candidates & pinned
    if not candidates:
        return None, [], []
    name = sorted(candidates)[0]
    allowed: list[Any] = []
    matching: list[int] = []
    for index, branch in enumerate(resolved):
        sub = branch["properties"][name]
        values = [sub["const"]] if "const" in sub else list(sub["enum"])
        allowed.extend(values)
        if name in instance and any(_json_equal(instance[name], v) for v in values):
            matching.append(index)
    return name, matching, allowed


def _report_no_match(instance, branches, path, root, errors, keyword, results) -> None:
    name, matching, allowed = _discriminate(instance, branches, root)
    if name is not None and len(matching) == 1:
        errors.extend(results[matching[0]])
        return
    if name is not None and isinstance(instance, dict):
        shown = ", ".join(_display(v) for v in allowed)
        value = _display(instance.get(name)) if name in instance else "missing"
        errors.append(SchemaError(_join(path, name), f"unexpected {name} {value}; expected one of: {shown}", keyword))
        return
    errors.append(SchemaError(path, f"value does not match any of the {len(branches)} allowed shapes", keyword))


def _kw_any_of(instance, argument, schema, path, root, errors) -> None:
    results = _branch_errors(instance, argument, path, root)
    if not any(not r for r in results):
        _report_no_match(instance, argument, path, root, errors, "anyOf", results)


def _kw_one_of(instance, argument, schema, path, root, errors) -> None:
    results = _branch_errors(instance, argument, path, root)
    matches = [i for i, r in enumerate(results) if not r]
    if len(matches) == 1:
        return
    if not matches:
        _report_no_match(instance, argument, path, root, errors, "oneOf", results)
    else:
        errors.append(SchemaError(path, f"value matches {len(matches)} shapes, expected exactly one", "oneOf"))


def _kw_not(instance, argument, schema, path, root, errors) -> None:
    inner: list[SchemaError] = []
    _check(instance, argument, path, root, inner)
    if not inner:
        errors.append(SchemaError(path, "value matches a forbidden shape", "not"))


_HANDLERS = {
    "type": _kw_type,
    "enum": _kw_enum,
    "const": _kw_const,
    "pattern": _kw_pattern,
    "minLength": _kw_min_length,
    "maxLength": _kw_max_length,
    "minimum": _kw_minimum,
    "maximum": _kw_maximum,
    "required": _kw_required,
    "properties": _kw_properties,
    "patternProperties": _kw_pattern_properties,
    "additionalProperties": _kw_additional_properties,
    "propertyNames": _kw_property_names,
    "minProperties": _kw_min_properties,
    "maxProperties": _kw_max_properties,
    "items": _kw_items,
    "minItems": _kw_min_items,
    "maxItems": _kw_max_items,
    "uniqueItems": _kw_unique_items,
    "allOf": _kw_all_of,
    "anyOf": _kw_any_of,
    "oneOf": _kw_one_of,
    "not": _kw_not,
}
