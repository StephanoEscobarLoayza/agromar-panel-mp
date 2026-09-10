"""Genera el PDF de cuadre de una corrida (botón "Reporte PDF" en Corridas).
Piezas compartidas (paleta, tarjetas KPI, etc.) viven en reporte_base.py."""
import unicodedata
from io import BytesIO

from reportlab.lib.units import mm
from reportlab.platypus import Table, TableStyle, Paragraph, Spacer

from reporte_base import (
    FOREST_2, CITRUS, OK, BAD, TEXT,
    PAGE_W, MARGIN, style_kpi_label, style_footnote,
    fmt_kg, fmt_num, fmt_fecha, fmt_hora, fmt_minutos, Banda, header_footer, kpi_card,
    encabezado, seccion, tabla, nuevo_doc, ahora_peru,
)


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


def _tabla_lotes_simple(lotes, con_saldo=False):
    """Lista chica de lotes (solo número + proveedor, y el saldo si aplica) -
    para las dos sub-listas de "Estado de estos lotes hoy" (terminados / con
    saldo para la siguiente corrida)."""
    if con_saldo:
        filas = [[f"#{l['lote_numero']}", l.get("proveedor") or "—", f"{fmt_kg(float(l['kg_saldo']))} kg"] for l in lotes]
        return tabla(["Lote", "Proveedor", "Saldo hoy"], filas, [22 * mm, 91 * mm, 60 * mm], align_derecha_desde=2)
    filas = [[f"#{l['lote_numero']}", l.get("proveedor") or "—"] for l in lotes]
    return tabla(["Lote", "Proveedor"], filas, [22 * mm, 151 * mm])


def _cuenta_como_pt(producto: dict) -> bool:
    """El enjuague sale de la línea pero no es producto terminado - no suma al
    PT kg, ni al rendimiento, ni al volumen de la corrida. Cualquier fila cuyo
    nombre mencione "enjuague" (con o sin tildes, mayúsculas, etc.) se deja
    fuera de esos totales. Igual se muestra normal en la tabla de productos.
    Todo lo demás (Jugo Simple Aséptico, Jugo Concentrado Congelado, etc.) sí
    cuenta."""
    nombre = "".join(
        c for c in unicodedata.normalize("NFD", (producto.get("producto") or "").lower())
        if unicodedata.category(c) != "Mn"
    )
    return "enjuague" not in nombre


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
    brix_medido = ratio_medido = brix_inicial_medido = None
    if mediciones:
        litros_pond = sum(float(m.get("litros") or 1) for m in mediciones)
        brix_pond_med = sum(float(m.get("litros") or 1) * float(m["brix_final"]) for m in mediciones)
        acidez_pond_med = sum(float(m.get("litros") or 1) * float(m["acidez"]) for m in mediciones)
        brix_medido = brix_pond_med / litros_pond
        acidez_medido = acidez_pond_med / litros_pond
        ratio_medido = brix_medido / acidez_medido if acidez_medido > 0 else None
        # Brix inicial promedio (ponderado por litros) - solo informativo, el
        # número principal sigue siendo el final. Cuenta solo los tanques que
        # tienen inicial cargado (los históricos podrían no tenerlo).
        med_con_ini = [m for m in mediciones if m.get("brix_inicial") is not None]
        if med_con_ini:
            litros_ini = sum(float(m.get("litros") or 1) for m in med_con_ini)
            brix_ini_pond = sum(float(m.get("litros") or 1) * float(m["brix_inicial"]) for m in med_con_ini)
            brix_inicial_medido = brix_ini_pond / litros_ini

    # el enjuague sale de la línea pero no es producto terminado - se deja fuera
    # del PT kg, del rendimiento y del volumen (ver _cuenta_como_pt).
    productos_pt = [p for p in productos if _cuenta_como_pt(p)]
    pt_total = sum(float(p["pt_kg"]) for p in productos_pt if p.get("pt_kg") is not None)
    litros_total = sum(float(p["volumen_litros"]) for p in productos_pt if p.get("volumen_litros") is not None)
    rendimiento = pt_total / kg_total if kg_total > 0 and pt_total > 0 else None

    paradas_cerradas = [p for p in paradas if p.get("duracion_minutos") is not None]
    min_parado = sum(p["duracion_minutos"] for p in paradas_cerradas)
    hay_en_curso = any(p.get("duracion_minutos") is None for p in paradas)

    periodo = f"{fmt_fecha(corrida.get('fecha_inicio'))}" + (
        f" — {fmt_fecha(corrida.get('fecha_final'))}" if corrida.get("fecha_final") else " · en curso"
    )
    story = [
        encabezado("Cuadre de corrida", corrida["nombre"], periodo),
        Spacer(1, 10 * mm),
        *seccion("Resumen"),
    ]

    # No hay "objetivo" de MP contra qué cuadrar - una corrida procesa lo que
    # entra, no una meta fijada de antemano (mismo criterio ya aplicado en
    # Corridas: se sacó el pill Cuadra/Incompleto/Excedido y la comparación
    # contra mp_kg_objetivo de Trazabilidad, ver memoria del proyecto). Solo
    # se arma una tarjeta cuando de verdad hay un dato real que mostrar - una
    # corrida recién creada (sin productos, sin paradas) no debería mostrar
    # tarjetas en blanco con "—", eso se ve roto. Las que sí aplican se
    # acomodan solas de a 3 por fila.
    ancho_kpi = (PAGE_W - 2 * MARGIN - 2 * 6) / 3
    kpis = [("MP consumida", f"{fmt_kg(kg_total)} kg", TEXT)]
    stock_inicio = corrida.get("stock_inicio_kg")
    stock_cierre = corrida.get("stock_cierre_kg")
    if stock_inicio is not None:
        kpis.append(("Stock de MP en piso al inicio", f"{fmt_kg(float(stock_inicio))} kg", TEXT))
    if stock_cierre is not None:
        kpis.append(("Stock de MP en piso al cierre", f"{fmt_kg(float(stock_cierre))} kg", TEXT))
    if brix_medido is not None:
        kpis.append(("Brix final (medido)", fmt_num(brix_medido, 2), OK))
        if brix_inicial_medido is not None:
            kpis.append(("Brix inicial (medido)", fmt_num(brix_inicial_medido, 2), TEXT))
        kpis.append(("Ratio", fmt_num(ratio_medido, 2), OK))
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
        tarjetas = [kpi_card(label, valor, ancho_kpi, color_valor=color) for label, valor, color in grupo]
        faltan = 3 - len(tarjetas)
        if faltan == 2:
            # una sola tarjeta sola en la fila - centrada, no pegada a la izquierda
            fila = ["", tarjetas[0], ""]
        elif faltan == 1:
            fila = tarjetas + [""]
        else:
            fila = tarjetas
        filas_kpi.append(fila)
    kpi_grid = Table(filas_kpi, colWidths=[ancho_kpi] * 3, spaceBefore=4)
    kpi_grid.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(kpi_grid)
    story.append(Spacer(1, 6 * mm))

    if kg_silo > 0 or kg_bines > 0:
        story.extend(seccion("MP por almacén de origen"))
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

    story.extend(seccion(f"Lotes de MP consumidos ({len(lotes)})"))
    story.append(_tabla_lotes(lotes) if lotes else Paragraph("Sin lotes registrados todavía.", style_footnote))
    if n_pendientes:
        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph(
            f"{n_pendientes} lote(s) con kg pendiente todavía no se cuentan en los KPIs de arriba ni en el gráfico Silo/Bines - "
            "se sabrá cuánto entró recién cuando se registre el kg real.",
            style_footnote,
        ))

    if lotes:
        # cuáles de estos lotes ya no tienen nada más que dar (terminaron) y
        # cuáles siguen con saldo que va a seguir alimentando la SIGUIENTE
        # corrida - es una foto de ahora mismo (cuando se genera el reporte),
        # no de cuando cerró esta corrida, porque el saldo de un lote sigue
        # moviéndose mientras otras corridas lo usan.
        lotes_terminados = [l for l in lotes if float(l.get("kg_saldo") or 0) <= 0.01]
        lotes_con_saldo = [l for l in lotes if float(l.get("kg_saldo") or 0) > 0.01]

        story.append(Spacer(1, 8 * mm))
        story.extend(seccion("Estado de estos lotes hoy"))
        story.append(Paragraph(
            "Foto de ahora mismo, no de cuando cerró esta corrida - el saldo de un lote sigue "
            "cambiando mientras otras corridas lo van usando.",
            style_footnote,
        ))
        story.append(Spacer(1, 4 * mm))
        story.append(Paragraph(f"Terminados ({len(lotes_terminados)})", style_kpi_label))
        story.append(Spacer(1, 2 * mm))
        story.append(_tabla_lotes_simple(lotes_terminados) if lotes_terminados else Paragraph("Ninguno todavía.", style_footnote))
        story.append(Spacer(1, 6 * mm))
        story.append(Paragraph(f"Con saldo para la siguiente corrida ({len(lotes_con_saldo)})", style_kpi_label))
        story.append(Spacer(1, 2 * mm))
        story.append(_tabla_lotes_simple(lotes_con_saldo, con_saldo=True) if lotes_con_saldo else Paragraph("Ninguno - todos terminaron.", style_footnote))

    if mediciones:
        story.append(Spacer(1, 8 * mm))
        story.extend(seccion(f"Tanques medidos ({len(mediciones)})"))
        story.append(_tabla_mediciones(mediciones))

    if productos:
        story.append(Spacer(1, 8 * mm))
        story.extend(seccion(f"Productos de salida ({len(productos)})"))
        story.append(_tabla_productos(productos))

    if paradas:
        story.append(Spacer(1, 8 * mm))
        story.extend(seccion(f"Paradas registradas ({len(paradas)})"))
        story.append(_tabla_paradas(paradas))

    story.append(Spacer(1, 10 * mm))
    story.append(Paragraph(f"Generado el {ahora_peru().strftime('%d/%m/%Y %H:%M')} desde el Panel de cuadre de producción.", style_footnote))

    doc.build(
        story,
        onFirstPage=lambda c, d: header_footer(c, d, corrida["nombre"]),
        onLaterPages=lambda c, d: header_footer(c, d, corrida["nombre"]),
    )
    return buf.getvalue()
