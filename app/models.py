from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime

class Template(BaseModel):
    id: str
    name: str
    kind: str
    placeholders: List[Dict[str, Any]]
    created_at: datetime

class Batch(BaseModel):
    id: str
    name: str
    status: str
    row_count: int
    columns: List[Dict[str, Any]]
    has_config: bool
    created_at: datetime
    
class BatchConfig(BaseModel):
    template_id: str
    field_map: Dict[str, str]
    email: Dict[str, Any]
    dedupe_columns: List[str] = []
    required_columns: List[str] = []
    constants: Dict[str, str] = {}
    filename_pattern: str = "{{NAME}}.pdf"

class JobRequest(BaseModel):
    action: str
    scope: str
    record_ids: Optional[List[str]] = None
    force_resend: bool = False
    confirm_count: Optional[int] = None
