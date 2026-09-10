"""Arma el .xlsx de "Trazabilidad" de UNA corrida, con el mismo formato que la
hoja de planta: encabezado de fechas, barra de título, tabla de lotes de MP,
bloque de resumen (MP kg, rendimiento, masa de jugo simple, ratio...) y las
tablas de saldo por lote.

Los datos que la app tiene entran como números; TODO lo que se calcula entra
como fórmula de Excel (referencias a celdas), para que si Stephano corrige un
dato de entrada -por ejemplo el % de descuento a brix que le pasa su jefe- o
llena a mano un campo que la app no guarda, la hoja recalcule sola, igual que
la de planta. Lo que no está en la app (GRP, N° Guía, % descuento a brix,
cáscara / semilla, GNC) sale en blanco para llenarlo a mano al cierre."""
import unicodedata
from datetime import date, datetime
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

HR_PULPEADO = 11             # fijo al generar el Excel (lo confirmó Stephano)
DENSIDAD_APARENTE = 1.04332  # kg/l - suele ser siempre este valor

_MESES = ["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO",
          "AGOSTO", "SETIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"]

_TITULO_FILL = PatternFill("solid", fgColor="FFE39B")
_HEAD_FILL = PatternFill("solid", fgColor="D9EAD3")
_TOTAL_FILL = PatternFill("solid", fgColor="F2F2F2")
_SEC_FILL = PatternFill("solid", fgColor="E8EEF6")
_BOLD = Font(bold=True)
_HEAD_FONT = Font(bold=True, size=9)
_BORDE = Border(*[Side("thin", color="B7B7B7")] * 4)
_CENTRO = Alignment(horizontal="center", vertical="center", wrap_text=True)
_FMT_KG = "#,##0.00"
_FMT_BRIX = "0.00"
_FMT_PCT = "0.0%"
_FMT_FECHA = "DD/MM/YYYY"
_FMT_FECHAHORA = "DD/MM/YYYY HH:MM"


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


def _es_pt(nombre):
    n = "".join(
        ch for ch in unicodedata.normalize("NFD", (nombre or "").lower())
        if unicodedata.category(ch) != "Mn"
    )
    return "enjuague" not in n


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


def generar_trazabilidad_xlsx(corrida, lotes, productos, mediciones) -> bytes:
    """corrida: fila de v_cuadre_corridas + corridas (nombre, tipo_proceso,
    fecha_inicio, fecha_final, mp_kg_objetivo, kg_asignados_total,
    brix_promedio_tk, rendimiento, fecha_proceso_ref).
    lotes: una fila por asignación, con join a lotes y v_saldo_lotes.
    productos: corrida_productos. mediciones: mediciones_tanque."""
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
    _set(ws, "B3", "EXTRACCIÓN (Hr)")   # se llenan a mano al cierre
    _set(ws, "B4", "ENVASADO (Hr)")

    _set(ws, "B6", titulo, bold=True, fill=_TITULO_FILL)
    ws["B6"].font = Font(bold=True, size=12)
    ws.merge_cells("B6:P6")

    # ---------- tabla de lotes de MP ----------
    cols = [
        "Proceso", "Lote", "GRP", "N° Guía", "F. Ingreso", "F. Proceso",
        "Procedencia", "Proveedor", "Peso Neto", "Silo / Bines", "Peso con descuento",
        "Brix Calidad recepción", "Brix Producción línea", "% Descuento a brix 10.5°B",
        "Descuento", "kg descuento",
    ]
    HFILA = 7
    for i, nombre in enumerate(cols, start=1):
        _set(ws, ws.cell(row=HFILA, column=i), nombre,
             fill=_HEAD_FILL, borde=True, centro=True)
        ws.cell(row=HFILA, column=i).font = _HEAD_FONT

    r = HFILA + 1
    for l in lotes:
        peso = _f(l.get("kg_asignados"))   # None = kg pendiente -> celda en blanco
        brix = _f(l.get("brix_recepcion"))
        alm = l.get("tipo_almacen_origen")
        if alm == "SILO":
            silo_bines = "Silo"
        elif alm == "BINES":
            bc = l.get("bines_consumidos")
            silo_bines = f"{bc} bines" if bc else "Bines"
        else:
            silo_bines = "—"
        # I = Peso Neto (dato), N = % descuento a brix (a mano, lo pasa el jefe)
        # K = Peso con descuento = Peso Neto - (% descuento * Peso Neto)   [=I-(N*I)]
        # P = kg descuento = % descuento * Peso Neto                       [=N*I]
        pcd = f'=IF(I{r}="","",I{r}-(N{r}*I{r}))'
        kdesc = f'=IF(OR(I{r}="",N{r}=""),"",N{r}*I{r})'
        fila = [
            proceso,
            l.get("lote_numero"),
            None,                              # GRP - no está en la app
            l.get("guia"),                     # N° Guía
            _f(l.get("fecha_ingreso")),
            _f(l.get("fecha_proceso")),
            l.get("procedencia") or "",
            l.get("proveedor") or "",
            peso,                              # I  Peso Neto (kg que entraron de este lote)
            silo_bines,                        # J  Silo / Bines
            pcd,                               # K  Peso con descuento (fórmula)
            brix,                              # L  Brix Calidad recepción
            _f(l.get("brix_produccion")),      # M  Brix Producción línea
            None,                              # N  % Descuento a brix (a mano)
            None,                              # O  Descuento
            kdesc,                             # P  kg descuento (fórmula)
        ]
        for i, v in enumerate(fila, start=1):
            c = _set(ws, ws.cell(row=r, column=i), v, borde=True)
            if i in (5, 6):
                c.number_format = _FMT_FECHA
            elif i in (9, 11, 15, 16):
                c.number_format = _FMT_KG
            elif i in (12, 13):
                c.number_format = _FMT_BRIX
            elif i == 14:
                c.number_format = _FMT_PCT
        r += 1

    n_lotes = r - (HFILA + 1)
    prim, ult = HFILA + 1, r - 1           # primera y última fila de lotes

    # ---- fila de totales ----
    _set(ws, ws.cell(row=r, column=8), "TOTAL", bold=True, fill=_TOTAL_FILL, borde=True)
    for col in (9, 11, 16):                 # Peso Neto, Peso con descuento, kg descuento
        letra = get_column_letter(col)
        val = f"=SUM({letra}{prim}:{letra}{ult})" if n_lotes else 0
        _set(ws, ws.cell(row=r, column=col), val, fmt=_FMT_KG, bold=True, fill=_TOTAL_FILL, borde=True)
    for col in (10, 12, 13, 14, 15):
        ws.cell(row=r, column=col).fill = _TOTAL_FILL
        ws.cell(row=r, column=col).border = _BORDE
    # brix ponderado por Peso Neto (I) - I/L son números o vacío, nunca texto
    pp = r + 1
    _set(ws, ws.cell(row=pp, column=7), "Promedio ponderado de brix proceso", bold=True)
    if n_lotes:
        _set(ws, ws.cell(row=pp, column=12),
             f'=IF(SUM(I{prim}:I{ult})=0,"",SUMPRODUCT(I{prim}:I{ult},L{prim}:L{ult})/SUM(I{prim}:I{ult}))',
             fmt=_FMT_BRIX, bold=True)

    # ---------- datos de entrada del bloque de resumen ----------
    prod_pt = [p for p in productos if _es_pt(p.get("producto"))]
    volumen = sum(_f(p.get("volumen_litros")) or 0.0 for p in prod_pt) or None
    tambores = (sum(_f(p.get("tambores")) or 0 for p in prod_pt)) or None
    peso_tambor = next((_f(p.get("peso_neto_tambor_kg")) for p in prod_pt if p.get("peso_neto_tambor_kg")), None)
    pt_kg = sum(_f(p.get("pt_kg")) or 0.0 for p in prod_pt) or None

    peso_med = [(_f(m.get("litros")) or 0.0, _f(m["brix_final"]))
                for m in mediciones if m.get("brix_final") is not None]
    if peso_med and sum(w for w, _ in peso_med) > 0:
        brix_tk = sum(w * b for w, b in peso_med) / sum(w for w, _ in peso_med)
    elif peso_med:
        brix_tk = sum(b for _, b in peso_med) / len(peso_med)
    else:
        brix_tk = _f(corrida.get("brix_promedio_tk"))
    mp_fallback = _f(corrida.get("mp_kg_objetivo")) or _f(corrida.get("kg_asignados_total")) or 0.0

    # ---------- bloque de resumen (fórmulas) + tablas de saldo ----------
    SEC = r + 3
    _set(ws, ws.cell(row=SEC, column=2), "RESUMEN", bold=True, fill=_SEC_FILL)
    _set(ws, ws.cell(row=SEC, column=8), "Stock inicial al siguiente proceso", bold=True, fill=_SEC_FILL)

    m0 = SEC + 1
    (rMP, rHR, rMPh, rVol, rTam, rPesoTam, rPT, rRend,
     rBrix, rDens, rMasa, rRJS, rSem, rCas, rCS, rGNC, rRatio) = range(m0, m0 + 17)

    def val(fila, etiqueta, valor, fmt=None):
        _set(ws, ws.cell(row=fila, column=2), etiqueta, bold=True)
        _set(ws, ws.cell(row=fila, column=4), valor, fmt=fmt)

    val(rMP, "MP kg", (f"=K{r}" if n_lotes else (round(mp_fallback, 2) or None)), _FMT_KG)
    val(rHR, "HR pulpeado", HR_PULPEADO, "0")
    val(rMPh, "MP kg/hr", f'=IF(D{rHR}=0,"",D{rMP}/D{rHR})', _FMT_KG)
    val(rVol, "Volumen  litros", round(volumen, 2) if volumen else None, _FMT_KG)
    _set(ws, ws.cell(row=rVol, column=5), f'=IF(OR(D{rVol}="",D{rMP}=0),"",D{rVol}/D{rMP})', fmt=_FMT_PCT)
    val(rTam, "Tambores  und", tambores, "0")
    val(rPesoTam, "Peso Neto del tambor  kg", peso_tambor, _FMT_KG)
    val(rPT, "PT  kg",
        (f"=D{rTam}*D{rPesoTam}" if (tambores and peso_tambor) else (round(pt_kg, 2) if pt_kg else None)),
        _FMT_KG)
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

    # ---- tabla de saldo por lote (a la derecha, desde col H) ----
    con_saldo = [l for l in lotes if (_f(l.get("kg_saldo")) or 0) > 0.01]
    sc = 8  # H
    sh = ["Lote", "Peso Neto", "Peso procesado", "Peso saldo", "Bines saldo", "Bines totales"]
    for k, nombre in enumerate(sh):
        _set(ws, ws.cell(row=m0, column=sc + k), nombre, fill=_HEAD_FILL, borde=True, centro=True)
        ws.cell(row=m0, column=sc + k).font = _HEAD_FONT
    j = 0
    for j, l in enumerate(con_saldo, start=1):
        rr = m0 + j
        _set(ws, ws.cell(row=rr, column=sc), l.get("lote_numero"), borde=True)
        _set(ws, ws.cell(row=rr, column=sc + 1), _f(l.get("peso_neto_kg")), fmt=_FMT_KG, borde=True)
        _set(ws, ws.cell(row=rr, column=sc + 2), _f(l.get("kg_consumidos")), fmt=_FMT_KG, borde=True)
        _set(ws, ws.cell(row=rr, column=sc + 3), f"=I{rr}-J{rr}", fmt=_FMT_KG, borde=True)  # saldo = neto - procesado
        _set(ws, ws.cell(row=rr, column=sc + 4), _f(l.get("bines_saldo")), borde=True)
        _set(ws, ws.cell(row=rr, column=sc + 5), _f(l.get("bines_totales")), borde=True)
    if con_saldo:
        tr = m0 + j + 1
        _set(ws, ws.cell(row=tr, column=sc + 2), "TOTAL", bold=True, borde=True)
        _set(ws, ws.cell(row=tr, column=sc + 3), f"=SUM(K{m0 + 1}:K{m0 + j})", fmt=_FMT_KG, bold=True, borde=True)
    else:
        _set(ws, ws.cell(row=m0 + 1, column=sc), "Sin saldo pendiente.")

    # ---------- anchos de columna ----------
    # D y E cargan las fechas INICIO/FINAL con hora ("DD/MM/YYYY HH:MM" ~16
    # caracteres) - si van angostas Excel muestra "#######".
    anchos = {1: 12, 2: 30, 3: 8, 4: 18, 5: 18, 6: 13, 7: 26, 8: 40, 9: 14, 10: 12,
              11: 16, 12: 13, 13: 13, 14: 14, 15: 11, 16: 13}
    for i, w in anchos.items():
        ws.column_dimensions[get_column_letter(i)].width = w

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
