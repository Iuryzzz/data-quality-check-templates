from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4
import os

import csv
import io
import json
import math
import sqlite3
import tempfile

import httpx
import pandas as pd
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.encoders import jsonable_encoder
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field

from app.schemas import StartResponse, StartRequest, BatchAnalysisRequest, ReportResponse, StatusResponse, \
    TemplateResponse, TemplateCreateRequest
from data_analyzer import DataQualityAnalyzer, TemplateValidator, load_data
from app.schemas import RecentFileResponse, FileFilter
from service import WebStorage

app = FastAPI()
security = HTTPBasic()
BASE_DIR = Path(__file__).resolve().parent
web_storage = WebStorage(BASE_DIR / "web_storage.sqlite3")

ADMIN_USER = "admin"
ADMIN_PASSWORD = "admin123"

recent_files: List[Dict[str, Any]] = []
uploaded_files: Dict[str, Dict[str, Any]] = {}
tasks: Dict[str, Dict[str, Any]] = {}
file_task_map: Dict[str, str] = {}
templates: Dict[str, Dict[str, Any]] = {
    "default": {
        "id": "default",
        "name": "Default checks",
        "description": "Basic validation template",
        "rules": ["not_empty", "no_duplicates", "no_missing_values"],
    }
}

# Банальная авторизация для всех защищенных эндпоинтов.
def require_basic_auth(credentials: HTTPBasicCredentials = Depends(security)) -> None:
    if credentials.username != ADMIN_USER or credentials.password != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Invalid credentials")


def _model_to_dict(model: Any) -> Any:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    if hasattr(model, "dict"):
        return model.dict()
    return model


def _sanitize_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _sanitize_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_json(item) for item in value]
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def _list_templates() -> List[Dict[str, str]]:
    templates_dir = BASE_DIR / "checks" / "templates"
    items: List[Dict[str, str]] = []
    if not templates_dir.exists():
        return items

    for path in sorted(templates_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        items.append(
            {
                "name": path.stem,
                "title": data.get("template_name", path.stem.title()),
                "description": data.get("description", ""),
            }
        )
    return items


def _load_uploaded_dataframe(file_name: str, content: bytes) -> pd.DataFrame:
    suffix = Path(file_name).suffix.lower() or ".csv"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
        temp_file.write(content)
        temp_path = Path(temp_file.name)

    try:
        if suffix == ".db":
            with sqlite3.connect(temp_path) as connection:
                tables = pd.read_sql_query(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name",
                    connection,
                )["name"].tolist()
                if not tables:
                    raise ValueError("SQLite database does not contain any tables")
                return pd.read_sql_query(f"SELECT * FROM {tables[0]}", connection)
        return load_data(str(temp_path))
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except PermissionError:
            pass


def _to_serializable_report(analysis: Any, validation: Any = None) -> Dict[str, Any]:
    analysis_data = _sanitize_json(jsonable_encoder(_model_to_dict(analysis)))
    validation_data = _sanitize_json(jsonable_encoder(_model_to_dict(validation))) if validation is not None else None
    return {"analysis": analysis_data, "validation": validation_data}



def _parse_uploaded_content(filename: str, content: bytes, template_name: Optional[str] = None) -> Dict[str, Any]:
    parser_url = os.environ.get("PARSER_SERVICE_URL")
    if parser_url:
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(
                    parser_url,
                    files={"file": (filename, content)},
                    data={"template_name": template_name or ""},
                )
                response.raise_for_status()
                payload = response.json()
                if isinstance(payload, dict) and "analysis" in payload:
                    return {
                        "schema": payload.get("schema", infer_schema_from_content(filename, content)["schema"]),
                        "analysis": payload.get("analysis", {}),
                        "validation": payload.get("validation"),
                    }
        except Exception as error:
            print(f"Parser service fallback used: {error}")

    schema_payload = infer_schema_from_content(filename, content)
    df = _load_uploaded_dataframe(filename, content)
    analyzer = DataQualityAnalyzer(df)
    analysis_report = analyzer.export_results("pydantic")
    validation_report = None
    if template_name:
        validator = TemplateValidator(df)
        validation_report = validator.validate_by_template(template_name)
    return {
        "schema": schema_payload["schema"],
        "analysis": _to_serializable_report(analysis_report)["analysis"],
        "validation": _to_serializable_report(None, validation_report)["validation"] if validation_report else None,
    }


def infer_value_type(value: Any) -> str:
    if value in (None, ""):
        return "string"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "float"

    text = str(value).strip()
    if not text:
        return "string"
    if text.lower() in {"true", "false"}:
        return "boolean"
    try:
        int(text)
        return "integer"
    except ValueError:
        pass
    try:
        float(text.replace(",", "."))
        return "float"
    except ValueError:
        pass
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
        return "datetime"
    except ValueError:
        return "string"


def build_schema(columns: List[str], rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    schema_columns = []
    for column in columns:
        values = [row.get(column) for row in rows]
        non_empty_values = [value for value in values if value not in (None, "")]

        if not non_empty_values:
            detected_type = "string"
        elif all(infer_value_type(value) == "integer" for value in non_empty_values):
            detected_type = "integer"
        elif all(infer_value_type(value) in {"integer", "float"} for value in non_empty_values):
            detected_type = "float" if any(infer_value_type(value) == "float" for value in non_empty_values) else "integer"
        elif all(infer_value_type(value) == "datetime" for value in non_empty_values):
            detected_type = "datetime"
        elif all(infer_value_type(value) == "boolean" for value in non_empty_values):
            detected_type = "boolean"
        else:
            detected_type = "string"

        schema_columns.append(
            {
                "name": column,
                "detected_type": detected_type,
                "is_nullable": any(value in (None, "") for value in values),
            }
        )
    return {"columns": schema_columns}


def infer_schema_from_content(filename: str, content: bytes) -> Dict[str, Any]:
    file_type = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    text = content.decode("utf-8", errors="ignore")

    if file_type == "csv" and text.strip():
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        columns = reader.fieldnames or []
        return {"schema": build_schema(columns, rows[:100])}

    if file_type == "json" and text.strip():
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if data is not None:
            rows: List[Dict[str, Any]] = []
            if isinstance(data, list):
                rows = [row for row in data if isinstance(row, dict)]
            elif isinstance(data, dict):
                rows = [data]
            columns = sorted({key for row in rows for key in row.keys()})
            return {"schema": build_schema(columns, rows[:100])}

    if file_type in {"db", "sqlite", "sqlite3"}:
        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_type}") as temp_file:
            temp_file.write(content)
            temp_path = Path(temp_file.name)
        try:
            with sqlite3.connect(temp_path) as connection:
                tables = pd.read_sql_query(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name",
                    connection,
                )["name"].tolist()
                if not tables:
                    raise ValueError("SQLite database does not contain any tables")
                df = pd.read_sql_query(f"SELECT * FROM {tables[0]} LIMIT 100", connection)
                return {"schema": build_schema(df.columns.tolist(), df.to_dict(orient="records"))}
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except PermissionError:
                pass


# Эндпоинт для загрузки файла с фронта и сохранения его метаданных.
@app.post("/api/v1/data/upload", dependencies=[Depends(require_basic_auth)])
async def upload_data(file: UploadFile = File(...)):
    """Загрузка файла с фронта и сохранения его метаданных"""
    file_id = str(uuid4())
    task_id = str(uuid4())
    now = datetime.now(timezone.utc)
    content = await file.read()
    file_type = (file.filename or "").rsplit(".", 1)[-1].lower() if "." in (file.filename or "") else "unknown"

    item = {
        "id": file_id,
        "filename": file.filename or "uploaded-file",
        "file_type": file_type,
        "uploaded_at": now,
        "size": len(content),
        "content": content,
        "schema": None,
        "stats": {
            "rows": 0,
            "columns": 0,
            "duplicates": 0,
            "missing_values": 0,
            "inconsistencies": 0,
        },
    }
    recent_files.insert(0, item)
    uploaded_files[file_id] = item
    task_record = {
        "task_id": task_id,
        "file_id": file_id,
        "template_id": None,
        "status": "done",
        "metrics": item["stats"],
        "schema": item["schema"],
        "issues": [],
        "created_at": now,
        "updated_at": now,
        "started_at": now,
        "analysis": None,
        "validation": None,
    }
    tasks[task_id] = task_record
    file_task_map[file_id] = task_id
    web_storage.save_upload(
        {
            "file_id": file_id,
            "task_id": task_id,
            "filename": item["filename"],
            "file_type": file_type,
            "uploaded_at": now.isoformat(),
            "size": len(content),
            "content": content,
            "schema": item["schema"],
            "stats": item["stats"],
        }
    )
    return {
        "file_id": file_id,
        "task_id": task_id,
        "filename": item["filename"],
        "status": "uploaded",
    }


# Эндпоинт для старта анализа после успешной загрузки файла.
@app.post("/api/v1/analysis/start", response_model=StartResponse, dependencies=[Depends(require_basic_auth)])
def start_analysis(payload: StartRequest):
    """Старт анализа после успешной загрузки файла"""
    stored_file = web_storage.get_file(payload.file_id)
    if stored_file is None:
        raise HTTPException(status_code=404, detail="File not found")

    started_at = datetime.now(timezone.utc)
    task_id = stored_file["task_id"]
    task = web_storage.get_task(task_id)
    if task is None:
        try:
            analysis_payload = _parse_uploaded_content(
                stored_file["filename"],
                stored_file["content"],
                payload.template_name,
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error))
        task = {
            "task_id": task_id,
            "file_id": payload.file_id,
            "template_id": payload.template_name,
            "status": "done",
            "metrics": stored_file["stats"],
            "analysis": analysis_payload["analysis"],
            "validation": analysis_payload["validation"],
            "issues": [],
            "created_at": started_at.isoformat(),
            "updated_at": started_at.isoformat(),
            "started_at": started_at.isoformat(),
        }
        web_storage.save_task(task)
    file_task_map[payload.file_id] = task_id
    return {"task_id": task_id, "file_id": payload.file_id, "started_at": started_at}


# Эндпоинт для получения списка недавних файлов с фильтрацией.
@app.get("/api/v1/data/recent", response_model=List[RecentFileResponse], dependencies=[Depends(require_basic_auth)])
def get_recent_files(file_type: FileFilter = FileFilter.all, limit: int = 20):
    """Получения списка недавних файлов с фильтрацией"""
    items = web_storage.list_recent_files(file_type.value, limit)
    return [
        {
            "id": item["file_id"],
            "filename": item["filename"],
            "file_type": item["file_type"],
            "uploaded_at": item["uploaded_at"],
            "size": item["size"],
            "stats": item["stats"],
        }
        for item in items
    ]


# Эндпоинт для получения статистики по недавним файлам.
@app.get("/api/v1/data/stats/recent", dependencies=[Depends(require_basic_auth)])
def get_recent_files_stats():
    """Получение статистики по недавним файлам"""
    return web_storage.recent_stats()


# Эндпоинт для пакетной обработки
@app.post("/api/v1/analysis/batch", dependencies=[Depends(require_basic_auth)])
def start_batch_analysis(payload: BatchAnalysisRequest):
    """Пакетная обработка файлов"""
    created_tasks = []

    for file_id in payload.file_ids:
        stored_file = web_storage.get_file(file_id)
        if stored_file is None:
            raise HTTPException(status_code=404, detail=f"File not found: {file_id}")

        started_at = datetime.now(timezone.utc)
        task_id = str(uuid4())
        try:
            analysis_payload = _parse_uploaded_content(stored_file["filename"], stored_file["content"], payload.template_id)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error))
        task_record = {
            "task_id": task_id,
            "file_id": file_id,
            "template_id": payload.template_id,
            "status": "done",
            "metrics": {
                "rows": analysis_payload["analysis"].get("total_rows", 0),
                "columns": analysis_payload["analysis"].get("total_columns", 0),
                "duplicates": analysis_payload["analysis"].get("duplicate_count", 0),
                "missing_values": analysis_payload["analysis"].get("total_missing", 0),
                "inconsistencies": analysis_payload["analysis"].get("total_outliers", 0),
            },
            "schema": stored_file["schema"],
            "analysis": analysis_payload,
            "validation": analysis_payload["validation"],
            "issues": analysis_payload["analysis"].get("outlier_details", []),
            "created_at": started_at,
            "updated_at": started_at,
            "started_at": started_at,
        }
        tasks[task_id] = task_record
        file_task_map[file_id] = task_id
        web_storage.save_task(
            {
                "task_id": task_id,
                "file_id": file_id,
                "template_id": payload.template_id,
                "status": "done",
                "metrics": task_record["metrics"],
                "analysis": analysis_payload["analysis"],
                "validation": analysis_payload["validation"],
                "issues": task_record["issues"],
                "created_at": started_at.isoformat(),
                "updated_at": started_at.isoformat(),
                "started_at": started_at.isoformat(),
            }
        )
        created_tasks.append(
            {
                "task_id": task_id,
                "file_id": file_id,
                "status": "done",
                "summary": {
                    "rows": task_record["metrics"]["rows"],
                    "columns": task_record["metrics"]["columns"],
                    "missing": task_record["metrics"]["missing_values"],
                    "duplicates": task_record["metrics"]["duplicates"],
                    "outliers": task_record["metrics"]["inconsistencies"],
                },
            }
        )

    return {"tasks": created_tasks}


# Эндпоинт для получения детального отчета по задаче анализа.
@app.get("/api/v1/analysis/report/{task_id}", response_model=ReportResponse, dependencies=[Depends(require_basic_auth)])
def get_analysis_report(task_id: str):
    """получение детального отчета по задаче анализа"""
    task = web_storage.get_task(task_id) or tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {
        "task_id": task["task_id"],
        "file_id": task["file_id"],
        "template_id": task["template_id"],
        "status": task["status"],
        "metrics": task["metrics"],
        "issues": task["issues"],
        "analysis": task.get("analysis"),
        "validation": task.get("validation"),
    }


# Эндпоинт для получения статуса выполнения анализа.
@app.get("/api/v1/analysis/status/{task_id}", response_model=StatusResponse, dependencies=[Depends(require_basic_auth)])
def get_analysis_status(task_id: str):
    """Получение статуса выполнения анализа"""
    task = web_storage.get_task(task_id) or tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {
        "task_id": task["task_id"],
        "status": task["status"],
        "created_at": task["created_at"],
        "updated_at": task["updated_at"],
    }


# Эндпоинт для создания пользовательского шаблона проверок.
@app.post("/api/v1/templates", response_model=TemplateResponse, dependencies=[Depends(require_basic_auth)])
def create_template(payload: TemplateCreateRequest):
    """Создание пользовательского шаблона"""
    template_id = str(uuid4())
    template = {
        "id": template_id,
        "name": payload.name,
        "description": payload.description,
        "rules": payload.rules,
    }
    templates[template_id] = template
    return template


# Эндпоинт для подключения к внешней БД
@app.post("/api/v1/data/connect-db", dependencies=[Depends(require_basic_auth)])
def connect_database():
    """Подключение к внешней бд"""
    return {"status": "connected"}



@app.get("/")
def base():
    """Доступ к главной странице приложения"""
    return FileResponse(BASE_DIR / "index.html", media_type="text/html; charset=utf-8")


# Отдает список доступных шаблонов проверки для селекта на фронте.
@app.get("/api/v1/templates/catalog")
def templates_catalog():
    return {"templates": _list_templates()}
