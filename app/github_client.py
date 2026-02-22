import httpx
from dataclasses import dataclass

GITHUB_API = "https://api.github.com"
REQUEST_TIMEOUT = 30.0


@dataclass
class RepoFile:
    path: str
    size: int
    download_url: str | None = None
    content: str | None = None


class GitHubClientError(Exception):
    pass


class GitHubClient:
    def __init__(self, token: str | None = None):
        headers = {"Accept": "application/vnd.github.v3+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.AsyncClient(
            headers=headers, timeout=REQUEST_TIMEOUT, follow_redirects=True
        )

    async def close(self):
        await self._client.aclose()

    async def get_default_branch(self, owner: str, repo: str) -> str:
        url = f"{GITHUB_API}/repos/{owner}/{repo}"
        resp = await self._client.get(url)
        if resp.status_code == 404:
            raise GitHubClientError(
                f"Repository '{owner}/{repo}' not found. "
                "Make sure it exists and is public."
            )
        if resp.status_code == 403:
            raise GitHubClientError("GitHub API rate limit exceeded. Try again later.")
        resp.raise_for_status()
        return resp.json()["default_branch"]

    async def get_repo_tree(
        self, owner: str, repo: str, branch: str
    ) -> list[RepoFile]:
        url = f"{GITHUB_API}/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
        resp = await self._client.get(url)
        resp.raise_for_status()
        data = resp.json()

        files: list[RepoFile] = []
        for item in data.get("tree", []):
            if item["type"] != "blob":
                continue
            files.append(
                RepoFile(
                    path=item["path"],
                    size=item.get("size", 0),
                    download_url=(
                        f"https://raw.githubusercontent.com/"
                        f"{owner}/{repo}/{branch}/{item['path']}"
                    ),
                )
            )
        return files

    async def fetch_file_content(self, file: RepoFile) -> str | None:
        if not file.download_url:
            return None
        try:
            resp = await self._client.get(file.download_url)
            if resp.status_code != 200:
                return None
            resp.encoding = "utf-8"
            return resp.text
        except (httpx.HTTPError, UnicodeDecodeError):
            return None
