from pydantic import BaseModel, field_validator
import re


class SummarizeRequest(BaseModel):
    github_url: str

    @field_validator("github_url")
    @classmethod
    def validate_github_url(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        pattern = r"^https?://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"
        if not re.match(pattern, v):
            raise ValueError(
                "Invalid GitHub repository URL. "
                "Expected format: https://github.com/owner/repo"
            )
        return v

    def parse_owner_repo(self) -> tuple[str, str]:
        parts = self.github_url.rstrip("/").split("/")
        return parts[-2], parts[-1]


class SummarizeResponse(BaseModel):
    summary: str
    technologies: list[str]
    structure: str


class ErrorResponse(BaseModel):
    status: str = "error"
    message: str
