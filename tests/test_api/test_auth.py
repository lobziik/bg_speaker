"""Tests for HTTP Basic Authentication."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPBasicCredentials
from pydantic import SecretStr

from src.api.auth import verify_credentials


class TestVerifyCredentials:
    """Tests for verify_credentials function."""

    def test_valid_credentials(self) -> None:
        """Test authentication with correct credentials."""
        mock_env = MagicMock()
        mock_env.admin_username = "admin"
        mock_env.admin_password = SecretStr("secret123")
        mock_env.has_basic_auth_configured.return_value = True

        with patch("src.api.auth.get_env_settings", return_value=mock_env):
            credentials = HTTPBasicCredentials(
                username="admin",
                password="secret123",
            )
            result = verify_credentials(credentials)
            assert result == "admin"

    def test_invalid_username(self) -> None:
        """Test authentication fails with wrong username."""
        mock_env = MagicMock()
        mock_env.admin_username = "admin"
        mock_env.admin_password = SecretStr("secret123")
        mock_env.has_basic_auth_configured.return_value = True

        with patch("src.api.auth.get_env_settings", return_value=mock_env):
            credentials = HTTPBasicCredentials(
                username="wrong",
                password="secret123",
            )
            with pytest.raises(HTTPException) as exc_info:
                verify_credentials(credentials)

            assert exc_info.value.status_code == 401
            assert exc_info.value.headers is not None
            assert exc_info.value.headers["WWW-Authenticate"] == "Basic"

    def test_invalid_password(self) -> None:
        """Test authentication fails with wrong password."""
        mock_env = MagicMock()
        mock_env.admin_username = "admin"
        mock_env.admin_password = SecretStr("secret123")
        mock_env.has_basic_auth_configured.return_value = True

        with patch("src.api.auth.get_env_settings", return_value=mock_env):
            credentials = HTTPBasicCredentials(
                username="admin",
                password="wrong",
            )
            with pytest.raises(HTTPException) as exc_info:
                verify_credentials(credentials)

            assert exc_info.value.status_code == 401

    def test_both_credentials_wrong(self) -> None:
        """Test authentication fails with both wrong."""
        mock_env = MagicMock()
        mock_env.admin_username = "admin"
        mock_env.admin_password = SecretStr("secret123")
        mock_env.has_basic_auth_configured.return_value = True

        with patch("src.api.auth.get_env_settings", return_value=mock_env):
            credentials = HTTPBasicCredentials(
                username="wrong",
                password="wrong",
            )
            with pytest.raises(HTTPException) as exc_info:
                verify_credentials(credentials)

            assert exc_info.value.status_code == 401

    def test_auth_not_configured_fails_fast(self) -> None:
        """Test server error when credentials not configured."""
        mock_env = MagicMock()
        mock_env.has_basic_auth_configured.return_value = False

        with patch("src.api.auth.get_env_settings", return_value=mock_env):
            credentials = HTTPBasicCredentials(
                username="any",
                password="any",
            )
            with pytest.raises(HTTPException) as exc_info:
                verify_credentials(credentials)

            assert exc_info.value.status_code == 500
            assert "not set" in str(exc_info.value.detail)

    def test_empty_username_fails(self) -> None:
        """Test authentication fails with empty username."""
        mock_env = MagicMock()
        mock_env.admin_username = "admin"
        mock_env.admin_password = SecretStr("secret123")
        mock_env.has_basic_auth_configured.return_value = True

        with patch("src.api.auth.get_env_settings", return_value=mock_env):
            credentials = HTTPBasicCredentials(
                username="",
                password="secret123",
            )
            with pytest.raises(HTTPException) as exc_info:
                verify_credentials(credentials)

            assert exc_info.value.status_code == 401

    def test_empty_password_fails(self) -> None:
        """Test authentication fails with empty password."""
        mock_env = MagicMock()
        mock_env.admin_username = "admin"
        mock_env.admin_password = SecretStr("secret123")
        mock_env.has_basic_auth_configured.return_value = True

        with patch("src.api.auth.get_env_settings", return_value=mock_env):
            credentials = HTTPBasicCredentials(
                username="admin",
                password="",
            )
            with pytest.raises(HTTPException) as exc_info:
                verify_credentials(credentials)

            assert exc_info.value.status_code == 401
