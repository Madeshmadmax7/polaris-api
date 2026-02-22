"""
LifeOS – RAG Service (Simplified)
PDF text extraction and storage - no embeddings/FAISS for faster performance.
"""

import os
from typing import Optional

from sqlalchemy.orm import Session
from PyPDF2 import PdfReader

from app.models.models import Document


# ═══════════════════════════════════════════════════════════
#  PDF TEXT EXTRACTION
# ═══════════════════════════════════════════════════════════

def extract_text_from_pdf(file_path: str) -> str:
    """Extract all text from a PDF file."""
    reader = PdfReader(file_path)
    text = ""
    for page in reader.pages:
        page_text = page.extract_text()
        if page_text:
            text += page_text + "\n"
    return text.strip()


def process_document(
    db: Session,
    user_id: str,
    file_path: str,
    filename: str,
) -> Document:
    """
    Fast PDF processing: extract text and store directly in DB.
    No chunking, no embeddings, no FAISS - instant upload.
    """
    # Extract text
    text = extract_text_from_pdf(file_path)
    if not text:
        raise ValueError("Could not extract text from PDF")

    # Store document with full text
    doc = Document(
        user_id=user_id,
        filename=filename,
        file_type="pdf",
        content=text,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def get_document_content(db: Session, document_id: str) -> Optional[str]:
    """Retrieve full document content by ID."""
    doc = db.query(Document).filter(Document.id == document_id).first()
    return doc.content if doc else None
