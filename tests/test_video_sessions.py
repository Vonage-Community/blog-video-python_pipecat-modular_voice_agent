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


def test_signals_reach_the_sdk_with_and_without_a_connection_id(private_key_pem, monkeypatch):
    """The SDK's validator rejects connection_id=None, so it must be left out."""
    video = VonageVideo(APPLICATION_ID, private_key_pem)
    sent: list[str] = []

    def fake_post(host, request_path="", params=None, **kwargs):
        sent.append(request_path)

    monkeypatch.setattr(video._signal_client.video._http_client, "post", fake_post)
    video.send_signal("SESSION", "transcript", "{}")
    video.send_signal("SESSION", "transcript", "{}", "conn-1")
    assert sent[0].endswith("/session/SESSION/signal")
    assert sent[1].endswith("/session/SESSION/connection/conn-1/signal")
