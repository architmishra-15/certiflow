import re
from pathlib import Path

import fitz  # PyMuPDF


# Assumes this file is app/services/pdf_service.py.
FONT_PATH = Path(__file__).resolve().parents[2] / "Mythology.ttf"
FONT_NAME = "certificate_font"


def extract_placeholders(pdf_bytes: bytes) -> list:
    """Extracts {{PLACEHOLDER}} tokens from a PDF template."""
    placeholders = {}
    pattern = re.compile(r"\{\{([A-Z0-9_]+)\}\}")

    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for page in doc:
            text = page.get_text()

            for match in pattern.findall(text):
                placeholders[match] = placeholders.get(match, 0) + 1

    return [
        {"key": key, "occurrences": count}
        for key, count in placeholders.items()
    ]


def generate_certificate(
    template_bytes: bytes,
    record_data: dict,
    field_map: dict,
) -> bytes:
    """Fill placeholders, centering NAME across the certificate page."""
    custom_font = fitz.Font(fontfile=str(FONT_PATH))

    with fitz.open(stream=template_bytes, filetype="pdf") as doc:
        for page in doc:
            replacements = []

            # Locate all placeholders before modifying the page.
            for placeholder, col_name in field_map.items():
                raw_value = record_data.get(col_name, "")
                value = "" if raw_value is None else str(raw_value)

                if placeholder == "NAME":
                    # Keep the name on a single line.
                    value = " ".join(value.split())

                token = f"{{{{{placeholder}}}}}"

                for inst in page.search_for(token):
                    replacements.append((placeholder, value, inst))
                    page.add_redact_annot(inst, text="")

            if not replacements:
                continue

            # Remove all placeholders before adding replacement text.
            page.apply_redactions()

            page.insert_font(
                fontname=FONT_NAME,
                fontfile=str(FONT_PATH),
            )

            for placeholder, value, inst in replacements:
                if not value:
                    continue

                font_size = 24
                x = inst.x0

                if placeholder == "NAME":
                    margin = min(40, page.rect.width / 4)
                    available_width = page.rect.width - (2 * margin)

                    # Measure using the actual custom font.
                    text_width = custom_font.text_length(
                        value,
                        fontsize=font_size,
                    )

                    # Shrink long names to fit between the margins.
                    if text_width > available_width:
                        font_size *= available_width / text_width

                        text_width = custom_font.text_length(
                            value,
                            fontsize=font_size,
                        )

                    x = (
                        page.rect.x0
                        + (page.rect.width - text_width) / 2
                    )

                page.insert_text(
                    fitz.Point(x, inst.y1),
                    value,
                    fontname=FONT_NAME,
                    fontsize=font_size,
                    color=(0, 0, 0),
                )

        # All pages and replacements are finished.
        doc.subset_fonts()

        return doc.write(
            garbage=4,
            deflate=True,
            deflate_images=True,
            deflate_fonts=True,
        )

