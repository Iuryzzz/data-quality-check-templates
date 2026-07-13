import pandas as pd
import numpy as np
from scipy import stats
from typing import Dict, List, Optional
from datetime import datetime
import json
import warnings
import os

from app.schemas import EDAReport, ColumnStat, CorrelationInfo, OutlierInfo, NumericStats, CheckResult, \
    TemplateValidationReport

warnings.filterwarnings('ignore')

# Загрузка данных
def load_data(source, source_type=None, **kwargs) -> pd.DataFrame:
    if isinstance(source, pd.DataFrame):
        print("Данные уже являются DataFrame")
        return source.copy()

    if source_type is None or source_type == 'auto':
        source_type = _detect_source_type(source)

    try:
        if source_type == 'csv':
            return _load_csv(source, **kwargs)
        elif source_type == 'json':
            return _load_json(source, **kwargs)
        elif source_type == 'postgresql':
            return _load_postgresql(source, **kwargs)
        else:
            raise ValueError(f"Неподдерживаемый тип источника: {source_type}")
    except Exception as e:
        print(f"Ошибка при загрузке данных: {e}")
        raise


def _detect_source_type(source):
    if isinstance(source, str):
        if os.path.exists(source):
            ext = os.path.splitext(source)[1].lower()
            if ext == '.csv':
                return 'csv'
            elif ext in ['.json', '.geojson']:
                return 'json'
    if isinstance(source, dict):
        if 'host' in source or 'database' in source or 'user' in source:
            return 'postgresql'
    return 'csv'


def _load_csv(file_path, **kwargs) -> pd.DataFrame:
    try:
        df = pd.read_csv(file_path, encoding='utf-8', **kwargs)
    except UnicodeDecodeError:
        df = pd.read_csv(file_path, encoding='latin-1', **kwargs)
    except Exception:
        df = pd.read_csv(file_path, **kwargs)
    print(f"CSV файл загружен: {len(df)} строк, {len(df.columns)} столбцов")
    return df


def _load_json(file_path, **kwargs) -> pd.DataFrame:
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    if isinstance(data, list):
        df = pd.DataFrame(data)
    elif isinstance(data, dict):
        if all(isinstance(v, list) for v in data.values()):
            df = pd.DataFrame(data)
        else:
            df = pd.DataFrame([data])
    else:
        raise ValueError("Неизвестный формат JSON")
    print(f"JSON файл загружен: {len(df)} строк, {len(df.columns)} столбцов")
    return df


def _load_postgresql(connection_params, **kwargs) -> pd.DataFrame:
    try:
        import psycopg2
    except ImportError:
        raise ImportError("Для работы с PostgreSQL установите psycopg2-binary")

    query = kwargs.get('query')
    table = kwargs.get('table')
    batch_size = kwargs.get('batch_size', 10000)

    if query is None and table is None:
        raise ValueError("Необходимо указать query или table")

    conn = psycopg2.connect(**connection_params)

    try:
        if query:
            chunks = []
            with conn.cursor(name='server_cursor') as cursor:
                cursor.execute(query)
                while True:
                    rows = cursor.fetchmany(batch_size)
                    if not rows:
                        break
                    col_names = [desc[0] for desc in cursor.description]
                    chunk = pd.DataFrame(rows, columns=col_names)
                    chunks.append(chunk)
                    print(f"  Загружено {len(chunk)} строк (всего: {sum(len(c) for c in chunks)})")
            df = pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame()
        else:
            with conn.cursor() as cursor:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                total_rows = cursor.fetchone()[0]

            print(f"Загрузка таблицы '{table}'. Всего строк: {total_rows}")

            if total_rows == 0:
                print(f"Таблица '{table}' пуста")
                return pd.DataFrame()

            chunks = []
            offset = 0
            # TODO: Limit offset оч медленно работает
            while offset < total_rows:
                chunk_query = f"SELECT * FROM {table} LIMIT {batch_size} OFFSET {offset}"
                chunk = pd.read_sql_query(chunk_query, conn)
                if chunk.empty:
                    break
                chunks.append(chunk)
                offset += len(chunk)
                progress = min(100, int((offset / total_rows) * 100))
                print(f"  Прогресс: {progress}% ({offset}/{total_rows})")
            df = pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame()
    except Exception as e:
        print(f"Ошибка при загрузке данных: {e}")
        raise
    finally:
        conn.close()

    print(f"Данные из PostgreSQL загружены: {len(df)} строк, {len(df.columns)} столбцов")
    return df


# Анализ EDA
class DataQualityAnalyzer:
    def __init__(self, df: pd.DataFrame):
        self.df = df.copy()
        self.results = {}
        self._run_full_analysis()

    def _run_full_analysis(self) -> None:
        basic_info = self.get_basic_info()
        missing_values = self.analyze_missing_values()
        data_types = self.analyze_data_types()
        outliers = self.detect_outliers()
        correlation = self.calculate_correlation()
        statistics = self.get_statistics()
        duplicates = self.find_duplicates()
        unique_values = self.analyze_unique_values()

        self.results = {
            'basic_info': basic_info,
            'missing_values': missing_values,
            'data_types': data_types,
            'outliers': outliers,
            'correlation': correlation,
            'statistics': statistics,
            'duplicates': duplicates,
            'unique_values': unique_values,
            'timestamp': datetime.now().isoformat()
        }

    def get_basic_info(self) -> Dict:
        return {
            'rows': len(self.df),
            'columns': len(self.df.columns),
            'memory_usage_mb': round(self.df.memory_usage(deep=True).sum() / (1024 * 1024), 2),
            'column_names': self.df.columns.tolist(),
            'dtypes_summary': {str(k): int(v) for k, v in self.df.dtypes.value_counts().to_dict().items()}
        }

    def analyze_missing_values(self) -> Dict:
        missing = self.df.isnull().sum()
        missing_percent = (missing / len(self.df)) * 100

        details = {}
        for col in self.df.columns:
            if missing[col] > 0:
                details[col] = {
                    'count': int(missing[col]),
                    'percentage': round(missing_percent[col], 2),
                    'type': 'missing_values'
                }

        return {
            'total_missing': int(missing.sum()),
            'total_cells': len(self.df) * len(self.df.columns),
            'missing_percentage': round((missing.sum() / (len(self.df) * len(self.df.columns))) * 100, 2),
            'columns_with_missing': len(details),
            'details': details
        }

    def analyze_data_types(self) -> Dict:
        type_analysis = {}
        recommendations = []

        for col in self.df.columns:
            original_type = self.df[col].dtype
            inferred_type = self._infer_best_type(self.df[col])
            is_mixed = self._check_mixed_types(self.df[col])

            type_analysis[col] = {
                'current_type': str(original_type),
                'inferred_type': inferred_type,
                'is_numeric': pd.api.types.is_numeric_dtype(self.df[col]),
                'is_integer': pd.api.types.is_integer_dtype(self.df[col]),
                'is_float': pd.api.types.is_float_dtype(self.df[col]),
                'is_object': pd.api.types.is_object_dtype(self.df[col]),
                'is_datetime': pd.api.types.is_datetime64_any_dtype(self.df[col]),
                'is_category': pd.api.types.is_categorical_dtype(self.df[col]),
                'is_mixed': is_mixed,
                'unique_count': self.df[col].nunique()
            }

            if original_type == 'object' and inferred_type == 'datetime':
                recommendations.append({
                    'column': col,
                    'recommendation': f'Столбец "{col}" содержит даты в текстовом формате. Рекомендуется преобразовать в datetime'
                })

            if original_type == 'object' and self.df[col].nunique() < len(self.df) * 0.1:
                recommendations.append({
                    'column': col,
                    'recommendation': f'Столбец "{col}" имеет мало уникальных значений ({self.df[col].nunique()}). Рекомендуется преобразовать в категориальный тип'
                })

        return {
            'details': type_analysis,
            'recommendations': recommendations
        }

    def _infer_best_type(self, series: pd.Series) -> str:
        clean_series = series.dropna()
        if len(clean_series) == 0:
            return 'empty'

        try:
            numeric_series = pd.to_numeric(clean_series)
            if numeric_series.dtype == 'int64':
                return 'integer'
            elif numeric_series.dtype == 'float64':
                return 'float'
        except:
            pass

        try:
            pd.to_datetime(clean_series)
            return 'datetime'
        except:
            pass

        return 'object'

    def _check_mixed_types(self, series: pd.Series) -> bool:
        clean_series = series.dropna()
        if len(clean_series) == 0:
            return False

        types = set()
        for val in clean_series.head(100):
            types.add(type(val).__name__)

        return len(types) > 1

    def detect_outliers(self, method: str = 'iqr', threshold: float = 1.5) -> Dict:
        numeric_cols = self.df.select_dtypes(include=[np.number]).columns.tolist()

        if not numeric_cols:
            return {
                'total_outliers': 0,
                'affected_columns': 0,
                'details': {}
            }

        outliers_result = {}
        total_outliers = 0

        for col in numeric_cols:
            clean_data = self.df[col].dropna()

            if len(clean_data) < 3:
                continue

            if clean_data.nunique() <= 1:
                continue

            try:
                if method == 'iqr':
                    outliers_indices, lower_bound, upper_bound = self._detect_outliers_iqr(clean_data, threshold)
                else:
                    outliers_indices, lower_bound, upper_bound = self._detect_outliers_zscore(clean_data, threshold)

                if outliers_indices is not None and len(outliers_indices) > 0:
                    outliers_result[col] = {
                        'count': len(outliers_indices),
                        'percentage': round((len(outliers_indices) / len(clean_data)) * 100, 2),
                        'lower_bound': round(lower_bound, 2) if lower_bound is not None else None,
                        'upper_bound': round(upper_bound, 2) if upper_bound is not None else None,
                        'sample_values': clean_data.iloc[outliers_indices].tolist()[:10]
                    }
                    total_outliers += len(outliers_indices)
            except Exception:
                continue

        return {
            'total_outliers': total_outliers,
            'affected_columns': len(outliers_result),
            'details': outliers_result
        }

    def _detect_outliers_iqr(self, series: pd.Series, threshold: float):
        if len(series) < 4:
            return np.array([]), None, None

        if series.nunique() <= 1:
            return np.array([]), None, None

        Q1 = series.quantile(0.25)
        Q3 = series.quantile(0.75)
        IQR = Q3 - Q1

        if IQR == 0:
            return np.array([]), None, None

        lower_bound = Q1 - threshold * IQR
        upper_bound = Q3 + threshold * IQR

        outliers = series[(series < lower_bound) | (series > upper_bound)]
        return outliers.index.values, lower_bound, upper_bound

    def _detect_outliers_zscore(self, series: pd.Series, threshold: float):
        if len(series) < 3:
            return np.array([]), None, None

        if series.nunique() <= 1:
            return np.array([]), None, None

        if series.std() == 0:
            return np.array([]), None, None

        try:
            z_scores = np.abs(stats.zscore(series))
            outliers = series[z_scores > threshold]
            return outliers.index.values, None, None
        except Exception:
            return np.array([]), None, None

    def calculate_correlation(self, method: str = 'pearson', threshold: float = 0.7) -> Dict:
        numeric_df = self.df.select_dtypes(include=[np.number])

        if len(numeric_df.columns) < 2:
            return {
                'strong_correlations': [],
                'perfect_correlations': []
            }

        try:
            corr_matrix = numeric_df.corr(method=method)
        except Exception:
            return {
                'strong_correlations': [],
                'perfect_correlations': []
            }

        strong_correlations = []
        perfect_correlations = []

        for i in range(len(corr_matrix.columns)):
            for j in range(i + 1, len(corr_matrix.columns)):
                corr_value = corr_matrix.iloc[i, j]
                if pd.isna(corr_value):
                    continue

                corr_info = {
                    'col1': corr_matrix.columns[i],
                    'col2': corr_matrix.columns[j],
                    'correlation': round(corr_value, 3)
                }

                if abs(corr_value) == 1.0:
                    perfect_correlations.append(corr_info)
                elif abs(corr_value) >= threshold:
                    corr_info['type'] = 'strong_positive' if corr_value > 0 else 'strong_negative'
                    strong_correlations.append(corr_info)

        return {
            'strong_correlations': strong_correlations,
            'perfect_correlations': perfect_correlations
        }

    def get_statistics(self) -> Dict:
        numeric_stats = {}
        numeric_cols = self.df.select_dtypes(include=[np.number]).columns

        for col in numeric_cols:
            clean_data = self.df[col].dropna()
            if len(clean_data) > 0:
                numeric_stats[col] = {
                    'count': len(clean_data),
                    'mean': round(clean_data.mean(), 2),
                    'std': round(clean_data.std(), 2),
                    'min': round(clean_data.min(), 2),
                    'q1': round(clean_data.quantile(0.25), 2),
                    'median': round(clean_data.median(), 2),
                    'q3': round(clean_data.quantile(0.75), 2),
                    'max': round(clean_data.max(), 2),
                    'skewness': round(clean_data.skew(), 3),
                    'kurtosis': round(clean_data.kurtosis(), 3)
                }

        return {
            'numeric': numeric_stats,
            'total_numeric_columns': len(numeric_stats)
        }

    def find_duplicates(self) -> Dict:
        duplicates_mask = self.df.duplicated()
        duplicate_count = duplicates_mask.sum()

        return {
            'count': int(duplicate_count),
            'percentage': round((duplicate_count / len(self.df)) * 100, 2),
            'has_duplicates': duplicate_count > 0
        }

    def analyze_unique_values(self) -> Dict:
        potential_primary_keys = []

        for col in self.df.columns:
            unique_count = self.df[col].nunique()
            if unique_count == len(self.df):
                potential_primary_keys.append(col)

        return {
            'potential_primary_keys': potential_primary_keys,
            'total_potential_pks': len(potential_primary_keys)
        }

    def _generate_recommendations(self) -> List[str]:
        recommendations = []

        missing = self.results['missing_values']
        if missing['total_missing'] > 0:
            recommendations.append(
                f"Удалить или заполнить {missing['total_missing']} пропусков "
                f"({missing['missing_percentage']}% от всех данных)"
            )
            for col, data in missing['details'].items():
                if data['percentage'] > 15:
                    recommendations.append(
                        f"Столбец '{col}' содержит {data['count']} пропусков ({data['percentage']}%). "
                        f"Рекомендуется заполнить медианой/модой или удалить строки"
                    )

        duplicates = self.results['duplicates']
        if duplicates.get('count', 0) > 0:
            recommendations.append(
                f"Удалить {duplicates['count']} дубликатов "
                f"({duplicates['percentage']}% от всех строк)"
            )

        outliers = self.results['outliers']
        if outliers['total_outliers'] > 0:
            recommendations.append(
                f"Обработать {outliers['total_outliers']} выбросов "
                f"в {outliers['affected_columns']} столбцах"
            )
            for col, data in outliers['details'].items():
                if data['percentage'] > 5:
                    recommendations.append(
                        f"Столбец '{col}' содержит {data['count']} выбросов ({data['percentage']}%). "
                        f"Значения за пределами [{data['lower_bound']}, {data['upper_bound']}] "
                        f"считаются аномальными"
                    )

        data_types = self.results.get('data_types', {})
        for rec in data_types.get('recommendations', []):
            recommendations.append(rec['recommendation'])

        numeric_stats = self.results['statistics']['numeric']
        for col, stats in numeric_stats.items():
            if abs(stats['skewness']) > 1:
                recommendations.append(
                    f"Столбец '{col}' имеет сильный перекос (skewness = {stats['skewness']:.2f}). "
                    f"Рекомендуется использовать логарифмическое преобразование"
                )

        return recommendations

    def export_results(self, format: str = 'pydantic'):
        if format == 'pydantic':
            return self._to_pydantic_model()
        elif format == 'dict':
            return self.results
        elif format == 'json':
            return json.dumps(self.results, default=str, ensure_ascii=False, indent=2)
        else:
            raise ValueError(f"Неподдерживаемый формат: {format}")

    def _to_pydantic_model(self) -> EDAReport:
        results = self.results

        missing_details = []
        for col, data in results['missing_values']['details'].items():
            missing_details.append(ColumnStat(
                name=col,
                data_type=str(self.df[col].dtype),
                null_count=data['count'],
                null_percentage=data['percentage'],
                unique_count=self.df[col].nunique(),
                duplicate_count=int(self.df[col].duplicated().sum()),
                sample_values=self.df[col].dropna().head(5).tolist()
            ))

        outlier_details = []
        for col, data in results['outliers']['details'].items():
            outlier_details.append(OutlierInfo(
                column=col,
                count=data['count'],
                percentage=data['percentage'],
                lower_bound=data.get('lower_bound'),
                upper_bound=data.get('upper_bound'),
                sample_values=data.get('sample_values', [])
            ))

        strong_correlations = []
        for c in results['correlation'].get('strong_correlations', []):
            strong_correlations.append(CorrelationInfo(
                col1=c['col1'],
                col2=c['col2'],
                correlation=c['correlation'],
                type=c.get('type', 'strong_positive')
            ))

        perfect_correlations = []
        for c in results['correlation'].get('perfect_correlations', []):
            perfect_correlations.append(CorrelationInfo(
                col1=c['col1'],
                col2=c['col2'],
                correlation=c['correlation'],
                type='perfect'
            ))

        numeric_stats = {}
        for col, data in results['statistics']['numeric'].items():
            numeric_stats[col] = NumericStats(**data)

        categorical_columns = [
            col for col in self.df.columns
            if self.df[col].dtype == 'object' or str(self.df[col].dtype) == 'category'
        ]

        recommendations = self._generate_recommendations()

        return EDAReport(
            total_rows=results['basic_info']['rows'],
            total_columns=results['basic_info']['columns'],
            memory_usage_mb=results['basic_info']['memory_usage_mb'],
            timestamp=datetime.now(),
            total_missing=results['missing_values']['total_missing'],
            missing_percentage=results['missing_values']['missing_percentage'],
            columns_with_missing=results['missing_values']['columns_with_missing'],
            missing_details=missing_details,
            duplicate_count=results['duplicates']['count'],
            duplicate_percentage=results['duplicates']['percentage'],
            duplicate_columns=self.df.columns.tolist(),
            total_outliers=results['outliers']['total_outliers'],
            outlier_columns=results['outliers']['affected_columns'],
            outlier_details=outlier_details,
            strong_correlations=strong_correlations,
            perfect_correlations=perfect_correlations,
            numeric_stats=numeric_stats,
            categorical_columns=categorical_columns,
            potential_primary_keys=results['unique_values']['potential_primary_keys'],
            recommendations=recommendations
        )


# Проверка по шаблонам
class TemplateValidator:
    def __init__(self, df: pd.DataFrame, templates_dir: str = "checks/templates", checks_dir: str = "checks"):
        self.df = df.copy()
        self.templates_dir = templates_dir
        self.checks_dir = checks_dir
        self.checks_definitions = self._load_all_checks()
        self.templates = self._load_all_templates()

    @staticmethod
    def _load_json_file(path: str) -> Dict:
        if not os.path.exists(path):
            raise FileNotFoundError(f"Файл не найден: {path}")
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _load_all_checks(self) -> Dict[str, Dict]:
        checks = {}
        if not os.path.exists(self.checks_dir):
            print(f"Директория с проверками не найдена: {self.checks_dir}")
            return checks

        for filename in os.listdir(self.checks_dir):
            if filename.endswith(".json"):
                path = os.path.join(self.checks_dir, filename)
                data = self._load_json_file(path)
                real_type = data["type"]

                if real_type == "required":
                    checks["required_fields"] = data
                elif real_type == "regex" and "email" in filename:
                    checks["regex_email"] = data
                elif real_type == "regex" and "phone" in filename:
                    checks["regex_phone"] = data
                elif real_type == "date":
                    checks["date_validation"] = data
                elif real_type == "logic":
                    checks["logical_rules"] = data
                else:
                    checks[real_type] = data
        return checks

    def _load_all_templates(self) -> Dict[str, Dict]:
        templates = {}
        if not os.path.exists(self.templates_dir):
            print(f"Директория с шаблонами не найдена: {self.templates_dir}")
            return templates

        for filename in os.listdir(self.templates_dir):
            if filename.endswith(".json"):
                path = os.path.join(self.templates_dir, filename)
                data = self._load_json_file(path)
                templates[filename.replace(".json", "")] = data
        return templates

    def list_available_templates(self) -> List[str]:
        return list(self.templates.keys())

    def validate_by_template(self, template_name: str, reference_data: Optional[Dict[str, pd.DataFrame]] = None) -> TemplateValidationReport:
        if template_name not in self.templates:
            raise ValueError(f"Шаблон '{template_name}' не найден. Доступные: {self.list_available_templates()}")

        template = self.templates[template_name]
        checks_to_run = template.get("checks", [])
        results = []

        for check_type in checks_to_run:
            if check_type not in self.checks_definitions:
                results.append(CheckResult(
                    check_name=f"Неизвестная проверка: {check_type}",
                    check_type=check_type,
                    status="WARNING",
                    message="Определение проверки не найдено"
                ))
                continue

            check_def = self.checks_definitions[check_type]
            result = self._run_single_check(check_def, reference_data)
            results.append(result)

        passed = sum(1 for r in results if r.status == "PASSED")
        failed = sum(1 for r in results if r.status == "FAILED")
        warnings = sum(1 for r in results if r.status == "WARNING")

        return TemplateValidationReport(
            template_name=template["template_name"],
            description=template["description"],
            total_checks=len(results),
            passed=passed,
            failed=failed,
            warnings=warnings,
            results=results
        )

    def _run_single_check(self, check: Dict, reference_data: Optional[Dict[str, pd.DataFrame]]) -> CheckResult:
        check_type = check["type"]
        name = check.get("name", check_type)
        desc = check.get("description", "")

        try:
            if check_type == "required":
                return self._check_required_fields(check)
            elif check_type == "duplicates":
                return self._check_duplicates(check)
            elif check_type == "unique":
                return self._check_unique(check)
            elif check_type == "regex":
                return self._check_regex(check)
            elif check_type == "allowed_values":
                return self._check_allowed_values(check)
            elif check_type == "date":
                return self._check_date(check)
            elif check_type == "range":
                return self._check_range(check)
            elif check_type == "logic":
                return self._check_logic(check)
            elif check_type == "foreign_key":
                return self._check_foreign_key(check, reference_data)
            else:
                return CheckResult(
                    check_name=name,
                    check_type=check_type,
                    status="WARNING",
                    message=f"Тип проверки не реализован: {check_type}"
                )
        except Exception as e:
            return CheckResult(
                check_name=name,
                check_type=check_type,
                status="FAILED",
                message=f"Ошибка выполнения: {str(e)}"
            )

    def _check_required_fields(self, check: Dict) -> CheckResult:
        columns = check["columns"]
        missing_cols = [col for col in columns if col not in self.df.columns]
        if missing_cols:
            return CheckResult(
                check_name=check["name"],
                check_type="required",
                status="FAILED",
                message=f"Отсутствуют столбцы: {', '.join(missing_cols)}"
            )

        null_counts = self.df[columns].isnull().sum()
        total_null = null_counts.sum()
        error_rows = self.df.index[self.df[columns].isnull().any(axis=1)].tolist()

        if total_null == 0:
            return CheckResult(
                check_name=check["name"],
                check_type="required",
                status="PASSED",
                message="Все обязательные поля заполнены"
            )
        else:
            return CheckResult(
                check_name=check["name"],
                check_type="required",
                status="FAILED",
                message=f"Найдено {total_null} пустых значений в обязательных полях",
                error_count=int(total_null),
                error_rows=error_rows[:100],
                details=null_counts.to_dict()
            )

    def _check_duplicates(self, check: Dict) -> CheckResult:
        columns = check.get("columns", None)
        if columns:
            if any(col not in self.df.columns for col in columns):
                return CheckResult(
                    check_name=check["name"],
                    check_type="duplicates",
                    status="FAILED",
                    message=f"Некоторые столбцы отсутствуют: {', '.join([c for c in columns if c not in self.df.columns])}"
                )
            mask = self.df.duplicated(subset=columns, keep=False)
        else:
            mask = self.df.duplicated(keep=False)

        count = mask.sum()
        rows = self.df.index[mask].tolist()

        if count == 0:
            return CheckResult(
                check_name=check["name"],
                check_type="duplicates",
                status="PASSED",
                message="Дубликаты не найдены"
            )
        return CheckResult(
            check_name=check["name"],
            check_type="duplicates",
            status="FAILED",
            message=f"Найдено {count} дублирующихся записей",
            error_count=int(count),
            error_rows=rows[:100]
        )

    def _check_unique(self, check: Dict) -> CheckResult:
        col = check["column"]
        if col not in self.df.columns:
            return CheckResult(
                check_name=check["name"],
                check_type="unique",
                status="FAILED",
                message=f"Столбец {col} отсутствует в наборе данных"
            )
        duplicates = self.df[col].duplicated(keep=False)
        count = duplicates.sum()
        rows = self.df.index[duplicates].tolist()

        if count == 0:
            return CheckResult(
                check_name=check["name"],
                check_type="unique",
                status="PASSED",
                message=f"Все значения в столбце {col} уникальны"
            )
        return CheckResult(
            check_name=check["name"],
            check_type="unique",
            status="FAILED",
            message=f"Найдено {count} неуникальных значений в столбце {col}",
            error_count=int(count),
            error_rows=rows[:100]
        )

    def _check_regex(self, check: Dict) -> CheckResult:
        col = check["column"]
        pattern = check["pattern"]
        if col not in self.df.columns:
            return CheckResult(
                check_name=check["name"],
                check_type="regex",
                status="FAILED",
                message=f"Столбец {col} отсутствует в наборе данных"
            )
        series = self.df[col].dropna().astype(str)
        invalid = ~series.str.match(pattern, na=False)
        count = invalid.sum()
        rows = series.index[invalid].tolist()

        if count == 0:
            return CheckResult(
                check_name=check["name"],
                check_type="regex",
                status="PASSED",
                message=f"Все значения в столбце {col} соответствуют формату"
            )
        return CheckResult(
            check_name=check["name"],
            check_type="regex",
            status="FAILED",
            message=f"Найдено {count} значений с неверным форматом в столбце {col}",
            error_count=int(count),
            error_rows=rows[:100]
        )

    def _check_allowed_values(self, check: Dict) -> CheckResult:
        col = check["column"]
        allowed = set(check["values"])
        if col not in self.df.columns:
            return CheckResult(
                check_name=check["name"],
                check_type="allowed_values",
                status="FAILED",
                message=f"Столбец {col} отсутствует в наборе данных"
            )
        series = self.df[col].dropna()
        invalid = ~series.isin(allowed)
        count = invalid.sum()
        rows = series.index[invalid].tolist()
        invalid_values = series[invalid].unique().tolist()

        if count == 0:
            return CheckResult(
                check_name=check["name"],
                check_type="allowed_values",
                status="PASSED",
                message=f"Все значения в столбце {col} из списка допустимых"
            )
        return CheckResult(
            check_name=check["name"],
            check_type="allowed_values",
            status="FAILED",
            message=f"Найдено {count} недопустимых значений в столбце {col}",
            error_count=int(count),
            error_rows=rows[:100],
            details={"invalid_values": invalid_values, "allowed": list(allowed)}
        )

    def _check_date(self, check: Dict) -> CheckResult:
        col = check["column"]
        fmt = check["format"]
        if col not in self.df.columns:
            return CheckResult(
                check_name=check["name"],
                check_type="date",
                status="FAILED",
                message=f"Столбец {col} отсутствует в наборе данных"
            )
        try:
            pd.to_datetime(self.df[col], format=fmt, errors="raise")
            return CheckResult(
                check_name=check["name"],
                check_type="date",
                status="PASSED",
                message=f"Все даты в столбце {col} соответствуют формату {fmt}"
            )
        except Exception as e:
            errors = pd.to_datetime(self.df[col], format=fmt, errors="coerce").isna()
            count = errors.sum()
            rows = self.df.index[errors].tolist()
            return CheckResult(
                check_name=check["name"],
                check_type="date",
                status="FAILED",
                message=f"Найдено {count} дат с неверным форматом в столбце {col}",
                error_count=int(count),
                error_rows=rows[:100]
            )

    def _check_range(self, check: Dict) -> CheckResult:
        col = check["column"]
        min_val = check.get("min")
        max_val = check.get("max")
        if col not in self.df.columns:
            return CheckResult(
                check_name=check["name"],
                check_type="range",
                status="FAILED",
                message=f"Столбец {col} отсутствует в наборе данных"
            )
        series = pd.to_numeric(self.df[col], errors="coerce").dropna()
        mask = pd.Series([True] * len(series), index=series.index)
        if min_val is not None:
            mask &= series >= min_val
        if max_val is not None:
            mask &= series <= max_val
        invalid = ~mask
        count = invalid.sum()
        rows = series.index[invalid].tolist()

        if count == 0:
            return CheckResult(
                check_name=check["name"],
                check_type="range",
                status="PASSED",
                message=f"Все значения в столбце {col} в диапазоне [{min_val}, {max_val}]"
            )
        else:
            return CheckResult(
                check_name=check["name"],
                check_type="range",
                status="FAILED",
                message=f"Найдено {count} значений вне диапазона в столбце {col}",
                error_count=int(count),
                error_rows=rows[:100]
            )

    def _check_logic(self, check: Dict) -> CheckResult:
        rule = check["rule"]
        try:
            mask = self.df.eval(rule, engine="python")
            invalid = ~mask
            count = invalid.sum()
            rows = self.df.index[invalid].tolist()
            if count == 0:
                return CheckResult(
                    check_name=check["name"],
                    check_type="logic",
                    status="PASSED",
                    message=f"Логическое правило выполнено для всех записей: {rule}"
                )
            return CheckResult(
                check_name=check["name"],
                check_type="logic",
                status="FAILED",
                message=f"Правило {rule} нарушено в {count} строках",
                error_count=int(count),
                error_rows=rows[:100]
            )
        except Exception as e:
            return CheckResult(
                check_name=check["name"],
                check_type="logic",
                status="FAILED",
                message=f"Невозможно выполнить правило: {str(e)}"
            )

    def _check_foreign_key(self, check: Dict, reference_data: Optional[Dict[str, pd.DataFrame]]) -> CheckResult:
        col = check["column"]
        ref_table = check["reference_table"]
        ref_col = check["reference_column"]

        if not reference_data or ref_table not in reference_data:
            return CheckResult(
                check_name=check["name"],
                check_type="foreign_key",
                status="WARNING",
                message=f"Таблица-справочник {ref_table} не передана для проверки"
            )

        if col not in self.df.columns:
            return CheckResult(
                check_name=check["name"],
                check_type="foreign_key",
                status="FAILED",
                message=f"Столбец {col} отсутствует в наборе данных"
            )

        ref_values = set(reference_data[ref_table][ref_col].dropna())
        values = self.df[col].dropna()
        invalid = ~values.isin(ref_values)
        count = invalid.sum()
        rows = values.index[invalid].tolist()

        if count == 0:
            return CheckResult(
                check_name=check["name"],
                check_type="foreign_key",
                status="PASSED",
                message=f"Все значения в столбце {col} существуют в таблице {ref_table}.{ref_col}"
            )
        return CheckResult(
            check_name=check["name"],
            check_type="foreign_key",
            status="FAILED",
            message=f"Найдено {count} ссылок на несуществующие записи",
            error_count=int(count),
            error_rows=rows[:100]
        )
