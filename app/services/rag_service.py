"""
LifeOS – RAG Service
PDF parsing, chunking, FAISS vector store, and semantic retrieval.
Uses SentenceTransformers (all-MiniLM-L6-v2) for cost-effective local embeddings.
"""

import os
import json
import numpy as np
from typing import List, Optional, Tuple
from pathlib import Path

from sqlalchemy.orm import Session
from PyPDF2 import PdfReader

from app.config.settings import settings
from app.models.models import Document, DocumentChunk

# Lazy-loaded globals for expensive models
_embedding_model = None
_faiss = None


def _get_embedding_model():
    """Lazy-load SentenceTransformer model."""
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer
        _embedding_model = SentenceTransformer(settings.EMBEDDING_MODEL)
    return _embedding_model


def _get_faiss():
    """Lazy-load FAISS."""
    global _faiss
    if _faiss is None:
        import faiss as _f
        _faiss = _f
    return _faiss


# ═══════════════════════════════════════════════════════════
#  PDF PARSING & CHUNKING
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


def chunk_text(
    text: str,
    chunk_size: int = None,
    chunk_overlap: int = None,
) -> List[str]:
    """Split text into overlapping chunks for embedding."""
    chunk_size = chunk_size or settings.CHUNK_SIZE
    chunk_overlap = chunk_overlap or settings.CHUNK_OVERLAP

    if not text:
        return []

    # Split by sentences first for better chunk boundaries
    sentences = text.replace("\n", " ").split(". ")
    chunks = []
    current_chunk = ""

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        if len(current_chunk) + len(sentence) + 2 <= chunk_size:
            current_chunk += sentence + ". "
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
            # Start new chunk with overlap
            overlap_text = current_chunk[-chunk_overlap:] if len(current_chunk) > chunk_overlap else current_chunk
            current_chunk = overlap_text + sentence + ". "

    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    return chunks


# ═══════════════════════════════════════════════════════════
#  FAISS INDEX MANAGEMENT
# ═══════════════════════════════════════════════════════════

def create_faiss_index(embeddings: np.ndarray) -> object:
    """Create a FAISS L2 index from embeddings."""
    faiss = _get_faiss()
    dimension = embeddings.shape[1]
    index = faiss.IndexFlatL2(dimension)
    index.add(embeddings.astype("float32"))
    return index


def save_faiss_index(index, index_path: str):
    """Persist FAISS index to disk."""
    faiss = _get_faiss()
    os.makedirs(os.path.dirname(index_path), exist_ok=True)
    faiss.write_index(index, index_path)


def load_faiss_index(index_path: str):
    """Load FAISS index from disk."""
    faiss = _get_faiss()
    if not os.path.exists(index_path):
        raise FileNotFoundError(f"FAISS index not found: {index_path}")
    return faiss.read_index(index_path)


# ═══════════════════════════════════════════════════════════
#  DOCUMENT PROCESSING PIPELINE
# ═══════════════════════════════════════════════════════════

def process_document(
    db: Session,
    user_id: str,
    file_path: str,
    filename: str,
) -> Document:
    """
    Full pipeline: PDF → text → chunks → embeddings → FAISS index.
    Stores metadata in MySQL, vectors in FAISS.
    """
    # 1. Extract text
    text = extract_text_from_pdf(file_path)
    if not text:
        raise ValueError("Could not extract text from PDF")

    # 2. Chunk
    chunks = chunk_text(text)
    if not chunks:
        raise ValueError("No chunks generated from document")

    # 3. Generate embeddings
    model = _get_embedding_model()
    embeddings = model.encode(chunks, show_progress_bar=False)
    embeddings = np.array(embeddings)

    # 4. Create & save FAISS index
    index = create_faiss_index(embeddings)
    index_dir = Path(settings.FAISS_INDEX_DIR) / user_id
    index_path = str(index_dir / f"{filename}.faiss")
    save_faiss_index(index, index_path)

    # 5. Store document metadata in DB
    doc = Document(
        user_id=user_id,
        filename=filename,
        file_type="pdf",
        chunk_count=len(chunks),
        faiss_index_path=index_path,
    )
    db.add(doc)
    db.flush()

    # 6. Store chunks in DB
    for i, chunk_content in enumerate(chunks):
        chunk = DocumentChunk(
            document_id=doc.id,
            chunk_index=i,
            content=chunk_content,
            chunk_metadata={"page_estimate": i // 3},  # Rough estimate
        )
        db.add(chunk)

    db.commit()
    db.refresh(doc)
    return doc


# ═══════════════════════════════════════════════════════════
#  SEMANTIC RETRIEVAL
# ═══════════════════════════════════════════════════════════

def retrieve_relevant_chunks(
    db: Session,
    document_id: str,
    query: str,
    top_k: int = None,
) -> List[Tuple[str, float]]:
    """
    Retrieve top-k relevant chunks for a query using FAISS similarity search.
    Returns list of (chunk_content, distance) tuples.
    """
    top_k = top_k or settings.TOP_K_RETRIEVAL

    # Load document info
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc or not doc.faiss_index_path:
        raise ValueError("Document or FAISS index not found")

    # Load FAISS index
    index = load_faiss_index(doc.faiss_index_path)

    # Embed query
    model = _get_embedding_model()
    query_embedding = model.encode([query])
    query_embedding = np.array(query_embedding).astype("float32")

    # Search
    distances, indices = index.search(query_embedding, min(top_k, doc.chunk_count))

    # Fetch chunks from DB
    chunks = db.query(DocumentChunk).filter(
        DocumentChunk.document_id == document_id
    ).order_by(DocumentChunk.chunk_index).all()

    results = []
    for i, idx in enumerate(indices[0]):
        if idx < len(chunks) and idx >= 0:
            results.append((chunks[idx].content, float(distances[0][i])))

    return results


def retrieve_context_for_query(
    db: Session,
    user_id: str,
    query: str,
    document_id: Optional[str] = None,
    top_k: int = None,
) -> str:
    """
    Retrieve context from user's documents for LLM grounding.
    Searches specific document or all user documents.
    """
    top_k = top_k or settings.TOP_K_RETRIEVAL

    if document_id:
        doc_ids = [document_id]
    else:
        docs = db.query(Document).filter(Document.user_id == user_id).all()
        doc_ids = [d.id for d in docs]

    if not doc_ids:
        return ""

    all_chunks = []
    for did in doc_ids:
        try:
            chunks = retrieve_relevant_chunks(db, did, query, top_k)
            all_chunks.extend(chunks)
        except (ValueError, FileNotFoundError):
            continue

    # Sort by relevance (lower distance = more relevant)
    all_chunks.sort(key=lambda x: x[1])

    # Deduplicate and take top-k
    seen = set()
    context_parts = []
    for content, dist in all_chunks[:top_k]:
        if content not in seen:
            seen.add(content)
            context_parts.append(content)

    return "\n\n---\n\n".join(context_parts)
