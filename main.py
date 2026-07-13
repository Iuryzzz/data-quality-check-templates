from config.db_config import DBConnectionConfig
from app.db_connector import DBConnectorService, BatchDispatcher
from app.data_analyzer import DataQualityAnalyzer, TemplateValidator

config = DBConnectionConfig.from_yaml("config/db_config.yaml")
print("Конфигурация загружена")

db = DBConnectorService(config)
if not db.test_connection():
    exit(1)
print("Подключение к общей БД успешно!")
print("Таблицы в базе:", db.list_tables())

engine = db.get_engine()
dispatcher = BatchDispatcher()

if "students" in db.list_tables():
    for df in dispatcher.load_table_batches(engine, "students"):
        print(f"\nНачинаю анализ {len(df)} строк...")
        
        analyzer = DataQualityAnalyzer(df)
        eda_report = analyzer.export_results("pydantic")
        print("\nОбщий анализ готов:")
        print(f"  Всего строк: {eda_report.total_rows}")
        print(f"  Пропусков: {eda_report.total_missing} ({eda_report.missing_percentage}%)")
        print(f"  Дубликатов: {eda_report.duplicate_count}")
        print(f"  Выбросов: {eda_report.total_outliers}")
        
        validator = TemplateValidator(df)
        val_report = validator.validate_by_template("students")
        print(f"\nПроверка по шаблону '{val_report.template_name}':")
        print(f"  Описание: {val_report.description}")
        print(f"  Всего проверок: {val_report.total_checks}")
        print(f"  Пройдено: {val_report.passed} | Ошибок: {val_report.failed} | Предупреждений: {val_report.warnings}")
        
        # Вывод таблицы с доступом к атрибутам объекта
        print("\n" + "-" * 110)
        print(f"{'Статус':<10} {'Тип проверки':<20} {'Название':<25} {'Сообщение'}")
        print("-" * 110)
        
        for res in val_report.results:
            status = res.status
            check_type = res.check_type
            check_name = res.check_name
            message = res.message
            print(f"{status:<10} {check_type:<20} {check_name:<25} {message}")
        
        print("-" * 110)
else:
    print("\nТаблица 'students' не найдена в базе данных")

print("\nРабота завершена!")