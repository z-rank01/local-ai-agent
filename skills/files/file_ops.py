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

# Character window for one read. Files larger than this are delivered in pages:
# the response carries total_chars/next_offset so the caller can continue instead
# of silently losing the tail. 0 disables the cap (single-shot read).
def _read_char_limit() -> int:
    try:
        return int(os.environ.get("FILE_READ_MAX_CHARS", "200000"))
    except (TypeError, ValueError):
        return 200000


_PAGE_MARKER = (
    "\n[本页为第 {start}-{end} 字符，共 {total} 字符；"
    "继续读取请传 offset={next_offset}]"
)
_TAIL_MARKER = "\n[已读到文件末尾：第 {start}-{end} 字符，共 {total} 字符]"

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

    def read(self, path: str, encoding: str = "utf-8", *,
             offset: int = 0, max_chars: int | None = None) -> dict:
        resolved = self._guard.resolve(path)
        if not resolved.exists():
            raise FileNotFoundError(f"File not found: {path!r}")
        if not resolved.is_file():
            raise IsADirectoryError(f"Path is a directory, not a file: {path!r}")

        # Read once: the sha256 always describes the same bytes this response is based on.
        raw = resolved.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        suffix = resolved.suffix.lower()

        # Built-in converters for common formats. Extraction happens first so the
        # character window always applies to text, never to raw bytes.
        if suffix in _XLSX_SUFFIXES:
            text, extra = _read_excel_as_text(resolved), {}
        elif suffix in _PDF_SUFFIXES:
            text, extra = _read_pdf_as_text(resolved), {}
        elif suffix in _TEXT_SUFFIXES or not _is_binary(raw):
            encodings = (encoding,) + tuple(
                e for e in self._FALLBACK_ENCODINGS if e != encoding
            )
            text, extra = None, {}
            for enc in encodings:
                try:
                    text = raw.decode(enc)
                    extra = {"encoding": enc}
                    break
                except (UnicodeDecodeError, LookupError):
                    continue
            if text is None:
                text = raw.decode("utf-8", errors="replace")
                extra = {"encoding": "utf-8+replace"}
        else:
            # Unsupported binary file — return structured metadata
            return {
                "unsupported": True,
                "extension": suffix,
                "path": path,
                "size": len(raw),
                "hint": f"此文件类型({suffix})需要转换器，请通过 file_convert 处理",
            }

        return self._window(text, path, digest, offset, max_chars, extra)

    @staticmethod
    def _window(text: str, path: str, digest: str, offset: int,
                max_chars: int | None, extra: dict) -> dict:
        """Slice extracted text into one page and describe how to fetch the next."""
        total = len(text)
        limit = max_chars if isinstance(max_chars, int) and max_chars > 0 else _read_char_limit()
        if limit <= 0:
            limit = total or 1

        if offset >= total:
            return {"content": f"[offset={offset} 已在文件末尾（共 {total} 字符）；"
                               "如需重读请传更小的 offset]",
                    "sha256": digest, "path": path,
                    "total_chars": total, "next_offset": None, **extra}

        end = min(offset + limit, total)
        body = text[offset:end]
        complete = end >= total
        marker = (_TAIL_MARKER if complete else _PAGE_MARKER).format(
            start=offset, end=end, total=total,
            next_offset=end if not complete else None)
        return {"content": body + marker, "sha256": digest, "path": path,
                "total_chars": total,
                "next_offset": None if complete else end,
                "range": {"start": offset, "end": end, "total": total},
                **extra}

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
