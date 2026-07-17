# main.py
import uvicorn
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from starlette.staticfiles import StaticFiles

from config.db_config import DBConnectionConfig
from app.db_connector import DBConnectorService
from app.router import router  # ✅ импортируем router, а не app

# Глобальная переменная — будет доступна из роутеров
db_connector: DBConnectorService | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Вызывается ОДИН РАЗ при старте сервера"""
    global db_connector

    print("=" * 60)
    print("Запуск сервера Data Quality Check...")
    print("=" * 60)

    # Инициализация БД
    try:
        config = DBConnectionConfig.from_yaml("config/db_config.yaml")
        print("✓ Конфигурация БД загружена")

        db_connector = DBConnectorService(config)
        if db_connector.test_connection():
            print("✓ Подключение к PostgreSQL успешно!")
            tables = db_connector.list_tables()
            print(f"✓ Доступно таблиц: {len(tables)}")
            if tables:
                print(f"  Таблицы: {', '.join(tables[:5])}{'...' if len(tables) > 5 else ''}")
        else:
            print("⚠ Не удалось подключиться к БД. Функционал БД будет недоступен.")
            db_connector = None
    except FileNotFoundError:
        print("⚠ Файл config/db_config.yaml не найден. БД не подключена.")
        db_connector = None
    except Exception as e:
        print(f"⚠ Ошибка инициализации БД: {e}")
        db_connector = None

    # ПРАВКА: кладём db_connector в app.state, чтобы эндпоинт
    # /api/v1/data/connect-db в app/router.py мог им пользоваться
    # (раньше эта заглушка вообще не была связана с реальным подключением).
    app.state.db_connector = db_connector

    print("=" * 60)
    print("Сервер готов к работе!")
    print("=" * 60)

    yield  # ← Сервер работает, принимает запросы

    # Остановка сервера
    print("\nОстановка сервера...")
    print("✓ Сервер остановлен")


# ✅ ОДИН раз создаём приложение
app = FastAPI(
    title="Data Quality Check API",
    description="API для анализа качества данных",
    version="1.0.0",
    lifespan=lifespan
)

# ✅ Подключаем router из app/router.py
app.include_router(router)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "app" / "static"), name="static")
if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )