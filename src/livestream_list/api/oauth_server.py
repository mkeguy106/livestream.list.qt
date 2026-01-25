"""Local OAuth callback server for authentication."""

import asyncio
import logging
import secrets
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)

# HTML page for Kick OAuth code flow - shows success and closes
KICK_CALLBACK_HTML = """<!DOCTYPE html>
<html>
<head>
    <title>Kick Authorization</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
            margin: 0;
            background: #0e0e10;
            color: #efeff1;
        }
        .container { text-align: center; padding: 2rem; }
        h1 { color: #53fc18; }
        .success { color: #00ff7f; }
        .error { color: #ff4444; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Livestream List</h1>
        <p id="status" class="success">Authorization received! You can close this window.</p>
    </div>
</body>
</html>
"""

KICK_ERROR_HTML = """<!DOCTYPE html>
<html>
<head>
    <title>Kick Authorization Failed</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            display: flex; justify-content: center; align-items: center;
            height: 100vh; margin: 0; background: #0e0e10; color: #efeff1;
        }
        .container { text-align: center; padding: 2rem; }
        h1 { color: #53fc18; }
        .error { color: #ff4444; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Livestream List</h1>
        <p class="error">Authorization failed: {error}</p>
    </div>
</body>
</html>
"""

# HTML page served to capture OAuth token from URL fragment (Twitch implicit flow)
CALLBACK_HTML = """<!DOCTYPE html>
<html>
<head>
    <title>Twitch Authorization</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
            margin: 0;
            background: #0e0e10;
            color: #efeff1;
        }
        .container {
            text-align: center;
            padding: 2rem;
        }
        h1 { color: #9147ff; }
        .success { color: #00ff7f; }
        .error { color: #ff4444; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Livestream List</h1>
        <p id="status">Processing authorization...</p>
    </div>
    <script>
        (function() {
            const hash = window.location.hash.substring(1);
            const params = new URLSearchParams(hash);
            const token = params.get('access_token');
            const state = params.get('state');
            const error = params.get('error');
            const statusEl = document.getElementById('status');

            if (error) {
                statusEl.className = 'error';
                statusEl.textContent = 'Auth failed: ' +
                    (params.get('error_description') || error);
                return;
            }

            if (token) {
                // Send token and state to server for validation
                let url = '/token?access_token=' + encodeURIComponent(token);
                if (state) {
                    url += '&state=' + encodeURIComponent(state);
                }
                fetch(url)
                    .then(response => {
                        if (response.ok) {
                            statusEl.className = 'success';
                            statusEl.textContent =
                                'Success! You can close this window.';
                        } else if (response.status === 400) {
                            throw new Error('Invalid state - possible CSRF attack');
                        } else {
                            throw new Error('Server error');
                        }
                    })
                    .catch(err => {
                        statusEl.className = 'error';
                        statusEl.textContent = err.message || 'Failed to save token.';
                    });
            } else {
                statusEl.className = 'error';
                statusEl.textContent = 'No access token received. Please try again.';
            }
        })();
    </script>
</body>
</html>
"""


class ReuseAddrHTTPServer(HTTPServer):
    """HTTP server that allows address reuse."""

    allow_reuse_address = True


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """HTTP request handler for OAuth callbacks."""

    def log_message(self, format, *args):
        """Suppress default logging."""
        pass

    def do_GET(self):
        """Handle GET requests."""
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path == "/redirect":
            # Check if this is a code flow (Kick) or implicit flow (Twitch)
            code = params.get("code", [None])[0]
            error = params.get("error", [None])[0]
            state = params.get("state", [None])[0]

            if error:
                # OAuth error
                desc = params.get("error_description", [error])[0]
                html = KICK_ERROR_HTML.format(error=desc)
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(html.encode())
            elif code:
                # Authorization code flow (Kick) - code in query param
                # Validate state parameter to prevent CSRF attacks
                if self.server.expected_state and state != self.server.expected_state:
                    logger.warning("OAuth state mismatch - possible CSRF attack")
                    html = KICK_ERROR_HTML.format(error="Invalid state parameter")
                    self.send_response(400)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(html.encode())
                    return

                if self.server.code_callback:
                    self.server.code_callback(code)
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(KICK_CALLBACK_HTML.encode())
            else:
                # Twitch implicit flow - token will be in URL fragment
                # Serve HTML that extracts it via JavaScript
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(CALLBACK_HTML.encode())

        elif parsed.path == "/token":
            # Receive the token from JavaScript (Twitch implicit flow)
            token = params.get("access_token", [None])[0]
            state = params.get("state", [None])[0]

            # Validate state parameter to prevent CSRF attacks
            if self.server.expected_state and state != self.server.expected_state:
                logger.warning("OAuth state mismatch in implicit flow - possible CSRF attack")
                self.send_response(400)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"Invalid state parameter")
                return

            if token and self.server.token_callback:
                self.server.token_callback(token)
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"OK")
            else:
                self.send_response(400)
                self.end_headers()

        else:
            self.send_response(404)
            self.end_headers()


class OAuthServer:
    """Local HTTP server for OAuth callback handling."""

    def __init__(self, port: int = 0):
        """
        Initialize the OAuth server.

        Args:
            port: Port to listen on. Use 0 to auto-select an available port.
        """
        self._port = port
        self._server: HTTPServer | None = None
        self._thread: Thread | None = None
        self._token: str | None = None
        self._code: str | None = None
        self._token_event = threading.Event()
        self._code_event = threading.Event()
        self._stopped = False
        self._expected_state: str | None = None

    @property
    def port(self) -> int:
        """Get the actual port the server is listening on."""
        if self._server:
            return self._server.server_address[1]
        return self._port

    @property
    def redirect_uri(self) -> str:
        """Get the redirect URI for OAuth."""
        return f"http://localhost:{self.port}/redirect"

    def generate_state(self) -> str:
        """Generate and store a cryptographically secure state parameter.

        Returns:
            The generated state string to include in the OAuth request.
        """
        self._expected_state = secrets.token_urlsafe(32)
        return self._expected_state

    @property
    def expected_state(self) -> str | None:
        """Get the expected state for validation."""
        return self._expected_state

    def _on_token(self, token: str) -> None:
        """Callback when token is received (implicit flow)."""
        self._token = token
        self._token_event.set()

    def _on_code(self, code: str) -> None:
        """Callback when authorization code is received (code flow)."""
        self._code = code
        self._code_event.set()

    def start(self) -> None:
        """Start the OAuth server."""
        # Find an available port if not specified
        if self._port == 0:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("localhost", 0))
                self._port = s.getsockname()[1]

        self._server = ReuseAddrHTTPServer(("localhost", self._port), OAuthCallbackHandler)
        self._server.token_callback = self._on_token
        self._server.code_callback = self._on_code
        self._server.expected_state = self._expected_state

        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

        logger.info(f"OAuth server started on port {self.port}")

    def stop(self) -> None:
        """Stop the OAuth server. Safe to call multiple times."""
        if self._stopped:
            return
        self._stopped = True

        if self._server:
            self._server.shutdown()
            self._server = None

        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

        logger.info("OAuth server stopped")

    async def wait_for_token(self, timeout: float = 300) -> str | None:
        """
        Wait for the OAuth token to be received.

        Args:
            timeout: Maximum seconds to wait.

        Returns:
            The access token, or None if timed out.
        """
        try:
            loop = asyncio.get_running_loop()
            got_token = await loop.run_in_executor(
                None, lambda: self._token_event.wait(timeout=timeout)
            )
            if got_token:
                return self._token
            return None
        finally:
            self.stop()

    async def wait_for_code(self, timeout: float = 300) -> str | None:
        """
        Wait for the authorization code to be received (code flow).

        Args:
            timeout: Maximum seconds to wait.

        Returns:
            The authorization code, or None if timed out.
        """
        try:
            loop = asyncio.get_running_loop()
            got_code = await loop.run_in_executor(
                None, lambda: self._code_event.wait(timeout=timeout)
            )
            if got_code:
                return self._code
            return None
        finally:
            self.stop()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
