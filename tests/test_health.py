from fastapi.testclient import TestClient


def test_healthz(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_unknown_page_404(client: TestClient) -> None:
    response = client.get("/nope", headers={"accept": "text/html"})
    assert response.status_code == 404
    assert "gibt es nicht" in response.text
