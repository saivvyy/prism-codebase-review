from typing import List, Optional
from pydantic import BaseModel, Field


class CodeRange(BaseModel):
    start_line: int
    end_line: int
    start_column: int = 0
    end_column: int = 0


class ImportStatement(BaseModel):
    source: str = Field(..., description="Module, library, or file being imported")
    imported_symbols: List[str] = Field(default_factory=list, description="Specific named imports")
    line_number: int


class FunctionDefinition(BaseModel):
    name: str
    class_name: Optional[str] = None
    parameters: List[str] = Field(default_factory=list)
    start_line: int
    end_line: int
    is_async: bool = False


class ClassDefinition(BaseModel):
    name: str
    superclasses: List[str] = Field(default_factory=list)
    methods: List[str] = Field(default_factory=list)
    start_line: int
    end_line: int


class FunctionCall(BaseModel):
    target: str = Field(..., description="Invoked function or method expression, e.g. 'db.query' or 'fetch'")
    enclosing_function: Optional[str] = None
    enclosing_class: Optional[str] = None
    line_number: int


class ChangedRegion(BaseModel):
    start_line: int
    end_line: int
    added_lines: List[str] = Field(default_factory=list)
    deleted_lines: List[str] = Field(default_factory=list)
    enclosing_function: Optional[str] = None
    enclosing_class: Optional[str] = None


class FileAnalysisResult(BaseModel):
    file_path: str
    language: str
    is_supported: bool
    parse_success: bool
    error_message: Optional[str] = None
    classes: List[ClassDefinition] = Field(default_factory=list)
    functions: List[FunctionDefinition] = Field(default_factory=list)
    imports: List[ImportStatement] = Field(default_factory=list)
    calls: List[FunctionCall] = Field(default_factory=list)
    changed_regions: List[ChangedRegion] = Field(default_factory=list)
    summary: str = Field(default="", description="Quick structural overview of the file")
