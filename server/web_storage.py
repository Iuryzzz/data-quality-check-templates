from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import Column, Integer, LargeBinary, MetaData, String, Table, Text, create_engine, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine


class WebStorage:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.engine: Engine = create_engine(
            f"sqlite:///{self.db_path}",
            future=True,
            connect_args={"check_same_thread": False},
        )
        self.metadata = MetaData()

        self.uploaded_files = Table(
            "uploaded_files",
            self.metadata,
            Column("file_id", String, primary_key=True),
            Column("task_id", String, nullable=False),
            Column("filename", String, nullable=False),
            Column("file_type", String, nullable=False),
            Column("uploaded_at", Text, nullable=False),
            Column("size", Integer, nullable=False),
            Column("content", LargeBinary, nullable=False),
            Column("schema_json", Text, nullable=False),
            Column("stats_json", Text, nullable=False),
        )
        self.analysis_tasks = Table(
            "analysis_tasks",
            self.metadata,
            Column("task_id", String, primary_key=True),
            Column("file_id", String, nullable=False),
            Column("template_id", String, nullable=True),
            Column("status", String, nullable=False),
            Column("metrics_json", Text, nullable=False),
            Column("analysis_json", Text, nullable=False),
            Column("validation_json", Text, nullable=True),
            Column("issues_json", Text, nullable=False),
            Column("created_at", Text, nullable=False),
            Column("updated_at", Text, nullable=False),
            Column("started_at", Text, nullable=False),
        )

        self.metadata.create_all(self.engine)

    def save_upload(self, record: Dict[str, Any]) -> None:
        statement = sqlite_insert(self.uploaded_files).values(
            file_id=record["file_id"],
            task_id=record["task_id"],
            filename=record["filename"],
            file_type=record["file_type"],
            uploaded_at=record["uploaded_at"],
            size=record["size"],
            content=record["content"],
            schema_json=json.dumps(record["schema"], ensure_ascii=False),
            stats_json=json.dumps(record["stats"], ensure_ascii=False),
        )
        statement = statement.on_conflict_do_update(
            index_elements=["file_id"],
            set_={
                "task_id": statement.excluded.task_id,
                "filename": statement.excluded.filename,
                "file_type": statement.excluded.file_type,
                "uploaded_at": statement.excluded.uploaded_at,
                "size": statement.excluded.size,
                "content": statement.excluded.content,
                "schema_json": statement.excluded.schema_json,
                "stats_json": statement.excluded.stats_json,
            },
        )
        with self.engine.begin() as connection:
            connection.execute(statement)

    def save_task(self, record: Dict[str, Any]) -> None:
        statement = sqlite_insert(self.analysis_tasks).values(
            task_id=record["task_id"],
            file_id=record["file_id"],
            template_id=record.get("template_id"),
            status=record["status"],
            metrics_json=json.dumps(record["metrics"], ensure_ascii=False),
            analysis_json=json.dumps(record["analysis"], ensure_ascii=False),
            validation_json=json.dumps(record["validation"], ensure_ascii=False) if record.get("validation") is not None else None,
            issues_json=json.dumps(record["issues"], ensure_ascii=False),
            created_at=record["created_at"],
            updated_at=record["updated_at"],
            started_at=record["started_at"],
        )
        statement = statement.on_conflict_do_update(
            index_elements=["task_id"],
            set_={
                "file_id": statement.excluded.file_id,
                "template_id": statement.excluded.template_id,
                "status": statement.excluded.status,
                "metrics_json": statement.excluded.metrics_json,
                "analysis_json": statement.excluded.analysis_json,
                "validation_json": statement.excluded.validation_json,
                "issues_json": statement.excluded.issues_json,
                "created_at": statement.excluded.created_at,
                "updated_at": statement.excluded.updated_at,
                "started_at": statement.excluded.started_at,
            },
        )
        with self.engine.begin() as connection:
            connection.execute(statement)

    def list_recent_files(self, file_type: str = "all", limit: int = 20) -> List[Dict[str, Any]]:
        statement = select(self.uploaded_files).order_by(self.uploaded_files.c.uploaded_at.desc()).limit(limit)
        if file_type != "all":
            statement = select(self.uploaded_files).where(self.uploaded_files.c.file_type == file_type).order_by(
                self.uploaded_files.c.uploaded_at.desc()
            ).limit(limit)

        with self.engine.begin() as connection:
            rows = connection.execute(statement).mappings().all()
            return [self._row_to_file_dict(row) for row in rows]

    def recent_stats(self) -> Dict[str, Any]:
        with self.engine.begin() as connection:
            total_files = connection.execute(select(func.count()).select_from(self.uploaded_files)).scalar_one()
            total_size = connection.execute(select(func.coalesce(func.sum(self.uploaded_files.c.size), 0))).scalar_one()
            counts = {
                "csv": connection.execute(
                    select(func.count()).select_from(self.uploaded_files).where(self.uploaded_files.c.file_type == "csv")
                ).scalar_one(),
                "json": connection.execute(
                    select(func.count()).select_from(self.uploaded_files).where(self.uploaded_files.c.file_type == "json")
                ).scalar_one(),
                "db": connection.execute(
                    select(func.count()).select_from(self.uploaded_files).where(self.uploaded_files.c.file_type == "db")
                ).scalar_one(),
                "unknown": connection.execute(
                    select(func.count()).select_from(self.uploaded_files).where(self.uploaded_files.c.file_type == "unknown")
                ).scalar_one(),
            }
        return {"total_files": total_files, "total_size": total_size, "file_types": counts}

    def get_file(self, file_id: str) -> Optional[Dict[str, Any]]:
        statement = select(self.uploaded_files).where(self.uploaded_files.c.file_id == file_id)
        with self.engine.begin() as connection:
            row = connection.execute(statement).mappings().first()
            if row is None:
                return None
            return self._row_to_file_dict(row)

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        statement = select(self.analysis_tasks).where(self.analysis_tasks.c.task_id == task_id)
        with self.engine.begin() as connection:
            row = connection.execute(statement).mappings().first()
            if row is None:
                return None
            return self._row_to_task_dict(row)

    def get_task_by_file(self, file_id: str) -> Optional[Dict[str, Any]]:
        statement = (
            select(self.analysis_tasks)
            .where(self.analysis_tasks.c.file_id == file_id)
            .order_by(self.analysis_tasks.c.updated_at.desc())
            .limit(1)
        )
        with self.engine.begin() as connection:
            row = connection.execute(statement).mappings().first()
            if row is None:
                return None
            return self._row_to_task_dict(row)

    def _row_to_file_dict(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "file_id": row["file_id"],
            "task_id": row["task_id"],
            "filename": row["filename"],
            "file_type": row["file_type"],
            "uploaded_at": row["uploaded_at"],
            "size": row["size"],
            "content": row["content"],
            "schema": json.loads(row["schema_json"]),
            "stats": json.loads(row["stats_json"]),
        }

    def _row_to_task_dict(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "task_id": row["task_id"],
            "file_id": row["file_id"],
            "template_id": row["template_id"],
            "status": row["status"],
            "metrics": json.loads(row["metrics_json"]),
            "analysis": json.loads(row["analysis_json"]),
            "validation": json.loads(row["validation_json"]) if row["validation_json"] else None,
            "issues": json.loads(row["issues_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "started_at": row["started_at"],
        }
