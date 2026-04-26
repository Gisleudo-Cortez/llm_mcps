import base64
import io
import mimetypes
import os
import re
from datetime import datetime

import numpy as np

import fitz  # PyMuPDF
import pandas as pd
import pypdf
from chromadb import PersistentClient
from markitdown import MarkItDown
from mcp.server.fastmcp import FastMCP, Image
from PIL import Image as PILImage

# Initialize the MCP server
mcp = FastMCP("RAG Document Tools")
md_converter = MarkItDown()

# Initialize Vector DB Storage (Instant)
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chroma_db")
chroma_client = PersistentClient(path=DB_PATH)

# Lazy-Loaded Embedding Model (Prevents Timeout during MCP Handshake)
_embed_model = None


def get_embed_model():
    """Returns the embedding model, initializing it only on the first call."""
    global _embed_model
    if _embed_model is None:
        # Import moved inside to avoid slowing down initial script execution
        from sentence_transformers import SentenceTransformer

        _embed_model = SentenceTransformer("all-MiniLM-L6-v2")
    return _embed_model


# --- Helper: Sentence-aware text chunker ---
def _chunk_text(text: str, chunk_target: int = 800, overlap: int = 100) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks: list[str] = []
    current_chunk: list[str] = []
    current_len = 0
    for sent in sentences:
        sent_len = len(sent)
        if current_len + sent_len > chunk_target and current_chunk:
            chunks.append(" ".join(current_chunk))
            overlap_sents: list[str] = []
            overlap_len = 0
            for s in reversed(current_chunk):
                if overlap_len + len(s) > overlap:
                    break
                overlap_sents.insert(0, s)
                overlap_len += len(s)
            current_chunk = overlap_sents
            current_len = overlap_len
        current_chunk.append(sent)
        current_len += sent_len
    if current_chunk:
        chunks.append(" ".join(current_chunk))
    return chunks


# --- Helper: Format File Size ---
def format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.2f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.2f} MB"


# --- Tool 1: Metadata Discovery ---
@mcp.tool()
def get_doc_metadata(file_path: str) -> str:
    """
    USE THIS FIRST when encountering a new file path. Determine file type, size,
    and structural complexity before attempting content extraction to prevent errors.

    **TRIGGER CONDITION:** Call this immediately after receiving any file path from user input.

    **SEQUENCE GUIDANCE:** Always call `get_doc_metadata` BEFORE `read_doc_content`,
    `index_document_for_search`, or `compare_documents`. Use the returned page counts,
    sheet names, and line counts to set appropriate parameters for subsequent tools.

    **CONSTRAINT WARNING:** Avoid calling this on files larger than 50MB without explicit
    user confirmation. Large PDFs (>200 pages) or Excel files with many sheets may cause
    context overflow when reading content—use pagination filters accordingly.

    **OUTPUT EXPECTATION:** Returns structured metadata (file size, page/sheet counts,
    author/title for PDFs). This output is ideal for making informed decisions about
    which extraction parameters to use next.

    *Example workflow:* get_doc_metadata → read_doc_content(start_page=1, end_page=10)
    """
    if not os.path.isfile(file_path):
        return f"Error: File '{file_path}' does not exist."

    stats = os.stat(file_path)
    ext = os.path.splitext(file_path)[1].lower()
    mime_type, _ = mimetypes.guess_type(file_path)

    metadata = [
        f"### Document Metadata: {os.path.basename(file_path)}",
        f"- **Path**: {file_path}",
        f"- **Size**: {format_size(stats.st_size)}",
        f"- **Last Modified**: {datetime.fromtimestamp(stats.st_mtime).strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **MIME Type**: {mime_type or 'Unknown'}",
        f"- **Extension**: {ext}",
    ]

    try:
        if ext == ".pdf":
            with open(file_path, "rb") as f:
                reader = pypdf.PdfReader(f)
                metadata.append(f"- **PDF Pages**: {len(reader.pages)}")
                if reader.metadata:
                    title = reader.metadata.get("/Title", "N/A")
                    author = reader.metadata.get("/Author", "N/A")
                    metadata.append(f"- **Author**: {author}")
                    metadata.append(f"- **Title**: {title}")

        elif ext in [".xlsx", ".xls", ".ods"]:
            xls = pd.ExcelFile(file_path)
            metadata.append(f"- **Excel Sheets**: {', '.join(xls.sheet_names)}")

        elif ext in [".csv", ".xml", ".json", ".txt", ".log"]:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                line_count = sum(1 for _ in f)
            metadata.append(f"- **Total Lines**: {line_count}")

    except Exception as e:
        metadata.append(
            f"- **Warning**: Could not parse deeper structural metadata ({str(e)})"
        )

    metadata.append(
        "\n*Note: Use `read_doc_content` with appropriate filters based on this metadata to extract the actual text.*"
    )
    return "\n".join(metadata)


# --- Tool 2: Targeted Content Extraction ---
@mcp.tool()
def read_doc_content(
    file_path: str,
    start_page: int = 0,
    end_page: int = 0,
    sheet_name: str = "",
    max_chars: int = 100000,
) -> str:
    """
    Extract specific text or pages from a known file. Use pagination filters to manage
    context window limits when dealing with large documents.

    **TRIGGER CONDITION:** Call this after `get_doc_metadata` has confirmed the document
    structure (page count, sheet names). Use only when you need actual content for analysis.

    **SEQUENCE GUIDANCE:** Always call `get_doc_metadata` first to determine optimal
    values for `start_page`, `end_page`, and `sheet_name`. For documents >100 pages,
    read in chunks (e.g., pages 1-50, then 51-100) rather than all at once.

    **CONSTRAINT WARNING:** Avoid calling without pagination filters on large PDFs (>200
    pages). If output is truncated, you'll see a warning message—call again with adjusted
    page ranges to retrieve additional content. Never set `max_chars` below 10k for
    meaningful analysis.

    **OUTPUT EXPECTATION:** Returns clean Markdown text (truncated at max_chars if needed).
    Ideal for feeding into downstream analysis, summarization, or indexing operations.

    *Error recovery:* If you receive "no readable text found," verify the file format
    is supported and try adjusting extraction parameters.
    """
    if not os.path.isfile(file_path):
        return f"Error: File '{file_path}' does not exist."

    ext = os.path.splitext(file_path)[1].lower()
    content = ""

    try:
        # 1. Targeted PDF Extraction
        if ext == ".pdf" and (start_page or end_page):
            with open(file_path, "rb") as f:
                reader = pypdf.PdfReader(f)
                total_pages = len(reader.pages)

                start_idx = max(0, (start_page or 1) - 1)
                end_idx = min(total_pages, (end_page or total_pages))

                extracted = []
                for i in range(start_idx, end_idx):
                    page_text = reader.pages[i].extract_text()
                    if page_text:
                        extracted.append(f"--- Page {i + 1} ---\n{page_text}")
                content = "\n\n".join(extracted)

        # 2. Targeted Excel Extraction
        elif ext in [".xlsx", ".xls", ".ods"] and sheet_name:
            df = pd.read_excel(file_path, sheet_name=sheet_name)
            content = f"### Sheet: {sheet_name}\n\n" + df.to_markdown(index=False)

        # 3. Universal Fallback
        else:
            result = md_converter.convert(file_path)
            content = result.text_content

        if not content.strip():
            return "Document was read successfully, but no readable text was found."

        if len(content) > max_chars:
            return (
                content[:max_chars]
                + f"\n\n... [Output truncated at {max_chars} characters to protect context window. Use pagination/filters to read further.]"
            )

        return content

    except Exception as e:
        return f"Error extracting content from document: {str(e)}"


# --- Tool 3: Visual Element Extraction (Vision Bridge) ---
@mcp.tool()
def list_document_images(file_path: str) -> str:
    """
    Identify all images/figures embedded within a PDF document before attempting extraction.

    **TRIGGER CONDITION:** Use this when you need to extract or analyze visual content
    from a PDF. Call BEFORE `extract_image_for_vision` to discover available image IDs.

    **SEQUENCE GUIDANCE:** Always call `list_document_images` first, review the returned
    list of image IDs and their locations, THEN call `extract_image_for_vision` with
    specific ID(s) you want to analyze. Do not guess image IDs—use this tool's output.

    **CONSTRAINT WARNING:** This tool ONLY works on PDF files (.pdf extension). Avoid
    calling on images, Office documents, or other formats—it will return an error. For
    multi-page PDFs with many images, expect a longer response listing all detected figures.

    **OUTPUT EXPECTATION:** Returns formatted list of image IDs, page numbers, and dimensions.
    Ideal for planning extraction workflows and selecting which visual elements to analyze.
    """
    if not file_path.lower().endswith(".pdf"):
        return "Error: Image listing is currently only supported for PDFs."

    doc = fitz.open(file_path)
    found_images = []

    for page_index in range(len(doc)):
        image_list = doc.get_page_images(page_index)
        for _img_index, img in enumerate(image_list):
            xref = img[0]
            found_images.append(
                f"ID: {xref} | Page: {page_index + 1} | Dimensions: {img[2]}x{img[3]}"
            )

    if not found_images:
        return "No images found in this document."

    return "### Detected Images\n" + "\n".join(found_images)


@mcp.tool()
def extract_image_for_vision(file_path: str, image_id: int) -> str:
    """
    Extract a specific image from a PDF as Base64-encoded PNG data for vision model analysis.

    **TRIGGER CONDITION:** Use this AFTER `list_document_images` has confirmed the
    existence and ID of an image you wish to analyze. Essential when your task requires
    visual understanding (e.g., chart interpretation, diagram analysis).

    **SEQUENCE GUIDANCE:** MUST call `list_document_images` first to obtain valid image IDs.
    Pass the exact `image_id` returned by that tool—invalid IDs will cause extraction failure.

    **CONSTRAINT WARNING:** Do not attempt this on non-PDF files (returns error). For
    standalone images outside PDFs, use `load_local_image` instead. Each call processes
    one image only—call multiple times for batch analysis.

    **OUTPUT EXPECTATION:** Returns Base64-encoded PNG string prefixed with data URI scheme.
    Ideal for direct injection into vision-capable models or downstream
    computer vision pipelines.

    *Workflow example:* list_document_images → extract_image_for_vision(image_id=123) → feed to vision model
    """
    try:
        doc = fitz.open(file_path)
        pix = fitz.Pixmap(doc, image_id)

        if pix.n - pix.alpha > 3:  # CMYK needs conversion to RGB
            pix = fitz.Pixmap(fitz.csRGB, pix)

        img_data = pix.tobytes("png")
        base64_str = base64.b64encode(img_data).decode("utf-8")

        return f"data:image/png;base64,{base64_str}"
    except Exception as e:
        return f"Error extracting image: {str(e)}"


# --- Tool 4: Semantic Search (Vector RAG) ---
@mcp.tool()
def index_document_for_search(file_path: str, collection_name: str = "default") -> str:
    """
    Prepare a document for semantic retrieval by chunking and storing in the Vector DB.

    **TRIGGER CONDITION:** Call this ONLY when you plan to perform `semantic_search` on
    a file that hasn't been indexed yet. Use after confirming content quality via
    `read_doc_content`.

    **SEQUENCE GUIDANCE:** Optimal workflow: (1) get_doc_metadata → (2) read_doc_content
    (verify text exists) → (3) chunk_and_preview (tune parameters) → (4) index_document_for_search.
    Once indexed, use `semantic_search` instead of repeated full-document reads.

    **CONSTRAINT WARNING:** This is a HEAVY operation—computationally expensive due to
    embedding generation. Call ONCE per file; re-indexing wastes resources and may cause
    duplicate entries. Never call on files that will be frequently modified—index the
    final version instead. For large documents (>50MB), expect indexing to take 10-30 seconds.

    **OUTPUT EXPECTATION:** Returns chunk count, collection name, and first-chunk preview.
    Ideal for confirming successful indexing before search operations. Use `list_indexed_collections`
    afterward to verify the document appears in your target collection.

    *Lifecycle note:* Indexed documents persist until explicitly deleted via `delete_from_index`.
    """

    text = read_doc_content(file_path, max_chars=1_000_000)
    if not text.strip() or text.startswith("Error") or text.startswith("Document was read"):
        return f"Error: Could not extract text from '{file_path}'. Run get_doc_metadata to verify the file."

    chunks = _chunk_text(text)

    if not chunks:
        return "Error: No text content found to index."

    collection = chroma_client.get_or_create_collection(name=collection_name)

    # --- Deduplication: remove any existing chunks from this file ---
    file_basename = os.path.basename(file_path)
    try:
        existing = collection.get(where={"source": file_path})
        if existing["ids"]:
            collection.delete(ids=existing["ids"])
    except Exception:
        pass  # Collection may be empty; safe to continue

    ids = [f"{file_basename}_{i}" for i in range(len(chunks))]
    metadatas = [{"source": file_path, "chunk_index": i} for i in range(len(chunks))]
    embeddings = get_embed_model().encode(chunks).tolist()

    collection.add(
        ids=ids, embeddings=embeddings, documents=chunks, metadatas=metadatas
    )

    preview = chunks[0][:200].replace("\n", " ")
    return (
        f"Indexed {len(chunks)} chunks from '{file_basename}' into '{collection_name}'.\n"
        f'First chunk preview: "{preview}..."'
    )


@mcp.tool()
def semantic_search(
    query: str,
    collection_name: str = "default",
    n_results: int = 3,
    source_filter: str = "",
    max_distance: float = 1.2,
) -> str:
    """
    Search for concept-based information across indexed documents using semantic similarity.

    **TRIGGER CONDITION:** Use this when you have a QUESTION or CONCEPT to find but don't
    know the exact wording in documents. Ideal for exploratory queries like "What are the
    security recommendations?" rather than keyword searches.

    **SEQUENCE GUIDANCE:** ALWAYS call `index_document_for_search` first on target files—
    searching unindexed collections returns no results. For focused searches, set `source_filter`
    to a specific file path after reviewing indexed collections via `list_indexed_collections`.

    **CONSTRAINT WARNING:** If results are irrelevant, try REPHRASING your query (semantic search
    depends on natural language phrasing). Adjust `max_distance`: lower values (<0.8) for stricter
    matching, higher values (>1.5) when no results appear. Default of 1.2 balances precision/recall—
    don't change unless necessary.

    **OUTPUT EXPECTATION:** Returns ranked list with relevance scores (lower = better), source
    filenames, and chunk excerpts. Ideal for comparative analysis across documents, finding related
    content, or answering conceptual questions without exact keyword matches.

    *Error recovery:* No results? Try: 1) Verify indexing via `list_indexed_collections`, 2) Increase
    `max_distance`, 3) Rephrase query more naturally, 4) Check source_filter isn't too restrictive.
    """
    try:
        collection = chroma_client.get_collection(name=collection_name)
        query_embedding = get_embed_model().encode([query]).tolist()

        where_clause = {"source": source_filter} if source_filter else None

        results = collection.query(
            query_embeddings=query_embedding,
            n_results=n_results,
            where=where_clause,
            include=["documents", "metadatas", "distances"],
        )

        docs = results["documents"][0]
        metas = results["metadatas"][0]
        distances = results["distances"][0]

        if not docs:
            return "No results found. Ensure documents are indexed first."

        formatted = [f"### Semantic search results for: '{query}'\n"]
        returned = 0
        for i, (doc, meta, dist) in enumerate(zip(docs, metas, distances)):
            if dist > max_distance:
                continue  # Skip low-quality matches
            returned += 1
            source = os.path.basename(meta.get("source", "unknown"))
            chunk_idx = meta.get("chunk_index", "?")
            formatted.append(
                f"**Result {returned}** | Source: `{source}` | Chunk: {chunk_idx} "
                f"| Relevance score: {dist:.4f} (lower = better)\n\n{doc}\n"
            )

        if returned == 0:
            return (
                f"No results met the quality threshold (max_distance={max_distance}). "
                f"Try increasing max_distance or re-phrasing the query."
            )

        return "\n---\n".join(formatted)

    except Exception as e:
        return (
            f"Error searching vector DB: {str(e)}. Ensure documents are indexed first."
        )


@mcp.tool()
def list_indexed_collections() -> str:
    """
    Discover all available indexed collections and their document counts before indexing or searching.

    **TRIGGER CONDITION:** Use this when starting a new session to understand what documents
    are already indexed, OR after `index_document_for_search` to confirm successful ingestion.

    **SEQUENCE GUIDANCE:** Call BEFORE any search operation to verify target collection exists
    and contains expected documents. Also call AFTER re-indexing to confirm updates were applied.

    **CONSTRAINT WARNING:** Empty collections indicate no indexing has occurred—call
    `index_document_for_search` first. Large collections (>10,000 chunks) may return truncated lists;
    use `source_filter` in searches to narrow focus.

    **OUTPUT EXPECTATION:** Returns collection names with chunk counts. Ideal for session initialization,
    verifying indexing state, and planning search queries against the correct target collection.
    """
    try:
        collections = chroma_client.list_collections()
        if not collections:
            return (
                "No collections found. Use 'index_document_for_search' to create one."
            )

        lines = ["### Indexed collections\n"]
        for col in collections:
            c = chroma_client.get_collection(col.name)
            count = c.count()
            lines.append(f"- **{col.name}**: {count} chunks indexed")

        return "\n".join(lines)

    except Exception as e:
        return f"Error listing collections: {str(e)}"


@mcp.tool()
def list_indexed_files(collection_name: str = "default") -> str:
    """
    List all source files indexed within a specific collection, with per-file chunk counts.

    **TRIGGER CONDITION:** Use this after `list_indexed_collections` to drill into a collection
    and see exactly which files it contains—before searching, deleting, or re-indexing.

    **SEQUENCE GUIDANCE:** Call `list_indexed_collections` first to confirm the collection exists,
    then `list_indexed_files` to see its contents. Use the returned paths as `source_filter`
    values in `semantic_search` to narrow results to a specific document.

    **OUTPUT EXPECTATION:** Returns file paths with chunk counts per file, sorted alphabetically.
    """
    try:
        collection = chroma_client.get_collection(name=collection_name)
        results = collection.get(include=["metadatas"])
        metadatas = results.get("metadatas") or []

        if not metadatas:
            return f"Collection '{collection_name}' exists but contains no indexed documents."

        from collections import Counter
        counts: Counter = Counter(m.get("source", "unknown") for m in metadatas)

        lines = [f"### Files in collection '{collection_name}'\n"]
        for path, count in sorted(counts.items()):
            lines.append(f"- `{path}` — {count} chunk{'s' if count != 1 else ''}")

        return "\n".join(lines)

    except Exception as e:
        return f"Error listing indexed files: {str(e)}"


@mcp.tool()
def delete_from_index(
    collection_name: str = "default",
    file_path: str = "",
    delete_collection: bool = False,
) -> str:
    """
    Remove indexed content from the Vector DB to clean up stale documents before re-indexing.

    **TRIGGER CONDITION:** Use this when a document has been MODIFIED and needs re-indexing,
    OR when cleaning up unused collections between sessions.

    **SEQUENCE GUIDANCE:** Call BEFORE `index_document_for_search` on modified files to avoid duplicate chunks.
    Set `delete_collection=True` ONLY when completely removing a collection (use with caution).

    **CONSTRAINT WARNING:** Avoid calling without a specific file_path unless you intend
    full collection deletion—this is irreversible for that collection's data. Always verify
    via `list_indexed_collections` before deletion to confirm target document exists.

    **OUTPUT EXPECTation:** Returns confirmation of deleted chunk count and source filename.
    Ideal for maintaining index hygiene and ensuring fresh indexing on updated documents.

    *Warning:* Deletion is permanent—ensure you've backed up any critical data externally first.
    """
    try:
        if delete_collection:
            chroma_client.delete_collection(name=collection_name)
            return f"Collection '{collection_name}' deleted successfully."

        if not file_path:
            return "Error: Provide a 'file_path' to delete specific chunks, or set delete_collection=True."

        collection = chroma_client.get_collection(name=collection_name)
        existing = collection.get(where={"source": file_path})

        if not existing["ids"]:
            return f"No indexed chunks found for '{file_path}' in '{collection_name}'."

        collection.delete(ids=existing["ids"])
        return (
            f"Removed {len(existing['ids'])} chunks for '{os.path.basename(file_path)}' "
            f"from '{collection_name}'."
        )

    except Exception as e:
        return f"Error deleting from index: {str(e)}"


@mcp.tool()
def chunk_and_preview(
    file_path: str,
    chunk_target: int = 800,
    overlap: int = 100,
    preview_n: int = 3,
) -> str:
    """
    Preview document chunking behavior WITHOUT writing to the Vector DB—ideal for parameter tuning.

    **TRIGGER CONDITION:** Use this BEFORE `index_document_for_search` when dealing with
    large or unusual documents to optimize chunk parameters. Also useful for debugging
    unexpected search results (chunk size may be too coarse/fine).

    **SEQUENCE GUIDANCE:** Call after confirming document content exists via `read_doc_content`.
    Review the preview output, then adjust `chunk_target` and `overlap` based on observed patterns:
    - Too many small chunks? Increase chunk_target.
    - Context loss between chunks? Increase overlap.

    **CONSTRAINT WARNING:** Avoid calling this repeatedly in production workflows—each call
    re-reads the full document which is expensive. Use once for parameter tuning, then
    cache optimal settings. Not needed for standard documents (800-char default works well).

    **OUTPUT EXPECTATION:** Returns total chunk count, size distribution statistics, and sample chunks.
    Ideal for understanding how your document will be segmented before committing to indexing.

    *Recommended workflow:* get_doc_metadata → read_doc_content → chunk_and_preview → index_document_for_search
    """
    text = read_doc_content(file_path, max_chars=1_000_000)
    if not text.strip() or text.startswith("Error"):
        return text

    chunks = _chunk_text(text, chunk_target, overlap)

    sizes = [len(c) for c in chunks]
    avg_size = sum(sizes) / len(sizes) if sizes else 0

    lines = [
        f"### Chunk preview for: {os.path.basename(file_path)}",
        f"- Total chunks: **{len(chunks)}**",
        f"- Avg chunk size: **{avg_size:.0f} chars**",
        f"- Min / Max: **{min(sizes)} / {max(sizes)} chars**",
        f"- Settings: chunk_target={chunk_target}, overlap={overlap}",
        "",
    ]

    for i, chunk in enumerate(chunks[:preview_n]):
        lines.append(
            f"**Chunk {i + 1}** ({len(chunk)} chars):\n> {chunk[:300].replace(chr(10), ' ')}...\n"
        )

    return "\n".join(lines)


@mcp.tool()
def compare_documents(
    file_path_a: str,
    file_path_b: str,
    query: str = "",
    max_chars: int = 4000,
) -> str:
    """
    Perform high-level semantic comparison between two documents to assess similarity or find differences.

    **TRIGGER CONDITION:** Use this for COMPARATIVE ANALYSIS tasks like version diffing,
    contract review, or finding related content across files. Ideal when you need a quick
    similarity score rather than line-by-line diffs.

    **SEQUENCE GUIDANCE:** Provide both file paths and optionally a `query` parameter:
    - Without query: Returns overall document similarity (cosine 0-1).
    - With query: Extracts most relevant passage from each doc for focused comparison.
    For granular, line-by-line differences, use standard text reading tools instead.

    **CONSTRAINT WARNING:** Avoid using this tool when exact string matching is required
    (e.g., legal clause verification, code diffing). Semantic similarity can miss minor but critical changes.
    Also avoid on files with vastly different sizes (>10x difference) as comparison may be biased.

    **OUTPUT EXPECTATION:** Returns cosine similarity score with interpretation (very similar/highly related/moderately
    unrelated), or side-by-side passage comparisons when query is provided. Ideal for triaging documents,
    identifying near-duplicates, or finding related content across a corpus.

    *Use case examples:* "Are these two policy drafts substantially different?" → call without query.
    "What does each document say about 'data retention'?" → call with query="data retention".
    """
    def cosine_similarity(a, b):
        a, b = np.array(a), np.array(b)
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-10))

    text_a = read_doc_content(file_path_a, max_chars=max_chars)
    text_b = read_doc_content(file_path_b, max_chars=max_chars)

    if text_a.startswith("Error"):
        return f"Error reading file A: {text_a}"
    if text_b.startswith("Error"):
        return f"Error reading file B: {text_b}"

    model = get_embed_model()
    name_a = os.path.basename(file_path_a)
    name_b = os.path.basename(file_path_b)

    if not query:
        # Overall document-level similarity
        emb_a = model.encode([text_a[:3000]])[0]
        emb_b = model.encode([text_b[:3000]])[0]
        sim = cosine_similarity(emb_a, emb_b)

        interpretation = (
            "very similar (likely near-duplicates or same document revision)"
            if sim > 0.92
            else (
                "highly related (same topic, different content)"
                if sim > 0.75
                else "moderately related" if sim > 0.5 else "mostly unrelated"
            )
        )

        return (
            f"### Document comparison\n"
            f"- **{name_a}** vs **{name_b}**\n"
            f"- Cosine similarity: **{sim:.4f}** — {interpretation}\n\n"
            f"Tip: provide a `query` parameter to extract and compare specific passages."
        )

    # Query-focused comparison: find best passage in each doc
    def top_passage(text, q_emb, chunk_size=600):
        sentences = re.split(r"(?<=[.!?])\s+", text)
        chunks, cur = [], []
        for s in sentences:
            cur.append(s)
            if sum(len(x) for x in cur) >= chunk_size:
                chunks.append(" ".join(cur))
                cur = []
        if cur:
            chunks.append(" ".join(cur))

        if not chunks:
            return text[:chunk_size]

        embs = model.encode(chunks)
        sims = [cosine_similarity(q_emb, e) for e in embs]
        best_idx = max(range(len(sims)), key=lambda i: sims[i])
        return chunks[best_idx], sims[best_idx]

    q_emb = model.encode([query])[0]
    passage_a, score_a = top_passage(text_a, q_emb)
    passage_b, score_b = top_passage(text_b, q_emb)

    return (
        f'### Passage comparison for query: "{query}"\n\n'
        f"**{name_a}** (relevance: {score_a:.4f}):\n> {passage_a[:600]}\n\n"
        f"---\n\n"
        f"**{name_b}** (relevance: {score_b:.4f}):\n> {passage_b[:600]}"
    )


# --- Tool 5: Standalone Local Image Loader ---
@mcp.tool()
def load_local_image(file_path: str):
    """
    Load and preprocess standalone image files for vision model analysis.

    **TRIGGER CONDITION:** Use this when analyzing INDIVIDUAL IMAGE FILES (PNG, JPG, JPEG)
    rather than images embedded within PDFs. Essential when your task requires visual understanding
    of diagrams, charts, screenshots, or photographs.

    **SEQUENCE GUIDANCE:** For images within PDFs, use `list_document_images` → `extract_image_for_vision` instead.
    This tool is for standalone image files only. After loading, the processed image injects directly
    into your vision model's context window.

    **CONSTRAINT WARNING:** Only supports PNG/JPG/JPEG formats—other formats will fail. Large images (>2000x2000)
    are automatically downsampled to 1024x1024 for context window protection (this is intentional and cannot be disabled).
    RGBA/transparency issues are handled automatically by converting to RGB with white background.

    **OUTPUT EXPECTATION:** Returns FastMCP Image object optimized for vision model consumption. Ideal for
    analysis tasks requiring visual input, such as chart interpretation, diagram understanding, or screenshot review.

    *Note:* The tool handles color normalization and resizing internally—no manual preprocessing required.
    """
    if not os.path.isfile(file_path):
        return f"Error: File '{file_path}' does not exist."

    try:
        # 1. Load the image from the local path using Pillow
        pil_img = PILImage.open(file_path)

        # 2. Normalize Color Space (Crucial for Vision Models)
        # Vision models can hallucinate on RGBA (transparency). We force standard RGB.
        if pil_img.mode not in ("RGB", "L"):
            if pil_img.mode in ("RGBA", "LA") or (
                pil_img.mode == "P" and "transparency" in pil_img.info
            ):
                background = PILImage.new("RGB", pil_img.size, (255, 255, 255))
                if pil_img.mode == "RGBA":
                    background.paste(pil_img, mask=pil_img.split()[3])
                else:
                    background.paste(pil_img)
                pil_img = background
            else:
                pil_img = pil_img.convert("RGB")

        # 3. Downscale Large Images (Context Window Protection)
        # 1024x1024 is the industry-standard safe maximum for modern vision models.
        max_dimension = 1024
        if pil_img.width > max_dimension or pil_img.height > max_dimension:
            pil_img.thumbnail(
                (max_dimension, max_dimension), PILImage.Resampling.LANCZOS
            )

        # 4. Export to standardized PNG bytes
        output_buffer = io.BytesIO()
        pil_img.save(output_buffer, format="PNG", optimize=True)
        standardized_bytes = output_buffer.getvalue()

        # 5. Return native FastMCP Image object
        # Note: No return type hint is used in the function signature to avoid Pydantic serialization errors.
        return Image(data=standardized_bytes, format="png")

    except Exception as e:
        return f"Error loading and processing local image: {str(e)}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
