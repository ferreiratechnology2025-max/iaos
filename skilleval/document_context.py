"""Document layer: interprets the RAW BYTES that governance already authorized and bound (fs.read_bytes).

This module has no path, file, network or governance access: it takes bytes in and returns data for cognition.
  * PDF   -> text extracted with pypdf from the in-memory bytes (no OCR, no fallback: no text => DocumentError)
  * image -> validated PNG/JPEG bytes the runtime attaches to the model request as-is (no conversion, no resizing)
The result is DATA for the model; it is never a Proposal and carries no authority.
"""
import io
from dataclasses import dataclass

SUPPORTED = {".pdf": "pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
SIGNATURES = {"pdf": (b"%PDF-",), "image/png": (b"\x89PNG\r\n\x1a\n",), "image/jpeg": (b"\xff\xd8\xff",)}


class DocumentError(Exception):
    """Stable, content-free ``code``: unsupported_document | invalid_document | document_no_text | context_too_large |
    document_support_unavailable."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PdfText:
    text: str
    pages: int


def kind_for(name: str) -> str:
    """'pdf' | 'image/png' | 'image/jpeg' from the extension alone (decided BEFORE any read is requested)."""
    leaf = name.replace("\\", "/").rsplit("/", 1)[-1]
    ext = ("." + leaf.rsplit(".", 1)[-1].lower()) if "." in leaf else ""
    kind = SUPPORTED.get(ext)
    if kind is None:
        raise DocumentError("unsupported_document")
    return kind


def check_signature(kind: str, data: bytes) -> None:
    """The authorized bytes must actually look like what the extension claims (cheap magic-number check)."""
    if not any(data.startswith(sig) for sig in SIGNATURES[kind]):
        raise DocumentError("invalid_document")


def extract_pdf(data: bytes, max_chars: int) -> PdfText:
    """Text of every page, in order. Stops as soon as ``max_chars`` is exceeded (context_too_large)."""
    if max_chars <= 0:
        raise DocumentError("context_too_large")
    try:
        import pypdf
    except ImportError:
        raise DocumentError("document_support_unavailable")
    try:
        reader = pypdf.PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise DocumentError("invalid_document")
        n = len(reader.pages)
        parts, total = [], 0
        for i, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if not text:
                continue
            block = f"--- page {i} ---\n{text}"
            total += len(block) + 2
            if total > max_chars:
                raise DocumentError("context_too_large")
            parts.append(block)
    except DocumentError:
        raise
    except Exception:  # malformed PDF: pypdf raises many types; none may leak content or reach the model
        raise DocumentError("invalid_document")
    if not parts:
        raise DocumentError("document_no_text")
    return PdfText("\n\n".join(parts), n)
