import os
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client():
    return TestClient(app)


def _skip_if_no_api_key():
    if not os.environ.get("NEBIUS_API_KEY"):
        pytest.skip("NEBIUS_API_KEY not set — skipping integration test")


class TestSummarizeEndpoint:
    """Integration tests that hit the real GitHub + LLM APIs."""

    def _check_valid_summary(self, client, github_url):
        _skip_if_no_api_key()
        response = client.post(
            "/summarize",
            json={"github_url": github_url},
        )

        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.json()}"
        data = response.json()

        assert "summary" in data
        assert "technologies" in data
        assert "structure" in data

        assert isinstance(data["summary"], str)
        assert len(data["summary"]) > 10
        print(data["summary"])

        assert isinstance(data["technologies"], list)
        assert len(data["technologies"]) > 0
        print(data["technologies"])

        assert isinstance(data["structure"], str)
        assert len(data["structure"]) > 10
        print(data["structure"])

    def test_big_repo_returns_valid_summary(self, client):
        self._check_valid_summary(client, "https://github.com/git/git")

    def test_deprecated_undocumented_repo_returns_valid_summary(self, client):
        self._check_valid_summary(client, "https://github.com/hasadna/Open-Knesset")
        
    def test_super_niche_repo_returns_valid_summary(self, client):
        self._check_valid_summary(client, "https://github.com/setuc/Matching-Algorithms")

    def test_invalid_url_returns_error(self, client):
        response = client.post(
            "/summarize",
            json={"github_url": "not-a-github-url"},
        )
        assert response.status_code == 422

    def test_nonexistent_repo_returns_404(self, client):
        _skip_if_no_api_key()

        response = client.post(
            "/summarize",
            json={
                "github_url": "https://github.com/thisownerdoesnotexist123456/norepo"
            },
        )
        assert response.status_code == 404
        data = response.json()
        assert data["status"] == "error"

    def test_missing_github_url_returns_422(self, client):
        response = client.post("/summarize", json={})
        assert response.status_code == 422
