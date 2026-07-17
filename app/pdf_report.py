# app/pdf_report.py
from __future__ import annotations
import io
import os

import platform
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.pdfbase.ttfonts import TTFont


def sanitize_data(data: Any) -> Any:
    """Рекурсивно очищает данные от проблемных значений"""
    if data is None:
        return None
    if isinstance(data, dict):
        return {str(k).strip(): sanitize_data(v) for k, v in data.items() if v is not None}
    elif isinstance(data, list):
        return [sanitize_data(item) for item in data if item is not None]
    elif isinstance(data, str):
        return data.strip()
    elif isinstance(data, float):
        return float(data) if not (isinstance(data, float) and (data != data or data in (float('inf'), float('-inf')))) else None
    return data


def safe_get(data: Any, key: str, default: Any = None) -> Any:
    """Безопасное получение значения из словаря или объекта"""
    if isinstance(data, dict):
        return data.get(key, default)
    return default


def safe_get_nested(data: Any, keys: List[str], default: Any = None) -> Any:
    """Безопасное получение вложенного значения"""
    if not isinstance(data, dict):
        return default
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current is None:
            return default
    return current


from reportlab.pdfbase import pdfmetrics


def _find_and_register_fonts():
    system = platform.system()
    candidates = []
    if system == "Windows":
        fonts_dir = Path(os.environ.get("WINDIR", "C:\\Windows")) / "Fonts"
        candidates = [
            fonts_dir / "arial.ttf",
            fonts_dir / "segoeui.ttf",
            fonts_dir / "calibri.ttf",
            fonts_dir / "DejaVuSans.ttf"
        ]
    elif system == "Darwin":
        candidates = [
            Path("/Library/Fonts/Arial.ttf"),
            Path("/System/Library/Fonts/Helvetica.ttf")
        ]
    else:
        candidates = [
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf")
        ]

    for path in candidates:
        if path.exists():
            try:
                from matplotlib import font_manager
                font_manager.fontManager.addfont(str(path))
                prop = font_manager.FontProperties(fname=str(path))
                plt.rcParams['font.family'] = prop.get_name()
                plt.rcParams['axes.unicode_minus'] = False

                pdfmetrics.registerFont(TTFont('Uni', str(path)))
                pdfmetrics.registerFont(TTFont('Uni-Bold', str(path)))
                pdfmetrics.registerFont(TTFont('Uni-Italic', str(path)))
                return True
            except Exception:
                continue
    return False


_fonts_registered = _find_and_register_fonts()


def _ensure_dict(data: Any) -> Dict:
    """Преобразует данные в словарь, если они не являются словарём"""
    if isinstance(data, dict):
        return data
    return {}


def _ensure_list(data: Any) -> List:
    """Преобразует данные в список, если они не являются списком"""
    if isinstance(data, list):
        return data
    return []


def _make_types_pie(detected_types: Any) -> bytes:
    """Создаёт круговую диаграмму типов данных"""
    if not detected_types:
        return _empty_chart("Нет данных о типах")
    
    counts: Dict[str, int] = {}
    
    # Если это словарь
    if isinstance(detected_types, dict):
        for t in detected_types.values():
            if isinstance(t, str):
                counts[t] = counts.get(t, 0) + 1
            else:
                counts[str(t)] = counts.get(str(t), 0) + 1
    # Если это список
    elif isinstance(detected_types, list):
        for item in detected_types:
            if isinstance(item, dict):
                t = item.get('data_type', 'unknown')
                if isinstance(t, str):
                    counts[t] = counts.get(t, 0) + 1
                else:
                    counts[str(t)] = counts.get(str(t), 0) + 1
            elif isinstance(item, str):
                counts[item] = counts.get(item, 0) + 1
    # Если это строка
    elif isinstance(detected_types, str):
        return _empty_chart("Нет данных о типах")
    
    if not counts:
        return _empty_chart("Нет данных о типах")
    
    fig, ax = plt.subplots(figsize=(6, 4))
    colors_list = ['#0366d6', '#28a745', '#ffc107', '#17a2b8', '#6f42c1', '#dc3545', '#fd7e14']
    ax.pie(
        list(counts.values()),
        labels=list(counts.keys()),
        autopct='%1.0f%%',
        colors=colors_list[:len(counts)],
        startangle=90
    )
    ax.set_title("Распределение типов колонок", pad=15, fontsize=12, fontweight='bold')
    return _fig_to_bytes(fig)


def _make_missing_bar(recommendations: Any) -> bytes:
    """Создаёт столбчатую диаграмму пропусков"""
    if not recommendations:
        return _empty_chart("Пропусков не обнаружено")
    
    # Преобразуем в список если нужно
    rec_list = _ensure_list(recommendations)
    
    # Фильтруем только словари с check_type == "missing_values"
    missing = []
    for r in rec_list:
        if isinstance(r, dict) and r.get("check_type") == "missing_values":
            missing.append(r)
        elif isinstance(r, dict) and "column" in r and "suggested_action" in r:
            # Пытаемся определить, что это про пропуски
            action = r.get("suggested_action", {})
            if isinstance(action, dict) and action.get("action_type") == "fill_missing":
                missing.append(r)
    
    if not missing:
        return _empty_chart("Пропусков не обнаружено")
    
    # Сортируем по количеству затронутых строк
    missing.sort(
        key=lambda r: safe_get_nested(r, ["suggested_action", "affected_rows"], 0),
        reverse=True
    )
    top = missing[:5]
    
    labels = []
    values = []
    for r in top:
        if isinstance(r, dict):
            labels.append(str(r.get("column", "?")))
            values.append(safe_get_nested(r, ["suggested_action", "affected_rows"], 0))
    
    if not labels or all(v == 0 for v in values):
        return _empty_chart("Пропусков не обнаружено")
    
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar(labels, values, color='#dc3545', edgecolor='white')
    ax.set_title("Топ-5 колонок по количеству пропусков", pad=15, fontsize=12, fontweight='bold')
    ax.set_ylabel("Количество пропусков")
    ax.grid(axis='y', alpha=0.3)
    
    if values:
        max_val = max(values) or 1
        for bar, val in zip(ax.patches, values):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + max_val * 0.02,
                str(val),
                ha='center',
                va='bottom',
                fontsize=9
            )
    
    plt.xticks(rotation=20, ha='right')
    plt.tight_layout()
    return _fig_to_bytes(fig)


def _make_outliers_bar(outlier_details: Any) -> bytes:
    """Создаёт столбчатую диаграмму выбросов"""
    if not outlier_details:
        return _empty_chart("Выбросов не обнаружено")
    
    out_list = _ensure_list(outlier_details)
    
    valid_outliers = []
    for o in out_list:
        if isinstance(o, dict):
            valid_outliers.append(o)
    
    if not valid_outliers:
        return _empty_chart("Выбросов не обнаружено")
    
    sorted_out = sorted(
        valid_outliers,
        key=lambda o: o.get("count", 0) if isinstance(o, dict) else 0,
        reverse=True
    )[:7]
    
    labels = []
    values = []
    for o in sorted_out:
        if isinstance(o, dict):
            labels.append(str(o.get("column", "?")))
            values.append(o.get("count", 0))
    
    if not labels or all(v == 0 for v in values):
        return _empty_chart("Выбросов не обнаружено")
    
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar(labels, values, color='#fd7e14', edgecolor='white')
    ax.set_title("Выбросы по колонкам", pad=15, fontsize=12, fontweight='bold')
    ax.set_ylabel("Количество выбросов")
    ax.grid(axis='y', alpha=0.3)
    plt.xticks(rotation=20, ha='right')
    plt.tight_layout()
    return _fig_to_bytes(fig)


def _make_validation_pie(validation: Any) -> bytes:
    """Создаёт круговую диаграмму результатов валидации"""
    if not validation:
        return _empty_chart("Валидация не проводилась")
    
    if not isinstance(validation, dict):
        return _empty_chart("Валидация не проводилась")
    
    passed = validation.get("passed", 0)
    failed = validation.get("failed", 0)
    warnings = validation.get("warnings", 0)
    
    if passed + failed + warnings == 0:
        return _empty_chart("Нет результатов валидации")
    
    fig, ax = plt.subplots(figsize=(5, 4))
    labels, values, colors_list = [], [], []
    if passed:
        labels.append("Пройдено")
        values.append(passed)
        colors_list.append('#28a745')
    if failed:
        labels.append("Ошибки")
        values.append(failed)
        colors_list.append('#dc3545')
    if warnings:
        labels.append("Предупреждения")
        values.append(warnings)
        colors_list.append('#ffc107')
    
    ax.pie(values, labels=labels, autopct='%1.0f%%', colors=colors_list, startangle=90)
    ax.set_title("Результаты валидации", pad=15, fontsize=12, fontweight='bold')
    return _fig_to_bytes(fig)


def _empty_chart(message: str) -> bytes:
    """Создаёт пустую диаграмму с сообщением"""
    fig, ax = plt.subplots(figsize=(6, 3))
    ax.text(0.5, 0.5, message, ha='center', va='center', fontsize=12, color='#6a737d', transform=ax.transAxes)
    ax.set_axis_off()
    return _fig_to_bytes(fig)


def _fig_to_bytes(fig) -> bytes:
    """Сохраняет фигуру matplotlib в bytes"""
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=150, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    buf.seek(0)
    return buf.read()


# === ГЕНЕРАЦИЯ PDF ЧЕРЕЗ REPORTLAB ===
def generate_report_pdf(task: Any, filename: str) -> bytes:
    """Генерирует PDF-отчёт с защитой от всех типов ошибок"""
    # Безопасная очистка данных
    clean_task = sanitize_data(task)
    
    # Безопасное извлечение analysis и validation
    analysis = {}
    validation = {}
    
    if isinstance(clean_task, dict):
        analysis = clean_task.get("analysis")
        validation = clean_task.get("validation")
    
    # Если analysis не словарь, пытаемся преобразовать
    if not isinstance(analysis, dict):
        if isinstance(analysis, str):
            try:
                import json
                analysis = json.loads(analysis)
                if not isinstance(analysis, dict):
                    analysis = {}
            except:
                analysis = {}
        else:
            analysis = {}
    
    # Если validation не словарь
    if not isinstance(validation, dict):
        validation = {}

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=50,
        leftMargin=50,
        topMargin=50,
        bottomMargin=50
    )
    story = []

    # Стили
    styles = getSampleStyleSheet()
    font_name = 'Uni' if _fonts_registered else 'Helvetica'
    font_bold = 'Uni-Bold' if _fonts_registered else 'Helvetica-Bold'

    title_style = ParagraphStyle(
        name='Title',
        fontName=font_bold,
        fontSize=22,
        textColor=colors.HexColor('#1a1a2e'),
        spaceAfter=24,
        alignment=1,
        backColor=colors.HexColor('#f0f4f8'),
        borderPadding=(20, 20, 20, 20),
        borderRadius=8
    )
    h1_style = ParagraphStyle(
        name='H1',
        fontName=font_bold,
        fontSize=16,
        textColor=colors.HexColor('#2c3e50'),
        spaceAfter=12,
        spaceBefore=18
    )
    h2_style = ParagraphStyle(
        name='H2',
        fontName=font_bold,
        fontSize=13,
        textColor=colors.HexColor('#2c3e50'),
        spaceAfter=8,
        spaceBefore=12
    )
    normal_style = ParagraphStyle(
        name='Normal',
        fontName=font_name,
        fontSize=10,
        textColor=colors.HexColor('#2c3e50'),
        leading=14
    )
    small_style = ParagraphStyle(
        name='Small',
        fontName=font_name,
        fontSize=8,
        textColor=colors.HexColor('#7f8c8d'),
        leading=11
    )

    # --- Титульная страница ---
    story.append(Spacer(1, 80))
    story.append(Paragraph("ОТЧЁТ ПО КАЧЕСТВУ ДАННЫХ", title_style))
    story.append(Spacer(1, 30))

    template_id = clean_task.get("template_id") if isinstance(clean_task, dict) else "Не использован"
    
    info_data = [
        ["Наименование отчёта:", f"Анализ данных — {filename}"],
        ["Дата формирования:", datetime.now().strftime('%d.%m.%Y %H:%M')],
        ["Имя файла:", filename],
        ["Шаблон проверки:", template_id if template_id else "Не использован"],
    ]
    t_info = Table(info_data, colWidths=[150, 300])
    t_info.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), font_bold),
        ('FONTNAME', (1, 0), (1, -1), font_name),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LINEBELOW', (0, -1), (-1, -1), 0.5, colors.HexColor('#e0e0e0')),
    ]))
    story.append(t_info)
    story.append(PageBreak())

    # --- 1. Общая статистика ---
    story.append(Paragraph("1. ОБЩАЯ СТАТИСТИКА", h1_style))

    # Безопасное получение значений
    total_rows = analysis.get("total_rows", 0) if isinstance(analysis, dict) else 0
    total_columns = analysis.get("total_columns", 0) if isinstance(analysis, dict) else 0
    memory_usage_mb = analysis.get("memory_usage_mb", 0) if isinstance(analysis, dict) else 0
    total_missing = analysis.get("total_missing", 0) if isinstance(analysis, dict) else 0
    missing_percentage = analysis.get("missing_percentage", 0) if isinstance(analysis, dict) else 0
    duplicate_count = analysis.get("duplicate_count", 0) if isinstance(analysis, dict) else 0
    duplicate_percentage = analysis.get("duplicate_percentage", 0) if isinstance(analysis, dict) else 0
    total_outliers = analysis.get("total_outliers", 0) if isinstance(analysis, dict) else 0

    metrics = [
        ["Показатель", "Значение"],
        ["Количество строк", str(total_rows)],
        ["Количество столбцов", str(total_columns)],
        ["Объём данных в памяти", f"{float(memory_usage_mb):.2f} MB" if memory_usage_mb else "0 MB"],
        ["Всего пропусков", f"{total_missing} ({float(missing_percentage):.1f}%)"],
        ["Дубликаты", f"{duplicate_count} ({float(duplicate_percentage):.1f}%)"],
        ["Всего выбросов", str(total_outliers)],
    ]
    t_metrics = Table(metrics, colWidths=[150, 150])
    t_metrics.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), font_name),
        ('FONTNAME', (0, 0), (0, -1), font_bold),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2c3e50')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e0e0e0')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
    ]))
    story.append(t_metrics)
    story.append(Spacer(1, 15))

    # --- 2. Графики ---
    story.append(PageBreak())
    story.append(Paragraph("2. ВИЗУАЛИЗАЦИЯ", h1_style))

    # Получаем detected_types из missing_details
    missing_details = analysis.get("missing_details", []) if isinstance(analysis, dict) else []
    detected_types = {}
    if isinstance(missing_details, list):
        for item in missing_details:
            if isinstance(item, dict):
                name = item.get("name", "")
                data_type = item.get("data_type", "unknown")
                if name:
                    detected_types[name] = data_type

    if detected_types:
        story.append(Paragraph("2.1. Распределение типов данных", h2_style))
        story.append(Image(io.BytesIO(_make_types_pie(detected_types)), width=380, height=240))
        story.append(Spacer(1, 15))
    else:
        # Пробуем получить detected_types напрямую
        direct_types = analysis.get("detected_types") if isinstance(analysis, dict) else None
        if direct_types:
            story.append(Paragraph("2.1. Распределение типов данных", h2_style))
            story.append(Image(io.BytesIO(_make_types_pie(direct_types)), width=380, height=240))
            story.append(Spacer(1, 15))

    recommendations = analysis.get("recommendations", []) if isinstance(analysis, dict) else []
    if recommendations and isinstance(recommendations, list) and len(recommendations) > 0:
        # Проверяем, есть ли реальные данные для графика
        has_data = False
        for r in recommendations:
            if isinstance(r, dict) and r.get("check_type") == "missing_values":
                if r.get("suggested_action", {}).get("affected_rows", 0) > 0:
                    has_data = True
                    break
        if has_data:
            story.append(Paragraph("2.2. Пропуски в данных", h2_style))
            story.append(Image(io.BytesIO(_make_missing_bar(recommendations)), width=420, height=240))
            story.append(Spacer(1, 15))

    outlier_details = analysis.get("outlier_details", []) if isinstance(analysis, dict) else []
    if outlier_details and isinstance(outlier_details, list) and len(outlier_details) > 0:
        has_outliers = False
        for o in outlier_details:
            if isinstance(o, dict) and o.get("count", 0) > 0:
                has_outliers = True
                break
        if has_outliers:
            story.append(Paragraph("2.3. Выбросы в данных", h2_style))
            story.append(Image(io.BytesIO(_make_outliers_bar(outlier_details)), width=420, height=240))
            story.append(Spacer(1, 15))

    # --- 3. Детали по колонкам ---
    story.append(PageBreak())
    story.append(Paragraph("3. ДЕТАЛЬНАЯ ИНФОРМАЦИЯ ПО КОЛОНКАМ", h1_style))

    if missing_details and isinstance(missing_details, list) and len(missing_details) > 0:
        story.append(Paragraph("3.1. Статистика по каждой колонке", h2_style))
        table_data = [["№", "Колонка", "Тип", "Пропуски", "%", "Уникальных", "Дубликатов"]]
        for i, row in enumerate(missing_details, 1):
            if isinstance(row, dict):
                table_data.append([
                    str(i),
                    str(row.get("name", ""))[:30],
                    str(row.get("data_type", ""))[:15],
                    str(row.get("null_count", 0)),
                    f"{float(row.get('null_percentage', 0)):.1f}%",
                    str(row.get("unique_count", 0)),
                    str(row.get("duplicate_count", 0))
                ])
        if len(table_data) > 1:
            t_cols = Table(table_data, colWidths=[40, 150, 80, 70, 50, 70, 70], repeatRows=1)
            t_cols.setStyle(TableStyle([
                ('FONTNAME', (0, 0), (-1, -1), font_name),
                ('FONTNAME', (0, 0), (-1, 0), font_bold),
                ('FONTSIZE', (0, 0), (-1, 0), 9),
                ('FONTSIZE', (0, 1), (-1, -1), 8),
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2c3e50')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('TOPPADDING', (0, 0), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                ('GRID', (0, 0), (-1, -1), 0.3, colors.HexColor('#e0e0e0')),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
            ]))
            story.append(t_cols)
            story.append(Spacer(1, 15))
        else:
            story.append(Paragraph("Детали по колонкам отсутствуют.", normal_style))
    else:
        story.append(Paragraph("Детали по колонкам отсутствуют.", normal_style))
    story.append(Spacer(1, 10))

    # --- 4. Выбросы ---
    if outlier_details and isinstance(outlier_details, list) and len(outlier_details) > 0:
        story.append(Paragraph("3.2. Детализация выбросов", h2_style))
        table_data = [["Колонка", "Количество", "% от данных", "Нижняя граница", "Верхняя граница"]]
        for row in outlier_details:
            if isinstance(row, dict):
                lb, ub = row.get("lower_bound"), row.get("upper_bound")
                table_data.append([
                    str(row.get("column", ""))[:25],
                    str(row.get("count", 0)),
                    f"{float(row.get('percentage', 0)):.1f}%",
                    f"{float(lb):.2f}" if lb is not None else "—",
                    f"{float(ub):.2f}" if ub is not None else "—"
                ])
        if len(table_data) > 1:
            t_out = Table(table_data, colWidths=[140, 80, 70, 85, 85], repeatRows=1)
            t_out.setStyle(TableStyle([
                ('FONTNAME', (0, 0), (-1, -1), font_name),
                ('FONTNAME', (0, 0), (-1, 0), font_bold),
                ('FONTSIZE', (0, 0), (-1, 0), 9),
                ('FONTSIZE', (0, 1), (-1, -1), 8),
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2c3e50')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('TOPPADDING', (0, 0), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                ('GRID', (0, 0), (-1, -1), 0.3, colors.HexColor('#e0e0e0')),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
            ]))
            story.append(t_out)
            story.append(Spacer(1, 15))

    # --- 5. Корреляции ---
    perfect_corrs = analysis.get("perfect_correlations", []) if isinstance(analysis, dict) else []
    strong_corrs = analysis.get("strong_correlations", []) if isinstance(analysis, dict) else []
    corrs = []
    if isinstance(perfect_corrs, list):
        corrs.extend(perfect_corrs)
    if isinstance(strong_corrs, list):
        corrs.extend(strong_corrs)

    if corrs:
        story.append(Paragraph("3.3. Корреляционный анализ", h2_style))
        table_data = [["№", "Колонка 1", "Колонка 2", "Коэффициент", "Тип"]]
        for i, row in enumerate(corrs[:20], 1):
            if isinstance(row, dict):
                table_data.append([
                    str(i),
                    str(row.get("col1", ""))[:25],
                    str(row.get("col2", ""))[:25],
                    f"{float(row.get('correlation', 0)):.3f}",
                    str(row.get("type", ""))[:15]
                ])
        if len(table_data) > 1:
            t_corr = Table(table_data, colWidths=[35, 140, 140, 70, 80], repeatRows=1)
            t_corr.setStyle(TableStyle([
                ('FONTNAME', (0, 0), (-1, -1), font_name),
                ('FONTNAME', (0, 0), (-1, 0), font_bold),
                ('FONTSIZE', (0, 0), (-1, 0), 9),
                ('FONTSIZE', (0, 1), (-1, -1), 8),
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2c3e50')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('TOPPADDING', (0, 0), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                ('GRID', (0, 0), (-1, -1), 0.3, colors.HexColor('#e0e0e0')),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
            ]))
            story.append(t_corr)
            story.append(Spacer(1, 15))

    # --- 6. Рекомендации ---
    recs = analysis.get("recommendations", []) if isinstance(analysis, dict) else []
    if recs and isinstance(recs, list) and len(recs) > 0:
        story.append(PageBreak())
        story.append(Paragraph("4. РЕКОМЕНДАЦИИ ПО УЛУЧШЕНИЮ КАЧЕСТВА ДАННЫХ", h1_style))
        for i, r in enumerate(recs, 1):
            if isinstance(r, str):
                story.append(Paragraph(f"{i}. {r}", normal_style))
            elif isinstance(r, dict):
                check_type = r.get('check_type', '')
                column = r.get('column', '')
                issue = r.get('issue', '')
                text = f"<b>{i}.</b> [{check_type}] <b>{column}</b>: {issue}"
                story.append(Paragraph(text, normal_style))
                action = r.get("suggested_action", {})
                if isinstance(action, dict) and action:
                    desc = action.get('description', '')
                    if desc:
                        story.append(
                            Paragraph(f"&nbsp;&nbsp;&nbsp;&nbsp;→ <i>{desc}</i>", normal_style)
                        )
            story.append(Spacer(1, 4))

    # --- 7. Валидация ---
    if validation and isinstance(validation, dict) and validation.get("results"):
        story.append(PageBreak())
        story.append(Paragraph("5. РЕЗУЛЬТАТЫ ВАЛИДАЦИИ ПО ШАБЛОНУ", h1_style))

        tpl_name = validation.get('template_name', 'Неизвестный шаблон')
        story.append(Paragraph(f"<b>Шаблон:</b> {tpl_name}", normal_style))
        story.append(Spacer(1, 5))

        passed = validation.get('passed', 0)
        failed = validation.get('failed', 0)
        warnings = validation.get('warnings', 0)
        
        summary_text = (
            f"<b>Пройдено:</b> {passed} &nbsp;|&nbsp; "
            f"<b>Ошибок:</b> {failed} &nbsp;|&nbsp; "
            f"<b>Предупреждений:</b> {warnings}"
        )
        story.append(Paragraph(summary_text, normal_style))
        story.append(Spacer(1, 12))

        if passed + failed + warnings > 0:
            story.append(Image(io.BytesIO(_make_validation_pie(validation)), width=300, height=180))
            story.append(Spacer(1, 15))

        results = validation.get("results", [])
        if results and isinstance(results, list):
            table_data = [["Статус", "Тип проверки", "Название", "Сообщение"]]
            for row in results:
                if isinstance(row, dict):
                    status = row.get("status", "")
                    color = '#28a745' if status == 'PASSED' else ('#dc3545' if status == 'FAILED' else '#ffc107')
                    status_str = f"<font color='{color}'><b>{status}</b></font>"
                    table_data.append([
                        status_str,
                        str(row.get("check_type", ""))[:20],
                        str(row.get("check_name", ""))[:25],
                        str(row.get("message", ""))[:60]
                    ])
            if len(table_data) > 1:
                t_val = Table(table_data, colWidths=[60, 80, 120, 200], repeatRows=1)
                t_val.setStyle(TableStyle([
                    ('FONTNAME', (0, 0), (-1, -1), font_name),
                    ('FONTNAME', (0, 0), (-1, 0), font_bold),
                    ('FONTSIZE', (0, 0), (-1, 0), 9),
                    ('FONTSIZE', (0, 1), (-1, -1), 8),
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2c3e50')),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                    ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                    ('TOPPADDING', (0, 0), (-1, -1), 6),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                    ('GRID', (0, 0), (-1, -1), 0.3, colors.HexColor('#e0e0e0')),
                    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
                ]))
                story.append(t_val)

    # Подпись
    story.append(Spacer(1, 30))
    story.append(Paragraph(
        f"<i>Отчёт сформирован автоматически {datetime.now().strftime('%d.%m.%Y в %H:%M')}.</i>",
        small_style
    ))

    doc.build(story)
    return buffer.getvalue()