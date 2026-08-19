"""Turn a provider's JSON Schema into fields the settings template can render.

Providers describe their own settings through ``get_settings_schema()``. This
module is the one place that interprets that schema, so the templates stay dumb
and a new provider gets a settings form for free.

The supported subset is deliberately small - what the provider schemas actually
use - and anything outside it raises rather than rendering a silently wrong
widget.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


class SchemaError(Exception):
    """Raised when a provider schema cannot be rendered as a form.

    This is a programming error in the provider, not operator input, so it
    fails loudly instead of dropping the field.
    """


class FieldKind(StrEnum):
    """Widget to render for a schema property."""

    SELECT = "select"
    NUMBER = "number"
    TEXT = "text"
    TEXTAREA = "textarea"


@dataclass(frozen=True)
class FieldOption:
    """One choice in a select.

    Attributes:
        value: Value submitted by the form.
        label: Human-readable text shown in the dropdown.
    """

    value: str
    label: str


@dataclass(frozen=True)
class SettingsField:
    """A single rendered form control.

    Attributes:
        name: Form field name, matching the settings model attribute.
        kind: Which widget to render.
        title: Label text.
        description: Help text shown under the control.
        value: Current value, already stringified for the template.
        options: Choices, for SELECT fields.
        minimum: Lower bound for numeric inputs.
        maximum: Upper bound for numeric inputs.
        step: Step for numeric inputs.
        optional: Whether an empty submission is meaningful (nullable field).
    """

    name: str
    kind: FieldKind
    title: str
    description: str
    value: str
    options: tuple[FieldOption, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    optional: bool = False


def _as_dict(value: object, context: str) -> dict[str, object]:
    """Narrow a schema fragment to a dict.

    Args:
        value: Fragment taken from the schema.
        context: Location, for the error message.

    Returns:
        The fragment as a dict with validated string keys.

    Raises:
        SchemaError: If the fragment is not an object with string keys.
    """
    if not isinstance(value, dict):
        raise SchemaError(f"{context}: expected an object, got {type(value).__name__}")
    return {_as_str(key, f"{context}: key"): item for key, item in value.items()}


def _as_str(value: object, context: str) -> str:
    """Narrow a schema value to a string.

    Args:
        value: Value taken from the schema.
        context: Location, for the error message.

    Returns:
        The value as a string.

    Raises:
        SchemaError: If the value is not a string.
    """
    if not isinstance(value, str):
        raise SchemaError(f"{context}: expected a string, got {type(value).__name__}")
    return value


def _as_number(value: object, context: str) -> float | None:
    """Narrow an optional numeric bound.

    Args:
        value: Value taken from the schema, or None if absent.
        context: Location, for the error message.

    Returns:
        The bound, or None when absent.

    Raises:
        SchemaError: If present but not numeric.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise SchemaError(f"{context}: expected a number, got {type(value).__name__}")
    return float(value)


def _field_types(prop: dict[str, object], context: str) -> tuple[str, bool]:
    """Split a property's ``type`` into its base type and nullability.

    Args:
        prop: Schema property.
        context: Location, for the error message.

    Returns:
        Tuple of (base type name, whether null is allowed).

    Raises:
        SchemaError: If the type is missing or not a supported shape.
    """
    raw = prop.get("type")

    if isinstance(raw, str):
        return raw, False

    if isinstance(raw, list):
        names = [_as_str(entry, f"{context}.type") for entry in raw]
        concrete = [name for name in names if name != "null"]
        if len(concrete) != 1:
            raise SchemaError(
                f"{context}: expected exactly one non-null type, got {names}"
            )
        return concrete[0], "null" in names

    raise SchemaError(f"{context}: 'type' is required and must be a string or list")


def _render_value(value: object) -> str:
    """Stringify a stored value for an HTML input.

    Args:
        value: Value from the settings model, possibly None.

    Returns:
        Text to place in the control; empty string for None.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _step_for(base_type: str, minimum: float | None, maximum: float | None) -> float:
    """Pick an input step that matches the field's range.

    Args:
        base_type: "integer" or "number".
        minimum: Lower bound, if any.
        maximum: Upper bound, if any.

    Returns:
        Step value for the number input.
    """
    if base_type == "integer":
        return 1.0
    if minimum is not None and maximum is not None and (maximum - minimum) <= 2:
        return 0.1
    return 0.01


def build_fields(
    schema: Mapping[str, object],
    values: Mapping[str, object],
    options: Mapping[str, Sequence[FieldOption]] | None = None,
) -> list[SettingsField]:
    """Build renderable form fields from a provider settings schema.

    Args:
        schema: The provider's ``get_settings_schema()`` output.
        values: Currently stored settings, as a plain mapping.
        options: Choices to use instead of the schema's ``enum`` for a field -
            how model and voice catalogues from the provider protocol reach the
            form with their display names.

    Returns:
        Fields in schema order.

    Raises:
        SchemaError: If the schema uses a shape this renderer does not support.
    """
    overrides = options or {}
    properties = _as_dict(schema.get("properties"), "schema.properties")

    fields: list[SettingsField] = []

    for name, raw_property in properties.items():
        context = f"schema.properties.{name}"
        prop = _as_dict(raw_property, context)
        base_type, optional = _field_types(prop, context)

        title = _as_str(prop.get("title", name), f"{context}.title")
        description = _as_str(prop.get("description", ""), f"{context}.description")
        value = _render_value(values.get(name, prop.get("default")))

        supplied = overrides.get(name)
        if supplied is not None:
            choices = tuple(supplied)
        elif "enum" in prop:
            raw_enum = prop["enum"]
            if not isinstance(raw_enum, list):
                raise SchemaError(f"{context}.enum: expected a list")
            choices = tuple(
                FieldOption(value=_as_str(entry, f"{context}.enum"), label=str(entry))
                for entry in raw_enum
            )
        else:
            choices = ()

        if choices:
            fields.append(
                SettingsField(
                    name=name,
                    kind=FieldKind.SELECT,
                    title=title,
                    description=description,
                    value=value,
                    options=choices,
                )
            )
            continue

        if base_type in {"number", "integer"}:
            minimum = _as_number(prop.get("minimum"), f"{context}.minimum")
            maximum = _as_number(prop.get("maximum"), f"{context}.maximum")
            fields.append(
                SettingsField(
                    name=name,
                    kind=FieldKind.NUMBER,
                    title=title,
                    description=description,
                    value=value,
                    minimum=minimum,
                    maximum=maximum,
                    step=_step_for(base_type, minimum, maximum),
                    optional=optional,
                )
            )
            continue

        if base_type == "string":
            multiline = prop.get("format") == "textarea"
            fields.append(
                SettingsField(
                    name=name,
                    kind=FieldKind.TEXTAREA if multiline else FieldKind.TEXT,
                    title=title,
                    description=description,
                    value=value,
                    optional=optional,
                )
            )
            continue

        raise SchemaError(f"{context}: unsupported type '{base_type}'")

    return fields
