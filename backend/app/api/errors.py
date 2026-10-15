"""Turns pydantic validation errors into a single readable Russian sentence.

The frontend shows `detail` to the user as-is, and pydantic's own output — an
English, machine-oriented list like `[{"type": "less_than_equal", "loc":
["capo"], ...}]` — is neither readable nor in the interface language. This
module owns the translation in one place so every endpoint that validates
user input reports problems the same way.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from pydantic import ValidationError

# Field names as the user knows them from the settings panel, keyed by both the
# camelCase alias (what the client sends) and the Python name.
_FIELD_LABELS = {
    "tuning": "строй",
    "capo": "каподастр",
    "maxFretSpan": "максимальная растяжка",
    "max_fret_span": "максимальная растяжка",
    "positionJumpWeight": "вес переходов по грифу",
    "position_jump_weight": "вес переходов по грифу",
    "openStrings": "открытые струны",
    "open_strings": "открытые струны",
    "file": "файл",
    "settings": "настройки",
}


def _field_label(loc: Iterable[Any]) -> str:
    parts = [part for part in loc if part not in ("body", "query", "path")]
    for part in parts:
        if isinstance(part, str) and part in _FIELD_LABELS:
            return _FIELD_LABELS[part]
    return ".".join(str(part) for part in parts) or "значение"


def _describe(error: Mapping[str, Any]) -> str:
    kind = error.get("type", "")
    ctx = error.get("ctx") or {}
    is_tuning = "tuning" in (error.get("loc") or ())

    if kind == "value_error":
        # Our own validators already phrase the problem in Russian; pydantic
        # just prefixes it with "Value error, ".
        message = str(ctx.get("error") or error.get("msg", ""))
        return message.removeprefix("Value error, ")
    if kind == "missing":
        return "обязательное поле не заполнено"
    if kind in ("greater_than_equal", "greater_than"):
        return f"значение должно быть не меньше {ctx.get('ge', ctx.get('gt'))}"
    if kind in ("less_than_equal", "less_than"):
        return f"значение должно быть не больше {ctx.get('le', ctx.get('lt'))}"
    if kind == "too_short":
        unit = "струны" if is_tuning else "элемента"
        return f"нужно минимум {ctx.get('min_length')} {unit}"
    if kind == "too_long":
        unit = "струн" if is_tuning else "элементов"
        return f"допускается максимум {ctx.get('max_length')} {unit}"
    if kind in ("int_parsing", "int_type", "int_from_float"):
        return "ожидается целое число"
    if kind in ("float_parsing", "float_type"):
        return "ожидается число"
    if kind in ("string_type", "list_type"):
        return "неверный тип значения"
    if kind in ("json_invalid", "json_type"):
        return "не удалось разобрать JSON"
    return str(error.get("msg", "некорректное значение"))


def describe_errors(errors: Iterable[Mapping[str, Any]], prefix: str = "Некорректные настройки") -> str:
    """Join pydantic-style error dicts into one human-readable sentence."""
    parts = []
    for error in errors:
        loc = error.get("loc") or ()
        label = _field_label(loc)
        description = _describe(error)
        # Tuning validators already name the string ("струна 2: ..."), and
        # whole-payload errors (bad JSON) have no meaningful field label.
        if error.get("type") in ("json_invalid", "json_type") or not loc:
            parts.append(description)
        else:
            parts.append(f"{label} — {description}")
    return f"{prefix}: " + "; ".join(parts) if parts else prefix


def describe_validation_error(exc: ValidationError, prefix: str = "Некорректные настройки") -> str:
    return describe_errors(exc.errors(include_url=False), prefix=prefix)
