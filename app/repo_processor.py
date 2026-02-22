import asyncio
import logging
from pathlib import PurePosixPath

from app.github_client import GitHubClient, RepoFile

logger = logging.getLogger(__name__)

MAX_CONTEXT_CHARS = 80_000
MAX_FILE_CHARS = 15_000
MAX_FILES_TO_FETCH = 40

SKIP_DIRECTORIES = {
    "node_modules", ".git", "vendor", "dist", "build", "__pycache__",
    ".next", ".nuxt", ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "venv", ".venv", "env", ".env", "eggs", ".eggs", "bower_components",
    "jspm_packages", ".gradle", ".idea", ".vscode", ".vs", "target",
    "out", "coverage", ".nyc_output", ".cache", "tmp", "temp",
}

SKIP_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg", ".webp",
    ".mp3", ".mp4", ".avi", ".mov", ".wav", ".flac",
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".rar", ".7z",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".o", ".a", ".class",
    ".pyc", ".pyo", ".wasm", ".map",
    ".lock", ".sum",
    ".min.js", ".min.css", ".bundle.js",
    ".DS_Store",
}

SKIP_FILENAMES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "Pipfile.lock",
    "poetry.lock", "composer.lock", "Gemfile.lock", "Cargo.lock",
    "go.sum", "bun.lockb",
    ".DS_Store", "Thumbs.db",
    ".gitattributes",
}

HIGH_PRIORITY_FILENAMES = {
    "README.md", "README.rst", "README.txt", "README",
    "package.json", "pyproject.toml", "setup.py", "setup.cfg",
    "Cargo.toml", "go.mod", "Gemfile", "build.gradle", "pom.xml",
    "composer.json", "mix.exs", "Makefile", "CMakeLists.txt",
    "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
    ".env.example", "requirements.txt",
}

MEDIUM_PRIORITY_FILENAMES = {
    "tsconfig.json", "webpack.config.js", "vite.config.ts", "vite.config.js",
    "rollup.config.js", "babel.config.js", ".babelrc",
    "jest.config.js", "jest.config.ts", "vitest.config.ts",
    "tox.ini", "pytest.ini", "conftest.py",
    ".github/workflows", "Procfile", "app.yaml", "vercel.json",
    "netlify.toml", "fly.toml",
    "CONTRIBUTING.md", "CHANGELOG.md", "LICENSE",
}

CONFIG_EXTENSIONS = {
    ".toml", ".yaml", ".yml", ".json", ".ini", ".cfg", ".conf",
}

SOURCE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".rb", ".java",
    ".kt", ".cs", ".cpp", ".c", ".h", ".hpp", ".swift", ".m",
    ".php", ".ex", ".exs", ".erl", ".hs", ".lua", ".r", ".scala",
    ".clj", ".sh", ".bash", ".zsh", ".sql", ".graphql", ".proto",
    ".vue", ".svelte", ".astro", ".html", ".css", ".scss", ".less",
}


def _is_in_skip_directory(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return any(part in SKIP_DIRECTORIES for part in parts)


def _has_skip_extension(path: str) -> bool:
    lower = path.lower()
    return any(lower.endswith(ext) for ext in SKIP_EXTENSIONS)


def _get_filename(path: str) -> str:
    return PurePosixPath(path).name


def _get_extension(path: str) -> str:
    return PurePosixPath(path).suffix.lower()


def _score_file(file: RepoFile) -> int:
    """Higher score = higher priority for inclusion."""
    name = _get_filename(file.path)
    ext = _get_extension(file.path)
    depth = len(PurePosixPath(file.path).parts)

    score = 0

    if name.upper().startswith("README"):
        score += 1000
    if name in HIGH_PRIORITY_FILENAMES:
        score += 800
    if name in MEDIUM_PRIORITY_FILENAMES or file.path in MEDIUM_PRIORITY_FILENAMES:
        score += 500

    if ext in CONFIG_EXTENSIONS:
        score += 200
    if ext in SOURCE_EXTENSIONS:
        score += 100

    # Prefer shallower files (top-level files are more informative)
    score += max(0, 50 - depth * 10)

    # Prefer smaller files (they're usually more focused)
    if file.size < 2000:
        score += 30
    elif file.size < 5000:
        score += 15
    elif file.size > 50000:
        score -= 50

    # Entry-point heuristics
    entry_names = {"main", "app", "index", "server", "cli", "__main__", "mod"}
    stem = PurePosixPath(name).stem.lower()
    if stem in entry_names:
        score += 300

    return score


def filter_files(files: list[RepoFile]) -> list[RepoFile]:
    """Remove files that should be skipped and sort by priority."""
    filtered = []
    for f in files:
        if _is_in_skip_directory(f.path):
            logger.debug("Skipping file %s because it's in a skip directory", f.path)
            continue
        if _has_skip_extension(f.path):
            logger.debug("Skipping file %s because it has a skip extension", f.path)
            continue
        if _get_filename(f.path) in SKIP_FILENAMES:
            logger.debug("Skipping file %s because it has a skip filename", f.path)
            continue
        if f.size > 500_000:
            continue
        filtered.append(f)
    filtered.sort(key=_score_file, reverse=True)
    return filtered


def build_directory_tree(files: list[RepoFile], max_lines: int = 150) -> str:
    """Build a compact directory tree representation."""
    dirs: set[str] = set()
    file_paths: list[str] = []

    for f in files:
        file_paths.append(f.path)
        parts = PurePosixPath(f.path).parts
        for i in range(1, len(parts)):
            dirs.add("/".join(parts[:i]) + "/")

    all_entries = sorted(dirs) + sorted(file_paths)
    lines = []
    for entry in all_entries:
        depth = entry.count("/")
        if entry.endswith("/"):
            depth -= 1
        indent = "  " * depth
        name = entry.rstrip("/").split("/")[-1]
        if entry.endswith("/"):
            name += "/"
        lines.append(f"{indent}{name}")
        if len(lines) >= max_lines:
            lines.append(f"  ... ({len(all_entries) - max_lines} more entries)")
            break

    return "\n".join(lines)


def truncate_content(content: str, max_chars: int = MAX_FILE_CHARS) -> str:
    if len(content) <= max_chars:
        return content
    return content[:max_chars] + "\n\n... [truncated]"


async def collect_repo_context(
    client: GitHubClient,
    files: list[RepoFile],
) -> str:
    """Fetch file contents and assemble the context string for the LLM."""
    prioritized = filter_files(files)
    to_fetch = prioritized[:MAX_FILES_TO_FETCH]

    semaphore = asyncio.Semaphore(10)

    async def _fetch(f: RepoFile):
        async with semaphore:
            content = await client.fetch_file_content(f)
            if content is not None:
                f.content = content

    await asyncio.gather(*[_fetch(f) for f in to_fetch])

    tree = build_directory_tree(files)
    parts: list[str] = [f"## Directory Structure\n```\n{tree}\n```\n"]
    total_chars = len(parts[0])

    for f in to_fetch:
        if f.content is None:
            continue
        content = truncate_content(f.content)
        section = f"## File: {f.path}\n```\n{content}\n```\n"

        if total_chars + len(section) > MAX_CONTEXT_CHARS:
            remaining = MAX_CONTEXT_CHARS - total_chars
            if remaining > 500:
                section = f"## File: {f.path}\n```\n{content[:remaining - 200]}\n... [truncated]\n```\n"
                parts.append(section)
            break
        parts.append(section)
        total_chars += len(section)

    return "\n".join(parts)
