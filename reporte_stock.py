"""Genera el PDF de "Stock de materia prima" - una foto del saldo que el
sistema calcula en vivo para cada lote (Silo + Bines), en un momento dado.
No agrega ningún conteo físico manual - es exactamente lo que ya muestra
v_saldo_lotes, solo que ordenado y presentado para imprimir o compartir."""
from datetime import datetime
from io import BytesIO

from reportlab.lib.units import mm
from reportlab.platypus import Table, TableStyle, Paragraph, Spacer, HRFlowable

from reporte_base import (
    FOREST_2, CITRUS, TEXT_2, BORDER,
    PAGE_W, MARGIN, style_section, style_kpi_label, style_footnote,
    fmt_kg, fmt_num, fmt_fecha, Banda, header_footer, kpi_card, encabezado, tabla, nuevo_doc,
)


def _tag_almacen(t):
    return "Bines" if t == "BINES" else "Silo" if t == "SILO" else "Mixto"


def _tabla_stock(lotes):
    filas = [[
        f"#{l['numero']}",
        l.get("proveedor") or "—",
        _tag_almacen(l.get("tipo_almacen")),
        fmt_fecha(l.get("fecha_ingreso")),
        (l.get("estado_actual") or "—").title(),
        fmt_kg(l.get("kg_saldo")),
        fmt_num(l.get("bines_saldo"), 0) if l.get("bines_saldo") is not None else "—",
    ] for l in lotes]
    return tabla(
        ["Lote", "Proveedor", "Almacén", "Ingreso", "Estado", "Kg saldo", "Bines"], filas,
        [18 * mm, 50 * mm, 18 * mm, 22 * mm, 24 * mm, 26 * mm, 16 * mm], align_derecha_desde=5,
    )


def generar_reporte_stock_pdf(lotes: list) -> bytes:
    """lotes: filas de v_saldo_lotes con kg_saldo > 0 (numero, proveedor,
    tipo_almacen, fecha_ingreso, estado_actual, kg_saldo, bines_saldo)."""
    buf = BytesIO()
    doc = nuevo_doc(buf, "Stock de materia prima")

    kg_silo = sum(float(l["kg_saldo"]) for l in lotes if l.get("tipo_almacen") == "SILO")
    kg_bines = sum(float(l["kg_saldo"]) for l in lotes if l.get("tipo_almacen") == "BINES")
    kg_total = kg_silo + kg_bines
    en_proceso = sum(1 for l in lotes if (l.get("estado_actual") or "").upper() == "EN PROCESO")
    en_espera = sum(1 for l in lotes if (l.get("estado_actual") or "").upper() == "EN ESPERA")

    ahora = datetime.now()

    story = [
        encabezado("Stock de materia prima", f"Foto del sistema al {ahora.strftime('%d/%m/%Y')}, {ahora.strftime('%H:%M')}"),
        Spacer(1, 10 * mm),
        Paragraph("Resumen", style_section),
    ]

    ancho_kpi = (PAGE_W - 2 * MARGIN - 3 * 6) / 4
    fila1 = [
        kpi_card("Stock total", f"{fmt_kg(kg_total)} kg", ancho_kpi),
        kpi_card("En Silo", f"{fmt_kg(kg_silo)} kg", ancho_kpi),
        kpi_card("En Bines", f"{fmt_kg(kg_bines)} kg", ancho_kpi),
        kpi_card("Lotes con saldo", fmt_num(len(lotes), 0), ancho_kpi),
    ]
    kpi_grid = Table([fila1], colWidths=[ancho_kpi] * 4, spaceBefore=4)
    kpi_grid.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(kpi_grid)
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(f"{en_proceso} lote(s) en proceso ahora mismo · {en_espera} en espera.", style_kpi_label))
    story.append(Spacer(1, 4 * mm))

    if kg_silo > 0 or kg_bines > 0:
        story.append(Paragraph("Stock por almacén", style_section))
        ancho_barra = PAGE_W - 2 * MARGIN - 30 * mm
        maximo = max(kg_silo, kg_bines)
        fila = [
            [Paragraph("Silo", style_kpi_label), Banda(ancho_barra, kg_silo, maximo, FOREST_2, fmt_kg(kg_silo) + " kg")],
            [Paragraph("Bines", style_kpi_label), Banda(ancho_barra, kg_bines, maximo, CITRUS, fmt_kg(kg_bines) + " kg")],
        ]
        t = Table(fila, colWidths=[18 * mm, ancho_barra])
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
        story.append(t)
        story.append(Spacer(1, 4 * mm))

    story.append(HRFlowable(width="100%", thickness=0.5, color=BORDER, spaceBefore=4, spaceAfter=10))

    story.append(Paragraph(f"Lotes con saldo, del más antiguo al más nuevo ({len(lotes)})", style_section))
    story.append(_tabla_stock(lotes) if lotes else Paragraph("No hay lotes con saldo en este momento.", style_footnote))

    story.append(Spacer(1, 10 * mm))
    story.append(Paragraph(
        "Esto es lo que el sistema calcula en vivo (peso neto menos lo ya asignado a corridas) - no reemplaza un conteo físico real.",
        style_footnote,
    ))

    doc.build(
        story,
        onFirstPage=lambda c, d: header_footer(c, d, "Stock de materia prima"),
        onLaterPages=lambda c, d: header_footer(c, d, "Stock de materia prima"),
    )
    return buf.getvalue()
