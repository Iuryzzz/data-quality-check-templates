# app/pdf_report.py
from __future__ import annotations
import io
import os

import platform
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.pdfbase.ttfonts import TTFont


def sanitize_data(data: Any) -> Any:
    if isinstance(data, dict):
        return {str(k).strip(): sanitize_data(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [sanitize_data(item) for item in data]
    elif isinstance(data, str):
        return data.strip()
    return data


from reportlab.pdfbase import pdfmetrics
def _find_and_register_fonts():
    system = platform.system()
    candidates = []
    if system == "Windows":
        fonts_dir = Path(os.environ.get("WINDIR", "C:\\Windows")) / "Fonts"
        candidates = [fonts_dir / "arial.ttf", fonts_dir / "segoeui.ttf", fonts_dir / "calibri.ttf"]
    elif system == "Darwin":
        candidates = [Path("/Library/Fonts/Arial.ttf")]
    else:
        candidates = [Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")]

    for path in candidates:
        if path.exists():
            try:
                # Регистрация для Matplotlib
                from matplotlib import font_manager
                font_manager.fontManager.addfont(str(path))
                prop = font_manager.FontProperties(fname=str(path))
                plt.rcParams['font.family'] = prop.get_name()
                plt.rcParams['axes.unicode_minus'] = False

                # Регистрация для ReportLab
                pdfmetrics.registerFont(TTFont('Uni', str(path)))
                pdfmetrics.registerFont(
                    TTFont('Uni-Bold', str(path)))  # ReportLab часто использует тот же файл для жирного
                pdfmetrics.registerFont(TTFont('Uni-Italic', str(path)))
                return True
            except Exception:
                continue
    return False


_fonts_registered = _find_and_register_fonts()


def _make_types_pie(detected_types: Dict[str, str]) -> bytes:
    if not detected_types:
        return _empty_chart("Нет данных о типах")
    counts: Dict[str, int] = {}
    for t in detected_types.values():
        counts[t] = counts.get(t, 0) + 1
    fig, ax = plt.subplots(figsize=(6, 4))
    colors_list = ['#0366d6', '#28a745', '#ffc107', '#17a2b8', '#6f42c1', '#dc3545', '#fd7e14']
    ax.pie(counts.values(), labels=counts.keys(), autopct='%1.0f%%', colors=colors_list[:len(counts)], startangle=90)
    ax.set_title("Распределение типов колонок", pad=15, fontsize=12, fontweight='bold')
    return _fig_to_bytes(fig)


def _make_missing_bar(recommendations: List[Dict[str, Any]]) -> bytes:
    missing = [r for r in recommendations if r.get("check_type") == "missing_values"]
    if not missing:
        return _empty_chart("Пропусков не обнаружено")
    missing.sort(key=lambda r: r.get("suggested_action", {}).get("affected_rows", 0), reverse=True)
    top = missing[:5]
    labels = [r.get("column", "?") for r in top]
    values = [r.get("suggested_action", {}).get("affected_rows", 0) for r in top]
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar(labels, values, color='#dc3545', edgecolor='white')
    ax.set_title("Топ-5 колонок по количеству пропусков", pad=15, fontsize=12, fontweight='bold')
    ax.set_ylabel("Количество пропусков")
    ax.grid(axis='y', alpha=0.3)
    for bar, val in zip(ax.patches, values):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + max(values) * 0.02, str(val), ha='center',
                va='bottom', fontsize=9)
    plt.xticks(rotation=20, ha='right')
    plt.tight_layout()
    return _fig_to_bytes(fig)


def _make_outliers_bar(outlier_details: List[Dict[str, Any]]) -> bytes:
    if not outlier_details:
        return _empty_chart("Выбросов не обнаружено")
    sorted_out = sorted(outlier_details, key=lambda o: o.get("count", 0), reverse=True)[:7]
    labels = [o.get("column", "?") for o in sorted_out]
    values = [o.get("count", 0) for o in sorted_out]
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar(labels, values, color='#fd7e14', edgecolor='white')
    ax.set_title("Выбросы по колонкам", pad=15, fontsize=12, fontweight='bold')
    ax.set_ylabel("Количество выбросов")
    ax.grid(axis='y', alpha=0.3)
    plt.xticks(rotation=20, ha='right')
    plt.tight_layout()
    return _fig_to_bytes(fig)


def _make_validation_pie(validation: Optional[Dict[str, Any]]) -> bytes:
    if not validation or not validation.get("results"):
        return _empty_chart("Валидация не проводилась")
    passed, failed, warnings = validation.get("passed", 0), validation.get("failed", 0), validation.get("warnings", 0)
    if passed + failed + warnings == 0:
        return _empty_chart("Нет результатов валидации")
    fig, ax = plt.subplots(figsize=(5, 4))
    labels, values, colors_list = [], [], []
    if passed: labels.append("Пройдено"); values.append(passed); colors_list.append('#28a745')
    if failed: labels.append("Ошибки"); values.append(failed); colors_list.append('#dc3545')
    if warnings: labels.append("Предупреждения"); values.append(warnings); colors_list.append('#ffc107')
    ax.pie(values, labels=labels, autopct='%1.0f%%', colors=colors_list, startangle=90)
    ax.set_title("Результаты валидации", pad=15, fontsize=12, fontweight='bold')
    return _fig_to_bytes(fig)


def _empty_chart(message: str) -> bytes:
    fig, ax = plt.subplots(figsize=(6, 3))
    ax.text(0.5, 0.5, message, ha='center', va='center', fontsize=12, color='#6a737d', transform=ax.transAxes)
    ax.set_axis_off()
    return _fig_to_bytes(fig)


def _fig_to_bytes(fig) -> bytes:
    buf = io.BytesIO()
    # DPI 150 для чёткости в PDF
    fig.savefig(buf, format='png', dpi=150, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    buf.seek(0)
    return buf.read()


# === 4. ГЕНЕРАЦИЯ PDF ЧЕРЕЗ REPORTLAB ===
def generate_report_pdf(task: Dict[str, Any], filename: str) -> bytes:
    clean_task = sanitize_data(task)
    analysis = clean_task.get("analysis") or {}
    validation = clean_task.get("validation") or {}

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
    story = []

    # Стили
    styles = getSampleStyleSheet()
    font_name = 'Uni' if _fonts_registered else 'Helvetica'
    font_bold = 'Uni-Bold' if _fonts_registered else 'Helvetica-Bold'

    title_style = ParagraphStyle(name='Title', fontName=font_bold, fontSize=24, textColor=colors.HexColor('#24292e'),
                                 spaceAfter=20, alignment=1)
    h1_style = ParagraphStyle(name='H1', fontName=font_bold, fontSize=16, textColor=colors.HexColor('#0366d6'),
                              spaceAfter=10, spaceBefore=15)
    h2_style = ParagraphStyle(name='H2', fontName=font_bold, fontSize=12, textColor=colors.HexColor('#24292e'),
                              spaceAfter=8)
    normal_style = ParagraphStyle(name='Normal', fontName=font_name, fontSize=10, textColor=colors.HexColor('#24292e'),
                                  leading=14)

    # --- Титульная страница ---
    story.append(Spacer(1, 100))
    story.append(Paragraph("Отчёт по качеству данных", title_style))
    story.append(Spacer(1, 20))
    story.append(Paragraph(f"<b>Файл:</b> {filename}", normal_style))
    story.append(Paragraph(f"<b>Дата анализа:</b> {datetime.now().strftime('%d.%m.%Y %H:%M')}", normal_style))
    if clean_task.get("template_id"):
        story.append(Paragraph(f"<b>Шаблон:</b> {clean_task.get('template_id')}", normal_style))
    story.append(PageBreak())

    # --- Общая статистика ---
    story.append(Paragraph("Общая статистика", h1_style))
    metrics = [
        ("Строк", str(analysis.get("total_rows", 0))),
        ("Столбцов", str(analysis.get("total_columns", 0))),
        ("Пропусков", f"{analysis.get('total_missing', 0)} ({analysis.get('missing_percentage', 0):.1f}%)"),
        ("Дубликатов", str(analysis.get("duplicate_count", 0))),
        ("Выбросов", str(analysis.get("total_outliers", 0))),
        ("Память", f"{analysis.get('memory_usage_mb', 0):.2f} MB"),
    ]

    # Таблица метрик без границ, выглядит как сетка карточек
    metric_data = []
    row = []
    for i, (label, value) in enumerate(metrics):
        row.append(Paragraph(
            f"<b><font color='#0366d6' size=14>{value}</font></b><br/><font color='#6a737d' size=9>{label}</font>",
            normal_style))
        if (i + 1) % 4 == 0 or i == len(metrics) - 1:
            metric_data.append(row)
            row = []
        elif len(row) < 4:
            row.append("")  # Заполнитель для выравнивания

    # Дополняем последнюю строку пустыми ячейками до 4-х
    while len(row) < 4:
        row.append("")
    if row and len(metric_data) > 0 and len(metric_data[-1]) != 4:  # на всякий случай
        pass  # логика выше уже обрабатывает

    t_metrics = Table(metric_data, colWidths=[120] * 4, spaceBefore=10, spaceAfter=20)
    t_metrics.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 15),
        ('TOPPADDING', (0, 0), (-1, -1), 15),
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f6f8fa')),
        ('ROUNDEDCORNERS', [5]),  # Работает в новых версиях reportlab
    ]))
    story.append(t_metrics)
    story.append(Spacer(1, 10))

    # --- Графики ---
    story.append(Paragraph("Визуализация", h1_style))
    detected_types = analysis.get("detected_types") or {m.get("name", ""): m.get("data_type", "unknown") for m in
                                                        analysis.get("missing_details", [])}

    story.append(Image(io.BytesIO(_make_types_pie(detected_types)), width=400, height=250))
    story.append(Spacer(1, 10))

    recommendations = analysis.get("recommendations", [])
    if recommendations and isinstance(recommendations[0], str):
        recommendations = []
    story.append(Image(io.BytesIO(_make_missing_bar(recommendations)), width=450, height=250))
    story.append(Spacer(1, 10))

    outlier_details = analysis.get("outlier_details", [])
    if outlier_details:
        story.append(Image(io.BytesIO(_make_outliers_bar(outlier_details)), width=450, height=250))
        story.append(Spacer(1, 10))

    story.append(PageBreak())

    # --- Детали по колонкам ---
    story.append(Paragraph("Детали по колонкам", h1_style))
    missing_details = analysis.get("missing_details", [])
    if missing_details:
        table_data = [["Колонка", "Тип", "Пропуски", "%", "Уник.", "Дублик."]]
        for row in missing_details:
            table_data.append([
                str(row.get("name", ""))[:25],
                str(row.get("data_type", ""))[:15],
                str(row.get("null_count", 0)),
                f"{row.get('null_percentage', 0):.1f}%",
                str(row.get("unique_count", 0)),
                str(row.get("duplicate_count", 0))
            ])
        t = Table(table_data, colWidths=[130, 70, 60, 50, 60, 60], repeatRows=1)
        t.setStyle(_get_clean_table_style(len(table_data)))
        story.append(t)
    else:
        story.append(Paragraph("Детали по колонкам отсутствуют.", normal_style))
    story.append(Spacer(1, 15))

    # --- Выбросы ---
    if outlier_details:
        story.append(Paragraph("Выбросы", h1_style))
        table_data = [["Колонка", "Кол-во", "%", "Мин. граница", "Макс. граница"]]
        for row in outlier_details:
            lb, ub = row.get("lower_bound"), row.get("upper_bound")
            table_data.append([
                str(row.get("column", ""))[:25],
                str(row.get("count", 0)),
                f"{row.get('percentage', 0):.1f}%",
                f"{lb:.2f}" if lb is not None else "—",
                f"{ub:.2f}" if ub is not None else "—"
            ])
        t = Table(table_data, colWidths=[130, 70, 70, 80, 80], repeatRows=1)
        t.setStyle(_get_clean_table_style(len(table_data)))
        story.append(t)
        story.append(Spacer(1, 15))

    # --- Корреляции ---
    corrs = (analysis.get("perfect_correlations", []) or []) + (analysis.get("strong_correlations", []) or [])
    if corrs:
        story.append(Paragraph("Корреляции", h1_style))
        table_data = [["Колонка 1", "Колонка 2", "Коэфф.", "Тип"]]
        for row in corrs[:20]:
            table_data.append([
                str(row.get("col1", ""))[:25],
                str(row.get("col2", ""))[:25],
                f"{row.get('correlation', 0):.3f}",
                str(row.get("type", ""))
            ])
        t = Table(table_data, colWidths=[140, 140, 70, 80], repeatRows=1)
        t.setStyle(_get_clean_table_style(len(table_data)))
        story.append(t)
        story.append(Spacer(1, 15))

    # --- Рекомендации ---
    recs = analysis.get("recommendations", [])
    if recs:
        story.append(Paragraph("Рекомендации", h1_style))
        for i, r in enumerate(recs, 1):
            if isinstance(r, str):
                story.append(Paragraph(f"{i}. {r}", normal_style))
            else:
                text = f"<b>{i}.</b> [{r.get('check_type', '')}] <b>{r.get('column', '')}</b>: {r.get('issue', '')}"
                story.append(Paragraph(text, normal_style))
                action = r.get("suggested_action", {})
                if action:
                    story.append(
                        Paragraph(f"&nbsp;&nbsp;&nbsp;&nbsp;→ <i>{action.get('description', '')}</i>", normal_style))
            story.append(Spacer(1, 4))

    # --- Валидация ---
    if validation and validation.get("results"):
        story.append(PageBreak())
        tpl_name = validation.get('template_name', 'Неизвестный шаблон')
        story.append(Paragraph(f"Проверки по шаблону: {tpl_name}", h1_style))

        summary_text = f"<b>Пройдено:</b> {validation.get('passed', 0)} &nbsp;|&nbsp; <b>Ошибок:</b> {validation.get('failed', 0)} &nbsp;|&nbsp; <b>Предупреждений:</b> {validation.get('warnings', 0)}"
        story.append(Paragraph(summary_text, normal_style))
        story.append(Spacer(1, 10))
        story.append(Image(io.BytesIO(_make_validation_pie(validation)), width=350, height=200))
        story.append(Spacer(1, 15))

        table_data = [["Статус", "Тип", "Название", "Сообщение"]]
        for row in validation.get("results", []):
            status = row.get("status", "")
            # Цветной текст для статуса
            color = '#28a745' if status == 'PASSED' else ('#dc3545' if status == 'FAILED' else '#ffc107')
            status_str = f"<font color='{color}'><b>{status}</b></font>"

            table_data.append([
                status_str,
                str(row.get("check_type", ""))[:20],
                str(row.get("check_name", ""))[:25],
                str(row.get("message", ""))[:50]
            ])
        t = Table(table_data, colWidths=[60, 80, 120, 170], repeatRows=1, splitByRow=1)

        # Кастомный стиль для таблицы валидации (без сетки, только линии)
        style = [
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f6f8fa')),
            ('FONTNAME', (0, 0), (-1, 0), font_bold),
            ('FONTNAME', (0, 1), (-1, -1), font_name),
            ('FONTSIZE', (0, 0), (-1, 0), 9),
            ('FONTSIZE', (0, 1), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
            ('TOPPADDING', (0, 0), (-1, 0), 8),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LINEBELOW', (0, 0), (-1, 0), 1, colors.HexColor('#e1e4e8')),
            ('LINEBELOW', (0, -1), (-1, -1), 1, colors.HexColor('#e1e4e8')),
        ]
        # Разрешаем HTML в первой колонке для цвета
        t.setStyle(TableStyle(style))
        story.append(t)

    # Собираем PDF
    doc.build(story)
    return buffer.getvalue()


def _get_clean_table_style(num_rows: int):
    """Возвращает профессиональный стиль для таблиц ReportLab"""
    font_name = 'Uni' if _fonts_registered else 'Helvetica'
    font_bold = 'Uni-Bold' if _fonts_registered else 'Helvetica-Bold'

    return TableStyle([
        # Заголовок
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f6f8fa')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#24292e')),
        ('FONTNAME', (0, 0), (-1, 0), font_bold),
        ('FONTNAME', (0, 1), (-1, -1), font_name),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('TOPPADDING', (0, 0), (-1, 0), 8),
        # Выравнивание
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        # Сетка
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e1e4e8')),
        # Чередование цветов строк для читаемости (если строк > 1)
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#fafbfc')]),
    ])