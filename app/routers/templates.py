from datetime import datetime, timezone
import uuid

import pymupdf
from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    status,
)

from ..services import pdf_service


router = APIRouter(prefix="/api/v1/templates", tags=["templates"])

# In-memory template storage
TEMPLATES_DB: dict[str, dict] = {}
TEMPLATE_FILES: dict[str, bytes] = {}


# GET: List all uploaded templates
@router.get("")
async def list_templates():
    return {"items": list(TEMPLATES_DB.values())}


# POST: Upload a PDF template
@router.post("")
async def upload_template(
    file: UploadFile = File(...),
    name: str | None = Form(default=None),
):
    filename = file.filename or ""

    if not filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=415,
            detail="Only PDF templates are supported.",
        )

    content = await file.read()
    await file.close()

    if not content:
        raise HTTPException(
            status_code=400,
            detail="The uploaded PDF is empty.",
        )

    try:
        placeholders = pdf_service.extract_placeholders(content)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail="Could not read this PDF. Please upload a valid PDF.",
        ) from exc

    template_id = str(uuid.uuid4())

    template = {
        "id": template_id,
        "name": (name or filename).strip() or filename,
        "kind": "pdf",
        "placeholders": placeholders,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    TEMPLATES_DB[template_id] = template
    TEMPLATE_FILES[template_id] = content

    return template


# GET: Generate and return the first-page PDF preview
@router.get("/{template_id}/preview.png")
async def preview_template(template_id: str):
    content = TEMPLATE_FILES.get(template_id)

    if content is None:
        raise HTTPException(
            status_code=404,
            detail="Template PDF not found.",
        )

    try:
        with pymupdf.open(
            stream=content,
            filetype="pdf",
        ) as document:

            if document.page_count == 0:
                raise HTTPException(
                    status_code=400,
                    detail="The PDF has no pages.",
                )

            page = document.load_page(0)

            pixmap = page.get_pixmap(
                matrix=pymupdf.Matrix(1.5, 1.5),
                alpha=False,
            )

            png_bytes = pixmap.tobytes("png")

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail="Could not render the PDF preview.",
        ) from exc

    return Response(
        content=png_bytes,
        media_type="image/png",
    )


# DELETE: Remove an uploaded template
@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(template_id: str):

    if template_id not in TEMPLATES_DB:
        raise HTTPException(
            status_code=404,
            detail="Template not found.",
        )

    TEMPLATES_DB.pop(template_id, None)
    TEMPLATE_FILES.pop(template_id, None)

    return Response(status_code=status.HTTP_204_NO_CONTENT)
