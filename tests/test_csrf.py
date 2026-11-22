import importlib

import pytest
from fastapi.testclient import TestClient

BLOCKED = {"detail": "Origem não permitida"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FARMCORTES_DATA", str(tmp_path))
    module = importlib.import_module("farmcortes.app")
    calls = []
    # The discovery trigger is a write route with an observable side effect; returning None yields 409.
    monkeypatch.setattr(module, "begin_discovery_run", lambda *args, **kwargs: calls.append(kwargs) or None)
    test_client = TestClient(module.app)  # TestClient sends Host: testserver
    test_client.calls = calls
    return test_client


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_foreign_origin_write_is_blocked_before_handler(client, method):  # CSRF-01
    response = client.request(method.upper(), "/api/discovery/run", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert response.json() == BLOCKED
    assert client.calls == []


def test_origin_with_different_port_is_blocked(client):  # CSRF-01 edge
    response = client.post("/api/discovery/run", headers={"Origin": "http://testserver:8080"})
    assert response.status_code == 403
    assert response.json() == BLOCKED
    assert client.calls == []


def test_null_origin_is_blocked(client):  # CSRF-02
    response = client.post("/api/discovery/run", headers={"Origin": "null"})
    assert response.status_code == 403
    assert response.json() == BLOCKED
    assert client.calls == []


def test_cross_site_fetch_without_origin_is_blocked(client):  # CSRF-03
    response = client.post("/api/discovery/run", headers={"Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 403
    assert response.json() == BLOCKED
    assert client.calls == []


def test_reads_are_not_checked(client):  # CSRF-04
    headers = {"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"}
    assert client.get("/health", headers=headers).status_code == 200
    assert client.head("/health", headers=headers).status_code != 403
    assert client.options("/health", headers=headers).status_code != 403


def test_same_origin_write_reaches_handler(client):  # CSRF-05
    response = client.post("/api/discovery/run", headers={"Origin": "http://testserver"})
    assert response.status_code == 409
    assert response.json() == {"detail": "Já existe uma varredura em andamento"}
    assert len(client.calls) == 1


@pytest.mark.parametrize("headers", [{}, {"Sec-Fetch-Site": "same-origin"}, {"Sec-Fetch-Site": "none"}])
def test_write_without_origin_reaches_handler(client, headers):  # CSRF-06
    response = client.post("/api/discovery/run", headers=headers)
    assert response.status_code == 409
    assert len(client.calls) == 1
