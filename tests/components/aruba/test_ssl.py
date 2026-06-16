"""Tests for Aruba SSL helpers."""

import ssl

import pytest

from homeassistant.components.aruba.ssl import get_ssl_context


@pytest.mark.parametrize(
    ("verify_ssl", "verify_mode", "check_hostname"),
    [
        pytest.param(True, ssl.CERT_REQUIRED, True, id="verify"),
        pytest.param(False, ssl.CERT_NONE, False, id="no-verify"),
    ],
)
def test_ssl_context(
    verify_ssl: bool,
    verify_mode: ssl.VerifyMode,
    check_hostname: bool,
) -> None:
    """Test Aruba SSL contexts tolerate abrupt TLS shutdowns."""
    context = get_ssl_context(verify_ssl)

    assert context.verify_mode is verify_mode
    assert context.check_hostname is check_hostname
    assert context.options & ssl.OP_IGNORE_UNEXPECTED_EOF
