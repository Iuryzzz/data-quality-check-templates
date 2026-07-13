from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional, Union, Tuple

#TODO: Вынес отдельно, что бы проект нормально выглядел
# Pydantic модели
class ColumnStat(BaseModel):
    name: str
    data_type: str
    null_count: int
    null_percentage: float
    unique_count: int
    duplicate_count: int
    sample_values: Optional[List[Any]] = None


class OutlierInfo(BaseModel):
    column: str
    count: int
    percentage: float
    lower_bound: Optional[float] = None
    upper_bound: Optional[float] = None
    sample_values: List[float] = []


class CorrelationInfo(BaseModel):
    col1: str
    col2: str
    correlation: float
    type: str


class NumericStats(BaseModel):
    count: int
    mean: float
    std: float
    min: float
    q1: float
    median: float
    q3: float
    max: float
    skewness: float
    kurtosis: float


class EDAReport(BaseModel):
    total_rows: int
    total_columns: int
    memory_usage_mb: float
    timestamp: datetime
    total_missing: int
    missing_percentage: float
    columns_with_missing: int
    missing_details: List[ColumnStat]
    duplicate_count: int
    duplicate_percentage: float
    duplicate_columns: List[str]
    total_outliers: int
    outlier_columns: int
    outlier_details: List[OutlierInfo]
    strong_correlations: List[CorrelationInfo]
    perfect_correlations: List[CorrelationInfo]
    numeric_stats: Dict[str, NumericStats]
    categorical_columns: List[str]
    potential_primary_keys: List[str]
    recommendations: List[str]


class CheckResult(BaseModel):
    check_name: str
    check_type: str
    status: str = Field(description="PASSED / WARNING / FAILED")
    message: str
    error_count: int = 0
    error_rows: Optional[List[int]] = None
    details: Optional[Dict[str, Any]] = None


class TemplateValidationReport(BaseModel):
    template_name: str
    description: str
    total_checks: int
    passed: int
    failed: int
    warnings: int
    results: List[CheckResult]
    timestamp: datetime = Field(default_factory=datetime.now)

class FileFilter(str, Enum):
    all = "all"
    csv = "csv"
    json = "json"
    db = "db"


class BatchAnalysisRequest(BaseModel):
    file_ids: List[str] = Field(..., min_items=1, description="IDs of uploaded files")
    template_id: Optional[str] = Field(default=None, description="Optional validation template")


class TemplateCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    rules: List[str] = Field(default_factory=list)


class ReportResponse(BaseModel):
    task_id: str
    file_id: str
    template_id: Optional[str]
    status: str
    metrics: Dict[str, Any]
    issues: List[Dict[str, Any]]
    analysis: Optional[Dict[str, Any]] = None
    validation: Optional[Dict[str, Any]] = None


class StatusResponse(BaseModel):
    task_id: str
    status: str
    created_at: datetime
    updated_at: datetime


class StartResponse(BaseModel):
    task_id: str
    file_id: str
    started_at: datetime


class StartRequest(BaseModel):
    file_id: str
    template_name: Optional[str] = Field(default='None', description="Optional validation template")


class TemplateResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    rules: List[str]


class RecentFileResponse(BaseModel):
    id: str
    filename: str
    file_type: str
    uploaded_at: datetime
    size: int
    stats: Dict[str, Any]

class CleaningAction(BaseModel):
    action_type: str  # "remove_duplicates", "fill_missing", "remove_outliers"
    description: str
    column: Optional[str] = None
    value: Optional[Any] = None
    affected_rows: int
    priority: str = "medium"


class SmartRecommendation(BaseModel):
    check_type: str
    column: str
    detected_type: str
    issue: str
    suggested_action: CleaningAction
    confidence: float


class SmartAnalysisReport(BaseModel):
    total_rows: int
    total_columns: int
    detected_types: Dict[str, str]  # column -> type
    recommendations: List[SmartRecommendation]
    cleaning_actions: List[CleaningAction]
    estimated_impact: str