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
        score = 1000
    elif name in HIGH_PRIORITY_FILENAMES:
        score = 800
    elif name in MEDIUM_PRIORITY_FILENAMES or file.path in MEDIUM_PRIORITY_FILENAMES:
        score = 500
    elif ext in CONFIG_EXTENSIONS:
        score = 200
    elif ext in SOURCE_EXTENSIONS:
        score = 100
        # Prefer test files among source files
        if "test" in name.lower():
            score + 50

    if "README.md" in name and depth == 2:
        pass
    # Strongly prefer top-level files; heavily penalize depth 2+
    score -= depth * 500  # depth 1: 0, depth 2: -500, depth 3: -1000, ...

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


def _build_tree_full(
    dirs: set[str], file_paths: list[str], max_lines: int
) -> list[str]:
    """Render a full indented directory tree (used when dirs <= 100)."""
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
    return lines


def _build_tree_summary(file_paths: list[str], max_lines: int) -> list[str]:
    """Render a top-level summary tree (used when dirs > 100)."""
    from collections import Counter

    top_level_dirs: set[str] = set()
    top_level_files: list[str] = []
    for fpath in file_paths:
        p = PurePosixPath(fpath)
        if len(p.parts) == 1:
            top_level_files.append(fpath)
        else:
            top_level_dirs.add(p.parts[0])

    lines = ["[Summary — over 100 directories, showing top level breakdown]"]

    if top_level_files:
        lines.append("")
        lines.append("Top-level files:")
        sorted_top = sorted(top_level_files)
        for fpath in sorted_top[:30]:
            lines.append(f"  {fpath}")
        if len(sorted_top) > 30:
            remaining = sorted_top[30:]
            type_counts: Counter = Counter(PurePosixPath(f).suffix or "(no_ext)" for f in remaining)
            type_str = ", ".join(f"{typ}: {cnt}" for typ, cnt in type_counts.most_common())
            lines.append(f"  ... ({len(remaining)} more: {type_str})")

    for d in sorted(top_level_dirs):
        file_types: Counter = Counter()
        subdirs: set[str] = set()
        for fpath in file_paths:
            p = PurePosixPath(fpath)
            if p.parts and p.parts[0] == d:
                if len(p.parts) == 2:
                    file_types[p.suffix or "(no_ext)"] += 1
                elif len(p.parts) > 2:
                    subdirs.add(p.parts[1])
                    file_types[p.suffix or "(no_ext)"] += 1
        lines.append("")
        lines.append(f"{d}/")
        if subdirs:
            sample = ", ".join(sorted(subdirs)[:5])
            ellipsis = "..." if len(subdirs) > 5 else ""
            lines.append(f"  Sub-directories: {len(subdirs)} ({sample}{ellipsis})")
        else:
            lines.append("  Sub-directories: 0")
        if file_types:
            top_types = file_types.most_common(5)
            str_types = ", ".join(f"{typ}: {cnt}" for typ, cnt in top_types)
            extra = f", ... ({sum(file_types.values())} files)" if len(file_types) > 5 else ""
            lines.append(f"  File types: {str_types}{extra}")
        else:
            lines.append("  No files detected")
        if len(lines) > max_lines:
            lines.append("  ... (output truncated)")
            break

    return lines


def build_directory_tree(files: list[RepoFile], max_lines: int = 150) -> str:
    """Build a compact directory tree representation."""
    dirs: set[str] = set()
    file_paths: list[str] = []

    for f in files:
        file_paths.append(f.path)
        parts = PurePosixPath(f.path).parts
        for i in range(1, len(parts)):
            dirs.add("/".join(parts[:i]) + "/")

    if len(dirs) > 100:
        lines = _build_tree_summary(file_paths, max_lines)
    else:
        lines = _build_tree_full(dirs, file_paths, max_lines)

    return "\n".join(lines)


def truncate_content(content: str, max_chars: int = MAX_FILE_CHARS) -> str:
    if len(content) <= max_chars:
        return content
    return content[:max_chars] + "\n\n... [truncated]"


async def collect_repo_context(
    client: GitHubClient,
    files: list[RepoFile],
    owner: str,
    repo: str,
    branch: str,
) -> str:
    """Fetch file contents and assemble the context string for the LLM."""
    prioritized = filter_files(files)
    to_fetch = prioritized[:MAX_FILES_TO_FETCH]

    semaphore = asyncio.Semaphore(10)

    async def _fetch(f: RepoFile):
        async with semaphore:
            content, commit_info = await asyncio.gather(
                client.fetch_file_content(f),
                client.get_file_commit_info(owner, repo, branch, f.path),
            )
            if content is not None:
                f.content = content
            f.last_commit_timestamp, f.commit_count = commit_info

    await asyncio.gather(*[_fetch(f) for f in to_fetch])

    tree = build_directory_tree(files)
    parts: list[str] = [f"## Directory Structure\n```\n{tree}\n```\n"]
    total_chars = len(parts[0])

    for f in to_fetch:
        if f.content is None:
            continue
        content = truncate_content(f.content)
        last_commit = f.last_commit_timestamp or "unknown"
        commit_count = f.commit_count if f.commit_count is not None else "unknown"
        section = (
            f"## File: {f.path}\n"
            f"Last commit timestamp: {last_commit}\n"
            f"Commit count: {commit_count}\n"
            f"```\n{content}\n```\n"
        )

        if total_chars + len(section) > MAX_CONTEXT_CHARS:
            remaining = MAX_CONTEXT_CHARS - total_chars
            if remaining > 500:
                section = f"## File: {f.path}\n```\n{content[:remaining - 200]}\n... [truncated]\n```\n"
                parts.append(section)
            break
        parts.append(section)
        total_chars += len(section)

    return "\n".join(parts)
