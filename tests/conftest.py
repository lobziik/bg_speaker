"""Pytest configuration and fixtures."""

import pytest
from pydantic import SecretStr


@pytest.fixture
def mock_api_key() -> SecretStr:
    """Provide a mock API key for testing."""
    return SecretStr("test-api-key-12345")


@pytest.fixture
def sample_message() -> str:
    """Provide a sample message for testing."""
    return "Hello everyone! I just found a legendary sword!"


@pytest.fixture
def sample_user() -> str:
    """Provide a sample username for testing."""
    return "DragonSlayer"


@pytest.fixture
def sample_system_prompt() -> str:
    """Provide a sample system prompt for testing."""
    return """You are a fantasy narrator. Transform messages into dramatic prose.
    Keep responses under 100 words. Refer to the user in third person."""
