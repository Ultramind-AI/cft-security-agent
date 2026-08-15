from pydantic import BaseModel

class Finding(BaseModel):
    id: str
    source: str
    rule_id: str
    title: str
    description: str
    file: str
    line_start: int | None = None
    line_end: int | None = None
    severity: str | None = None
    service: str | None = None
