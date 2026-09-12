"""Genera el PDF de cuadre de una corrida (botón "Reporte PDF" en Corridas).
Piezas compartidas (paleta, tarjetas KPI, etc.) viven en reporte_base.py."""
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
    filas = []
    for l in lotes:
        es_bines = l.get("tipo_almacen_origen") == "BINES"
        kg_txt = fmt_kg(l["kg_asignados"]) if l.get("kg_asignados") is not None else "pendiente"
        # en bines el conteo de patio manda más que el kg (que es un
        # estimado) - se muestra al lado, no reemplazando el kg.
        if es_bines and l.get("bines_consumidos") is not None:
            kg_txt += f" ({fmt_num(l['bines_consumidos'], 0)} bines)"
        filas.append([
            f"#{l['lote_numero']}",
            l.get("proveedor") or "—",
            "Bines" if es_bines else "Silo" if l.get("tipo_almacen_origen") == "SILO" else "—",
            kg_txt,
            fmt_num(l.get("brix_recepcion"), 2),
            fmt_num(l.get("acidez"), 2),
            fmt_num(l.get("ratio"), 2),
        ])
    return tabla(
        ["Lote", "Proveedor", "Almacén", "Kg usado", "Brix", "Acidez", "Ratio"], filas,
        [22 * mm, 50 * mm, 20 * mm, 31 * mm, 16 * mm, 18 * mm, 16 * mm], align_derecha_desde=3,
    )


def _tabla_lotes_simple(lotes, con_saldo=False, cerrada=False):
    """Lista chica de lotes (solo número + proveedor, y el saldo si aplica) -
    para las dos sub-listas de "Estado de estos lotes hoy/al cerrar"
    (terminados / con saldo para la siguiente corrida)."""
    if con_saldo:
        filas = []
        for l in lotes:
            saldo_txt = f"{fmt_kg(float(l['kg_saldo']))} kg"
            # si el lote es de bines, el saldo en bines dice más que el kg -
            # es lo que de verdad se cuenta en el patio.
            if l.get("tipo_almacen_origen") == "BINES" and l.get("bines_saldo") is not None:
                saldo_txt += f" · {fmt_num(l['bines_saldo'], 0)} bines"
            filas.append([f"#{l['lote_numero']}", l.get("proveedor") or "—", saldo_txt])
        encabezado_saldo = "Saldo al cierre" if cerrada else "Saldo hoy"
        return tabla(["Lote", "Proveedor", encabezado_saldo], filas, [22 * mm, 79 * mm, 72 * mm], align_derecha_desde=2)
    filas = [[f"#{l['lote_numero']}", l.get("proveedor") or "—"] for l in lotes]
    return tabla(["Lote", "Proveedor"], filas, [22 * mm, 151 * mm])


def _es_salida(producto: dict) -> bool:
    """"salida" = lo que produjo esta corrida (va en Productos de salida).
    "entrada" = un insumo que se metió a la corrida desde afuera (ej.
    reposición para subir Brix) - va en Insumos de entrada, nunca es PT."""
    return producto.get("tipo", "salida") != "entrada"


def _cuenta_como_pt(producto: dict) -> bool:
    """Si esta fila suma al PT kg, al rendimiento y al volumen de la corrida.
    Es una marca a mano por fila (`corrida_productos.cuenta_como_pt`), NO se
    adivina por el nombre - antes se descartaba cualquier fila que dijera
    "enjuague", pero eso no cubría casos reales como un saldo de tambor sin
    completar, o un insumo de entrada para subir Brix (Calidad SÍ lo cuenta
    como PT al bajar de tanques, Producción no porque no lo produjo esta
    corrida). Un "entrada" nunca cuenta, sin importar esta marca. Igual se
    muestra normal en la tabla que le toque, solo no suma a esos 3 totales."""
    if not _es_salida(producto):
        return False
    return producto.get("cuenta_como_pt", True) is not False


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


def _tabla_insumos_entrada(insumos):
    filas = [[
        p.get("producto") or "—",
        fmt_num(p.get("tambores"), 0),
        fmt_kg(p.get("peso_neto_tambor_kg")),
        fmt_kg(p.get("pt_kg")),
        p.get("observaciones") or "—",
    ] for p in insumos]
    return tabla(
        ["Insumo", "Tambores", "Peso/tambor", "Kg", "Detalle"], filas,
        [40 * mm, 20 * mm, 25 * mm, 25 * mm, 46 * mm], align_derecha_desde=1,
    )


def _tabla_paradas(paradas):
    filas = [[
        fmt_hora(p.get("hora_inicio")),
        fmt_hora(p.get("hora_fin")) if p.get("hora_fin") else "en curso",
        fmt_minutos(p.get("duracion_minutos")),
        p.get("area_proceso") or "—",
        p.get("tipo_parada") or "—",
        (p.get("descripcion_falla") or p.get("equipo_afectado") or "—"),
    ] for p in paradas]
    return tabla(
        ["Inicio", "Fin", "Duración", "Área", "Tipo", "Falla"], filas,
        [30 * mm, 30 * mm, 18 * mm, 28 * mm, 26 * mm, 50 * mm], align_derecha_desde=2,
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

    # el enjuague y el saldo de tambor sin completar salen de la línea pero no
    # son producto terminado - se dejan fuera del PT kg, del rendimiento y del
    # volumen (ver _cuenta_como_pt). Los insumos de "entrada" (reposición para
    # subir Brix) son otra cosa: ni siquiera son "salida" de esta corrida -
    # Calidad los cuenta al pesar tambores (registra el bruto tal cual se lo
    # dan, ej. 78 cilindros), Producción no porque no los produjo. El PT real
    # SIEMPRE se calcula restando el bruto de Calidad menos lo que entró de
    # afuera - no hace falta que Stephano reste a mano antes de registrar.
    productos_salida = [p for p in productos if _es_salida(p)]
    productos_entrada = [p for p in productos if not _es_salida(p)]
    productos_pt = [p for p in productos_salida if _cuenta_como_pt(p)]
    pt_bruto_total = sum(float(p["pt_kg"]) for p in productos_pt if p.get("pt_kg") is not None)
    litros_bruto_total = sum(float(p["volumen_litros"]) for p in productos_pt if p.get("volumen_litros") is not None)
    entrada_kg_total = sum(float(p["pt_kg"]) for p in productos_entrada if p.get("pt_kg") is not None)
    entrada_litros_total = sum(float(p["volumen_litros"]) for p in productos_entrada if p.get("volumen_litros") is not None)
    pt_total = pt_bruto_total - entrada_kg_total
    litros_total = litros_bruto_total - entrada_litros_total
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
        (fmt_minutos(min_parado) + (" · hay una en curso" if hay_en_curso else "")) if paradas else "Sin paradas",
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
    if entrada_kg_total > 0:
        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph(
            f"El producto terminado de arriba ya descuenta {fmt_kg(entrada_kg_total)} kg de insumos que "
            f"entraron a la corrida desde afuera (reposición para subir Brix, ver \"Insumos de entrada\" "
            f"más abajo) - Calidad reporta {fmt_kg(pt_bruto_total)} kg en total al pesar los tambores, "
            f"sin hacer esa resta.",
            style_footnote,
        ))
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
        # corrida. Si esta corrida YA CERRÓ, `kg_saldo`/`bines_saldo` vienen
        # calculados al momento del cierre (ver _SQL_KG_SALDO_AL_CIERRE en
        # api.py) - una foto fija que ya no se mueve después, aunque otra
        # corrida siga consumiendo el mismo lote. Si sigue ABIERTA, siguen
        # siendo el saldo en vivo, porque todavía se está armando.
        lotes_terminados = [l for l in lotes if float(l.get("kg_saldo") or 0) <= 0.01]
        lotes_con_saldo = [l for l in lotes if float(l.get("kg_saldo") or 0) > 0.01]
        cerrada = corrida.get("fecha_final") is not None

        story.append(Spacer(1, 8 * mm))
        story.extend(seccion("Estado de estos lotes al cerrar esta corrida" if cerrada else "Estado de estos lotes hoy"))
        story.append(Paragraph(
            (
                "Foto de cuando se cerró esta corrida - queda fija así, aunque después otra "
                "corrida siga consumiendo el saldo de estos mismos lotes."
            ) if cerrada else (
                "Foto de ahora mismo (esta corrida sigue abierta) - el saldo de un lote puede "
                "seguir cambiando mientras se registra más consumo, acá o en otra corrida."
            ),
            style_footnote,
        ))
        story.append(Spacer(1, 4 * mm))
        story.append(Paragraph(f"Terminados ({len(lotes_terminados)})", style_kpi_label))
        story.append(Spacer(1, 2 * mm))
        story.append(_tabla_lotes_simple(lotes_terminados) if lotes_terminados else Paragraph("Ninguno todavía.", style_footnote))
        story.append(Spacer(1, 6 * mm))
        story.append(Paragraph(f"Con saldo para la siguiente corrida ({len(lotes_con_saldo)})", style_kpi_label))
        story.append(Spacer(1, 2 * mm))
        story.append(_tabla_lotes_simple(lotes_con_saldo, con_saldo=True, cerrada=cerrada) if lotes_con_saldo else Paragraph("Ninguno - todos terminaron.", style_footnote))

    if mediciones:
        story.append(Spacer(1, 8 * mm))
        story.extend(seccion(f"Tanques medidos ({len(mediciones)})"))
        story.append(_tabla_mediciones(mediciones))

    if productos_salida:
        story.append(Spacer(1, 8 * mm))
        story.extend(seccion(f"Productos de salida ({len(productos_salida)})"))
        story.append(_tabla_productos(productos_salida))

    if productos_entrada:
        story.append(Spacer(1, 8 * mm))
        story.extend(seccion(f"Insumos de entrada ({len(productos_entrada)})"))
        story.append(Paragraph(
            "Producto que se metió a la corrida desde afuera (no lo produjo ella) - nunca cuenta como PT ni suma al rendimiento.",
            style_footnote,
        ))
        story.append(Spacer(1, 2 * mm))
        story.append(_tabla_insumos_entrada(productos_entrada))

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
