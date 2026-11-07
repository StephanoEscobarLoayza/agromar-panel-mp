"""Genera el PDF de "Resumen de producción por período" (botón en Corridas).
Junta lo que pasó en un rango de fechas: MP procesada, corridas, tipos de
proceso, producto terminado, proveedores y paradas. Piezas compartidas
(paleta, KPIs, tablas) viven en reporte_base.py."""
from datetime import timedelta
from io import BytesIO

from reportlab.lib.units import mm
from reportlab.platypus import Table, TableStyle, Paragraph, Spacer

from reporte_base import (
    FOREST_2, CITRUS, BAD, TEXT, TEXT_MUTED, MESES_ES,
    PAGE_W, MARGIN, style_kpi_label, style_footnote,
    fmt_kg, fmt_num, fmt_fecha, fmt_minutos, header_footer, kpi_card,
    encabezado, seccion, tabla, nuevo_doc, _parse_dt,
)
from reporte_graficos import BarrasH, BarraCelda, ColumnasApiladas, color_tipo, corto_proveedor

TIPOS_ORDEN = ["JSA", "JCC", "JCC TASTE", "JSC", "ICEGEN", "PRE CC"]
# ancho que de verdad tiene el área de contenido: el marco de la página deja 6 pt de relleno a cada lado
ANCHO_UTIL = PAGE_W - 2 * MARGIN - 12


def _tipo_de(c):
    return c.get("tipo_proceso") if c.get("tipo_proceso") in TIPOS_ORDEN else "Otro"


def _como_fecha(x):
    d = _parse_dt(x)
    return d.date() if hasattr(d, "date") else d


def _columnas_de_tiempo(desde, hasta):
    """Una columna por día (períodos de hasta 35 días) o por semana (más largos). Devuelve (claves, etiquetas, clave_de(fecha),
    por_semana): las etiquetas llevan el día y, solo cuando cambia, el mes."""
    dias = (hasta - desde).days + 1
    if dias <= 35:
        claves = [desde + timedelta(days=i) for i in range(dias)]
        clave_de, por_semana = (lambda d: d), False
    else:
        lunes = desde - timedelta(days=desde.weekday())
        claves = [lunes + timedelta(days=7 * i) for i in range((hasta - lunes).days // 7 + 1)]
        clave_de, por_semana = (lambda d: d - timedelta(days=d.weekday())), True
    etiquetas, mes_previo = [], None
    for k in claves:
        etiquetas.append((k.strftime("%d"), MESES_ES.get(k.strftime("%b"), k.strftime("%b")) if k.month != mes_previo else None))
        mes_previo = k.month
    return claves, etiquetas, clave_de, por_semana


def _mp_de_corrida(c):
    return float(c.get("mp_kg_objetivo") or c.get("kg_asignados_total") or 0)


def _es_pt(producto: dict) -> bool:
    """Si esta fila cuenta como PT real - marca a mano por fila
    (`corrida_productos.cuenta_como_pt`), no se adivina por el nombre. Un
    insumo de "entrada" (reposición para subir Brix, etc.) nunca cuenta."""
    if producto.get("tipo", "salida") == "entrada":
        return False
    return producto.get("cuenta_como_pt", True) is not False


def _aplica_descuento(producto: dict) -> bool:
    """Solo aplica a filas "entrada" - mismo criterio que reporte_corrida.py:
    el mismo campo (cuenta_como_pt) significa "¿se resta del total?" para un
    insumo de entrada, no "¿cuenta como PT?"."""
    if producto.get("tipo", "salida") != "entrada":
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
    # el PT real siempre resta los insumos de "entrada" (reposición para subir
    # Brix, etc.) del bruto que reportan las filas de "salida" - Calidad
    # registra el bruto tal cual (ej. 78 cilindros), Producción no lo produjo
    # todo ella. Ver mismo criterio en reporte_corrida.py.
    prod_pt = [p for p in productos if _es_pt(p)]
    prod_entrada = [p for p in productos if p.get("tipo") == "entrada"]
    prod_entrada_desc = [p for p in prod_entrada if _aplica_descuento(p)]
    pt_total = sum(float(p["pt_kg"]) for p in prod_pt if p.get("pt_kg") is not None) - \
        sum(float(p["pt_kg"]) for p in prod_entrada_desc if p.get("pt_kg") is not None)
    litros_total = sum(float(p["volumen_litros"]) for p in prod_pt if p.get("volumen_litros") is not None) - \
        sum(float(p["volumen_litros"]) for p in prod_entrada_desc if p.get("volumen_litros") is not None)
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
            "hay ninguno de los dos. No se promedia entre corridas.",
            style_footnote,
        ))

    columnas, etiquetas_t, clave_de, por_semana = _columnas_de_tiempo(desde, hasta)

    # --- MP procesada en el tiempo, apilada por tipo de proceso ---
    if corridas and mp_total > 0:
        tipos_presentes = [t for t in TIPOS_ORDEN + ["Otro"] if any(_tipo_de(c) == t for c in corridas)]
        pos = {k: n for n, k in enumerate(columnas)}
        valores = [[0.0] * len(tipos_presentes) for _ in columnas]
        for c in corridas:
            f = _como_fecha(c.get("fecha_inicio"))
            if f is not None and clave_de(f) in pos:
                valores[pos[clave_de(f)]][tipos_presentes.index(_tipo_de(c))] += _mp_de_corrida(c)
        story.append(Spacer(1, 6 * mm))
        story.extend(seccion("MP procesada en el tiempo"))
        story.append(ColumnasApiladas(
            ANCHO_UTIL, 150, etiquetas_t, [(t, color_tipo(t)) for t in tipos_presentes], valores,
        ))
        story.append(Spacer(1, 1 * mm))
        story.append(Paragraph(
            ("Kg de MP por semana (cada columna parte del lunes), según el tipo de proceso de cada corrida." if por_semana
             else "Kg de MP por día, según el tipo de proceso de cada corrida."), style_footnote))

    # --- MP por tipo de proceso ---
    if corridas:
        por_tipo = {}
        for c in corridas:
            d = por_tipo.setdefault(_tipo_de(c), {"kg": 0.0, "n": 0})
            d["kg"] += _mp_de_corrida(c)
            d["n"] += 1
        orden = [t for t in TIPOS_ORDEN + ["Otro"] if t in por_tipo]
        story.append(Spacer(1, 6 * mm))
        story.extend(seccion("MP por tipo de proceso"))
        story.append(BarrasH(ANCHO_UTIL, [{
            "etiqueta": t, "valor": por_tipo[t]["kg"], "texto": fmt_kg(por_tipo[t]["kg"]) + " kg",
            "nota": f"{por_tipo[t]['n']} {'corrida' if por_tipo[t]['n'] == 1 else 'corridas'} · {por_tipo[t]['kg'] / mp_total * 100:.0f} %" if mp_total > 0
            else f"{por_tipo[t]['n']} corrida(s)",
            "color": color_tipo(t),
        } for t in orden], ancho_etq=70))

    # --- corridas del período ---
    story.append(Spacer(1, 6 * mm))
    story.extend(seccion(f"Corridas del período ({len(corridas)})", largo=True))
    if corridas:
        pt_por_corrida = {}
        for p in prod_pt:
            if p.get("pt_kg") is not None:
                pt_por_corrida[p["corrida"]] = pt_por_corrida.get(p["corrida"], 0) + float(p["pt_kg"])
        for p in prod_entrada_desc:
            if p.get("pt_kg") is not None:
                pt_por_corrida[p["corrida"]] = pt_por_corrida.get(p["corrida"], 0) - float(p["pt_kg"])
        maximo_mp = max((_mp_de_corrida(c) for c in corridas), default=0)
        ancho_mp = 46 * mm - 12
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
                BarraCelda(ancho_mp, mp_c, maximo_mp, color_tipo(_tipo_de(c)), fmt_kg(mp_c)),
                fmt_kg(pt_c) if pt_c else "—",
                _rend_txt(rend),
            ])
        story.append(tabla(
            ["Corrida", "Fecha", "Tipo", "MP kg", "PT kg", "Rend."], filas,
            [44 * mm, 23 * mm, 22 * mm, 46 * mm, 20 * mm, 16 * mm], align_derecha_desde=4,
        ))
        story.append(Spacer(1, 2 * mm))
        story.append(Paragraph("La barra de «MP kg» lleva el color del tipo de proceso (mismos colores que en Dashboards).", style_footnote))
    else:
        story.append(Paragraph("Ninguna corrida en este período.", style_footnote))

    # --- top proveedores ---
    if proveedores:
        story.append(Spacer(1, 8 * mm))
        story.extend(seccion("Proveedores del período (por MP entregada)"))
        total_prov = sum(float(kg) for _, kg in proveedores) or 1
        story.append(BarrasH(ANCHO_UTIL, [{
            "etiqueta": corto_proveedor(prov), "valor": float(kg), "texto": fmt_kg(float(kg)) + " kg",
            "nota": f"{float(kg) / total_prov * 100:.0f} %", "color": FOREST_2,
        } for prov, kg in proveedores], ancho_etq=182))
        story.append(Spacer(1, 1 * mm))
        story.append(Paragraph("Los nombres van abreviados en el gráfico (S.A.C., Coop.). El porcentaje es sobre la MP de los proveedores mostrados.", style_footnote))

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
                k = p.get(clave) or "Sin especificar"
                acc[k] = acc.get(k, 0.0) + float(p.get("minutos") or 0)
            return sorted(acc.items(), key=lambda kv: kv[1], reverse=True)

        def _barras(titulo, items):
            items = [(k, m) for k, m in items if m > 0][:10]
            if not items:
                return
            total = sum(m for _, m in items) or 1
            story.append(Spacer(1, 5 * mm))
            story.append(Paragraph(titulo, style_kpi_label))
            story.append(Spacer(1, 2 * mm))
            story.append(BarrasH(ANCHO_UTIL, [{
                "etiqueta": str(k), "valor": m, "texto": fmt_minutos(m), "nota": f"{m / total * 100:.0f} %", "color": CITRUS,
            } for k, m in items], ancho_etq=120))

        _barras("Por área / proceso", _sumar("area_proceso"))
        _barras("Por tipo de parada", _sumar("tipo_parada"))

        # por día (o por semana): columnas de minutos parados
        pos = {k: n for n, k in enumerate(columnas)}
        minutos = [[0.0] for _ in columnas]
        for p in paradas:
            f = _como_fecha(p.get("hora_inicio"))
            if f is not None and clave_de(f) in pos:
                minutos[pos[clave_de(f)]][0] += float(p.get("minutos") or 0)
        if any(m[0] > 0 for m in minutos):
            story.append(Spacer(1, 5 * mm))
            story.append(Paragraph("Por semana" if por_semana else "Por día", style_kpi_label))
            story.append(Spacer(1, 2 * mm))
            story.append(ColumnasApiladas(ANCHO_UTIL, 130, etiquetas_t, [("Tiempo parado", CITRUS)], minutos, unidad_eje="min"))

    doc.build(
        story,
        onFirstPage=lambda c, d: header_footer(c, d, "Resumen de producción"),
        onLaterPages=lambda c, d: header_footer(c, d, "Resumen de producción"),
    )
    return buf.getvalue()
