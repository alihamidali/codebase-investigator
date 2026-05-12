import re
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

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

    zip_url = f"https://api.github.com/repos/{owner}/{repo}/zipball"
    zip_path = TEMP_DIR / f"{owner}_{repo}.zip"

    req = urllib.request.Request(zip_url, headers={"User-Agent": "codebase-investigator"})
    with urllib.request.urlopen(req) as resp, open(zip_path, "wb") as f:
        shutil.copyfileobj(resp, f)

    extract_dir = TEMP_DIR / f"{owner}_{repo}_extract"
    if extract_dir.exists():
        shutil.rmtree(extract_dir)

    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_dir)

    zip_path.unlink(missing_ok=True)

    # GitHub zips contain a single top-level directory like "{owner}-{repo}-{sha}"
    contents = list(extract_dir.iterdir())
    if len(contents) == 1 and contents[0].is_dir():
        shutil.move(str(contents[0]), str(repo_dir))
        shutil.rmtree(extract_dir)
    else:
        shutil.move(str(extract_dir), str(repo_dir))

    return repo_dir


def cleanup_repo(repo_path: Path) -> None:
    if repo_path and repo_path.exists():
        shutil.rmtree(repo_path, ignore_errors=True)
