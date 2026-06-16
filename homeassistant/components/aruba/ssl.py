"""SSL helpers for Aruba Instant."""

import ssl

from homeassistant.util.ssl import SSL_ALPN_HTTP11, create_client_context

_SSL_CONTEXT = create_client_context(alpn_protocols=SSL_ALPN_HTTP11)
_SSL_CONTEXT.options |= ssl.OP_IGNORE_UNEXPECTED_EOF

_SSL_CONTEXT_NO_VERIFY = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
_SSL_CONTEXT_NO_VERIFY.check_hostname = False
_SSL_CONTEXT_NO_VERIFY.verify_mode = ssl.CERT_NONE
_SSL_CONTEXT_NO_VERIFY.options |= ssl.OP_IGNORE_UNEXPECTED_EOF
_SSL_CONTEXT_NO_VERIFY.set_alpn_protocols(["http/1.1"])


def get_ssl_context(verify_ssl: bool) -> ssl.SSLContext:
    """Return an SSL context compatible with Aruba's abrupt TLS shutdown."""
    return _SSL_CONTEXT if verify_ssl else _SSL_CONTEXT_NO_VERIFY
