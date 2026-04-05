from pathlib import Path

from path_guard import PathGuard

_XLSX_SUFFIXES = {".xlsx", ".xls", ".xlsm", ".xlsb"}


def _read_excel_as_text(resolved: Path) -> str:
    """Convert an Excel workbook to a CSV-like text representation."""
    try:
        import pandas as pd
    except ImportError as exc:
        raise ImportError("pandas is required to read Excel files. Install it with: pip install pandas openpyxl") from exc

    xl = pd.ExcelFile(resolved, engine="openpyxl" if resolved.suffix.lower() != ".xls" else "xlrd")
    parts: list[str] = []
    for sheet in xl.sheet_names:
        df = xl.parse(sheet)
        parts.append(f"[Sheet: {sheet}]\n{df.to_csv(index=False)}")
    return "\n".join(parts)


class FileOps:
    def __init__(self, guard: PathGuard):
        self._guard = guard

    def read(self, path: str, encoding: str = "utf-8") -> str:
        resolved = self._guard.resolve(path)
        if not resolved.exists():
            raise FileNotFoundError(f"File not found: {path!r}")
        if not resolved.is_file():
            raise IsADirectoryError(f"Path is a directory, not a file: {path!r}")
        if resolved.suffix.lower() in _XLSX_SUFFIXES:
            return _read_excel_as_text(resolved)
        return resolved.read_text(encoding=encoding)

    def write(self, path: str, content: str, encoding: str = "utf-8") -> dict:
        resolved = self._guard.resolve(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding=encoding)
        return {"written": str(resolved), "bytes": len(content.encode(encoding))}

    def list_dir(self, path: str) -> list[dict]:
        resolved = self._guard.resolve(path)
        if not resolved.exists():
            raise FileNotFoundError(f"Directory not found: {path!r}")
        if not resolved.is_dir():
            raise NotADirectoryError(f"Not a directory: {path!r}")
        entries = []
        for child in sorted(resolved.iterdir()):
            entries.append({
                "name": child.name,
                "type": "dir" if child.is_dir() else "file",
                "size": child.stat().st_size if child.is_file() else None,
            })
        return entries
