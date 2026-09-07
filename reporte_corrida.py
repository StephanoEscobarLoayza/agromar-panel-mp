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
        fmt_kg(l["kg_asignados"]) if l.get("kg_asignados") is not None else "pendiente",
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


def _tabla_mediciones(mediciones):
    filas = [[
        m.get("tanque") or "—",
        fmt_kg(m.get("litros")) if m.get("litros") is not None else "—",
        fmt_num(m.get("brix_inicial"), 2) if m.get("brix_inicial") is not None else "—",
        fmt_num(m.get("brix_final"), 2),
        fmt_num(m.get("acidez"), 3),
        fmt_num(m.get("ph"), 2) if m.get("ph") is not None else "—",
        fmt_num(m.get("ratio"), 2) if m.get("ratio") is not None else "—",
    ] for m in mediciones]
    return tabla(
        ["Tanque", "Litros", "Brix i.", "Brix f.", "Acidez", "pH", "Ratio"], filas,
        [22 * mm, 24 * mm, 20 * mm, 20 * mm, 20 * mm, 16 * mm, 16 * mm], align_derecha_desde=1,
    )


def generar_reporte_pdf(corrida: dict, lotes: list, productos: list, paradas: list, mediciones: list = None) -> bytes:
    """corrida: fila de v_cuadre_corridas (+ nombre/fechas/tipo_proceso).
    lotes: filas de asignaciones + join a lotes (numero, proveedor, tipo_almacen_origen, kg_asignados, brix_recepcion, acidez, ratio).
    productos: filas de corrida_productos.
    paradas: filas de paradas (+ duracion_minutos)."""
    buf = BytesIO()
    doc = nuevo_doc(buf, f"Cuadre - {corrida['nombre']}")

    # los lotes con "kg pendiente" (kg_asignados NULL - ya empezaron a
    # alimentar la corrida pero todavía no se sabe cuánto, típico en Silo
    # mientras dura la corrida) no aportan nada a ninguna suma hasta que se
    # les registre el kg real - ver comentario en schema.sql.
    lotes_con_kg = [l for l in lotes if l.get("kg_asignados") is not None]
    n_pendientes = len(lotes) - len(lotes_con_kg)

    kg_total = float(corrida.get("kg_asignados_total") or 0)
    kg_objetivo = corrida.get("mp_kg_objetivo")
    kg_silo = sum(float(l["kg_asignados"]) for l in lotes_con_kg if l.get("tipo_almacen_origen") == "SILO")
    kg_bines = sum(float(l["kg_asignados"]) for l in lotes_con_kg if l.get("tipo_almacen_origen") == "BINES")

    brix_pond = sum(float(l["kg_asignados"]) * float(l["brix_recepcion"]) for l in lotes_con_kg if l.get("brix_recepcion") is not None)
    acidez_pond = sum(float(l["kg_asignados"]) * float(l["acidez"]) for l in lotes_con_kg if l.get("acidez") is not None)
    kg_con_calidad = sum(float(l["kg_asignados"]) for l in lotes_con_kg if l.get("brix_recepcion") is not None)
    brix_prom = brix_pond / kg_con_calidad if kg_con_calidad > 0 else None
    ratio_prom = brix_pond / acidez_pond if acidez_pond > 0 else None

    # si hay mediciones reales de tanque, mandan sobre el estimado de los
    # lotes - el estimado no puede capturar el enjuague ni otros ajustes de
    # estandarización, así que en cuanto hay UNA medición real, esa es la
    # que se muestra (ver comentario en schema.sql, tabla mediciones_tanque).
    mediciones = mediciones or []
    brix_medido = ratio_medido = None
    if mediciones:
        litros_pond = sum(float(m.get("litros") or 1) for m in mediciones)
        brix_pond_med = sum(float(m.get("litros") or 1) * float(m["brix_final"]) for m in mediciones)
        acidez_pond_med = sum(float(m.get("litros") or 1) * float(m["acidez"]) for m in mediciones)
        brix_medido = brix_pond_med / litros_pond
        acidez_medido = acidez_pond_med / litros_pond
        ratio_medido = brix_medido / acidez_medido if acidez_medido > 0 else None

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

    # Solo se arma una tarjeta cuando de verdad hay un dato que mostrar - una
    # corrida recién creada (sin MP objetivo de Trazabilidad todavía, sin
    # productos, sin paradas) no debería mostrar 5 tarjetas en blanco con "—",
    # eso se ve roto. Las que sí aplican se acomodan solas de a 3 por fila.
    ancho_kpi = (PAGE_W - 2 * MARGIN - 2 * 6) / 3
    kpis = [("MP consumida", f"{fmt_kg(kg_total)} kg", TEXT)]
    if kg_objetivo:
        kpis.append(("MP objetivo (Trazabilidad)", f"{fmt_kg(float(kg_objetivo))} kg", TEXT))
        kpis.append(("Diferencia", f"{fmt_kg(float(kg_objetivo) - kg_total)} kg",
                     OK if estado == "cuadra" else WARN if estado == "incompleto" else BAD if estado == "excedido" else TEXT))
    if brix_medido is not None:
        kpis.append(("Brix real (medido)", fmt_num(brix_medido, 2), OK))
        kpis.append(("Ratio real (medido)", fmt_num(ratio_medido, 2), OK))
    elif brix_prom is not None:
        kpis.append(("Brix ponderado (estimado)", fmt_num(brix_prom, 2), TEXT))
        if ratio_prom is not None:
            kpis.append(("Ratio ponderado (estimado)", fmt_num(ratio_prom, 2), TEXT))
    if rendimiento:
        kpis.append(("Rendimiento", f"{rendimiento * 100:.1f} %", TEXT))
    if pt_total:
        kpis.append(("Producto terminado", f"{fmt_kg(pt_total)} kg", TEXT))
    if litros_total:
        kpis.append(("Volumen", f"{fmt_kg(litros_total)} L", TEXT))
    kpis.append((
        "Tiempo parado",
        (fmt_minutos(min_parado) + (" · hay una en curso" if hay_en_curso else "")) if paradas else "sin paradas",
        BAD if min_parado > 60 else TEXT,
    ))

    filas_kpi = []
    for i in range(0, len(kpis), 3):
        grupo = kpis[i:i + 3]
        fila = [kpi_card(label, valor, ancho_kpi, color_valor=color) for label, valor, color in grupo]
        fila += [""] * (3 - len(fila))  # ultima fila incompleta: relleno en blanco, sin tarjeta
        filas_kpi.append(fila)
    kpi_grid = Table(filas_kpi, colWidths=[ancho_kpi] * 3, spaceBefore=4)
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
    if n_pendientes:
        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph(
            f"{n_pendientes} lote(s) con kg pendiente todavía no se cuentan en los KPIs de arriba ni en el gráfico Silo/Bines - "
            "se sabrá cuánto entró recién cuando se registre el kg real.",
            style_footnote,
        ))

    if mediciones:
        story.append(Spacer(1, 8 * mm))
        story.append(Paragraph(f"Tanques medidos ({len(mediciones)})", style_section))
        story.append(_tabla_mediciones(mediciones))

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
