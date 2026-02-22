import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException

load_dotenv()
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.github_client import GitHubClient, GitHubClientError
from app.llm_client import LLMClient, LLMError
from app.models import ErrorResponse, SummarizeRequest, SummarizeResponse
from app.repo_processor import collect_repo_context

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="GitHub Repository Summarizer",
    description="Analyzes a public GitHub repository and returns an LLM-generated summary.",
    version="1.0.0",
)


def _get_llm_client() -> LLMClient:
    api_key = os.environ.get("NEBIUS_API_KEY", "")
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="NEBIUS_API_KEY environment variable is not set.",
        )
    base_url = os.environ.get(
        "NEBIUS_API_BASE", "https://api.tokenfactory.nebius.com/v1/"
    )
    model = os.environ.get("NEBIUS_MODEL", "meta-llama/Meta-Llama-3.1-8B-Instruct")
    return LLMClient(api_key=api_key, base_url=base_url, model=model)


@app.post(
    "/summarize",
    response_model=SummarizeResponse,
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def summarize(request: SummarizeRequest):
    owner, repo = request.parse_owner_repo()
    logger.info("Summarizing repository: %s/%s", owner, repo)

    github_token = os.environ.get("GITHUB_TOKEN")
    github = GitHubClient(token=github_token)

    try:
        branch = await github.get_default_branch(owner, repo)
        logger.info("Default branch: %s", branch)

        files = await github.get_repo_tree(owner, repo, branch)
        if not files:
            return JSONResponse(
                status_code=400,
                content=ErrorResponse(
                    message="Repository appears to be empty."
                ).model_dump(),
            )
        logger.info("Found %d files in repo tree", len(files))

        context = await collect_repo_context(github, files)
        logger.info("Assembled context: %d characters", len(context))

    except GitHubClientError as e:
        logger.warning("GitHub error: %s", e)
        status = 404 if "not found" in str(e).lower() else 502
        return JSONResponse(
            status_code=status,
            content=ErrorResponse(message=str(e)).model_dump(),
        )
    except Exception as e:
        logger.exception("Unexpected error fetching repository")
        return JSONResponse(
            status_code=502,
            content=ErrorResponse(
                message=f"Failed to fetch repository data: {e}"
            ).model_dump(),
        )
    finally:
        await github.close()

    llm = _get_llm_client()
    try:
        result = await llm.summarize(owner, repo, context)
        logger.info("Summary generated successfully")
        return result
    except LLMError as e:
        logger.error("LLM error: %s", e)
        return JSONResponse(
            status_code=502,
            content=ErrorResponse(
                message=f"LLM processing failed: {e}"
            ).model_dump(),
        )
    except Exception as e:
        logger.exception("Unexpected error during LLM summarization")
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                message=f"Internal error during summarization: {e}"
            ).model_dump(),
        )


@app.exception_handler(ValidationError)
async def validation_error_handler(request, exc):
    return JSONResponse(
        status_code=422,
        content=ErrorResponse(
            message=str(exc.errors()[0]["msg"]) if exc.errors() else str(exc)
        ).model_dump(),
    )
