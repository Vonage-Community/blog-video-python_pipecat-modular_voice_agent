from __future__ import annotations

from app.video_sessions import VonageVideo
from tests.conftest import APPLICATION_ID


def test_signals_use_their_own_http_headers(private_key_pem):
    """vonage-python-sdk#323: a JSON request leaves Content-Type on the shared headers,
    which makes the next session creation fail with HTTP 415."""
    video = VonageVideo(APPLICATION_ID, private_key_pem)
    sessions = video._client.video._http_client._headers
    signals = video._signal_client.video._http_client._headers
    assert sessions is not signals

    signals["Content-Type"] = "application/json"  # what a signal request leaves behind
    assert "Content-Type" not in sessions
