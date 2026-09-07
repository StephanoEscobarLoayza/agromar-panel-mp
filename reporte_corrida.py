"""Genera el PDF de cuadre de una corrida (botón "Reporte PDF" en Corridas).
Piezas compartidas (paleta, tarjetas KPI, badges, etc.) viven en reporte_base.py."""
from datetime import datetime
from io import BytesIO

from reportlab.lib.units import mm
from reportlab.platypus import Table, TableStyle, Paragraph, Spacer, HRFlowable

from reporte_base import (
    FOREST_2, CITRUS, OK, OK_BG, WARN, WARN_BG, BAD, BAD_BG, TEXT, TEXT_2, SURFACE_2, BORDER,
    PAGE_W, MARGIN, style_section, style_kpi_label, style_cell, style_footnote,
    fmt_kg, fmt_num, fmt_fecha, fmt_hora, fmt_minutos, Banda, header_footer, kpi_card, badge,
    encabezado, tabla, nuevo_doc,
)

ESTADO_CUADRE_INFO = {
    "cuadra": ("✓ CUADRA", OK, OK_BG),
    "incompleto": ("△ INCOMPLETO", WARN, WARN_BG),
    "excedido": ("✕ EXCEDIDO", BAD, BAD_BG),
    "sin_objetivo": ("SIN OBJETIVO MP", TEXT_2, SURFACE_2),
}


def _tabla_lotes(lotes):
    filas = [[
        f"#{l['lote_numero']}",
        l.get("proveedor") or "—",
        "Bines" if l.get("tipo_almacen_origen") == "BINES" else "Silo" if l.get("tipo_almacen_origen") == "SILO" else "—",
        fmt_kg(l["kg_asignados"]),
        fmt_num(l.get("brix_recepcion"), 2),
        fmt_num(l.get("acidez"), 2),
        fmt_num(l.get("ratio"), 2),
    ] for l in lotes]
    return tabla(
        ["Lote", "Proveedor", "Almacén", "Kg usado", "Brix", "Acidez", "Ratio"], filas,
        [22 * mm, 55 * mm, 20 * mm, 26 * mm, 16 * mm, 18 * mm, 16 * mm], align_derecha_desde=3,
    )


def _tabla_productos(productos):
    filas = [[
        p.get("producto") or "—",
        fmt_num(p.get("tambores"), 0),
        fmt_kg(p.get("peso_neto_tambor_kg")),
        fmt_kg(p.get("pt_kg")),
        fmt_kg(p.get("volumen_litros")),
    ] for p in productos]
    return tabla(
        ["Producto", "Tambores", "Peso/tambor", "PT kg", "Litros"], filas,
        [45 * mm, 25 * mm, 30 * mm, 30 * mm, 30 * mm], align_derecha_desde=1,
    )


def _tabla_paradas(paradas):
    filas = [[
        p.get("motivo") or "—",
        fmt_hora(p.get("hora_inicio")),
        fmt_hora(p.get("hora_fin")) if p.get("hora_fin") else "en curso",
        fmt_minutos(p.get("duracion_minutos")),
    ] for p in paradas]
    return tabla(
        ["Motivo", "Inicio", "Fin", "Duración"], filas,
        [70 * mm, 35 * mm, 35 * mm, 20 * mm], align_derecha_desde=1,
    )


def generar_reporte_pdf(corrida: dict, lotes: list, productos: list, paradas: list) -> bytes:
    """corrida: fila de v_cuadre_corridas (+ nombre/fechas/tipo_proceso).
    lotes: filas de asignaciones + join a lotes (numero, proveedor, tipo_almacen_origen, kg_asignados, brix_recepcion, acidez, ratio).
    productos: filas de corrida_productos.
    paradas: filas de paradas (+ duracion_minutos)."""
    buf = BytesIO()
    doc = nuevo_doc(buf, f"Cuadre - {corrida['nombre']}")

    kg_total = float(corrida.get("kg_asignados_total") or 0)
    kg_objetivo = corrida.get("mp_kg_objetivo")
    kg_silo = sum(float(l["kg_asignados"]) for l in lotes if l.get("tipo_almacen_origen") == "SILO")
    kg_bines = sum(float(l["kg_asignados"]) for l in lotes if l.get("tipo_almacen_origen") == "BINES")

    brix_pond = sum(float(l["kg_asignados"]) * float(l["brix_recepcion"]) for l in lotes if l.get("brix_recepcion") is not None)
    acidez_pond = sum(float(l["kg_asignados"]) * float(l["acidez"]) for l in lotes if l.get("acidez") is not None)
    kg_con_calidad = sum(float(l["kg_asignados"]) for l in lotes if l.get("brix_recepcion") is not None)
    brix_prom = brix_pond / kg_con_calidad if kg_con_calidad > 0 else None
    ratio_prom = brix_pond / acidez_pond if acidez_pond > 0 else None

    pt_total = sum(float(p["pt_kg"]) for p in productos if p.get("pt_kg") is not None)
    litros_total = sum(float(p["volumen_litros"]) for p in productos if p.get("volumen_litros") is not None)
    rendimiento = pt_total / kg_total if kg_total > 0 and pt_total > 0 else None

    paradas_cerradas = [p for p in paradas if p.get("duracion_minutos") is not None]
    min_parado = sum(p["duracion_minutos"] for p in paradas_cerradas)
    hay_en_curso = any(p.get("duracion_minutos") is None for p in paradas)

    estado = corrida.get("estado_cuadre") or "sin_objetivo"
    badge_txt, badge_fg, badge_bg = ESTADO_CUADRE_INFO.get(estado, ESTADO_CUADRE_INFO["sin_objetivo"])

    story = [
        encabezado(
            "Cuadre de corrida",
            f"{corrida['nombre']} · {fmt_fecha(corrida.get('fecha_inicio'))}" +
            (f" — {fmt_fecha(corrida.get('fecha_final'))}" if corrida.get("fecha_final") else " · en curso"),
        ),
        Spacer(1, 10 * mm),
        badge(badge_txt, badge_fg, badge_bg),
        Spacer(1, 6 * mm),
        Paragraph("Resumen", style_section),
    ]

    ancho_kpi = (PAGE_W - 2 * MARGIN - 2 * 6) / 3
    fila1 = [
        kpi_card("MP consumida", f"{fmt_kg(kg_total)} kg", ancho_kpi),
        kpi_card("MP objetivo (Trazabilidad)", f"{fmt_kg(float(kg_objetivo))} kg" if kg_objetivo else "—", ancho_kpi),
        kpi_card("Diferencia", f"{fmt_kg(float(kg_objetivo) - kg_total)} kg" if kg_objetivo else "—", ancho_kpi,
                 color_valor=(OK if estado == "cuadra" else WARN if estado == "incompleto" else BAD if estado == "excedido" else TEXT)),
    ]
    fila2 = [
        kpi_card("Brix ponderado", fmt_num(brix_prom, 2), ancho_kpi),
        kpi_card("Ratio ponderado", fmt_num(ratio_prom, 2), ancho_kpi),
        kpi_card("Rendimiento", f"{rendimiento * 100:.1f} %" if rendimiento else "—", ancho_kpi),
    ]
    fila3 = [
        kpi_card("Producto terminado", f"{fmt_kg(pt_total)} kg" if pt_total else "—", ancho_kpi),
        kpi_card("Volumen", f"{fmt_kg(litros_total)} L" if litros_total else "—", ancho_kpi),
        kpi_card("Tiempo parado", (fmt_minutos(min_parado) + (" · hay una en curso" if hay_en_curso else "")) if paradas else "sin paradas", ancho_kpi,
                 color_valor=(BAD if min_parado > 60 else TEXT)),
    ]
    kpi_grid = Table([fila1, fila2, fila3], colWidths=[ancho_kpi] * 3, spaceBefore=4)
    kpi_grid.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(kpi_grid)
    story.append(Spacer(1, 6 * mm))

    if kg_silo > 0 or kg_bines > 0:
        story.append(Paragraph("MP por almacén de origen", style_section))
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

    story.append(Paragraph(f"Lotes de MP consumidos ({len(lotes)})", style_section))
    story.append(_tabla_lotes(lotes) if lotes else Paragraph("Sin lotes registrados todavía.", style_footnote))

    if productos:
        story.append(Spacer(1, 8 * mm))
        story.append(Paragraph(f"Productos de salida ({len(productos)})", style_section))
        story.append(_tabla_productos(productos))

    if paradas:
        story.append(Spacer(1, 8 * mm))
        story.append(Paragraph(f"Paradas registradas ({len(paradas)})", style_section))
        story.append(_tabla_paradas(paradas))

    story.append(Spacer(1, 10 * mm))
    story.append(Paragraph(f"Generado el {datetime.now().strftime('%d/%m/%Y %H:%M')} desde el Panel de cuadre de producción.", style_footnote))

    doc.build(
        story,
        onFirstPage=lambda c, d: header_footer(c, d, corrida["nombre"]),
        onLaterPages=lambda c, d: header_footer(c, d, corrida["nombre"]),
    )
    return buf.getvalue()
