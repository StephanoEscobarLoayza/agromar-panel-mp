"""Arma el .xlsx de "Trazabilidad" de UNA corrida, con el mismo formato que la
hoja de planta: encabezado de fechas, barra de título, tabla de lotes de MP con
su fila de totales, bloque de resumen (MP kg, rendimiento, masa de jugo simple,
ratio...) y las tablas de saldo por lote.

Lo que la app tiene se llena solo; lo que no está en la app (GRP, N° Guía,
Brix producción línea si no se cargó, descuentos por brix, cáscara / semilla,
consumo de GNC) queda en blanco para completarlo a mano - igual que esa hoja
se termina de llenar a mano al cierre de la corrida."""
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
    if fmt and valor is not None:
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
    lotes: una fila por asignación, con join a lotes y v_saldo_lotes
    (lote_numero, kg_asignados, bines_consumidos, brix_produccion,
    tipo_almacen_origen, fecha_proceso, proveedor, procedencia, guia,
    fecha_ingreso, brix_recepcion, peso_neto_kg, bines_totales, kg_saldo,
    bines_saldo, kg_consumidos, estado_actual).
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
    ws.merge_cells("B6:H6")

    # ---------- tabla de lotes de MP ----------
    cols = [
        "Proceso", "Lote", "GRP", "N° Guía", "F. Ingreso", "F. Proceso",
        "Procedencia", "Proveedor", "Peso Neto", "Silo / Bines", "Peso con descuento",
        "Brix Calidad recepción", "Brix Producción línea", "% Descuento a brix 10.5°B",
        "Descuento", "kg descuento",
    ]
    HFILA = 8
    for i, nombre in enumerate(cols, start=1):
        _set(ws, ws.cell(row=HFILA, column=i), nombre,
             fill=_HEAD_FILL, borde=True, centro=True)
        ws.cell(row=HFILA, column=i).font = _HEAD_FONT

    r = HFILA + 1
    total_peso = 0.0
    brix_pond = 0.0
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
        fila = [
            proceso,
            l.get("lote_numero"),
            None,                              # GRP - no está en la app
            l.get("guia"),                     # N° Guía
            _f(l.get("fecha_ingreso")),
            _f(l.get("fecha_proceso")),
            l.get("procedencia") or "",
            l.get("proveedor") or "",
            peso,                              # Peso Neto = kg que entraron de este lote
            silo_bines,
            peso,                              # Peso con descuento = Peso Neto (la app no maneja descuento por brix)
            brix,                              # Brix Calidad recepción
            _f(l.get("brix_produccion")),      # Brix Producción línea
            None, None, None,                  # % dscto, Descuento, kg descuento
        ]
        for i, v in enumerate(fila, start=1):
            c = _set(ws, ws.cell(row=r, column=i), v, borde=True)
            if i in (5, 6):
                c.number_format = _FMT_FECHA
            elif i in (9, 11):
                c.number_format = _FMT_KG
            elif i in (12, 13):
                c.number_format = _FMT_BRIX
        total_peso += peso or 0.0
        if brix is not None and peso:
            brix_pond += brix * peso
        r += 1

    # fila de totales
    _set(ws, ws.cell(row=r, column=8), "TOTAL", bold=True, fill=_TOTAL_FILL, borde=True)
    for cidx in (9, 11):
        _set(ws, ws.cell(row=r, column=cidx), round(total_peso, 2),
             fmt=_FMT_KG, bold=True, fill=_TOTAL_FILL, borde=True)
    for cidx in (10, 12, 13):
        ws.cell(row=r, column=cidx).fill = _TOTAL_FILL
        ws.cell(row=r, column=cidx).border = _BORDE
    brix_prom_recep = (brix_pond / total_peso) if total_peso else None
    _set(ws, ws.cell(row=r + 1, column=7), "Promedio ponderado de brix proceso", bold=True)
    _set(ws, ws.cell(row=r + 1, column=12),
         round(brix_prom_recep, 2) if brix_prom_recep is not None else None, fmt=_FMT_BRIX, bold=True)

    # ---------- valores del bloque de resumen ----------
    prod_pt = [p for p in productos if _es_pt(p.get("producto"))]
    mp_kg = round(total_peso, 2) or _f(corrida.get("mp_kg_objetivo")) or _f(corrida.get("kg_asignados_total")) or 0.0
    volumen = sum(_f(p.get("volumen_litros")) or 0.0 for p in prod_pt)
    tambores = sum(_f(p.get("tambores")) or 0 for p in prod_pt)
    peso_tambor = next((_f(p.get("peso_neto_tambor_kg")) for p in prod_pt if p.get("peso_neto_tambor_kg")), None)
    pt_kg = sum(_f(p.get("pt_kg")) or 0.0 for p in prod_pt)

    peso_med = [(_f(m.get("litros")) or 0.0, _f(m["brix_final"]))
                for m in mediciones if m.get("brix_final") is not None]
    if peso_med and sum(w for w, _ in peso_med) > 0:
        brix_tk = sum(w * b for w, b in peso_med) / sum(w for w, _ in peso_med)
    elif peso_med:
        brix_tk = sum(b for _, b in peso_med) / len(peso_med)
    else:
        brix_tk = _f(corrida.get("brix_promedio_tk"))

    masa_js = volumen * DENSIDAD_APARENTE if volumen else 0.0
    rendimiento = (pt_kg / mp_kg) if (pt_kg and mp_kg) else None
    rend_js_tk = (masa_js / mp_kg) if (masa_js and mp_kg) else None
    ratio_kg_m3 = (masa_js / pt_kg) if (masa_js and pt_kg) else None
    vol_pct = (volumen / mp_kg) if (volumen and mp_kg) else None

    fila0 = r + 3
    resumen = [
        ("MP kg", round(mp_kg, 2), _FMT_KG, None, None),
        ("HR pulpeado", HR_PULPEADO, "0", None, None),
        ("MP kg/hr", (mp_kg / HR_PULPEADO) if mp_kg else None, _FMT_KG, None, None),
        ("Volumen  litros", round(volumen, 2) or None, _FMT_KG, vol_pct, _FMT_PCT),
        ("Tambores  und", tambores or None, "0", None, None),
        ("Peso Neto del tambor  kg", peso_tambor, _FMT_KG, None, None),
        ("PT  kg", round(pt_kg, 2) or None, _FMT_KG, None, None),
        ("Rendimiento", rendimiento, _FMT_PCT, None, None),
        ("Brix Promedio TK", round(brix_tk, 2) if brix_tk is not None else None, _FMT_BRIX, None, None),
        ("Densidad Aparente  kg/l", DENSIDAD_APARENTE, "0.00000", None, None),
        ("Masa del Jugo Simple  kg", round(masa_js, 2) or None, _FMT_KG, None, None),
        ("Rendimiento de Jugo Simple Tanques", rend_js_tk, _FMT_PCT, None, None),
        ("Semilla", None, _FMT_KG, None, None),
        ("Cáscara", None, _FMT_KG, None, None),
        ("Cáscara / Semilla", None, _FMT_KG, None, None),
        ("Consumo de GNC  m³", None, _FMT_KG, None, None),
        ("Ratio  kg/m³", round(ratio_kg_m3, 3) if ratio_kg_m3 is not None else None, "0.000", None, None),
    ]
    for i, (label, val, fmt, extra, extra_fmt) in enumerate(resumen):
        rr = fila0 + i
        _set(ws, ws.cell(row=rr, column=2), label, bold=True)
        _set(ws, ws.cell(row=rr, column=4), val, fmt=fmt)
        if extra is not None:
            _set(ws, ws.cell(row=rr, column=5), extra, fmt=extra_fmt or _FMT_PCT)

    # ---------- tablas de saldo por lote (stock inicial al siguiente proceso) ----------
    con_saldo = [l for l in lotes if (_f(l.get("kg_saldo")) or 0) > 0.01]
    scol = 8  # columna H
    _set(ws, ws.cell(row=fila0 - 1, column=scol), "Stock inicial al siguiente proceso", bold=True)
    sh = ["Lote", "Peso Neto", "Peso procesado", "Peso saldo", "Bines saldo", "Bines totales"]
    for k, nombre in enumerate(sh):
        _set(ws, ws.cell(row=fila0, column=scol + k), nombre,
             fill=_HEAD_FILL, borde=True, centro=True)
        ws.cell(row=fila0, column=scol + k).font = _HEAD_FONT
    total_saldo = 0.0
    j = 0
    for j, l in enumerate(con_saldo, start=1):
        rr = fila0 + j
        saldo = _f(l.get("kg_saldo")) or 0.0
        total_saldo += saldo
        vals = [
            l.get("lote_numero"),
            _f(l.get("peso_neto_kg")),
            _f(l.get("kg_consumidos")),
            round(saldo, 2),
            _f(l.get("bines_saldo")),
            _f(l.get("bines_totales")),
        ]
        for k, v in enumerate(vals):
            c = _set(ws, ws.cell(row=rr, column=scol + k), v, borde=True)
            if k in (1, 2, 3):
                c.number_format = _FMT_KG
    _set(ws, ws.cell(row=fila0 + j + 1, column=scol + 2), "TOTAL", bold=True, borde=True)
    _set(ws, ws.cell(row=fila0 + j + 1, column=scol + 3), round(total_saldo, 2),
         fmt=_FMT_KG, bold=True, borde=True)
    if not con_saldo:
        _set(ws, ws.cell(row=fila0 + 1, column=scol), "Sin saldo pendiente.", )

    # ---------- anchos de columna ----------
    anchos = {1: 12, 2: 8, 3: 7, 4: 12, 5: 12, 6: 12, 7: 26, 8: 40, 9: 13, 10: 12,
              11: 14, 12: 12, 13: 12, 14: 14, 15: 11, 16: 12}
    for i, w in anchos.items():
        ws.column_dimensions[get_column_letter(i)].width = w

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
