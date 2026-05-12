import re
import shutil
import tempfile
from pathlib import Path

import git

TEMP_DIR = Path(tempfile.gettempdir()) / "codebase_investigator"
TEMP_DIR.mkdir(exist_ok=True)


def parse_github_url(url: str) -> tuple[str, str]:
    patterns = [
        r"github\.com[:/]([^/]+)/([^/\s.]+?)(?:\.git)?(?:/.*)?$",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1), match.group(2)
    raise ValueError(f"Could not parse GitHub URL: {url!r}")


def clone_repo(url: str) -> Path:
    owner, repo = parse_github_url(url)
    repo_dir = TEMP_DIR / f"{owner}_{repo}"

    if repo_dir.exists():
        shutil.rmtree(repo_dir)

    clean_url = f"https://github.com/{owner}/{repo}.git"
    git.Repo.clone_from(clean_url, repo_dir, depth=1, single_branch=True)

    return repo_dir


def cleanup_repo(repo_path: Path) -> None:
    if repo_path and repo_path.exists():
        shutil.rmtree(repo_path, ignore_errors=True)
