"""Base types and strict model configuration for the narrator bot."""

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Base model with strict validation.

    All models should inherit from this to ensure:
    - Strict type validation
    - Immutable instances (frozen)
    - No extra fields allowed
    - Default values are validated
    """

    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        validate_default=True,
    )


class MutableStrictModel(BaseModel):
    """Mutable version of StrictModel for settings that need updates.

    Use this for models that need to be modified after creation,
    such as runtime settings or builder patterns.
    """

    model_config = ConfigDict(
        strict=True,
        frozen=False,
        extra="forbid",
        validate_default=True,
    )
