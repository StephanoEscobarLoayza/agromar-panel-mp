"""Genera el PDF de "Stock de materia prima" - una foto del saldo que el
sistema calcula en vivo para cada lote (Silo + Bines), en un momento dado.
No agrega ningún conteo físico manual - es exactamente lo que ya muestra
v_saldo_lotes, solo que ordenado y presentado para imprimir o compartir."""
from io import BytesIO

from reportlab.lib.units import mm
from reportlab.platypus import Table, TableStyle, Paragraph, Spacer

from reporte_base import (
    FOREST_2, CITRUS, TEXT_2,
    PAGE_W, MARGIN, style_kpi_label, style_footnote,
    fmt_kg, fmt_num, fmt_fecha, Banda, header_footer, kpi_card, encabezado, seccion, tabla, nuevo_doc,
    ahora_peru,
)


def _tag_almacen(t):
    return "Bines" if t == "BINES" else "Silo" if t == "SILO" else "Mixto"


def _tabla_stock(lotes):
    """Lotes con saldo PARCIAL de verdad - ya se les asignó algo a alguna
    corrida y todavía les queda un resto."""
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


def _tabla_completos(lotes):
    """Lotes SIN TOCAR todavía - el "saldo" acá es el peso completo tal
    como llegó, no el resto de haber usado algo. No es lo mismo que un
    saldo parcial, por eso van en su propia tabla."""
    filas = [[
        f"#{l['numero']}",
        l.get("proveedor") or "—",
        _tag_almacen(l.get("tipo_almacen")),
        fmt_fecha(l.get("fecha_ingreso")),
        fmt_kg(l.get("peso_neto_kg")),
    ] for l in lotes]
    return tabla(
        ["Lote", "Proveedor", "Almacén", "Ingreso", "Peso"], filas,
        [18 * mm, 60 * mm, 20 * mm, 26 * mm, 30 * mm], align_derecha_desde=4,
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

    # "saldo" en sentido estricto es lo que sobra de un lote YA EMPEZADO -
    # un lote que todavía no se tocó (kg_saldo == su peso neto completo) no
    # tiene ningún sobrante, está completo tal como llegó. Mezclar los dos
    # en una sola lista de "lotes con saldo" confunde (Stephano lo notó) -
    # se separan en dos tablas.
    lotes_completos = [l for l in lotes if abs(float(l["kg_saldo"]) - float(l["peso_neto_kg"])) < 0.01]
    lotes_parciales = [l for l in lotes if abs(float(l["kg_saldo"]) - float(l["peso_neto_kg"])) >= 0.01]

    ahora = ahora_peru()

    story = [
        encabezado(
            "Inventario de planta", "Stock de materia prima",
            f"Foto del sistema al {ahora.strftime('%d/%m/%Y')}, {ahora.strftime('%H:%M')}",
        ),
        Spacer(1, 10 * mm),
        *seccion("Resumen"),
    ]

    ancho_kpi = (PAGE_W - 2 * MARGIN - 3 * 6) / 4
    fila1 = [
        kpi_card("Stock total", f"{fmt_kg(kg_total)} kg", ancho_kpi),
        kpi_card("En Silo", f"{fmt_kg(kg_silo)} kg", ancho_kpi),
        kpi_card("En Bines", f"{fmt_kg(kg_bines)} kg", ancho_kpi),
        kpi_card("Lotes en planta", fmt_num(len(lotes), 0), ancho_kpi),
    ]
    kpi_grid = Table([fila1], colWidths=[ancho_kpi] * 4, spaceBefore=4)
    kpi_grid.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(kpi_grid)
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(
        f"{en_proceso} lote(s) en proceso ahora mismo · {en_espera} en espera · "
        f"{len(lotes_completos)} completo(s) sin tocar · {len(lotes_parciales)} con saldo parcial.",
        style_kpi_label,
    ))
    story.append(Spacer(1, 4 * mm))

    if kg_silo > 0 or kg_bines > 0:
        story.extend(seccion("Stock por almacén"))
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

    story.extend(seccion(f"Lotes con saldo parcial, del más antiguo al más nuevo ({len(lotes_parciales)})"))
    story.append(Paragraph(
        "Ya se les asignó algo a alguna corrida y todavía les queda un resto por usar.",
        style_footnote,
    ))
    story.append(Spacer(1, 3 * mm))
    story.append(_tabla_stock(lotes_parciales) if lotes_parciales else Paragraph("Ninguno en este momento.", style_footnote))

    story.append(Spacer(1, 8 * mm))
    story.extend(seccion(f"Lotes completos, todavía sin tocar ({len(lotes_completos)})"))
    story.append(Paragraph(
        "Llegaron a planta pero todavía no se les registró ningún consumo - el peso de acá es el completo, no un sobrante.",
        style_footnote,
    ))
    story.append(Spacer(1, 3 * mm))
    story.append(_tabla_completos(lotes_completos) if lotes_completos else Paragraph("Ninguno en este momento.", style_footnote))

    story.append(Spacer(1, 10 * mm))
    story.append(Paragraph(
        "Cálculo en vivo del sistema (peso neto menos lo ya asignado a corridas), para ir siguiendo el stock mientras dura la "
        "campaña. El conteo físico real de la MP se hace al cierre y ese es el número que manda. "
        "Solo se cuentan lotes \"En proceso\" o \"En espera\" - un lote \"Procesado\" con algo de saldo casi siempre es ruido de "
        "medición de Trazabilidad, no materia prima real disponible para usar.",
        style_footnote,
    ))

    doc.build(
        story,
        onFirstPage=lambda c, d: header_footer(c, d, "Stock de materia prima"),
        onLaterPages=lambda c, d: header_footer(c, d, "Stock de materia prima"),
    )
    return buf.getvalue()
