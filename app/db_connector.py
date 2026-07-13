from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy import inspect
from typing import Iterator, List
import pandas as pd
from config.db_config import DBConnectionConfig


class DBConnectorService:
    def __init__(self, config: DBConnectionConfig):
        self.config = config
        self._engine: Engine | None = None

    def _build_connection_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.config.user}:{self.config.password}"
            f"@{self.config.host}:{self.config.port}/{self.config.database}"
        )

    def get_engine(self) -> Engine:
        if not self._engine:
            self._engine = create_engine(
                self._build_connection_url(),
                connect_args=self.config.extra_params or {},
                pool_pre_ping=True
            )
        return self._engine

    def test_connection(self) -> bool:
        try:
            with self.get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception as e:
            print(f"Ошибка подключения: {e}")
            return False

    def list_tables(self) -> List[str]:
        return inspect(self.get_engine()).get_table_names()


class BatchDispatcher:
    def __init__(self, batch_size: int = 5000):
        self.batch_size = batch_size

    def load_table_batches(self, engine: Engine, table_name: str) -> Iterator[pd.DataFrame]:
        print(f"Загружаю таблицу: {table_name}")
        for chunk in pd.read_sql_query(f"SELECT * FROM {table_name}", engine, chunksize=self.batch_size):
            yield chunk
            print(f"Получено {len(chunk)} строк")