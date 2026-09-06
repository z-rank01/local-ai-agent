import hashlib
import difflib
import tempfile
import threading
import os

_FILE_LOCK = threading.RLock()

from pathlib import Path

from path_guard import PathGuard

_XLSX_SUFFIXES = {".xlsx", ".xls", ".xlsm", ".xlsb"}
_PDF_SUFFIXES = {".pdf"}

# Known text file extensions — read directly with encoding detection.
_TEXT_SUFFIXES = {
    ".txt", ".csv", ".tsv", ".json", ".jsonl", ".xml", ".yaml", ".yml",
    ".md", ".rst", ".log", ".ini", ".cfg", ".conf", ".toml",
    ".py", ".js", ".ts", ".java", ".c", ".cpp", ".h", ".cs", ".go",
    ".rs", ".rb", ".php", ".sh", ".bat", ".ps1", ".sql", ".r",
    ".html", ".htm", ".css", ".scss", ".less", ".svg",
    ".tex", ".bib", ".env", ".gitignore", ".dockerfile",
}


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


def _read_pdf_as_text(resolved: Path) -> str:
    """Extract text from a PDF file using PyMuPDF."""
    try:
        import fitz  # pymupdf
    except ImportError as exc:
        raise ImportError("pymupdf is required to read PDF files. Install it with: pip install pymupdf") from exc

    doc = fitz.open(str(resolved))
    parts: list[str] = []
    for i, page in enumerate(doc, 1):
        text = page.get_text().strip()
        if text:
            parts.append(f"[Page {i}]\n{text}")
    doc.close()

    if not parts:
        return "[PDF文件无法提取文本内容，可能是扫描件或纯图片PDF]"
    return "\n\n".join(parts)


def _is_binary(data: bytes, sample_size: int = 8192) -> bool:
    """Heuristic: if more than 10% of the sample contains null bytes or
    non-text control characters, treat the file as binary."""
    sample = data[:sample_size]
    if not sample:
        return False
    if b"\x00" in sample:
        return True
    control = sum(1 for b in sample if b < 8 or (14 <= b < 32))
    return control / len(sample) > 0.10


class FileOps:
    def __init__(self, guard: PathGuard):
        self._guard = guard

    _FALLBACK_ENCODINGS = ("utf-8", "gbk", "gb2312", "gb18030", "big5", "latin-1")

    def read(self, path: str, encoding: str = "utf-8") -> dict | str:
        resolved = self._guard.resolve(path)
        if not resolved.exists():
            raise FileNotFoundError(f"File not found: {path!r}")
        if not resolved.is_file():
            raise IsADirectoryError(f"Path is a directory, not a file: {path!r}")

        suffix = resolved.suffix.lower()

        # Built-in converters for common formats
        if suffix in _XLSX_SUFFIXES:
            return _read_excel_as_text(resolved)
        if suffix in _PDF_SUFFIXES:
            return _read_pdf_as_text(resolved)

        raw = resolved.read_bytes()

        # Known text extensions or small files: try text decoding
        if suffix in _TEXT_SUFFIXES or not _is_binary(raw):
            encodings = (encoding,) + tuple(
                e for e in self._FALLBACK_ENCODINGS if e != encoding
            )
            for enc in encodings:
                try:
                    return {'content':raw.decode(enc), 'sha256':hashlib.sha256(raw).hexdigest(), 'encoding':enc, 'path':path}
                except (UnicodeDecodeError, LookupError):
                    continue
            return raw.decode("utf-8", errors="replace")

        # Unsupported binary file — return structured metadata
        return {
            "unsupported": True,
            "extension": suffix,
            "path": path,
            "size": len(raw),
            "hint": f"此文件类型({suffix})需要转换器，请通过 file_convert 处理",
        }

    def write(self, path: str, content: str, encoding: str = "utf-8") -> dict:
        resolved = self._guard.resolve(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding=encoding)
        return {"written": str(resolved), "bytes": len(content.encode(encoding))}

    def edit(self, path, old_text, new_text, expected_sha256):
        resolved = self._guard.resolve(path)
        with _FILE_LOCK:
            original = resolved.read_bytes()
            if hashlib.sha256(original).hexdigest() != expected_sha256:
                raise ValueError('文件已变化，请重新读取后再修改')
            if not old_text:
                raise ValueError('old_text 不能为空')
            try:
                text = original.decode('utf-8')
            except UnicodeDecodeError:
                raise ValueError('精确编辑仅支持 UTF-8；原文件未改变，请先显式转换编码') from None
            if text.count(old_text) != 1:
                raise ValueError('原文必须唯一匹配，请读取更多上下文后重试')
            updated = text.replace(old_text, new_text, 1)
            fd, temporary = tempfile.mkstemp(dir=resolved.parent)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(updated.encode('utf-8'))
                    stream.flush()
                    os.fsync(stream.fileno())
                if resolved.read_bytes() != original:
                    raise ValueError('文件在修改期间变化，请重新读取')
                os.replace(temporary, resolved)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            diff = ''.join(difflib.unified_diff(text.splitlines(True), updated.splitlines(True), fromfile=path, tofile=path))
            return {'path':path, 'sha256':hashlib.sha256(updated.encode('utf-8')).hexdigest(), 'diff':diff,
                    'changed':text != updated}

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
