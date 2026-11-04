import fitz # PyMuPDF
import re

def extract_placeholders(pdf_bytes: bytes) -> list:
    '''Extracts {{PLACEHOLDER}} tokens from a PDF template.'''
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    placeholders = {}
    pattern = re.compile(r"\{\{([A-Z0-9_]+)\}\}")
    
    for page in doc:
        text = page.get_text()
        for match in pattern.findall(text):
            placeholders[match] = placeholders.get(match, 0) + 1
            
    return [{"key": k, "occurrences": v} for k, v in placeholders.items()]

def generate_certificate(template_bytes: bytes, record_data: dict, field_map: dict) -> bytes:
    '''Fills placeholders in PDF with record data.'''
    doc = fitz.open(stream=template_bytes, filetype="pdf")
    
    for page in doc:
        for placeholder, col_name in field_map.items():
            value = str(record_data.get(col_name, ""))
            token = f"{{{{{placeholder}}}}}"
            
            # Find and redact/replace tokens
            text_instances = page.search_for(token)
            for inst in text_instances:
                page.add_redact_annot(inst, text="")
                page.apply_redactions()
                # Insert the new value at the bottom-left of the bounding box
                page.insert_text(inst.bl, value, fontsize=12, color=(0,0,0))
                
    return doc.write()
