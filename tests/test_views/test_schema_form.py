"""Tests for rendering provider settings schemas as form fields."""

import pytest
from pydantic import SecretStr

from src.providers.llm.gemini import GeminiLLMProvider
from src.providers.tts.gemini import GeminiTTSProvider
from src.providers.tts.piper import PiperTTSProvider
from src.views.schema_form import (
    FieldKind,
    FieldOption,
    SchemaError,
    build_fields,
)


def _schema(**properties: object) -> dict[str, object]:
    """Wrap properties in a minimal object schema."""
    return {"type": "object", "properties": properties}


class TestFieldKinds:
    """Each supported property shape maps to one widget."""

    def test_enum_becomes_a_select(self) -> None:
        """A string with an enum renders as a dropdown of its values."""
        schema = _schema(mode={"type": "string", "enum": ["fast", "slow"], "title": "Mode"})

        fields = build_fields(schema, {"mode": "slow"})

        assert len(fields) == 1
        assert fields[0].kind is FieldKind.SELECT
        assert fields[0].value == "slow"
        assert [option.value for option in fields[0].options] == ["fast", "slow"]

    def test_number_carries_its_bounds(self) -> None:
        """Numeric bounds reach the input so the browser can enforce them."""
        schema = _schema(
            temperature={"type": "number", "minimum": 0, "maximum": 2, "default": 0.8}
        )

        field = build_fields(schema, {})[0]

        assert field.kind is FieldKind.NUMBER
        assert field.value == "0.8"
        assert field.minimum == 0.0
        assert field.maximum == 2.0
        assert field.step == 0.1

    def test_integer_steps_by_one(self) -> None:
        """Integers cannot be nudged by fractions."""
        schema = _schema(max_tokens={"type": "integer", "minimum": 50, "maximum": 2000})

        field = build_fields(schema, {"max_tokens": 500})[0]

        assert field.step == 1.0
        assert field.value == "500"

    def test_nullable_field_is_optional_and_renders_empty(self) -> None:
        """A null-able number accepts a blank input, meaning "model default"."""
        schema = _schema(thinking_budget={"type": ["integer", "null"], "minimum": -1})

        field = build_fields(schema, {"thinking_budget": None})[0]

        assert field.optional is True
        assert field.value == ""

    def test_textarea_hint(self) -> None:
        """A long free-text field asks for a textarea."""
        schema = _schema(style={"type": "string", "format": "textarea"})

        assert build_fields(schema, {"style": "Whisper"})[0].kind is FieldKind.TEXTAREA

    def test_plain_string_is_a_text_input(self) -> None:
        """Without a format hint a string is a single-line input."""
        schema = _schema(label={"type": "string"})

        assert build_fields(schema, {})[0].kind is FieldKind.TEXT


class TestValuesAndOptions:
    """Stored values and injected catalogues."""

    def test_stored_value_wins_over_default(self) -> None:
        """The form shows what is configured, not the schema default."""
        schema = _schema(model={"type": "string", "enum": ["a", "b"], "default": "a"})

        assert build_fields(schema, {"model": "b"})[0].value == "b"

    def test_default_used_when_nothing_stored(self) -> None:
        """A field absent from storage falls back to the schema default."""
        schema = _schema(model={"type": "string", "enum": ["a", "b"], "default": "a"})

        assert build_fields(schema, {})[0].value == "a"

    def test_supplied_options_replace_the_enum(self) -> None:
        """Model and voice catalogues arrive with display names attached."""
        schema = _schema(model={"type": "string", "enum": ["raw-id"]})

        fields = build_fields(
            schema,
            {"model": "gemini-2.5-flash"},
            {"model": [FieldOption(value="gemini-2.5-flash", label="Gemini 2.5 Flash")]},
        )

        assert [(o.value, o.label) for o in fields[0].options] == [
            ("gemini-2.5-flash", "Gemini 2.5 Flash")
        ]

    def test_field_order_follows_the_schema(self) -> None:
        """Providers control the order their settings appear in."""
        schema = _schema(
            first={"type": "string"},
            second={"type": "integer"},
            third={"type": "number"},
        )

        assert [field.name for field in build_fields(schema, {})] == [
            "first",
            "second",
            "third",
        ]


class TestSchemaValidation:
    """A malformed schema is a provider bug and must not render silently."""

    def test_missing_properties(self) -> None:
        """A schema without properties cannot produce a form."""
        with pytest.raises(SchemaError, match="expected an object"):
            build_fields({"type": "object"}, {})

    def test_missing_type(self) -> None:
        """Every property must say what it is."""
        with pytest.raises(SchemaError, match="'type' is required"):
            build_fields(_schema(broken={"title": "No type"}), {})

    def test_unsupported_type(self) -> None:
        """An unknown type raises instead of rendering the wrong widget."""
        with pytest.raises(SchemaError, match="unsupported type 'array'"):
            build_fields(_schema(items={"type": "array"}), {})

    def test_ambiguous_union(self) -> None:
        """Only "X or null" unions are renderable."""
        with pytest.raises(SchemaError, match="exactly one non-null type"):
            build_fields(_schema(mixed={"type": ["string", "integer"]}), {})

    def test_non_numeric_bound(self) -> None:
        """A bound that is not a number would produce a broken input."""
        with pytest.raises(SchemaError, match="expected a number"):
            build_fields(_schema(size={"type": "integer", "minimum": "small"}), {})


class TestRealProviderSchemas:
    """Every shipped provider schema renders."""

    def test_gemini_llm(self) -> None:
        """The Gemini LLM form covers every constructor knob."""
        provider = GeminiLLMProvider(api_key=SecretStr("test"))

        fields = build_fields(provider.get_settings_schema(), {})

        assert [field.name for field in fields] == [
            "model",
            "temperature",
            "max_output_tokens",
            "thinking_budget",
            "safety_threshold",
        ]
        assert fields[3].optional is True

    def test_gemini_tts(self) -> None:
        """The Gemini TTS form puts the style direction in a textarea."""
        provider = GeminiTTSProvider(api_key=SecretStr("test"))

        fields = {field.name: field for field in build_fields(provider.get_settings_schema(), {})}

        assert fields["voice_name"].kind is FieldKind.SELECT
        assert fields["style_prompt"].kind is FieldKind.TEXTAREA

    def test_piper(self) -> None:
        """Piper exposes its voice plus all three synthesis knobs."""
        provider = PiperTTSProvider()

        names = [field.name for field in build_fields(provider.get_settings_schema(), {})]

        assert names == ["voice", "length_scale", "noise_scale", "noise_w"]
