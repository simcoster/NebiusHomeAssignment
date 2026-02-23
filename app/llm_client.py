import json
import logging

from openai import AsyncOpenAI

from app.models import SummarizeResponse

logger = logging.getLogger(__name__)

NEBIUS_API_BASE = "https://api.studio.nebius.com/v1/"
MODEL = "meta-llama/Meta-Llama-3.1-70B-Instruct"

SYSTEM_PROMPT = """\
You are a software project analyst. Given the contents of a GitHub repository—where each content section is marked and sorted by an edit timestamp—produce a structured JSON analysis with exactly three fields:

If there is contradictory information in the repository contents, always prefer and use the information from the newer timestamp.

1. "summary": A clear, human-readable description (2-4 sentences) of what the project does, its purpose, and who it's for. Be specific and informative.

2. "technologies": A JSON array of strings listing the main programming languages, frameworks, libraries, and tools the project uses. Include only significant dependencies, not every transitive package. Order by importance.

3. "structure": A brief description (2-3 sentences) of how the project is organized. Focus on the purpose and relationships between major parts, not just directory names. 
For example: where the core logic lives, where tests are, how the project is built, 
and any notable architectural patterns (e.g. plugin system, monorepo, library + CLI, etc.).

Respond ONLY with valid JSON. No markdown, no code fences, no extra text.
"""

USER_PROMPT_TEMPLATE = """\
Analyze the following GitHub repository and produce a JSON summary.

Repository: {owner}/{repo}

{context}

Respond with a JSON object containing "summary", "technologies", and "structure" fields.\
"""


class LLMError(Exception):
    pass


class LLMClient:
    def __init__(self, api_key: str, base_url: str = NEBIUS_API_BASE, model: str = MODEL):
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model = model

    async def summarize(
        self, owner: str, repo: str, context: str, tree: list[dict]
    ) -> SummarizeResponse:
        user_prompt = USER_PROMPT_TEMPLATE.format(
            owner=owner, repo=repo, context=context, tree=json.dumps(tree)
        )

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                max_tokens=4096,
                response_format={"type": "json_object"},
            )
        except Exception as e:
            logger.error("LLM API call failed: %s", e)
            raise LLMError(f"LLM API call failed: {e}") from e

        raw = response.choices[0].message.content
        if not raw:
            raise LLMError("LLM returned an empty response")

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse LLM response as JSON: %s", raw[:500])
            raise LLMError("LLM returned invalid JSON") from e

        try:
            return SummarizeResponse(
                summary=data.get("summary", ""),
                technologies=data.get("technologies", []),
                structure=data.get("structure", ""),
            )
        except Exception as e:
            raise LLMError(f"LLM response did not match expected schema: {e}") from e
