"""
PDF and image processing tools for the Deep Agent.

- PDF: extracts text + tables via pdfplumber
- Images: analyzes via vision model (Gemini/Claude) for ticket/receipt OCR
- Uploaded files stored in ARTIFACTS_DIR/{task_id}/uploads/
"""
from __future__ import annotations

import base64
import json
import os
from typing import Any

from langfuse import observe

ARTIFACTS_DIR = os.path.join(os.environ.get("TEMP", "/tmp"), "cfo_artifacts")
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB per file
MAX_PDF_PAGES = 50


def save_upload(task_id: str, filename: str, content: bytes) -> str:
    """Save an uploaded file and return its path."""
    if len(content) > MAX_FILE_SIZE:
        raise ValueError(f"File too large: {len(content)} bytes (max {MAX_FILE_SIZE})")
    upload_dir = os.path.join(ARTIFACTS_DIR, task_id, "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    path = os.path.join(upload_dir, filename)
    with open(path, "wb") as f:
        f.write(content)
    return path


@observe(name="extract_pdf")
def extract_pdf(file_path: str, max_pages: int = MAX_PDF_PAGES) -> dict:
    """
    Extract text and tables from a PDF file.

    Returns:
    {
        "pages": int,
        "text": str,           # full text
        "tables": list[list],  # list of tables (each table = list of rows)
        "page_texts": list[str],  # text per page
    }
    """
    try:
        import pdfplumber
    except ImportError:
        return {"error": "pdfplumber not installed", "pages": 0, "text": "", "tables": []}

    result = {"pages": 0, "text": "", "tables": [], "page_texts": []}

    try:
        with pdfplumber.open(file_path) as pdf:
            result["pages"] = min(len(pdf.pages), max_pages)
            all_text = []

            for i, page in enumerate(pdf.pages[:max_pages]):
                page_text = page.extract_text() or ""
                all_text.append(page_text)
                result["page_texts"].append(page_text)

                # Extract tables
                tables = page.extract_tables()
                for table in tables:
                    if table:
                        # Clean None values
                        clean_table = [
                            [cell.strip() if cell else "" for cell in row]
                            for row in table if row
                        ]
                        if clean_table:
                            result["tables"].append(clean_table)

            result["text"] = "\n\n".join(all_text)
    except Exception as e:
        result["error"] = str(e)

    return result


@observe(name="analyze_image")
def analyze_image_with_vision(
    file_path: str,
    prompt: str = "Extract all financial data from this image. Include amounts, dates, supplier/client names, and any other relevant information.",
    model_id: str = "gemini-3.0-flash",
) -> dict:
    """
    Analyze an image (receipt, ticket, invoice) using a vision model.

    Returns: {"text": str, "structured_data": dict, "usage": dict}
    """
    from hackathon_backend.services.lambdas.agent.core.config import completion

    # Read and encode image
    with open(file_path, "rb") as f:
        image_data = base64.b64encode(f.read()).decode("utf-8")

    # Detect MIME type
    ext = os.path.splitext(file_path)[1].lower()
    mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".gif": "image/gif", ".webp": "image/webp", ".pdf": "application/pdf"}
    mime_type = mime_map.get(ext, "image/jpeg")

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_data}"}},
            ],
        }
    ]

    try:
        response = completion(model_id=model_id, messages=messages, temperature=0.1)
        text = response.choices[0].message.content or ""
        u = getattr(response, "usage", None)
        usage = {
            "model": model_id,
            "step": "vision_analysis",
            "prompt_tokens": getattr(u, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(u, "completion_tokens", 0) or 0,
            "total_tokens": getattr(u, "total_tokens", 0) or 0,
        }

        # Try to extract structured data if the model returned JSON
        structured = {}
        if "```json" in text:
            try:
                json_start = text.index("```json") + 7
                json_end = text.index("```", json_start)
                structured = json.loads(text[json_start:json_end].strip())
            except (ValueError, json.JSONDecodeError):
                pass

        return {"text": text, "structured_data": structured, "usage": usage}
    except Exception as e:
        return {"text": "", "error": str(e), "structured_data": {}, "usage": {}}


def analyze_document(
    file_path: str,
    model_id: str = "gemini-3.0-flash",
) -> dict:
    """
    Smart document analysis — picks the right method based on file type.

    Returns: {"type": "pdf"|"image", "content": dict, "usage": list[dict]}
    """
    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".pdf":
        pdf_result = extract_pdf(file_path)
        usage = []

        # If PDF has very little text, it's probably scanned — use vision
        if pdf_result.get("text", "").strip() and len(pdf_result["text"]) > 100:
            return {"type": "pdf", "content": pdf_result, "usage": usage}
        else:
            # Scanned PDF — use vision model
            vision_result = analyze_image_with_vision(
                file_path,
                prompt="This is a scanned financial document (PDF). Extract all text, amounts, dates, and structured data.",
                model_id=model_id,
            )
            if vision_result.get("usage"):
                usage.append(vision_result["usage"])
            return {"type": "scanned_pdf", "content": vision_result, "usage": usage}

    elif ext in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        vision_result = analyze_image_with_vision(file_path, model_id=model_id)
        usage = [vision_result["usage"]] if vision_result.get("usage") else []
        return {"type": "image", "content": vision_result, "usage": usage}

    else:
        # Try to read as text
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                text = f.read()
            return {"type": "text", "content": {"text": text}, "usage": []}
        except Exception:
            return {"type": "unknown", "content": {"error": f"Unsupported file type: {ext}"}, "usage": []}
