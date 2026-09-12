"""Arma el .xlsx de "Trazabilidad" de UNA corrida, con el mismo formato que la
hoja de planta: encabezado de fechas, barra de título, tabla de lotes de MP,
bloque de resumen (MP kg, rendimiento, masa de jugo simple, ratio...) y los
dos cuadros de stock por lote (saldos de esta corrida + lotes completos que
siguen en piso).

Los datos que la app tiene entran como números; TODO lo que se calcula entra
como fórmula de Excel (referencias a celdas), para que si Stephano corrige un
dato de entrada -por ejemplo el % de descuento a brix que le pasa su jefe- o
llena a mano un campo que la app no guarda, la hoja recalcule sola, igual que
la de planta. Lo que no está en la app (GRP, N° Guía, % descuento a brix,
cáscara / semilla, GNC) sale en blanco para llenarlo a mano al cierre."""
from datetime import date, datetime
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

HR_PULPEADO = 11             # fijo al generar el Excel (lo confirmó Stephano)
DENSIDAD_APARENTE = 1.04332  # kg/l - suele ser siempre este valor

_MESES = ["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO",
          "AGOSTO", "SETIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"]

_TITULO_FILL = PatternFill("solid", fgColor="F6C244")
_HEAD_FILL = PatternFill("solid", fgColor="CFE2C1")
_TOTAL_FILL = PatternFill("solid", fgColor="EFEFEF")
_SEC_FILL = PatternFill("solid", fgColor="DCE6F1")
_BOLD = Font(bold=True)
_HEAD_FONT = Font(bold=True, size=9)
_THIN = Side("thin", color="9BB08C")
_BORDE = Border(_THIN, _THIN, _THIN, _THIN)
_CENTRO = Alignment(horizontal="center", vertical="center", wrap_text=True)
_FMT_KG = "#,##0.00"
_FMT_BRIX = "0.00"
_FMT_PCT = "0.0%"
_FMT_FECHA = "DD/MM/YYYY"
_FMT_FECHAHORA = "DD/MM/YYYY HH:MM"

C0 = 2  # la tabla arranca en la columna B


def L(k):
    """Letra de columna para el campo k de la tabla (0 = B)."""
    return get_column_letter(C0 + k)


def _f(v):
    """Decimal/None -> float/None, y datetime con zona -> sin zona (Excel)."""
    if v is None:
        return None
    if isinstance(v, datetime) and v.tzinfo is not None:
        v = v.replace(tzinfo=None)
    if isinstance(v, (datetime, date)):
        return v
    try:
        return float(v)
    except (TypeError, ValueError):
        return v


def _es_pt(producto: dict) -> bool:
    """Si esta fila cuenta como PT real - marca a mano por fila
    (`corrida_productos.cuenta_como_pt`), no se adivina por el nombre. Un
    insumo de "entrada" (reposición para subir Brix, etc.) nunca cuenta."""
    if producto.get("tipo", "salida") == "entrada":
        return False
    return producto.get("cuenta_como_pt", True) is not False


def _set(ws, celda, valor, *, fmt=None, bold=False, fill=None, borde=False, centro=False):
    c = ws[celda] if isinstance(celda, str) else celda
    c.value = valor
    if fmt and valor is not None and valor != "":
        c.number_format = fmt
    if bold:
        c.font = _BOLD
    if fill:
        c.fill = fill
    if borde:
        c.border = _BORDE
    if centro:
        c.alignment = _CENTRO
    return c


def _cuadro_stock(ws, fila0, titulo, filas):
    """Dibuja un cuadrito de stock por lote (Lote / Peso Neto / Peso procesado /
    Peso saldo / Bines saldo / Bines totales + TOTAL) empezando en la col H.
    Devuelve la última fila usada. `filas` = lista de dicts de v_saldo_lotes."""
    h = 8  # col H
    _set(ws, ws.cell(row=fila0, column=h), titulo, bold=True, fill=_SEC_FILL)
    hdr = ["Lote", "Peso Neto", "Peso procesado", "Peso saldo", "Bines saldo", "Bines totales"]
    for k, nom in enumerate(hdr):
        c = _set(ws, ws.cell(row=fila0 + 1, column=h + k), nom, fill=_HEAD_FILL, borde=True, centro=True)
        c.font = _HEAD_FONT
    if not filas:
        _set(ws, ws.cell(row=fila0 + 2, column=h), "Sin lotes.", borde=True)
        for k in range(1, 6):
            ws.cell(row=fila0 + 2, column=h + k).border = _BORDE
        return fila0 + 2
    i = 0
    for i, s in enumerate(filas, start=1):
        rr = fila0 + 1 + i
        _set(ws, ws.cell(row=rr, column=h), s.get("numero"), borde=True)
        _set(ws, ws.cell(row=rr, column=h + 1), _f(s.get("peso_neto_kg")), fmt=_FMT_KG, borde=True)
        _set(ws, ws.cell(row=rr, column=h + 2), _f(s.get("kg_consumidos")), fmt=_FMT_KG, borde=True)
        _set(ws, ws.cell(row=rr, column=h + 3), f"=I{rr}-J{rr}", fmt=_FMT_KG, borde=True)
        _set(ws, ws.cell(row=rr, column=h + 4), _f(s.get("bines_saldo")), borde=True, centro=True)
        _set(ws, ws.cell(row=rr, column=h + 5), _f(s.get("bines_totales")), borde=True, centro=True)
    tr = fila0 + 2 + i
    _set(ws, ws.cell(row=tr, column=h + 2), "TOTAL", bold=True, fill=_TOTAL_FILL, borde=True)
    _set(ws, ws.cell(row=tr, column=h + 3), f"=SUM(K{fila0 + 2}:K{tr - 1})",
         fmt=_FMT_KG, bold=True, fill=_TOTAL_FILL, borde=True)
    for k in (0, 1, 4, 5):
        ws.cell(row=tr, column=h + k).fill = _TOTAL_FILL
        ws.cell(row=tr, column=h + k).border = _BORDE
    return tr


def generar_trazabilidad_xlsx(corrida, lotes, productos, mediciones, stock=None) -> bytes:
    """corrida: fila de v_cuadre_corridas + corridas. lotes: una fila por
    asignación (join a lotes + v_saldo_lotes). productos: corrida_productos.
    mediciones: mediciones_tanque. stock: lotes de v_saldo_lotes con saldo
    disponible (EN PROCESO / EN ESPERA) - se parte en los que tocó esta corrida
    y los demás."""
    stock = stock or []
    wb = Workbook()
    ws = wb.active
    ws.title = "Trazabilidad"
    ws.sheet_view.showGridLines = False
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True

    fi = _f(corrida.get("fecha_inicio"))
    ff = _f(corrida.get("fecha_final"))
    proceso = corrida.get("tipo_proceso") or ""
    if isinstance(fi, datetime):
        titulo = f"NARANJA {fi.day:02d} DE {_MESES[fi.month - 1]} {fi.year}  -  {proceso}".strip(" -")
    else:
        titulo = f"NARANJA  -  {proceso}".strip(" -")

    # ---------- encabezado de fechas ----------
    _set(ws, "B1", "FECHA PROCESO", bold=True)
    _set(ws, "D1", "INICIO", bold=True, centro=True)
    _set(ws, "E1", "FINAL", bold=True, centro=True)
    _set(ws, "B2", "Fecha / hora de proceso")
    _set(ws, "D2", fi, fmt=_FMT_FECHAHORA)
    _set(ws, "E2", ff, fmt=_FMT_FECHAHORA)
    _set(ws, "B3", "EXTRACCIÓN (Hr)", bold=True)   # se llenan a mano al cierre
    _set(ws, "B4", "ENVASADO (Hr)", bold=True)

    _set(ws, "B6", titulo, bold=True, fill=_TITULO_FILL)
    ws["B6"].font = Font(bold=True, size=12)
    ws.merge_cells(start_row=6, start_column=C0, end_row=6, end_column=C0 + 15)

    # ---------- tabla de lotes de MP (arranca en col B) ----------
    cols = [
        "Proceso", "Lote", "GRP", "N° Guía", "F. Ingreso", "F. Proceso",
        "Procedencia", "Proveedor", "Peso Neto", "Silo / Bines", "Peso con descuento",
        "Brix Calidad recepción", "Brix Producción línea", "% Descuento a brix 10.5°B",
        "Descuento", "kg descuento",
    ]
    HFILA = 7
    for k, nom in enumerate(cols):
        c = _set(ws, ws.cell(row=HFILA, column=C0 + k), nom, fill=_HEAD_FILL, borde=True, centro=True)
        c.font = _HEAD_FONT

    # letras de las columnas que se usan en fórmulas
    cPN, cPCD, cBRX, cPDTO, cKDTO = L(8), L(10), L(11), L(13), L(15)

    r = HFILA + 1
    for lo in lotes:
        peso = _f(lo.get("kg_asignados"))   # None = kg pendiente -> celda en blanco
        brix = _f(lo.get("brix_recepcion"))
        alm = lo.get("tipo_almacen_origen")
        if alm == "SILO":
            silo_bines = "Silo"
        elif alm == "BINES":
            bc = lo.get("bines_consumidos")
            silo_bines = f"{bc} bines" if bc else "Bines"
        else:
            silo_bines = "—"
        # Peso con descuento = Peso Neto - (% descuento * Peso Neto)   [ = J-(O*J) ]
        # kg descuento       = % descuento * Peso Neto                 [ = O*J ]
        pcd = f'=IF({cPN}{r}="","",{cPN}{r}-({cPDTO}{r}*{cPN}{r}))'
        kdto = f'=IF(OR({cPN}{r}="",{cPDTO}{r}=""),"",{cPDTO}{r}*{cPN}{r})'
        fila = [
            proceso,
            lo.get("lote_numero"),
            None,                              # GRP
            lo.get("guia"),                    # N° Guía
            _f(lo.get("fecha_ingreso")),
            _f(lo.get("fecha_proceso")),
            lo.get("procedencia") or "",
            lo.get("proveedor") or "",
            peso,                              # Peso Neto
            silo_bines,
            pcd,                               # Peso con descuento (fórmula)
            brix,                              # Brix Calidad recepción
            _f(lo.get("brix_produccion")),     # Brix Producción línea
            None,                              # % Descuento a brix (a mano)
            None,                              # Descuento
            kdto,                              # kg descuento (fórmula)
        ]
        for k, v in enumerate(fila):
            c = _set(ws, ws.cell(row=r, column=C0 + k), v, borde=True)
            if k in (4, 5):
                c.number_format = _FMT_FECHA
            elif k in (8, 10, 14, 15):
                c.number_format = _FMT_KG
            elif k in (11, 12):
                c.number_format = _FMT_BRIX
            elif k == 13:
                c.number_format = _FMT_PCT
        r += 1

    n_lotes = r - (HFILA + 1)
    prim, ult = HFILA + 1, r - 1

    # ---- fila de totales ----
    _set(ws, ws.cell(row=r, column=C0 + 7), "TOTAL", bold=True, fill=_TOTAL_FILL, borde=True)
    for k in (8, 10, 15):                    # Peso Neto, Peso con descuento, kg descuento
        letra = L(k)
        v = f"=SUM({letra}{prim}:{letra}{ult})" if n_lotes else 0
        _set(ws, ws.cell(row=r, column=C0 + k), v, fmt=_FMT_KG, bold=True, fill=_TOTAL_FILL, borde=True)
    for k in (9, 11, 12, 13, 14):
        ws.cell(row=r, column=C0 + k).fill = _TOTAL_FILL
        ws.cell(row=r, column=C0 + k).border = _BORDE
    pp = r + 1
    _set(ws, ws.cell(row=pp, column=C0 + 5), "Promedio ponderado de brix proceso", bold=True)
    if n_lotes:
        _set(ws, ws.cell(row=pp, column=C0 + 11),
             f'=IF(SUM({cPN}{prim}:{cPN}{ult})=0,"",'
             f'SUMPRODUCT({cPN}{prim}:{cPN}{ult},{cBRX}{prim}:{cBRX}{ult})/SUM({cPN}{prim}:{cPN}{ult}))',
             fmt=_FMT_BRIX, bold=True)

    # ---------- datos de entrada del bloque de resumen ----------
    prod_pt = [p for p in productos if _es_pt(p)]
    volumen = sum(_f(p.get("volumen_litros")) or 0.0 for p in prod_pt) or None
    tambores = (sum(_f(p.get("tambores")) or 0 for p in prod_pt)) or None
    peso_tambor = next((_f(p.get("peso_neto_tambor_kg")) for p in prod_pt if p.get("peso_neto_tambor_kg")), None)
    pt_kg = sum(_f(p.get("pt_kg")) or 0.0 for p in prod_pt) or None
    # insumos que entraron a la corrida desde afuera (reposición para subir
    # Brix, etc.) - Calidad los cuenta al pesar tambores, Producción no
    # porque no los produjo esta corrida. Se muestran restados, no se adivina
    # nada: si no hay ninguno registrado, "PT según Calidad" = PT kg.
    entrada_kg = sum(_f(p.get("pt_kg")) or 0.0 for p in productos if p.get("tipo") == "entrada") or None

    peso_med = [(_f(m.get("litros")) or 0.0, _f(m["brix_final"]))
                for m in mediciones if m.get("brix_final") is not None]
    if peso_med and sum(w for w, _ in peso_med) > 0:
        brix_tk = sum(w * b for w, b in peso_med) / sum(w for w, _ in peso_med)
    elif peso_med:
        brix_tk = sum(b for _, b in peso_med) / len(peso_med)
    else:
        brix_tk = _f(corrida.get("brix_promedio_tk"))
    mp_fallback = _f(corrida.get("mp_kg_objetivo")) or _f(corrida.get("kg_asignados_total")) or 0.0

    # ---------- bloque de resumen (fórmulas) ----------
    SEC = r + 3
    _set(ws, ws.cell(row=SEC, column=2), "RESUMEN", bold=True, fill=_SEC_FILL)

    m0 = SEC + 1
    (rMP, rHR, rMPh, rVol, rTam, rPesoTam, rPT, rEntrada, rPTCalidad, rRend,
     rBrix, rDens, rMasa, rRJS, rSem, rCas, rCS, rGNC, rRatio) = range(m0, m0 + 19)
    NEG = {rMP, rPT, rRend, rRJS, rRatio}   # valores en negrita (los "titulares")

    def val(fila, etiqueta, valor, fmt=None):
        _set(ws, ws.cell(row=fila, column=2), etiqueta, bold=True)
        _set(ws, ws.cell(row=fila, column=4), valor, fmt=fmt, bold=(fila in NEG))

    total_col = L(10)  # Peso con descuento (total)
    val(rMP, "MP kg", (f"={total_col}{r}" if n_lotes else (round(mp_fallback, 2) or None)), _FMT_KG)
    val(rHR, "HR pulpeado", HR_PULPEADO, "0")
    val(rMPh, "MP kg/hr", f'=IF(D{rHR}=0,"",D{rMP}/D{rHR})', _FMT_KG)
    val(rVol, "Volumen  litros", round(volumen, 2) if volumen else None, _FMT_KG)
    _set(ws, ws.cell(row=rVol, column=5), f'=IF(OR(D{rVol}="",D{rMP}=0),"",D{rVol}/D{rMP})', fmt=_FMT_PCT)
    val(rTam, "Tambores  und", tambores, "0")
    val(rPesoTam, "Peso Neto del tambor  kg", peso_tambor, _FMT_KG)
    val(rPT, "PT  kg",
        (f"=D{rTam}*D{rPesoTam}" if (tambores and peso_tambor) else (round(pt_kg, 2) if pt_kg else None)),
        _FMT_KG)
    val(rEntrada, "Insumos de entrada  kg  (resta)", round(entrada_kg, 2) if entrada_kg else None, _FMT_KG)
    val(rPTCalidad, "PT según Calidad  kg", f'=IF(D{rEntrada}="",D{rPT},D{rPT}+D{rEntrada})', _FMT_KG)
    val(rRend, "Rendimiento", f'=IF(OR(D{rPT}="",D{rMP}=0),"",D{rPT}/D{rMP})', _FMT_PCT)
    val(rBrix, "Brix Promedio TK", round(brix_tk, 2) if brix_tk is not None else None, _FMT_BRIX)
    val(rDens, "Densidad Aparente  kg/l", DENSIDAD_APARENTE, "0.00000")
    val(rMasa, "Masa del Jugo Simple  kg", f'=IF(D{rVol}="","",D{rVol}*D{rDens})', _FMT_KG)
    val(rRJS, "Rendimiento de Jugo Simple Tanques", f'=IF(OR(D{rMasa}="",D{rMP}=0),"",D{rMasa}/D{rMP})', _FMT_PCT)
    val(rSem, "Semilla", None, _FMT_KG)
    val(rCas, "Cáscara", None, _FMT_KG)
    val(rCS, "Cáscara / Semilla", f'=IF(OR(D{rCas}="",D{rSem}="",D{rSem}=0),"",D{rCas}/D{rSem})', _FMT_KG)
    val(rGNC, "Consumo de GNC  m³", None, _FMT_KG)
    val(rRatio, "Ratio  kg/m³", f'=IF(OR(D{rMasa}="",D{rPT}="",D{rPT}=0),"",D{rMasa}/D{rPT})', "0.000")

    # ---------- dos cuadros de stock por lote (a la derecha) ----------
    # arriba: lo que ESTA corrida no terminó (cualquier lote suyo con saldo);
    # abajo: los demás lotes completos que siguen en piso para el siguiente proceso.
    mis_lotes = {lo.get("lote_numero") for lo in lotes}
    vistos, parciales = set(), []
    for lo in lotes:
        n = lo.get("lote_numero")
        if n in vistos or (_f(lo.get("kg_saldo")) or 0) <= 0.01:
            continue
        vistos.add(n)
        parciales.append({
            "numero": n,
            "peso_neto_kg": lo.get("peso_neto_kg"),
            "kg_consumidos": lo.get("kg_consumidos"),
            "kg_saldo": lo.get("kg_saldo"),
            "bines_saldo": lo.get("bines_saldo"),
            "bines_totales": lo.get("bines_totales"),
        })
    completos = [s for s in stock if s.get("numero") not in mis_lotes]

    fin1 = _cuadro_stock(ws, SEC, "Stock inicial al siguiente proceso  —  saldos de esta corrida", parciales)
    _cuadro_stock(ws, fin1 + 3, "Lotes completos que siguen en stock", completos)

    # ---------- anchos de columna ----------
    anchos = {1: 3, 2: 12, 3: 8, 4: 18, 5: 18, 6: 13, 7: 13, 8: 26, 9: 34, 10: 14,
              11: 12, 12: 15, 13: 12, 14: 12, 15: 14, 16: 11, 17: 13}
    for i, w in anchos.items():
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "B8"

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
