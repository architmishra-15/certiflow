import html
import re
import pymupdf

from fastapi import BackgroundTasks, Header
from ..models import JobRequest
from ..services import email_service, pdf_service
from .templates import TEMPLATE_FILES, TEMPLATES_DB

from fastapi import Response
from ..services import pdf_service
from .templates import TEMPLATE_FILES, TEMPLATES_DB

import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import (
    APIRouter,
    UploadFile,
    File,
    Form,
    HTTPException,
    Query,
)

from ..models import BatchConfig
from ..services import polars_service

router = APIRouter(prefix="/api/v1/batches", tags=["batches"])

# In-memory storage
BATCHES_DB = {}
BATCH_DATA_DB = {}
BATCH_ROWS_DB = {}
CONFIG_DB = {}
RECORDS_DB = {}

JOBS_DB = {}
JOB_BY_BATCH = {}
IDEMPOTENCY_DB = {}
GENERATED_PDFS = {}

jobs_router = APIRouter(
    prefix="/api/v1/jobs",
    tags=["jobs"],
)

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")
PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")

LOCKED_STATUSES = {
    "sent", "sending", "generating", "generated", "failed"
}


def as_text(value):
    return "" if value is None else str(value).strip()


def resolve_value(row, config, key):
    column = config.get("field_map", {}).get(key)

    if column:
        return as_text(row.get(column))

    return as_text(config.get("constants", {}).get(key))


def build_records(batch_id):
    config = CONFIG_DB[batch_id]
    rows = BATCH_ROWS_DB[batch_id]

    previous = {
        r["row_number"]: r
        for r in RECORDS_DB.get(batch_id, [])
    }

    email_cfg = config.get("email") or {}

    primary_col = email_cfg.get("primary_column") or ""
    fallback_col = email_cfg.get("fallback_column") or ""

    required = config.get("required_columns") or []
    dedupe = config.get("dedupe_columns") or []

    filename_pattern = (
        config.get("filename_pattern") or "{{NAME}}.pdf"
    )

    seen = {}
    records = []

    for index, source_row in enumerate(rows, start=1):

        fields = {
            key: as_text(value)
            for key, value in source_row.items()
        }

        old = previous.get(index, {})

        name = resolve_value(fields, config, "NAME") or None
        student_id = resolve_value(fields, config, "ID") or None

        primary_email = fields.get(primary_col, "")
        fallback_email = fields.get(fallback_col, "") if fallback_col else ""

        recipient_email = next(
            (
                email
                for email in (primary_email, fallback_email)
                if email and EMAIL_RE.match(email)
            ),
            None,
        )

        issues = []

        def add_issue(issue_type, column, message):
            issues.append({
                "record_id": old.get("id"),
                "row_number": index,
                "type": issue_type,
                "column": column,
                "message": message,
            })

        # Check name
        if not name:
            add_issue(
                "missing_name",
                config.get("field_map", {}).get("NAME"),
                "Name is empty",
            )

        # Check required columns
        name_column = config.get("field_map", {}).get("NAME")

        for column in required:
            if (
                column in fields
                and not fields[column]
                and column not in {name_column, primary_col}
            ):
                add_issue(
                    "missing_required_field",
                    column,
                    f"{column} is empty",
                )

        # Check email
        if not recipient_email:
            raw_email = primary_email or fallback_email

            add_issue(
                "invalid_email" if raw_email else "missing_email",
                primary_col or None,
                (
                    "Not a valid email address"
                    if raw_email
                    else "Email is empty"
                ),
            )

        # Check duplicates
        duplicate = False

        if dedupe:
            dedupe_key = "|".join(
                fields.get(column, "")
                for column in dedupe
            ).lower()

            if dedupe_key in seen:
                duplicate = True

                add_issue(
                    "duplicate_record",
                    None,
                    f"Duplicate of row {seen[dedupe_key]}",
                )
            else:
                seen[dedupe_key] = index

        # Determine status
        old_status = old.get("status")

        if old_status in LOCKED_STATUSES:
            status = old_status
        elif duplicate:
            status = "skipped_duplicate"
        elif issues:
            status = "invalid"
        else:
            status = "pending"

        # Generate filename
        def replace_placeholder(match):
            return resolve_value(
                fields,
                config,
                match.group(1),
            )

        filename = PLACEHOLDER_RE.sub(
            replace_placeholder,
            filename_pattern,
        )

        filename = re.sub(
            r"[^\w.\- ]+",
            "_",
            filename,
        ).strip() or f"record_{index}.pdf"

        record_id = old.get("id") or str(uuid.uuid4())

        for issue in issues:
            issue["record_id"] = record_id

        records.append({
            "id": record_id,
            "row_number": index,
            "fields": fields,
            "name": name,
            "student_id": student_id,
            "recipient_email": recipient_email,
            "certificate_filename": filename if not issues else None,
            "status": status,
            "attempts": old.get("attempts", 0),
            "issues": issues,
            "sent_at": old.get("sent_at"),
            "error": old.get("error"),
        })

    RECORDS_DB[batch_id] = records

    return records


def make_report(records):
    issues_by_type = {}
    all_issues = []

    for record in records:
        for issue in record["issues"]:
            key = issue["type"]

            issues_by_type[key] = (
                issues_by_type.get(key, 0) + 1
            )

            all_issues.append(issue)

    valid = sum(
        r["status"] == "pending" for r in records
    )

    invalid = sum(
        r["status"] == "invalid" for r in records
    )

    duplicates = sum(
        r["status"] == "skipped_duplicate" for r in records
    )

    return {
        "total": len(records),
        "valid": valid,
        "invalid": invalid,
        "duplicates": duplicates,
        "expected_count": valid,
        "issues_by_type": issues_by_type,
        "sample_issues": all_issues[:8],
    }


# --------------------------------------------------
# CREATE BATCH
# --------------------------------------------------

@router.post("")
async def create_batch(
    file: UploadFile = File(...),
    name: str = Form(...),
    sheet: Optional[str] = Form(None),
):
    content = await file.read()
    filename = file.filename or ""

    try:
        columns, row_count, df = polars_service.process_file(
            content,
            filename,
            sheet,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=415,
            detail=str(exc),
        ) from exc
    finally:
        await file.close()

    batch_id = str(uuid.uuid4())

    BATCH_DATA_DB[batch_id] = df
    BATCH_ROWS_DB[batch_id] = df.to_dicts()

    batch = {
        "id": batch_id,
        "name": name,
        "status": "uploaded",
        "row_count": row_count,
        "columns": columns,
        "has_config": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    BATCHES_DB[batch_id] = batch

    return batch


# --------------------------------------------------
# SAVE CONFIGURATION
# --------------------------------------------------

@router.put("/{batch_id}/config")
async def save_config(batch_id: str, config: BatchConfig):

    if batch_id not in BATCHES_DB:
        raise HTTPException(
            status_code=404,
            detail="Batch not found",
        )

    CONFIG_DB[batch_id] = config.model_dump()

    BATCHES_DB[batch_id]["has_config"] = True
    BATCHES_DB[batch_id]["status"] = "configured"

    return config


# --------------------------------------------------
# VALIDATE BATCH
# --------------------------------------------------

@router.post("/{batch_id}/validate")
async def validate_batch_data(batch_id: str):

    if batch_id not in BATCHES_DB:
        raise HTTPException(
            status_code=404,
            detail="Batch not found",
        )

    if not BATCHES_DB[batch_id]["has_config"]:
        raise HTTPException(
            status_code=409,
            detail="Config incomplete",
        )

    records = build_records(batch_id)

    report = make_report(records)

    BATCHES_DB[batch_id]["status"] = "validated"

    return report


# --------------------------------------------------
# GET RECORDS  <-- MISSING ENDPOINT
# --------------------------------------------------

@router.get("/{batch_id}/records")
async def list_batch_records(
    batch_id: str,
    status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    q: Optional[str] = Query(None),
):

    if batch_id not in BATCHES_DB:
        raise HTTPException(
            status_code=404,
            detail="Batch not found",
        )

    if batch_id not in RECORDS_DB:
        raise HTTPException(
            status_code=409,
            detail="Validate the batch first",
        )

    records = RECORDS_DB[batch_id]

    # Filter by status
    if status:
        statuses = {
            value.strip()
            for value in status.split(",")
            if value.strip()
        }

        records = [
            r for r in records
            if r["status"] in statuses
        ]

    # Search
    if q:
        query = q.casefold()

        records = [
            r for r in records
            if query in " ".join([
                r.get("name") or "",
                r.get("student_id") or "",
                r.get("recipient_email") or "",
            ]).casefold()
        ]

    total = len(records)

    start = (page - 1) * page_size

    return {
        "items": records[start:start + page_size],
        "page": page,
        "page_size": page_size,
        "total": total,
    }


# --------------------------------------------------
# UPDATE / FIX INDIVIDUAL RECORD
# --------------------------------------------------

@router.patch("/{batch_id}/records/{record_id}")
async def update_batch_record(
    batch_id: str,
    record_id: str,
    payload: dict,
):

    if batch_id not in BATCHES_DB:
        raise HTTPException(
            status_code=404,
            detail="Batch not found",
        )

    if batch_id not in RECORDS_DB:
        raise HTTPException(
            status_code=409,
            detail="Validate the batch first",
        )

    record = next(
        (
            r for r in RECORDS_DB[batch_id]
            if r["id"] == record_id
        ),
        None,
    )

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="Record not found",
        )

    fields = payload.get("fields")

    if not isinstance(fields, dict):
        raise HTTPException(
            status_code=422,
            detail="Expected a fields object",
        )

    source_row = BATCH_ROWS_DB[batch_id][
        record["row_number"] - 1
    ]

    unknown = set(fields) - set(source_row)

    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown columns: {', '.join(sorted(unknown))}",
        )

    source_row.update(fields)

    records = build_records(batch_id)

    return next(
        r for r in records
        if r["id"] == record_id
    )


def _preview_value(record: dict, config: dict, key: str) -> str:
    """Resolve a placeholder using the field mapping or a fixed constant."""
    column = (config.get("field_map") or {}).get(key)

    if column:
        return str(record.get("fields", {}).get(column, "") or "")

    return str((config.get("constants") or {}).get(key, "") or "")


def _fill_preview_text(template: str, record: dict, config: dict) -> str:
    """Replace {{PLACEHOLDER}} tokens with HTML-escaped record values."""
    return re.sub(
        r"\{\{([A-Za-z0-9_]+)\}\}",
        lambda match: html.escape(
            _preview_value(record, config, match.group(1))
        ),
        template or "",
    )


def _get_preview_record(batch_id: str, record_id: str):
    if batch_id not in BATCHES_DB:
        raise HTTPException(status_code=404, detail="Batch not found")

    if batch_id not in CONFIG_DB:
        raise HTTPException(status_code=409, detail="Batch configuration missing")

    records = RECORDS_DB.get(batch_id)

    if records is None:
        raise HTTPException(status_code=409, detail="Validate the batch first")

    record = next(
        (r for r in records if r["id"] == record_id),
        None,
    )

    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")

    return record, CONFIG_DB[batch_id]


# --------------------------------------------------
# EMAIL PREVIEW
# --------------------------------------------------

@router.get("/{batch_id}/preview/email")
async def preview_email(batch_id: str, record_id: str):

    record, config = _get_preview_record(batch_id, record_id)

    email_config = config.get("email") or {}

    subject = re.sub(
        r"\{\{([A-Za-z0-9_]+)\}\}",
        lambda match: _preview_value(
            record, config, match.group(1)
        ),
        email_config.get("subject", ""),
    )

    body_html = _fill_preview_text(
        email_config.get("body_html", ""),
        record,
        config,
    )

    return {
        "record_id": record["id"],
        "to": record.get("recipient_email"),
        "subject": subject,
        "body_html": body_html,
        "attachment_filename": record.get("certificate_filename"),
    }


# --------------------------------------------------
# CERTIFICATE PREVIEW
# --------------------------------------------------

@router.get("/{batch_id}/preview/certificate")
async def preview_certificate(batch_id: str, record_id: str):

    record, config = _get_preview_record(batch_id, record_id)

    template_id = config.get("template_id")

    template_bytes = TEMPLATE_FILES.get(template_id)

    if template_bytes is None:
        raise HTTPException(
            status_code=404,
            detail="Template PDF not found. Please upload it again.",
        )

    template_info = TEMPLATES_DB.get(template_id, {})

    # Get every placeholder, including constants and mapped fields.
    keys = {
        item["key"]
        for item in template_info.get("placeholders", [])
    }

    keys.update((config.get("field_map") or {}).keys())
    keys.update((config.get("constants") or {}).keys())

    # Resolve all placeholders before generating the certificate.
    values = {
        key: _preview_value(record, config, key)
        for key in keys
    }

    # The PDF service expects placeholder -> record-column mappings.
    identity_map = {
        key: key
        for key in keys
    }

    try:
        certificate_bytes = pdf_service.generate_certificate(
            template_bytes,
            values,
            identity_map,
        )

        # Render the generated PDF's first page as PNG.
        with pymupdf.open(
            stream=certificate_bytes,
            filetype="pdf",
        ) as document:

            if document.page_count == 0:
                raise ValueError("Generated PDF contains no pages")

            page = document.load_page(0)

            pixmap = page.get_pixmap(
                matrix=pymupdf.Matrix(1.5, 1.5),
                alpha=False,
            )

            png_bytes = pixmap.tobytes("png")

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Certificate preview generation failed: {exc}",
        ) from exc

    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/{batch_id}/stats")
@router.get("/{batch_id}/stats")
async def get_batch_stats(batch_id: str):

    if batch_id not in BATCHES_DB:
        raise HTTPException(404, "Batch not found")

    records = RECORDS_DB.get(batch_id, [])

    statuses = [
        "pending",
        "invalid",
        "generating",
        "generated",
        "sending",
        "sent",
        "failed",
        "skipped_duplicate",
    ]

    counts = {status: 0 for status in statuses}

    for record in records:
        status = record.get("status")
        if status in counts:
            counts[status] += 1

    job_id = JOB_BY_BATCH.get(batch_id)
    job = JOBS_DB.get(job_id) if job_id else None

    return {
        "total": len(records),
        "counts": counts,
        "job": job,
    }

import html


def _job_value(record, config, key):
    column = (config.get("field_map") or {}).get(key)

    if column:
        return str(record.get("fields", {}).get(column) or "")

    return str((config.get("constants") or {}).get(key) or "")


def _job_pdf(batch_id, record):
    config = CONFIG_DB[batch_id]
    template_id = config["template_id"]

    template = TEMPLATE_FILES.get(template_id)
    if template is None:
        raise ValueError("Template PDF not found.")

    template_info = TEMPLATES_DB.get(template_id, {})

    keys = {
        item["key"]
        for item in template_info.get("placeholders", [])
    }

    keys.update((config.get("field_map") or {}).keys())
    keys.update((config.get("constants") or {}).keys())

    values = {
        key: _job_value(record, config, key)
        for key in keys
    }

    identity_map = {key: key for key in keys}

    return pdf_service.generate_certificate(
        template,
        values,
        identity_map,
    )


def _job_email_text(template, record, config):
    def replace(match):
        key = match.group(1)
        return html.escape(_job_value(record, config, key))

    return re.sub(
        r"\{\{([A-Za-z0-9_]+)\}\}",
        replace,
        template or "",
    )


async def _execute_job(job_id, batch_id, record_ids):
    job = JOBS_DB[job_id]
    config = CONFIG_DB[batch_id]
    records = {
        record["id"]: record
        for record in RECORDS_DB[batch_id]
    }

    email_config = config.get("email") or {}

    job["state"] = "running"

    for record_id in record_ids:

        if job["cancel_requested"]:
            break

        record = records.get(record_id)
        if record is None:
            continue

        try:
            # Generate certificate
            record["status"] = "generating"

            pdf_bytes = _job_pdf(batch_id, record)

            GENERATED_PDFS[(batch_id, record_id)] = pdf_bytes

            if job["action"] == "generate":
                record["status"] = "generated"
                record["error"] = None

            else:
                # Prepare email
                recipient = record.get("recipient_email")

                if not recipient:
                    raise ValueError("Recipient email is missing.")

                subject = _job_email_text(
                    email_config.get("subject", ""),
                    record,
                    config,
                )

                body_html = _job_email_text(
                    email_config.get("body_html", ""),
                    record,
                    config,
                )

                filename = (
                    record.get("certificate_filename")
                    or f"certificate_{record['row_number']}.pdf"
                )

                record["status"] = "sending"
                record["attempts"] = record.get("attempts", 0) + 1

                await email_service.send_certificate_email(
                    to_email=recipient,
                    subject=subject,
                    html_body=body_html,
                    pdf_bytes=pdf_bytes,
                    filename=filename,
                )

                record["status"] = "sent"
                record["sent_at"] = datetime.now(
                    timezone.utc
                ).isoformat()

                record["error"] = None

        except Exception as exc:
            record["status"] = "failed"

            record["error"] = {
                "code": "job_failed",
                "message": str(exc),
            }

            job["failed"] += 1

        job["done"] += 1

    if job["cancel_requested"]:
        job["state"] = "cancelled"
    elif job["failed"]:
        job["state"] = "completed_with_errors"
    else:
        job["state"] = "completed"

    job["finished_at"] = datetime.now(
        timezone.utc
    ).isoformat()


# --------------------------------------------------
# START JOB
# --------------------------------------------------

@router.post("/{batch_id}/jobs", status_code=202)
async def start_batch_job(
    batch_id: str,
    payload: JobRequest,
    background_tasks: BackgroundTasks,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
    ),
):

    if batch_id not in BATCHES_DB:
        raise HTTPException(404, "Batch not found")

    if batch_id not in CONFIG_DB or batch_id not in RECORDS_DB:
        raise HTTPException(
            409,
            "Configure and validate the batch first.",
        )

    if payload.action not in {
        "generate",
        "send",
        "generate_and_send",
    }:
        raise HTTPException(422, "Invalid job action")

    if payload.scope not in {"all_valid", "failed"}:
        raise HTTPException(422, "Invalid job scope")

    # Idempotency protection
    if idempotency_key:
        previous_id = IDEMPOTENCY_DB.get(
            (batch_id, idempotency_key)
        )

        if previous_id:
            return JOBS_DB[previous_id]

    # Prevent simultaneous jobs on the same batch
    previous_job_id = JOB_BY_BATCH.get(batch_id)

    if previous_job_id:
        previous_job = JOBS_DB.get(previous_job_id)

        if previous_job and previous_job["state"] in {
            "queued", "running", "cancelling"
        }:
            raise HTTPException(
                409,
                detail="A job is already running for this batch.",
            )

    all_records = RECORDS_DB[batch_id]

    if payload.scope == "failed":
        eligible = [
            r for r in all_records
            if r["status"] == "failed"
        ]

    elif payload.action == "generate":
        eligible = [
            r for r in all_records
            if r["status"] == "pending"
        ]

    else:
        eligible = [
            r for r in all_records
            if r["status"] in {"pending", "generated"}
        ]

    if payload.record_ids is not None:
        requested = set(payload.record_ids)
        available = {r["id"] for r in eligible}

        unknown = requested - available

        if unknown:
            raise HTTPException(
                422,
                detail="Some requested records are not eligible.",
            )

        eligible = [
            r for r in eligible
            if r["id"] in requested
        ]

    # Verify the confirmation count before sending
    if payload.action != "generate":
        if payload.confirm_count != len(eligible):
            raise HTTPException(
                409,
                detail={
                    "code": "confirm_mismatch",
                    "message": (
                        f"Expected {len(eligible)} records, "
                        f"but received confirmation for "
                        f"{payload.confirm_count}."
                    ),
                },
            )

    job_id = str(uuid.uuid4())

    job = {
        "id": job_id,
        "batch_id": batch_id,
        "action": payload.action,
        "scope": payload.scope,
        "state": "queued",
        "total": len(eligible),
        "done": 0,
        "failed": 0,
        "cancel_requested": False,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
    }

    JOBS_DB[job_id] = job
    JOB_BY_BATCH[batch_id] = job_id

    if idempotency_key:
        IDEMPOTENCY_DB[
            (batch_id, idempotency_key)
        ] = job_id

    background_tasks.add_task(
        _execute_job,
        job_id,
        batch_id,
        [r["id"] for r in eligible],
    )

    return job


# --------------------------------------------------
# CANCEL JOB
# --------------------------------------------------

@jobs_router.post("/{job_id}/cancel")
async def cancel_job(job_id: str):

    job = JOBS_DB.get(job_id)

    if job is None:
        raise HTTPException(404, "Job not found")

    if job["state"] in {"queued", "running"}:
        job["cancel_requested"] = True
        job["state"] = "cancelling"

    return job
