"""Genera el PDF de cuadre de una corrida (botón "Reporte PDF" en Corridas).
Piezas compartidas (paleta, tarjetas KPI, etc.) viven en reporte_base.py."""
from io import BytesIO

from reportlab.lib.units import mm
from reportlab.platypus import Table, TableStyle, Paragraph, Spacer

from reporte_base import (
    FOREST_2, CITRUS, OK, BAD, TEXT, TEXT_MUTED,
    PAGE_W, MARGIN, style_kpi_label, style_footnote,
    fmt_kg, fmt_num, fmt_tambores, fmt_lote, fmt_fecha, fmt_hora, fmt_minutos, header_footer, kpi_card,
    encabezado, seccion, tabla, nuevo_doc, etiqueta_derivado, _parse_dt,
)
from reporte_graficos import (
    BarrasH, BarraCelda, Anillo, Mancuernas, LineaTiempo, FOREST_3, SERIE_AZUL,
)

# ancho que de verdad tiene el área de contenido: el marco de la página deja 6 pt de relleno a cada lado
ANCHO_UTIL = PAGE_W - 2 * MARGIN - 12
ANCHO_KG_LOTE = 37 * mm - 12            # ancho de la celda «Kg usado» (37 mm de columna menos el relleno de la tabla)


def _grafico_productos(productos_salida):
    items = []
    for p in productos_salida:
        if p.get("pt_kg") is None:
            continue
        cuenta = _cuenta_como_pt(p)
        tambores = f"{fmt_tambores(p.get('tambores'))} tambor(es)" if p.get("tambores") is not None else None
        items.append({
            "etiqueta": p.get("producto") or "—", "valor": float(p["pt_kg"]), "texto": f"{fmt_kg(p['pt_kg'])} kg",
            "nota": tambores if cuenta else ((tambores + " · " if tambores else "") + "no cuenta como PT"),
            "color": SERIE_AZUL if cuenta else TEXT_MUTED,
        })
    if not items:
        return None
    hay_ambos = any(i["color"] is SERIE_AZUL for i in items) and any(i["color"] is TEXT_MUTED for i in items)
    return BarrasH(ANCHO_UTIL, items, ancho_etq=170, leyenda=[(SERIE_AZUL, "Producto terminado"), (TEXT_MUTED, "No cuenta como PT")] if hay_ambos else None)


def _grafico_tanques(mediciones):
    filas = [{
        "etiqueta": m.get("tanque") or "—",
        "sub": f"{fmt_kg(m['litros'])} L" if m.get("litros") is not None else None,
        "a": m.get("brix_inicial"), "b": m.get("brix_final"),
    } for m in mediciones if m.get("brix_final") is not None]
    if not any(f["a"] is not None for f in filas):
        return None                      # sin Brix inicial no hay «antes → después» que dibujar; queda solo la tabla
    return Mancuernas(ANCHO_UTIL, filas, ancho_etq=60)


def _grafico_paradas(paradas, fecha_inicio, fecha_final):
    """Línea de tiempo de la corrida con cada parada marcada, o None si no hay horas válidas para dibujarla."""
    inicio = _parse_dt(fecha_inicio)
    if inicio is None:
        return None
    pares = []
    for p in paradas:
        a, b = _parse_dt(p.get("hora_inicio")), _parse_dt(p.get("hora_fin"))
        if a is not None:
            pares.append((a, b))
    if not pares:
        return None
    return LineaTiempo(ANCHO_UTIL, inicio, _parse_dt(fecha_final), pares)


def _grafico_paradas_por_area(paradas):
    acc = {}
    for p in paradas:
        if p.get("duracion_minutos") is not None:
            k = p.get("area_proceso") or "Sin especificar"
            acc[k] = acc.get(k, 0.0) + float(p["duracion_minutos"])
    if len(acc) < 2:
        return None
    total = sum(acc.values()) or 1
    items = [{
        "etiqueta": k, "valor": v, "texto": fmt_minutos(v), "nota": f"{v / total * 100:.0f} %", "color": CITRUS,
    } for k, v in sorted(acc.items(), key=lambda kv: kv[1], reverse=True)[:8]]
    return BarrasH(ANCHO_UTIL, items, ancho_etq=110)


def _es_reproceso(lote: dict) -> bool:
    """Fila de consumo de un lote derivado de REPROCESO (jugo que se vuelve a procesar): no es materia prima nueva."""
    return (lote.get("lote_numero") or 0) < 0 and etiqueta_derivado(lote.get("proveedor")) == "REPROCESO"


def _tabla_reproceso(filas):
    total = sum(float(l["kg_asignados"]) for l in filas if l.get("kg_asignados") is not None)
    datos = [[fmt_lote(l["lote_numero"], l.get("proveedor")), l.get("proveedor") or "—",
              fmt_kg(l["kg_asignados"]) if l.get("kg_asignados") is not None else "pendiente"] for l in filas]
    datos.append(["", "Total reprocesado", fmt_kg(total)])
    return tabla(["Lote", "Descripción", "Kg reprocesados"], datos, [22 * mm, 111 * mm, 40 * mm], align_derecha_desde=2)


def _tabla_lotes(lotes):
    """Tabla de lotes con una barra de datos en «Kg usado» (verde oscuro = silo, verde claro = bines), para ver de un vistazo cuáles
    lotes pesaron más en la corrida."""
    color_almacen = {"SILO": FOREST_2, "BINES": FOREST_3}
    maximo = max((float(l["kg_asignados"]) for l in lotes if l.get("kg_asignados") is not None), default=0)
    filas = []
    for l in lotes:
        es_bines = l.get("tipo_almacen_origen") == "BINES"
        notas = []
        # en bines el conteo de patio manda más que el kg (que es un estimado) - se muestra al lado, no reemplazando el kg.
        if es_bines and l.get("bines_consumidos") is not None:
            notas.append(f"{fmt_num(l['bines_consumidos'], 0)} bines")
        if l.get("porcentaje_descuento") is not None:
            notas.append(f"-{fmt_num(l['porcentaje_descuento'], 1)}% brix")
        if l.get("kg_asignados") is not None:
            kg_celda = BarraCelda(
                ANCHO_KG_LOTE, l["kg_asignados"], maximo, color_almacen.get(l.get("tipo_almacen_origen"), TEXT_MUTED),
                fmt_kg(l["kg_asignados"]), nota=" · ".join(notas) or None,
            )
        else:
            kg_celda = "pendiente"
        filas.append([
            fmt_lote(l["lote_numero"], l.get("proveedor")),
            l.get("proveedor") or "—",
            "Bines" if es_bines else "Silo" if l.get("tipo_almacen_origen") == "SILO" else "—",
            kg_celda,
            fmt_num(l.get("brix_recepcion"), 2),
            fmt_num(l.get("acidez"), 2),
            fmt_num(l.get("ratio"), 2),
        ])
    return tabla(
        ["Lote", "Proveedor", "Almacén", "Kg usado", "Brix", "Acidez", "Ratio"], filas,
        [20 * mm, 50 * mm, 19 * mm, 37 * mm, 14 * mm, 15 * mm, 13 * mm], align_derecha_desde=4,
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
            filas.append([fmt_lote(l["lote_numero"], l.get("proveedor")), l.get("proveedor") or "—", saldo_txt])
        encabezado_saldo = "Saldo al cierre" if cerrada else "Saldo hoy"
        return tabla(["Lote", "Proveedor", encabezado_saldo], filas, [22 * mm, 79 * mm, 72 * mm], align_derecha_desde=2)
    filas = [[fmt_lote(l["lote_numero"], l.get("proveedor")), l.get("proveedor") or "—"] for l in lotes]
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


def _aplica_descuento(producto: dict) -> bool:
    """Solo tiene sentido para filas "entrada". Mismo campo de base
    (`cuenta_como_pt`) que para "salida" significa "¿cuenta como PT?", pero
    aquí significa "¿se resta del total de la corrida?" - un insumo de
    entrada no siempre debe descontarse (ej. un cilindro que solo ajusta
    Brix/Ratio, no un producto "extra" que haya que restarle a lo que salió).
    Se registra igual en "Insumos de entrada" para trazabilidad, con este
    check en false simplemente no resta nada."""
    if _es_salida(producto):
        return False
    return producto.get("cuenta_como_pt", True) is not False


def _tabla_productos(productos):
    filas = [[
        p.get("producto") or "—",
        fmt_tambores(p.get("tambores")),
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
        fmt_tambores(p.get("tambores")),
        fmt_kg(p.get("peso_neto_tambor_kg")),
        fmt_kg(p.get("pt_kg")),
        (p.get("observaciones") or "—") if _aplica_descuento(p) else "(no resta del total) " + (p.get("observaciones") or ""),
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
        [28 * mm, 28 * mm, 18 * mm, 28 * mm, 26 * mm, 50 * mm], align_derecha_desde=2,       # 178 mm: el ancho útil de la página
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

    # el jugo de REPROCESO (p. ej. cilindros observados que se vuelven a procesar) no es materia prima nueva: no entra en la
    # MP consumida, en el gráfico Silo/Bines ni en el Brix ponderado, y se informa en un cuadro aparte
    reproceso = [l for l in lotes if _es_reproceso(l)]
    lotes = [l for l in lotes if not _es_reproceso(l)]

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
    # afuera - no hace falta restar a mano antes de registrar.
    productos_salida = [p for p in productos if _es_salida(p)]
    productos_entrada = [p for p in productos if not _es_salida(p)]
    productos_pt = [p for p in productos_salida if _cuenta_como_pt(p)]
    # un insumo de entrada se sigue mostrando siempre en "Insumos de entrada"
    # (productos_entrada, completo) - pero solo los que tienen el check
    # activado restan de los totales (productos_entrada_desc).
    productos_entrada_desc = [p for p in productos_entrada if _aplica_descuento(p)]
    pt_bruto_total = sum(float(p["pt_kg"]) for p in productos_pt if p.get("pt_kg") is not None)
    litros_bruto_total = sum(float(p["volumen_litros"]) for p in productos_pt if p.get("volumen_litros") is not None)
    entrada_kg_total = sum(float(p["pt_kg"]) for p in productos_entrada_desc if p.get("pt_kg") is not None)
    entrada_litros_total = sum(float(p["volumen_litros"]) for p in productos_entrada_desc if p.get("volumen_litros") is not None)
    pt_total = pt_bruto_total - entrada_kg_total
    # mismo criterio que el Brix/Acidez medido arriba: el litraje tipeado a
    # mano en "productos de salida" es un estimado - si hay mediciones reales
    # de tanque, se suman esos litros en vez de restar el insumo de entrada
    # del bruto tipeado.
    litros_medidos_total = sum(float(m.get("litros") or 0) for m in mediciones)
    litros_total = litros_medidos_total if litros_medidos_total > 0 else (litros_bruto_total - entrada_litros_total)
    rendimiento = pt_total / kg_total if kg_total > 0 and pt_total > 0 else None

    # mismo criterio de "bruto - entrada" que ya tenía PT kg y Volumen, pero
    # faltaba para Tambores - sin esto, el conteo de tambores que de verdad
    # salió de la corrida no aparecía en ningún lado del PDF (solo el bruto
    # por fila, en la tabla de "Productos de salida"), así que alguien que
    # sumara tambores a ojo se quedaba con el número de Calidad sin descontar
    # los cilindros de entrada (confusión real detectada en planta).
    tambores_bruto_total = sum(float(p["tambores"]) for p in productos_pt if p.get("tambores") is not None)
    entrada_tambores_total = sum(float(p["tambores"]) for p in productos_entrada_desc if p.get("tambores") is not None)
    tambores_total = tambores_bruto_total - entrada_tambores_total

    paradas_cerradas = [p for p in paradas if p.get("duracion_minutos") is not None]
    min_parado = sum(p["duracion_minutos"] for p in paradas_cerradas)
    hay_en_curso = any(p.get("duracion_minutos") is None for p in paradas)

    periodo = f"{fmt_fecha(corrida.get('fecha_inicio'))}" + (
        f" — {fmt_fecha(corrida.get('fecha_final'))}" if corrida.get("fecha_final") else " · en curso"
    )
    story = [
        encabezado("Cuadre de corrida", corrida["nombre"], periodo),
        Spacer(1, 6 * mm),
        *seccion("Resumen"),
    ]

    # No hay "objetivo" de MP contra qué cuadrar - una corrida procesa lo que
    # entra, no una meta fijada de antemano (mismo criterio ya aplicado en
    # Corridas: se sacó el pill Cuadra/Incompleto/Excedido y la comparación
    # contra mp_kg_objetivo de Trazabilidad). Solo
    # se arma una tarjeta cuando de verdad hay un dato real que mostrar - una
    # corrida recién creada (sin productos, sin paradas) no debería mostrar
    # tarjetas en blanco con "—", eso se ve roto. Las que sí aplican se
    # acomodan solas de a 3 por fila.
    ancho_kpi = (PAGE_W - 2 * MARGIN - 2 * 6) / 3
    # el rendimiento se muestra en grande en «Del jugo al tambor»; en ese caso no se repite como tarjeta
    hay_anillo = bool(rendimiento) and pt_total > 0 and kg_total > 0
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
    if rendimiento and not hay_anillo:
        kpis.append(("Rendimiento", f"{rendimiento * 100:.1f} %", TEXT))
    if pt_total:
        kpis.append(("Producto terminado", f"{fmt_kg(pt_total)} kg", TEXT))
    if tambores_total:
        kpis.append(("Tambores", fmt_tambores(tambores_total), TEXT))
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
    if entrada_kg_total > 0 or entrada_tambores_total > 0:
        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph(
            f"El producto terminado ya descuenta {fmt_kg(entrada_kg_total)} kg y {fmt_tambores(entrada_tambores_total)} "
            f"tambor(es) correspondientes a insumos de entrada (ver \"Insumos de entrada\" más abajo). "
            f"Calidad reporta {fmt_kg(pt_bruto_total)} kg y {fmt_tambores(tambores_bruto_total)} tambor(es) brutos al "
            f"pesar, antes de este descuento.",
            style_footnote,
        ))
    story.append(Spacer(1, 6 * mm))

    if hay_anillo:
        story.extend(seccion("Del jugo al tambor"))
        ancho_anillo = 38 * mm
        anillo = Anillo(30 * mm, rendimiento, f"{rendimiento * 100:.1f} %", "Rendimiento")
        barras = BarrasH(ANCHO_UTIL - ancho_anillo, [
            {"etiqueta": "MP consumida", "valor": kg_total, "texto": f"{fmt_kg(kg_total)} kg", "color": FOREST_2},
            {"etiqueta": "Producto terminado", "valor": pt_total, "texto": f"{fmt_kg(pt_total)} kg",
             "nota": f"{rendimiento * 100:.1f} % de la MP", "color": SERIE_AZUL},
        ], ancho_etq=96)
        par = Table([[anillo, barras]], colWidths=[ancho_anillo, ANCHO_UTIL - ancho_anillo])
        par.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (0, 0), (0, 0), "LEFT"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        story.append(par)
        story.append(Spacer(1, 1 * mm))
        story.append(Paragraph("Rendimiento = producto terminado ÷ MP consumida.", style_footnote))
        story.append(Spacer(1, 3 * mm))

    story.extend(seccion(f"Lotes de MP consumidos ({len(lotes)})", largo=True))
    story.append(_tabla_lotes(lotes) if lotes else Paragraph("Sin lotes registrados todavía.", style_footnote))
    if kg_silo > 0 and kg_bines > 0:
        story.append(Spacer(1, 2 * mm))
        story.append(Paragraph(
            f'Barra: kg usado de cada lote. <font color="#{FOREST_2.hexval()[2:]}">■</font> Silo {fmt_kg(kg_silo)} kg · '
            f'<font color="#{FOREST_3.hexval()[2:]}">■</font> Bines {fmt_kg(kg_bines)} kg.', style_footnote))
    if n_pendientes:
        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph(
            f"{n_pendientes} lote(s) con kg pendiente no se cuentan en los KPIs ni en los gráficos.",
            style_footnote,
        ))

    if reproceso:
        story.append(Spacer(1, 8 * mm))
        story.extend(seccion("Reproceso (informativo)"))
        story.append(_tabla_reproceso(reproceso))
        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph(
            "Jugo que se volvió a procesar en esta corrida. No está sumado en la MP consumida, en los gráficos de MP ni en el rendimiento.",
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
        g_tanques = _grafico_tanques(mediciones)
        if g_tanques:
            story.append(g_tanques)
            story.append(Spacer(1, 2 * mm))
            story.append(Paragraph("Brix de cada tanque antes y después de estandarizar. La tabla trae también acidez, pH y ratio.", style_footnote))
            story.append(Spacer(1, 4 * mm))
        story.append(_tabla_mediciones(mediciones))

    if productos_salida:
        story.append(Spacer(1, 8 * mm))
        story.extend(seccion(f"Productos de salida ({len(productos_salida)})"))
        g_productos = _grafico_productos(productos_salida)
        if g_productos:
            story.append(g_productos)
            story.append(Spacer(1, 4 * mm))
        story.append(_tabla_productos(productos_salida))

    if productos_entrada:
        story.append(Spacer(1, 8 * mm))
        story.extend(seccion(f"Insumos de entrada ({len(productos_entrada)})"))
        story.append(Paragraph(
            "Material ingresado a la corrida desde una fuente externa; no forma parte del producto terminado ni del rendimiento.",
            style_footnote,
        ))
        story.append(Spacer(1, 2 * mm))
        story.append(_tabla_insumos_entrada(productos_entrada))

    if paradas:
        story.append(Spacer(1, 8 * mm))
        story.extend(seccion(f"Paradas registradas ({len(paradas)})", largo=not (len(paradas) < 6)))
        g_tiempo = _grafico_paradas(paradas, corrida.get("fecha_inicio"), corrida.get("fecha_final"))
        if g_tiempo:
            story.append(g_tiempo)
            ini, fin = g_tiempo.t0, g_tiempo.t1
            total_min = (fin - ini).total_seconds() / 60
            if total_min > 0 and min_parado > 0:
                pct_parado = min(float(min_parado) / total_min * 100, 100)
                story.append(Spacer(1, 1 * mm))
                story.append(Paragraph(
                    f"{len(paradas)} {'parada' if len(paradas) == 1 else 'paradas'} · {fmt_minutos(min_parado)} con la planta parada, "
                    f"{pct_parado:.0f} % de las {fmt_minutos(total_min)} que va de la corrida.", style_footnote))
            story.append(Spacer(1, 4 * mm))
        g_area = _grafico_paradas_por_area(paradas)
        if g_area:
            story.append(Paragraph("Tiempo parado por área", style_kpi_label))
            story.append(Spacer(1, 2 * mm))
            story.append(g_area)
            story.append(Spacer(1, 4 * mm))
        story.append(_tabla_paradas(paradas))

    doc.build(
        story,
        onFirstPage=lambda c, d: header_footer(c, d, corrida["nombre"]),
        onLaterPages=lambda c, d: header_footer(c, d, corrida["nombre"]),
    )
    return buf.getvalue()
