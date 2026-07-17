from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy import inspect
from typing import Iterator, List, Optional
import pandas as pd
from config.db_config import DBConnectionConfig


class DBConnectorService:
    """
    Подключение к БД через SQLAlchemy.

    ПРАВКА: раньше строка подключения всегда собиралась под Postgres
    (postgresql+psycopg2), хотя requirements.txt уже содержит драйверы
    для mysql/mssql/oracle/clickhouse. Теперь тип СУБД реально влияет
    на то, какой драйвер используется.
    """

    # Диалект SQLAlchemy + драйвер для каждой поддерживаемой СУБД.
    # Перед использованием конкретного типа нужно установить соответствующий
    # пакет (все они уже есть в requirements.txt проекта):
    #   postgres   -> psycopg2-binary
    #   mysql      -> pymysql
    #   mssql      -> pyodbc (+ установленный в системе ODBC Driver for SQL Server)
    #   oracle     -> oracledb
    #   clickhouse -> clickhouse-connect
    DRIVERS = {
        "postgres": "postgresql+psycopg2",
        "mysql": "mysql+pymysql",
        "mssql": "mssql+pyodbc",
        "oracle": "oracle+oracledb",
        "clickhouse": "clickhouse+http",
    }

    def __init__(self, config: DBConnectionConfig):
        self.config = config
        self._engine: Engine | None = None

    def _build_connection_url(self) -> str:
        driver = self.DRIVERS.get(self.config.type_db)
        if not driver:
            raise ValueError(
                f"Неподдерживаемый тип БД: {self.config.type_db}. "
                f"Доступные варианты: {list(self.DRIVERS.keys())}"
            )

        # MSSQL через pyodbc требует явного указания ODBC-драйвера в query-параметрах строки
        if self.config.type_db == "mssql":
            odbc_driver = (self.config.extra_params or {}).get(
                "odbc_driver", "ODBC Driver 17 for SQL Server"
            ).replace(" ", "+")
            return (
                f"{driver}://{self.config.user}:{self.config.password}"
                f"@{self.config.host}:{self.config.port}/{self.config.database}?driver={odbc_driver}"
            )

        return (
            f"{driver}://{self.config.user}:{self.config.password}"
            f"@{self.config.host}:{self.config.port}/{self.config.database}"
        )

    def get_engine(self) -> Engine:
        if not self._engine:
            self._engine = create_engine(
                self._build_connection_url(),
                connect_args=self.config.extra_params or {} if self.config.type_db != "mssql" else {},
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

    def close(self):
        """Закрывает пул соединений. Важно вызывать для разовых (ad-hoc) подключений с фронта,
        чтобы не копить открытые соединения при каждом запросе к /connect-db."""
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None


class BatchDispatcher:
    """
    Батчевое чтение данных из БД.

    ПРАВКА: раньше DataQualityAnalyzer вызывался на каждом батче отдельно
    (см. старый analyze_students.py) — из-за этого корреляции и часть
    дубликатов считались только внутри одного батча, а не по всей таблице.
    Теперь батчи читаются только для экономии памяти при выгрузке,
    а перед анализом их нужно явно склеить через merge_batches().
    """

    def __init__(self, batch_size: int = 5000):
        self.batch_size = batch_size

    def load_table_batches(self, engine: Engine, table_name: str) -> Iterator[pd.DataFrame]:
        """Читает таблицу целиком, порциями по batch_size строк."""
        print(f"Загружаю таблицу: {table_name}")
        for chunk in pd.read_sql_query(f"SELECT * FROM {table_name}", engine, chunksize=self.batch_size):
            yield chunk
            print(f"Получено {len(chunk)} строк")

    def load_query_batches(self, engine: Engine, query: str) -> Iterator[pd.DataFrame]:
        """
        ПРАВКА (новое): читает данные произвольным SQL-запросом, а не только
        целой таблицей — нужно, если с фронта придёт запрос с фильтрацией/JOIN'ом.
        """
        print(f"Выполняю запрос: {query}")
        for chunk in pd.read_sql_query(query, engine, chunksize=self.batch_size):
            yield chunk
            print(f"Получено {len(chunk)} строк")

    @staticmethod
    def merge_batches(batches: Iterator[pd.DataFrame]) -> pd.DataFrame:
        """
        Склеивает батчи в один DataFrame. Нужно вызывать перед тем, как отдать
        данные в DataQualityAnalyzer — иначе корреляции/дубликаты посчитаются
        неверно (только в рамках одного батча).
        """
        chunks = list(batches)
        if not chunks:
            return pd.DataFrame()
        merged = pd.concat(chunks, ignore_index=True)
        print(f"Батчи склеены, всего строк: {len(merged)}")
        return merged
