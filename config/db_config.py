import yaml
from pydantic import BaseModel, Field, field_validator
from typing import Optional, Dict, Any

# Должно совпадать со словарём DRIVERS в app/db_connector.py
SUPPORTED_DB_TYPES = {"postgres", "mysql", "mssql", "oracle", "clickhouse"}


class DBConnectionConfig(BaseModel):
    server_name: str
    type_db: str
    host: str
    port: int
    user: str
    password: str
    database: str
    extra_params: Optional[Dict[str, Any]] = Field(default_factory=dict)

    # ПРАВКА: раньше type_db не проверялся вообще — можно было прислать
    # с фронта любую строку, и упасть с непонятной ошибкой уже при подключении.
    @field_validator("type_db")
    @classmethod
    def validate_type_db(cls, value: str) -> str:
        value = value.lower().strip()
        if value not in SUPPORTED_DB_TYPES:
            raise ValueError(
                f"type_db должен быть одним из {sorted(SUPPORTED_DB_TYPES)}, получено: {value}"
            )
        return value

    @classmethod
    def from_yaml(cls, path: str = "config/db_config.yaml") -> "DBConnectionConfig":
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls(**data)