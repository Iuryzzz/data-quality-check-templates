import yaml
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any


class DBConnectionConfig(BaseModel):
    server_name: str
    type_db: str
    host: str
    port: int
    user: str
    password: str
    database: str
    extra_params: Optional[Dict[str, Any]] = Field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: str = "config/db_config.yaml") -> "DBConnectionConfig":
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls(**data)