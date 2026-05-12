import subprocess
from pathlib import Path

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "build", ".next", ".nuxt", "vendor", "target", ".cargo",
    "coverage", ".pytest_cache", ".mypy_cache", ".ruff_cache",
}

BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp", ".bmp",
    ".pdf", ".zip", ".tar", ".gz", ".7z", ".rar", ".exe", ".bin",
    ".pyc", ".pyo", ".class", ".so", ".dylib", ".dll", ".wasm",
    ".mp3", ".mp4", ".avi", ".mov", ".wav", ".flac",
    ".ttf", ".woff", ".woff2", ".eot",
}


class CodebaseTools:
    def __init__(self, repo_path: Path):
        self.repo_path = repo_path.resolve()

    def call(self, tool_name: str, tool_input: dict) -> dict:
        handlers = {
            "read_file": self.read_file,
            "search_code": self.search_code,
            "list_files": self.list_files,
            "get_structure": self.get_structure,
        }
        handler = handlers.get(tool_name)
        if not handler:
            return {"error": f"Unknown tool: {tool_name}"}
        try:
            return handler(**{k: v for k, v in tool_input.items() if v is not None})
        except TypeError as e:
            return {"error": f"Invalid arguments for {tool_name}: {e}"}

    def _safe_path(self, path: str) -> Path:
        resolved = (self.repo_path / path.lstrip("/")).resolve()
        if not str(resolved).startswith(str(self.repo_path)):
            raise ValueError("Path traversal not allowed")
        return resolved

    def read_file(self, path: str, start_line: int = None, end_line: int = None) -> dict:
        try:
            full = self._safe_path(path)
        except ValueError as e:
            return {"error": str(e)}

        if not full.exists():
            return {"error": f"File not found: {path}"}
        if not full.is_file():
            return {"error": f"Not a file: {path}"}
        if full.suffix.lower() in BINARY_EXTENSIONS:
            return {"error": f"Binary file skipped: {path}"}

        try:
            text = full.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return {"error": f"Read error: {e}"}

        lines = text.splitlines()
        total = len(lines)

        s = max(0, (start_line or 1) - 1)
        e = min(total, (end_line or s + 80))
        # Hard cap to avoid context bloat — use start_line/end_line to target sections
        e = min(e, s + 120)

        numbered = "\n".join(f"{s + i + 1}: {line}" for i, line in enumerate(lines[s:e]))

        result = {
            "path": path,
            "total_lines": total,
            "shown": f"{s + 1}-{e}",
            "content": numbered,
        }
        if e < total and not end_line:
            result["note"] = f"Showing lines {s+1}-{e} of {total} total. Use start_line/end_line to read other sections."
        return result

    def search_code(self, pattern: str, file_glob: str = None, context_lines: int = 2) -> dict:
        cmd = [
            "grep", "-rn", "--color=never", "-I",  # -I skips binary files
            f"--context={context_lines}",
        ]
        if file_glob:
            cmd += ["--include", file_glob]
        for d in SKIP_DIRS:
            cmd += ["--exclude-dir", d]
        cmd += [pattern, str(self.repo_path)]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        except subprocess.TimeoutExpired:
            return {"error": "Search timed out — try a more specific pattern"}
        except FileNotFoundError:
            return self._python_search(pattern, file_glob, context_lines)

        raw_lines = result.stdout.splitlines()
        if not raw_lines:
            return {"pattern": pattern, "matches": "", "message": "No matches found"}

        prefix = str(self.repo_path) + "/"
        cleaned = [ln.replace(prefix, "") for ln in raw_lines[:300]]
        return {
            "pattern": pattern,
            "matches": "\n".join(cleaned),
            "truncated": len(raw_lines) > 300,
        }

    def _python_search(self, pattern: str, file_glob: str, context_lines: int) -> dict:
        """Fallback search when grep is unavailable."""
        import re
        matches = []
        glob = file_glob or "*"
        try:
            rx = re.compile(pattern, re.IGNORECASE)
        except re.error:
            rx = re.compile(re.escape(pattern), re.IGNORECASE)

        for fpath in self.repo_path.rglob(glob):
            if not fpath.is_file():
                continue
            if any(p in SKIP_DIRS or p.startswith(".") for p in fpath.parts):
                continue
            if fpath.suffix.lower() in BINARY_EXTENSIONS:
                continue
            try:
                lines = fpath.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception:
                continue
            rel = str(fpath.relative_to(self.repo_path))
            for i, line in enumerate(lines):
                if rx.search(line):
                    start = max(0, i - context_lines)
                    end = min(len(lines), i + context_lines + 1)
                    for j in range(start, end):
                        sep = ":" if j == i else "-"
                        matches.append(f"{rel}{sep}{j+1}{sep}{lines[j]}")
                    matches.append("--")
                    if len(matches) > 500:
                        break
            if len(matches) > 500:
                break

        return {"pattern": pattern, "matches": "\n".join(matches[:400])}

    def list_files(self, directory: str = ".", pattern: str = None) -> dict:
        try:
            base = self._safe_path(directory) if directory != "." else self.repo_path
        except ValueError as e:
            return {"error": str(e)}

        if not base.exists():
            return {"error": f"Directory not found: {directory}"}

        files = []
        for item in sorted(base.rglob(pattern or "*")):
            parts = item.relative_to(self.repo_path).parts
            if any(p in SKIP_DIRS or p.startswith(".") for p in parts):
                continue
            if not item.is_file():
                continue
            if item.suffix.lower() in BINARY_EXTENSIONS:
                continue
            files.append(str(item.relative_to(self.repo_path)))
            if len(files) >= 300:
                break

        return {"directory": directory, "files": files, "total": len(files)}

    def get_structure(self, max_depth: int = 3, directory: str = None) -> dict:
        try:
            base = self._safe_path(directory) if directory else self.repo_path
        except ValueError as e:
            return {"error": str(e)}

        lines: list[str] = []
        self._walk(base, 0, max_depth, lines)
        return {"structure": "\n".join(lines[:600])}

    def _walk(self, path: Path, depth: int, max_depth: int, lines: list[str]) -> None:
        if depth > max_depth:
            return
        try:
            entries = sorted(path.iterdir())
        except PermissionError:
            return

        indent = "  " * depth
        dirs = [e for e in entries if e.is_dir() and e.name not in SKIP_DIRS and not e.name.startswith(".")]
        files = [e for e in entries if e.is_file() and e.suffix.lower() not in BINARY_EXTENSIONS and not e.name.startswith(".")]

        for d in dirs:
            lines.append(f"{indent}{d.name}/")
            self._walk(d, depth + 1, max_depth, lines)

        for f in files[:50]:
            lines.append(f"{indent}{f.name}")
