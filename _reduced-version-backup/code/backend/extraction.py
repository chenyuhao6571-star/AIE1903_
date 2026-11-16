from io import BytesIO
from pathlib import Path
from zipfile import ZipFile, BadZipFile

from docx import Document
from pypdf import PdfReader

MAX_BYTES = 15 * 1024 * 1024
MAX_TEXT = 120_000


class DocumentError(Exception):
    pass


def extract_document(content: bytes, filename: str) -> list[dict]:
    if not content or len(content) > MAX_BYTES:
        raise DocumentError("Choose a non-empty file smaller than 15 MB.")
    suffix = Path(filename).suffix.lower()
    try:
        if suffix == ".pdf":
            reader = PdfReader(BytesIO(content))
            if reader.is_encrypted and not reader.decrypt(""):
                raise DocumentError("This PDF is password protected. Upload an unlocked copy.")
            if len(reader.pages) > 80:
                raise DocumentError("This CV is too long. Upload a document with at most 80 pages.")
            pages = [{"number": i + 1, "text": p.extract_text() or ""}
                     for i, p in enumerate(reader.pages)]
        elif suffix == ".docx":
            with ZipFile(BytesIO(content)) as archive:
                if sum(x.file_size for x in archive.infolist()) > 30 * 1024 * 1024:
                    raise DocumentError("The Word document is too large when unpacked.")
            document = Document(BytesIO(content))
            # Word has no stable page boundaries. Preserve document order as blocks.
            blocks = []
            for element in document.iter_inner_content():
                if hasattr(element, "rows"):
                    blocks.append("\n".join(" | ".join(c.text for c in row.cells)
                                            for row in element.rows))
                else:
                    blocks.append(element.text)
            pages = [{"number": i + 1, "text": t} for i, t in enumerate(blocks) if t.strip()]
        else:
            raise DocumentError("Upload a PDF or .docx file. Save older .doc files as .docx first.")
    except DocumentError:
        raise
    except (Exception, BadZipFile) as exc:
        raise DocumentError("Could not read this document. Re-save it as PDF or .docx and try again.") from exc
    count = sum(len(p["text"].strip()) for p in pages)
    if count < 50:
        raise DocumentError("No readable text found. Upload a text-based PDF or a .docx file; scans need OCR first.")
    if count > MAX_TEXT:
        raise DocumentError("The extracted text is too long. Upload a shorter CV.")
    return pages
