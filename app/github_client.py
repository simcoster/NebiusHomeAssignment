import re

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
    last_commit_timestamp: str | None = None
    commit_count: int | None = None


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
    ) -> tuple[list[RepoFile], list[dict]]:
        url = f"{GITHUB_API}/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
        resp = await self._client.get(url)
        resp.raise_for_status()
        data = resp.json()

        files: list[RepoFile] = []
        tree = data.get("tree", [])
        for item in tree:
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
        return files, tree

    async def get_file_commit_info(
        self, owner: str, repo: str, branch: str, path: str
    ) -> tuple[str | None, int | None]:
        url = f"{GITHUB_API}/repos/{owner}/{repo}/commits"
        params = {"path": path, "sha": branch, "per_page": 1}
        resp = await self._client.get(url, params=params)
        if resp.status_code == 403:
            raise GitHubClientError("GitHub API rate limit exceeded. Try again later.")
        if resp.status_code != 200:
            return None, None
        commits = resp.json()
        if not commits:
            return None, 0

        commit = commits[0].get("commit", {})
        committer = commit.get("committer") or {}
        author = commit.get("author") or {}
        timestamp = committer.get("date") or author.get("date")
        commit_count = self._parse_commit_count(resp.headers.get("Link"), len(commits))
        return timestamp, commit_count

    @staticmethod
    def _parse_commit_count(link_header: str | None, fallback: int) -> int:
        if not link_header:
            return fallback
        for part in link_header.split(","):
            if 'rel="last"' not in part:
                continue
            m = re.search(r"page=(\d+)>", part)
            if m:
                return int(m.group(1))
        return fallback

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
