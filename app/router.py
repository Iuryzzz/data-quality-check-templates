import traceback
from datetime import datetime, timezone
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
from fastapi import BackgroundTasks
import httpx
import pandas as pd
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.openapi.models import Response
from fastapi.responses import FileResponse
from fastapi.encoders import jsonable_encoder

from .pdf_report import generate_report_pdf
from .schemas import (
    StartResponse, StartRequest, BatchAnalysisRequest, ReportResponse,
    StatusResponse, TemplateResponse, TemplateCreateRequest,
    RecentFileResponse, FileFilter
)
from .data_analyzer import DataQualityAnalyzer, TemplateValidator, load_data
from .service import WebStorage


router = APIRouter()
BASE_DIR = Path(__file__).resolve().parent
web_storage = WebStorage(BASE_DIR / "web_storage.sqlite3")

TEMPLATES_DIR = BASE_DIR / "checks" / "templates"
CHECKS_DIR = BASE_DIR / "checks"
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
CHECKS_DIR.mkdir(parents=True, exist_ok=True)



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


def _load_json_file(path: Path) -> Dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json_file(path: Path, data: Dict) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )



def _list_templates() -> List[Dict[str, str]]:
    """Считывает все шаблоны из папки checks/templates/"""
    items = []
    for path in sorted(TEMPLATES_DIR.glob("*.json")):
        try:
            data = _load_json_file(path)
            items.append({
                "name": path.stem,
                "title": data.get("template_name", path.stem),
                "description": data.get("description", ""),
                "checks": data.get("checks", []),
            })
        except Exception:
            continue
    return items


def _list_available_checks() -> List[Dict[str, str]]:
    """Считывает все доступные проверки из папки checks/ (кроме templates/)"""
    checks = []
    for path in sorted(CHECKS_DIR.glob("*.json")):
        if path.parent.name == "templates":
            continue
        try:
            data = _load_json_file(path)
            checks.append({
                "id": data.get("id", path.stem),
                "name": data.get("name", path.stem),
                "type": data.get("type", "unknown"),
                "description": data.get("description", ""),
            })
        except Exception:
            continue
    return checks


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
        elif all(infer_value_type(v) == "integer" for v in non_empty_values):
            detected_type = "integer"
        elif all(infer_value_type(v) in {"integer", "float"} for v in non_empty_values):
            detected_type = "float" if any(infer_value_type(v) == "float" for v in non_empty_values) else "integer"
        elif all(infer_value_type(v) == "datetime" for v in non_empty_values):
            detected_type = "datetime"
        elif all(infer_value_type(v) == "boolean" for v in non_empty_values):
            detected_type = "boolean"
        else:
            detected_type = "string"
        schema_columns.append({
            "name": column,
            "detected_type": detected_type,
            "is_nullable": any(v in (None, "") for v in values),
        })
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
            rows = [row for row in data if isinstance(row, dict)] if isinstance(data, list) else [data]
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
        template_path = TEMPLATES_DIR / f"{template_name}.json"
        if template_path.exists():
            validator = TemplateValidator(df)
            validation_report = validator.validate_by_template(template_name)
        else:
            print(f"⚠ Шаблон '{template_name}' не найден, пропуск валидации")

    return {
        "schema": schema_payload["schema"],
        "analysis": _to_serializable_report(analysis_report)["analysis"],
        "validation": _to_serializable_report(None, validation_report)["validation"] if validation_report else None,
    }



@router.post("/api/v1/data/upload")
async def upload_data(file: UploadFile = File(...)):
    """Загрузка файла"""
    file_id = str(uuid4())
    task_id = str(uuid4())
    now = datetime.now(timezone.utc)
    content = await file.read()
    file_type = (file.filename or "").rsplit(".", 1)[-1].lower() if "." in (file.filename or "") else "unknown"

    web_storage.save_upload({
        "file_id": file_id,
        "task_id": task_id,
        "filename": file.filename or "uploaded-file",
        "file_type": file_type,
        "uploaded_at": now.isoformat(),
        "size": len(content),
        "content": content,
        "schema": None,
        "stats": {"rows": 0, "columns": 0, "duplicates": 0, "missing_values": 0, "inconsistencies": 0},
    })
    return {
        "file_id": file_id,
        "task_id": task_id,
        "filename": file.filename,
        "status": "uploaded",
    }


@router.get("/api/v1/data/recent", response_model=List[RecentFileResponse])
def get_recent_files(file_type: FileFilter = FileFilter.all, limit: int = 20):
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


@router.get("/api/v1/data/stats/recent")
def get_recent_files_stats():
    return web_storage.recent_stats()



@router.post("/api/v1/analysis/start", response_model=StartResponse)
def start_analysis(payload: StartRequest):
    """Старт анализа. Теперь принимает template_name для валидации."""
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
            "metrics": stored_file.get("stats", {}),
            "analysis": analysis_payload["analysis"],
            "validation": analysis_payload["validation"],
            "issues": [],
            "created_at": started_at.isoformat(),
            "updated_at": started_at.isoformat(),
            "started_at": started_at.isoformat(),
        }
        web_storage.save_task(task)

    return {"task_id": task_id, "file_id": payload.file_id, "started_at": started_at}


@router.post("/api/v1/analysis/batch")
def start_batch_analysis(payload: BatchAnalysisRequest):
    created_tasks = []
    for file_id in payload.file_ids:
        stored_file = web_storage.get_file(file_id)
        if stored_file is None:
            raise HTTPException(status_code=404, detail=f"File not found: {file_id}")

        started_at = datetime.now(timezone.utc)
        task_id = str(uuid4())
        analysis_payload = _parse_uploaded_content(
            stored_file["filename"],
            stored_file["content"],
            payload.template_id,
        )
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
            "analysis": analysis_payload["analysis"],
            "validation": analysis_payload["validation"],
            "issues": analysis_payload["analysis"].get("outlier_details", []),
            "created_at": started_at.isoformat(),
            "updated_at": started_at.isoformat(),
            "started_at": started_at.isoformat(),
        }
        web_storage.save_task(task_record)
        created_tasks.append({"task_id": task_id, "file_id": file_id, "status": "done"})
    return {"tasks": created_tasks}


@router.get("/api/v1/analysis/report/{task_id}", response_model=ReportResponse)
def get_analysis_report(task_id: str):
    task = web_storage.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {
        "task_id": task["task_id"],
        "file_id": task["file_id"],
        "template_id": task.get("template_id"),
        "status": task["status"],
        "metrics": task["metrics"],
        "issues": task["issues"],
        "analysis": task.get("analysis"),
        "validation": task.get("validation"),
    }


@router.get("/api/v1/analysis/status/{task_id}", response_model=StatusResponse)
def get_analysis_status(task_id: str):
    task = web_storage.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {
        "task_id": task["task_id"],
        "status": task["status"],
        "created_at": task["created_at"],
        "updated_at": task["updated_at"],
    }


@router.get("/api/v1/templates/catalog")
def templates_catalog():
    """Список всех шаблонов (из папки checks/templates/)"""
    return {"templates": _list_templates()}


@router.get("/api/v1/templates/available-checks")
def available_checks():
    """Список всех доступных проверок, которые можно добавить в шаблон"""
    return {"checks": _list_available_checks()}


@router.get("/api/v1/templates/{template_name}")
def get_template(template_name: str):
    """Получить содержимое конкретного шаблона"""
    path = TEMPLATES_DIR / f"{template_name}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Шаблон '{template_name}' не найден")
    return _load_json_file(path)


@router.post("/api/v1/templates", response_model=TemplateResponse)
def create_template(payload: TemplateCreateRequest):
    """
    Создать шаблон и сохранить его как JSON-файл в checks/templates/.
    Формат JSON совпадает с тем, который читает TemplateValidator.
    """
    safe_name = "".join(
        c if c.isalnum() or c in "_-" else "_" for c in payload.name.strip().lower()
    )
    if not safe_name:
        raise HTTPException(status_code=400, detail="Имя шаблона пустое или содержит недопустимые символы")

    path = TEMPLATES_DIR / f"{safe_name}.json"
    if path.exists():
        raise HTTPException(status_code=409, detail=f"Шаблон с именем '{safe_name}' уже существует")

    template_data = {
        "template_name": payload.name,
        "description": payload.description or "",
        "checks": payload.rules or [],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_json_file(path, template_data)

    return TemplateResponse(
        id=safe_name,
        name=payload.name,
        description=payload.description,
        rules=payload.rules,
    )


@router.put("/api/v1/templates/{template_name}", response_model=TemplateResponse)
def update_template(template_name: str, payload: TemplateCreateRequest):
    """Обновить существующий шаблон"""
    path = TEMPLATES_DIR / f"{template_name}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Шаблон '{template_name}' не найден")

    template_data = {
        "template_name": payload.name,
        "description": payload.description or "",
        "checks": payload.rules or [],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_json_file(path, template_data)

    return TemplateResponse(
        id=template_name,
        name=payload.name,
        description=payload.description,
        rules=payload.rules,
    )


@router.delete("/api/v1/templates/{template_name}")
def delete_template(template_name: str):
    """Удалить шаблон"""
    path = TEMPLATES_DIR / f"{template_name}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Шаблон '{template_name}' не найден")
    path.unlink()
    return {"status": "deleted", "name": template_name}



@router.post("/api/v1/data/connect-db")
def connect_database():
    return {"status": "connected", "tables": []}



@router.get("/")
def base():
    return FileResponse(BASE_DIR / "static" / "index.html", media_type="text/html; charset=utf-8")


from .data_analyzer import SmartTemplateAnalyzer
from .schemas import SmartAnalysisReport


@router.post("/api/v1/analysis/smart")
def smart_analysis(payload: StartRequest):
    """Умный анализ с автоматическими рекомендациями"""
    stored_file = web_storage.get_file(payload.file_id)
    if stored_file is None:
        raise HTTPException(status_code=404, detail="File not found")

    df = _load_uploaded_dataframe(stored_file["filename"], stored_file["content"])
    analyzer = SmartTemplateAnalyzer(df)
    result = analyzer.analyze()

    task_id = str(uuid4())

    web_storage.save_task({
        "task_id": task_id,
        "file_id": payload.file_id,
        "template_id": "smart",
        "status": "done",
        "metrics": {
            "rows": len(df),
            "columns": len(df.columns),
            "recommendations": len(result["recommendations"]),
        },
        "analysis": result,
        "validation": None,
        "issues": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "started_at": datetime.now(timezone.utc).isoformat(),
    })

    return {
        "task_id": task_id,
        "total_rows": len(df),
        "total_columns": len(df.columns),
        "detected_types": result["detected_types"],
        "recommendations": result["recommendations"],
        "cleaning_actions": result["cleaning_actions"],
        "estimated_impact": result["estimated_impact"]
    }


@router.post("/api/v1/analysis/apply-cleaning")
def apply_cleaning(payload: Dict[str, Any]):
    """Применяет выбранные действия по очистке и сохраняет результат"""
    task_id = payload.get("task_id")
    actions = payload.get("actions", [])

    task = web_storage.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    stored_file = web_storage.get_file(task["file_id"])
    df = _load_uploaded_dataframe(stored_file["filename"], stored_file["content"])

    analyzer = SmartTemplateAnalyzer(df)
    cleaned_df = analyzer.apply_cleaning(actions)

    new_file_id = str(uuid4())
    cleaned_content = cleaned_df.to_csv(index=False).encode('utf-8')

    web_storage.save_upload({
        "file_id": new_file_id,
        "task_id": str(uuid4()),
        "filename": f"cleaned_{stored_file['filename']}",
        "file_type": "csv",
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "size": len(cleaned_content),
        "content": cleaned_content,
        "schema": None,
        "stats": {
            "rows": len(cleaned_df),
            "columns": len(cleaned_df.columns),
            "duplicates": int(cleaned_df.duplicated().sum()),
            "missing_values": int(cleaned_df.isnull().sum().sum()),
            "inconsistencies": 0,
        },
    })

    return {
        "new_file_id": new_file_id,
        "original_rows": len(df),
        "cleaned_rows": len(cleaned_df),
        "rows_removed": len(df) - len(cleaned_df),
        "download_url": f"/api/v1/data/download/{new_file_id}"
    }


@router.get("/api/v1/data/download/{file_id}")
def download_cleaned_file(file_id: str):
    """Скачивание обработанного файла напрямую из БД"""
    file_record = web_storage.get_file(file_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="File not found")

    from fastapi.responses import Response as FastAPIResponse

    return FastAPIResponse(
        content=file_record["content"],
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{file_record["filename"]}"'
        }
    )


@router.get("/api/v1/analysis/report/{task_id}/pdf")
def download_pdf_report(task_id: str):
    """Скачивание PDF-отчёта по задаче анализа"""
    task = web_storage.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    stored_file = web_storage.get_file(task.get("file_id"))
    filename = stored_file["filename"] if stored_file else f"report_{task_id}.pdf"
    from fastapi.responses import Response as FastAPIResponse
    try:
        pdf_bytes = bytes(generate_report_pdf(task, filename))
    except Exception as e:
        print("=" * 60)
        print("КРИТИЧЕСКАЯ ОШИБКА ГЕНЕРАЦИИ PDF:")
        traceback.print_exc()
        print("=" * 60)
        raise HTTPException(status_code=500, detail=f"Ошибка генерации PDF: {str(e)}")

    pdf_filename = f"report_{Path(filename).stem}.pdf"

    return FastAPIResponse(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{pdf_filename}"'
        }
    )