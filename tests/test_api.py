from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.video_sessions import VideoApiError
from tests.conftest import with_settings
from tests.fakes import AgentRecorder, FakeVideo

JSON = {"Content-Type": "application/json"}


@pytest.fixture
def agents():
    return AgentRecorder()


@pytest.fixture
def client(settings, agents):
    app = create_app(settings=settings, video=FakeVideo(), agent_factory=agents)
    with TestClient(app) as test_client:
        yield test_client


def create(client: TestClient) -> dict:
    response = client.post("/api/sessions", json={})
    assert response.status_code == 201, response.text
    return response.json()


def auth(session: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {session['sessionKey']}"}


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["cache-control"] == "no-store"


def test_serves_the_browser_app(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "client-sdk-video@2.36.0" in page.text
    assert client.get("/app.js").status_code == 200


def test_create_returns_only_what_the_browser_needs(client, agents):
    session = create(client)
    assert set(session) == {
        "id",
        "sessionKey",
        "applicationId",
        "vonageSessionId",
        "token",
        "agentMode",
        "options",
        "maxSessionSeconds",
        "expiresAt",
    }
    assert session["token"].startswith("token-for-user-")
    agent_token = agents.agents[0].connection.token
    assert agent_token.startswith("token-for-agent-")
    assert agent_token not in str(session)


def test_create_requires_json(client):
    response = client.post("/api/sessions", content=b"", headers={"Content-Type": "text/plain"})
    assert response.status_code == 415


def test_status_requires_the_session_key(client):
    session = create(client)
    url = f"/api/sessions/{session['id']}"
    assert client.get(url).status_code == 401
    assert client.get(url, headers={"Authorization": "Bearer wrong"}).status_code == 403
    assert client.get("/api/sessions/unknown", headers=auth(session)).status_code == 404

    response = client.get(url, headers=auth(session))
    assert response.status_code == 200
    body = response.json()
    assert body["state"] in ("starting", "agent_joined")
    assert session["sessionKey"] not in response.text
    assert "token" not in response.text


def test_session_key_in_query_string_is_ignored(client):
    session = create(client)
    url = f"/api/sessions/{session['id']}?authorization=Bearer%20{session['sessionKey']}"
    assert client.get(url).status_code == 401


def test_ready_starts_the_conversation_once(client, agents):
    session = create(client)
    url = f"/api/sessions/{session['id']}/ready"
    first = client.post(url, headers=auth(session))
    second = client.post(url, headers=auth(session))
    assert first.json()["started"] is True
    assert second.json()["started"] is False
    assert first.json()["state"] == "active"
    assert agents.agents[0].conversations_started == 1


def test_ready_accepts_the_browser_connection_id(client, agents):
    session = create(client)
    url = f"/api/sessions/{session['id']}/ready"
    response = client.post(url, headers=auth(session), json={"connectionId": "a1b2-c3d4"})
    assert response.status_code == 200
    assert response.json()["started"] is True
    stored = client.app.state.manager.get(session["id"])
    assert stored.browser_connection_id == "a1b2-c3d4"


def test_ready_rejects_a_malformed_connection_id(client, agents):
    session = create(client)
    url = f"/api/sessions/{session['id']}/ready"
    response = client.post(url, headers=auth(session), json={"connectionId": "<script>"})
    assert response.status_code == 422
    assert agents.agents[0].conversations_started == 0


def test_ready_requires_the_session_key(client, agents):
    session = create(client)
    response = client.post(f"/api/sessions/{session['id']}/ready")
    assert response.status_code == 401
    assert agents.agents[0].conversations_started == 0


def test_end_is_idempotent_and_blocks_ready(client, agents):
    session = create(client)
    url = f"/api/sessions/{session['id']}"
    first = client.post(f"{url}/end", headers=auth(session))
    second = client.post(f"{url}/end", headers=auth(session))
    assert first.status_code == second.status_code == 200
    assert first.json()["state"] == "ended"
    assert first.json()["endReason"] == "left"
    assert agents.agents[0].finished
    assert client.post(f"{url}/ready", headers=auth(session)).status_code == 409


def test_second_session_is_rejected_while_one_runs(client):
    first = create(client)
    assert client.post("/api/sessions", json={}).status_code == 409
    client.post(f"/api/sessions/{first['id']}/end", headers=auth(first))
    create(client)


def test_rate_limit_returns_429_with_retry_after(settings, agents):
    limited = with_settings(settings, max_active_sessions=5, session_creates_per_minute=1)
    app = create_app(settings=limited, video=FakeVideo(), agent_factory=agents)
    with TestClient(app) as client:
        create(client)
        response = client.post("/api/sessions", json={})
    assert response.status_code == 429
    assert int(response.headers["retry-after"]) > 0


def test_vonage_errors_become_502_with_a_safe_message(settings, agents):
    video = FakeVideo(fail_with=VideoApiError("Vonage rejected the credentials (401)."))
    app = create_app(settings=settings, video=video, agent_factory=agents)
    with TestClient(app) as client:
        response = client.post("/api/sessions", json={})
    assert response.status_code == 502
    assert "Vonage rejected the credentials" in response.json()["detail"]


def test_unconfigured_server_explains_what_is_missing(monkeypatch, tmp_path):
    for name in ("VONAGE_APPLICATION_ID", "DEEPGRAM_API_KEY", "AGENT_MODE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VONAGE_PRIVATE_KEY_PATH", str(tmp_path / "missing.key"))
    with TestClient(create_app()) as client:
        assert client.get("/api/health").status_code == 200
        response = client.post("/api/sessions", json={})
    assert response.status_code == 503
    problems = response.json()["detail"]["problems"]
    assert any("VONAGE_APPLICATION_ID" in p for p in problems)


def test_shutdown_stops_running_agents(settings, agents):
    app = create_app(settings=settings, video=FakeVideo(), agent_factory=agents)
    with TestClient(app) as client:
        create(client)
    assert agents.agents[0].finished


def test_options_describe_the_waiting_room_without_secrets(client, monkeypatch):
    from app.call_options import Voice, VoiceDirectory

    async def fake_voices(self):
        return [Voice("abcdefghij0123456789", "My Designed Voice", "british")], "ok"

    monkeypatch.setattr(VoiceDirectory, "account_voices", fake_voices)
    response = client.get("/api/options")
    assert response.status_code == 200
    body = response.json()
    assert body["defaults"]["openaiModel"] == "gpt-4.1"
    assert body["openaiModels"] == ["gpt-4.1", "gpt-4.1-mini"]
    assert body["voices"]["recommended"][0]["name"].startswith("Eddie")
    assert body["voices"]["yours"][0]["name"] == "My Designed Voice"
    assert body["voices"]["yoursStatus"] == "ok"
    text = response.text
    for secret in ("sk-test-0123456789abcdef", "sk_test_not_a_real_key", "dg-test-not-a-real-key"):
        assert secret not in text


def test_session_uses_the_waiting_room_choices(client, agents):
    response = client.post(
        "/api/sessions",
        json={"options": {"openaiModel": "gpt-4.1-mini", "keyterms": ["Vonage"]}},
    )
    assert response.status_code == 201
    assert response.json()["options"]["openaiModel"] == "gpt-4.1-mini"
    assert agents.agents[0].events.options.keyterms == ("Vonage",)


def test_invalid_choices_are_explained_and_start_nothing(client, agents):
    response = client.post("/api/sessions", json={"options": {"openaiModel": "gpt-9"}})
    assert response.status_code == 422
    assert any("openaiModel" in p for p in response.json()["detail"]["problems"])
    assert agents.agents == []
