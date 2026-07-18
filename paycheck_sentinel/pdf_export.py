"""
pdf_export.py — generisanje PDF izvestaja sa sumnjivim transakcijama.

Koristi ugradjeni DejaVuSans font (paycheck_sentinel/fonts/) da bi srpska
slova (č ć š ž đ) ispravno prikazivala, nezavisno od fontova instaliranih
na serveru.

Developed by Zeljko Tripcevski
"""

import io
import os

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")

_FONTS_REGISTERED = False


def _ensure_fonts():
    global _FONTS_REGISTERED
    if _FONTS_REGISTERED:
        return
    pdfmetrics.registerFont(TTFont("DejaVuSans", os.path.join(FONTS_DIR, "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", os.path.join(FONTS_DIR, "DejaVuSans-Bold.ttf")))
    _FONTS_REGISTERED = True


def build_pdf_report(batch, columns, rows, stats, only_flagged, mode, report_kind="flagged"):
    """
    Vraca BytesIO sa gotovim PDF-om.

    batch: dict (row iz batches tabele) — koristi se i za mapirane nazive
           kolona (paid_col, amount_col, itd.) da bi se PDF tabela ogranicila
           na relevantna polja umesto na sve sirove XML tagove.
    columns: list[str] svih detektovanih kolona (fallback ako mapiranje ne postoji)
    rows: list[dict] transakcija (kljucevi: raw, flags, paid_amount/amount ...)
    stats: dict sa statistikom (total, flagged_count, itd.)
    only_flagged: bool — da li je izvestaj filtriran samo na flagovane redove
    mode: 'generic' ili 'bank_statement'
    """
    _ensure_fonts()

    display_columns = _pick_display_columns(batch, columns, mode)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        leftMargin=14 * mm, rightMargin=14 * mm,
        topMargin=14 * mm, bottomMargin=14 * mm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "TitleSr", parent=styles["Title"], fontName="DejaVuSans-Bold", fontSize=16,
    )
    normal_style = ParagraphStyle(
        "NormalSr", parent=styles["Normal"], fontName="DejaVuSans", fontSize=9,
    )
    small_style = ParagraphStyle(
        "SmallSr", parent=styles["Normal"], fontName="DejaVuSans", fontSize=8, textColor=colors.grey,
    )
    cell_style = ParagraphStyle(
        "CellSr", parent=styles["Normal"], fontName="DejaVuSans", fontSize=7, leading=9,
    )
    header_style = ParagraphStyle(
        "HeaderSr", parent=styles["Normal"], fontName="DejaVuSans-Bold", fontSize=7.5,
        textColor=colors.white, leading=9,
    )

    story = []

    titles = {
        "full": "Izveštaj o punom povratu",
        "flagged": "Izveštaj o upozorenjima",
        "all": "Izveštaj o svim transakcijama",
    }
    title_text = titles.get(report_kind, "Izveštaj o upozorenjima")
    story.append(Paragraph(title_text, title_style))
    story.append(Spacer(1, 4 * mm))

    meta_lines = [f"Batch #{batch['id']} — {batch.get('label', '')}"]
    if batch.get("own_account"):
        meta_lines.append(f"Račun vlasnika izvoda: {batch['own_account']}")
    created = (batch.get("created_at") or "").replace("T", " ")[:16]
    if created:
        meta_lines.append(f"Datum upload-a: {created}")
    mode_label = "Bankovni izvod (kružni povrat)" if mode == "bank_statement" else "Standardno (plaćeno/vraćeno)"
    meta_lines.append(f"Mod analize: {mode_label}")

    for line in meta_lines:
        story.append(Paragraph(line, normal_style))
    story.append(Spacer(1, 4 * mm))

    # statistika
    stat_data = [["Ukupno redova", "Sa upozorenjem", "Pun povrat / potvrđeno", "Iznos punog povrata"]]
    stat_data.append([
        str(stats.get("total", "")),
        str(stats.get("flagged_count", "")),
        str(stats.get("full_count", stats.get("confirmed_count", ""))),
        str(stats.get("full_sum", stats.get("confirmed_sum", ""))),
    ])
    stat_table = Table(stat_data, hAlign="LEFT")
    stat_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "DejaVuSans"),
        ("FONTNAME", (0, 0), (-1, 0), "DejaVuSans-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2a3441")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(stat_table)
    story.append(Spacer(1, 6 * mm))

    # glavna tabela transakcija
    header_row = [Paragraph(_column_label(c), header_style) for c in display_columns] + \
                 [Paragraph("Upozorenja", header_style)]

    table_data = [header_row]
    row_colors = []
    for r in rows:
        raw = r.get("raw", {})
        row_cells = [Paragraph(_truncate(raw.get(c, "")), cell_style) for c in display_columns]
        flags = r.get("flags", [])
        flags_text = "+".join(f["label"] for f in flags) if flags else "OK"
        row_cells.append(Paragraph(flags_text, cell_style))
        table_data.append(row_cells)

        is_full = any(f["type"] in ("full", "circular_confirmed") for f in flags)
        if is_full:
            row_colors.append(colors.HexColor("#fbdede"))
        elif flags:
            row_colors.append(colors.HexColor("#faedc7"))
        else:
            row_colors.append(None)

    if len(table_data) == 1:
        story.append(Paragraph("Nema redova za prikaz.", normal_style))
    else:
        available_width = landscape(A4)[0] - 28 * mm
        col_count = len(display_columns) + 1
        col_width = available_width / col_count
        main_table = Table(table_data, colWidths=[col_width] * col_count, repeatRows=1)
        style_commands = [
            ("FONTNAME", (0, 0), (-1, -1), "DejaVuSans"),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2a3441")),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#dddddd")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]
        for i, color in enumerate(row_colors, start=1):
            if color:
                style_commands.append(("BACKGROUND", (0, i), (-1, i), color))
            elif i % 2 == 0:
                style_commands.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#f5f5f5")))
        main_table.setStyle(TableStyle(style_commands))
        story.append(main_table)

    doc.build(story)
    buf.seek(0)
    return buf


def _pick_display_columns(batch, all_columns, mode):
    """Bira uzi, citljiv skup kolona za PDF tabelu na osnovu mapiranja iz batch-a,
    umesto da prikazuje sve sirove XML tagove (koji mogu biti previse za citljivu tabelu)."""
    picked = ["__source_file"]

    if mode == "bank_statement":
        for key in ("date_col", "benefit_col", "amount_col", "ref_col"):
            col = batch.get(key)
            if col and col in all_columns and col not in picked:
                picked.append(col)
        # koristan kontekst ako postoji (uobicajena iBank polja) - dodaj samo ako su prisutna
        for extra in ("payeeinfo.name", "payeeaccountinfo.acctid", "purpose"):
            if extra in all_columns and extra not in picked:
                picked.append(extra)
    else:
        for key in ("date_col", "debtor_col", "id_col", "paid_col", "returned_col"):
            col = batch.get(key)
            if col and col in all_columns and col not in picked:
                picked.append(col)

    # fallback: ako mapiranje nije nadjeno ni za jednu kolonu (npr. stariji batch
    # bez sacuvanog mapiranja), prikazi sve detektovane kolone
    if len(picked) == 1:
        picked = ["__source_file"] + list(all_columns)

    return picked


def _column_label(col):
    """Skraceni naziv kolone za zaglavlje tabele (npr. 'payeeaccountinfo.acctid' -> 'acctid')."""
    if col == "__source_file":
        return "Fajl"
    return col.split(".")[-1]


def _truncate(value, max_len=40):
    s = str(value) if value is not None else ""
    if len(s) > max_len:
        return s[:max_len - 1] + "…"
    return s
