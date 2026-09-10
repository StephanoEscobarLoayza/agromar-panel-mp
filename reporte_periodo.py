"""Genera el PDF de "Resumen de producción por período" (botón en Corridas).
Junta lo que pasó en un rango de fechas: MP procesada, corridas, tipos de
proceso, producto terminado, proveedores y paradas. Piezas compartidas
(paleta, KPIs, tablas) viven en reporte_base.py."""
import unicodedata
from io import BytesIO

from reportlab.lib.units import mm
from reportlab.platypus import Table, TableStyle, Paragraph, Spacer

from reporte_base import (
    FOREST_2, CITRUS, OK, BAD, TEXT,
    PAGE_W, MARGIN, style_kpi_label, style_footnote,
    fmt_kg, fmt_num, fmt_fecha, fmt_minutos, Banda, header_footer, kpi_card,
    encabezado, seccion, tabla, nuevo_doc, ahora_peru,
)

TIPOS_ORDEN = ["JSA", "JCC", "JCC TASTE", "JSC", "ICEGEN"]


def _mp_de_corrida(c):
    return float(c.get("mp_kg_objetivo") or c.get("kg_asignados_total") or 0)


def _es_pt(nombre):
    n = "".join(
        ch for ch in unicodedata.normalize("NFD", (nombre or "").lower())
        if unicodedata.category(ch) != "Mn"
    )
    return "enjuague" not in n


def _rend_txt(r):
    if r is None:
        return "—"
    r = float(r)
    return f"{r * 100:.1f} %" if r < 2 else fmt_num(r, 1)


def generar_reporte_periodo_pdf(desde, hasta, corridas, productos, proveedores, paradas) -> bytes:
    """desde/hasta: date. corridas: filas de v_cuadre_corridas + corridas
    (nombre, fecha_inicio, tipo_proceso, mp_kg_objetivo, kg_asignados_total,
    rendimiento) del período. productos: corrida_productos del período
    (corrida, producto, pt_kg, volumen_litros). proveedores: [(proveedor, kg)]
    ya agregado. paradas: [(motivo, veces, minutos)] ya agregado."""
    buf = BytesIO()
    doc = nuevo_doc(buf, f"Resumen de producción {desde} a {hasta}")

    mp_total = sum(_mp_de_corrida(c) for c in corridas)
    prod_pt = [p for p in productos if _es_pt(p.get("producto"))]
    pt_total = sum(float(p["pt_kg"]) for p in prod_pt if p.get("pt_kg") is not None)
    litros_total = sum(float(p["volumen_litros"]) for p in prod_pt if p.get("volumen_litros") is not None)
    min_parado = sum(float(m or 0) for _, _, m in paradas)

    periodo = f"{fmt_fecha(desde)} — {fmt_fecha(hasta)}"
    story = [
        encabezado("Resumen de producción", "Por período", periodo),
        Spacer(1, 10 * mm),
        *seccion("Resumen"),
    ]

    ancho_kpi = (PAGE_W - 2 * MARGIN - 2 * 6) / 3
    kpis = [
        ("MP procesada", f"{fmt_kg(mp_total)} kg", TEXT),
        ("Corridas", str(len(corridas)), TEXT),
    ]
    if pt_total:
        kpis.append(("Producto terminado", f"{fmt_kg(pt_total)} kg", TEXT))
    if litros_total:
        kpis.append(("Volumen", f"{fmt_kg(litros_total)} L", TEXT))
    kpis.append((
        "Tiempo parado",
        fmt_minutos(min_parado) if paradas else "sin paradas",
        BAD if min_parado > 60 else TEXT,
    ))
    filas_kpi = []
    for i in range(0, len(kpis), 3):
        grupo = kpis[i:i + 3]
        tarjetas = [kpi_card(l, v, ancho_kpi, color_valor=col) for l, v, col in grupo]
        faltan = 3 - len(tarjetas)
        fila = (["", tarjetas[0], ""] if faltan == 2 else tarjetas + [""] * faltan)
        filas_kpi.append(fila)
    grid = Table(filas_kpi, colWidths=[ancho_kpi] * 3, spaceBefore=4)
    grid.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(grid)
    story.append(Spacer(1, 4 * mm))
    if pt_total:
        story.append(Paragraph(
            "Producto terminado y volumen: solo de las corridas del período que ya tienen "
            "tambores cargados a mano. El rendimiento se muestra corrida por corrida (cada "
            "hoja de Trazabilidad lo mide a su manera según el proceso).",
            style_footnote,
        ))

    # --- corridas del período ---
    story.append(Spacer(1, 6 * mm))
    story.extend(seccion(f"Corridas del período ({len(corridas)})"))
    if corridas:
        pt_por_corrida = {}
        for p in prod_pt:
            if p.get("pt_kg") is not None:
                pt_por_corrida[p["corrida"]] = pt_por_corrida.get(p["corrida"], 0) + float(p["pt_kg"])
        filas = [[
            c.get("nombre") or "—",
            fmt_fecha(c.get("fecha_inicio")),
            c.get("tipo_proceso") or "—",
            fmt_kg(_mp_de_corrida(c)),
            fmt_kg(pt_por_corrida[c["nombre"]]) if c.get("nombre") in pt_por_corrida else "—",
            _rend_txt(c.get("rendimiento")),
        ] for c in corridas]
        story.append(tabla(
            ["Corrida", "Fecha", "Tipo", "MP kg", "PT kg", "Rend."], filas,
            [52 * mm, 24 * mm, 24 * mm, 26 * mm, 24 * mm, 18 * mm], align_derecha_desde=3,
        ))
    else:
        story.append(Paragraph("Ninguna corrida en este período.", style_footnote))

    # --- MP por tipo de proceso ---
    if corridas:
        por_tipo = {}
        for c in corridas:
            t = c.get("tipo_proceso") if c.get("tipo_proceso") in TIPOS_ORDEN else "Otro"
            d = por_tipo.setdefault(t, {"kg": 0.0, "n": 0})
            d["kg"] += _mp_de_corrida(c)
            d["n"] += 1
        orden = [t for t in TIPOS_ORDEN if t in por_tipo] + (["Otro"] if "Otro" in por_tipo else [])
        maximo = max((por_tipo[t]["kg"] for t in orden), default=1) or 1
        story.append(Spacer(1, 8 * mm))
        story.extend(seccion("MP por tipo de proceso"))
        ancho_barra = PAGE_W - 2 * MARGIN - 34 * mm
        filas = []
        for t in orden:
            d = por_tipo[t]
            etq = f"{t}"
            filas.append([
                Paragraph(f"{etq} <font size=7 color='#8B9186'>({d['n']})</font>", style_kpi_label),
                Banda(ancho_barra, d["kg"], maximo, FOREST_2, fmt_kg(d["kg"]) + " kg"),
            ])
        tt = Table(filas, colWidths=[34 * mm, ancho_barra])
        tt.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
        story.append(tt)

    # --- top proveedores ---
    if proveedores:
        story.append(Spacer(1, 8 * mm))
        story.extend(seccion("Proveedores del período (por MP entregada)"))
        maximo = float(proveedores[0][1]) or 1
        ancho_barra = PAGE_W - 2 * MARGIN - 62 * mm
        filas = []
        for prov, kg in proveedores:
            filas.append([
                Paragraph((prov or "—")[:38], style_kpi_label),
                Banda(ancho_barra, float(kg), maximo, CITRUS, fmt_kg(float(kg)) + " kg"),
            ])
        tt = Table(filas, colWidths=[62 * mm, ancho_barra])
        tt.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
        story.append(tt)

    # --- paradas ---
    story.append(Spacer(1, 8 * mm))
    story.extend(seccion("Paradas del período"))
    if paradas:
        filas = [[m or "—", str(int(n)), fmt_minutos(mins)] for m, n, mins in paradas]
        story.append(tabla(
            ["Motivo", "Veces", "Tiempo"], filas,
            [92 * mm, 25 * mm, 40 * mm], align_derecha_desde=1,
        ))
    else:
        story.append(Paragraph("Sin paradas registradas en este período.", style_footnote))

    story.append(Spacer(1, 10 * mm))
    story.append(Paragraph(
        f"Generado el {ahora_peru().strftime('%d/%m/%Y %H:%M')} desde el Panel de cuadre de producción. "
        f"Las corridas se agrupan por su fecha de inicio.",
        style_footnote,
    ))

    doc.build(
        story,
        onFirstPage=lambda c, d: header_footer(c, d, "Resumen de producción"),
        onLaterPages=lambda c, d: header_footer(c, d, "Resumen de producción"),
    )
    return buf.getvalue()
