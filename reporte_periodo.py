"""Genera el PDF de "Resumen de producción por período" (botón en Corridas).
Junta lo que pasó en un rango de fechas: MP procesada, corridas, tipos de
proceso, producto terminado, proveedores y paradas. Piezas compartidas
(paleta, KPIs, tablas) viven en reporte_base.py."""
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


def _es_pt(producto: dict) -> bool:
    """Si esta fila cuenta como PT real - marca a mano por fila
    (`corrida_productos.cuenta_como_pt`), no se adivina por el nombre. Un
    insumo de "entrada" (reposición para subir Brix, etc.) nunca cuenta."""
    if producto.get("tipo", "salida") == "entrada":
        return False
    return producto.get("cuenta_como_pt", True) is not False


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
    ya agregado. paradas: filas de paradas del período (hora_inicio,
    area_proceso, tipo_parada, minutos)."""
    buf = BytesIO()
    doc = nuevo_doc(buf, f"Resumen de producción {desde} a {hasta}")

    mp_total = sum(_mp_de_corrida(c) for c in corridas)
    prod_pt = [p for p in productos if _es_pt(p)]
    pt_total = sum(float(p["pt_kg"]) for p in prod_pt if p.get("pt_kg") is not None)
    litros_total = sum(float(p["volumen_litros"]) for p in prod_pt if p.get("volumen_litros") is not None)
    min_parado = sum(float(p.get("minutos") or 0) for p in paradas)

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
        fmt_minutos(min_parado) if paradas else "Sin paradas",
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
    if corridas:
        story.append(Spacer(1, 4 * mm))
        story.append(Paragraph(
            "Producto terminado y volumen: solo de las corridas con tambores cargados a mano. "
            "El rendimiento (columna «Rend.») se muestra corrida por corrida: sale de PT kg ÷ MP kg "
            "si la corrida ya tiene tambores, o del que trae su hoja de Trazabilidad; «—» si aún no "
            "hay ninguno de los dos. No se promedia — cada proceso mide el rendimiento a su manera.",
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
        filas = []
        for c in corridas:
            pt_c = pt_por_corrida.get(c.get("nombre"))
            mp_c = _mp_de_corrida(c)
            # el rendimiento sale de los tambores cargados (PT kg / MP kg) si los
            # hay; si no, del que trae la hoja de Trazabilidad de esa corrida
            rend = (pt_c / mp_c) if (pt_c and mp_c > 0) else c.get("rendimiento")
            filas.append([
                c.get("nombre") or "—",
                fmt_fecha(c.get("fecha_inicio")),
                c.get("tipo_proceso") or "—",
                fmt_kg(mp_c),
                fmt_kg(pt_c) if pt_c else "—",
                _rend_txt(rend),
            ])
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

    # --- paradas: total + cortes por área, por tipo y por día ---
    story.append(Spacer(1, 8 * mm))
    story.extend(seccion("Paradas del período"))
    if not paradas:
        story.append(Paragraph("Sin paradas registradas en este período.", style_footnote))
    else:
        n_paradas = len(paradas)
        story.append(Paragraph(
            f"{fmt_minutos(min_parado)} parados en {n_paradas} parada(s).", style_kpi_label,
        ))

        def _sumar(clave):
            acc = {}
            for p in paradas:
                k = (p.get(clave) or "Sin especificar") if clave != "dia" else fmt_fecha(p.get("hora_inicio"))
                acc[k] = acc.get(k, 0.0) + float(p.get("minutos") or 0)
            return sorted(acc.items(), key=lambda kv: kv[1], reverse=True)

        def _barras(titulo, items, color, ancho_etq):
            if not any(m > 0 for _, m in items):
                return
            story.append(Spacer(1, 5 * mm))
            story.append(Paragraph(titulo, style_kpi_label))
            story.append(Spacer(1, 2 * mm))
            maximo = max((m for _, m in items), default=1) or 1
            ancho_barra = PAGE_W - 2 * MARGIN - ancho_etq - 4 * mm
            filas = [[
                Paragraph(str(etq)[:34], style_kpi_label),
                Banda(ancho_barra, m, maximo, color, fmt_minutos(m)),
            ] for etq, m in items]
            tt = Table(filas, colWidths=[ancho_etq, ancho_barra])
            tt.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]))
            story.append(tt)

        _barras("Por área / proceso", _sumar("area_proceso"), FOREST_2, 40 * mm)
        _barras("Por tipo de parada", _sumar("tipo_parada"), CITRUS, 40 * mm)
        _barras("Por día", _sumar("dia"), FOREST_2, 30 * mm)

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
