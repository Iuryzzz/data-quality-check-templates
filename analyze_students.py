from config.db_config import DBConnectionConfig
from app.db_connector import DBConnectorService, BatchDispatcher
from app.data_analyzer import DataQualityAnalyzer, TemplateValidator


def main():
    # Загрузка конфигурации
    config = DBConnectionConfig.from_yaml("config/db_config.yaml")
    print("✓ Конфигурация загружена")

    db = DBConnectorService(config)
    if not db.test_connection():
        print("✗ Не удалось подключиться к БД")
        exit(1)
    print("✓ Подключение к общей БД успешно!")
    print(f"✓ Таблицы в базе: {db.list_tables()}")

    engine = db.get_engine()
    dispatcher = BatchDispatcher()

    if "students" not in db.list_tables():
        print("\n✗ Таблица 'students' не найдена в базе данных")
        return

    # ПРАВКА: раньше DataQualityAnalyzer вызывался на каждом батче внутри цикла —
    # из-за этого корреляции и часть дубликатов считались только внутри одного
    # батча, а не по всей таблице целиком. Теперь батчи сначала читаются порциями
    # (экономия памяти при выгрузке из БД), а затем склеиваются в один DataFrame
    # перед анализом — метрики становятся корректными для таблиц любого размера.
    batches = dispatcher.load_table_batches(engine, "students")
    df = BatchDispatcher.merge_batches(batches)

    if df.empty:
        print("\n✗ Таблица 'students' пуста")
        return

    print(f"\nНачинаю анализ {len(df)} строк...")

    # EDA анализ
    analyzer = DataQualityAnalyzer(df)
    eda_report = analyzer.export_results("pydantic")
    print("\nОбщий анализ готов:")
    print(f"  Всего строк: {eda_report.total_rows}")
    print(f"  Пропусков: {eda_report.total_missing} ({eda_report.missing_percentage}%)")
    print(f"  Дубликатов: {eda_report.duplicate_count}")
    print(f"  Выбросов: {eda_report.total_outliers}")

    # Валидация по шаблону
    validator = TemplateValidator(df)
    val_report = validator.validate_by_template("students")
    print(f"\nПроверка по шаблону '{val_report.template_name}':")
    print(f"  Описание: {val_report.description}")
    print(f"  Всего проверок: {val_report.total_checks}")
    print(f"  Пройдено: {val_report.passed} | Ошибок: {val_report.failed} | Предупреждений: {val_report.warnings}")

    # Таблица результатов
    print("\n" + "-" * 110)
    print(f"{'Статус':<10} {'Тип проверки':<20} {'Название':<25} {'Сообщение'}")
    print("-" * 110)

    for res in val_report.results:
        print(f"{res.status:<10} {res.check_type:<20} {res.check_name:<25} {res.message}")

    print("-" * 110)
    print("\n✓ Работа завершена!")


if __name__ == "__main__":
    main()
