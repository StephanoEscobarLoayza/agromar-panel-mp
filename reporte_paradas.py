"""Genera el .xlsx de "Paradas por período" (botón en Paradas) como un libro de análisis:

  · Tablero  - indicadores y gráficos: pareto de causas, minutos por área, tendencia, turno, responsable, equipos,
               hora de inicio, matriz área × tipo y las 10 paradas más largas.
  · Análisis - las tablas que alimentan los gráficos.
  · Datos    - una fila por parada como Tabla de Excel (filtros, columnas de fecha / semana / mes), lista para armar
               tablas dinámicas y segmentaciones propias.
  · Detalle  - el listado legible e imprimible, con los textos completos.

Los números del tablero se escriben como valores (no como fórmulas) a propósito: así los gráficos se ven completos
también en la Vista protegida de Excel, donde un archivo descargado no recalcula hasta «Habilitar edición».
Las paradas en curso se cuentan, pero no suman minutos hasta que se cierran."""
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from io import BytesIO

from openpyxl import Workbook
from openpyxl.chart import BarChart, DoughnutChart, LineChart, Reference
from openpyxl.chart.label import DataLabelList
from openpyxl.chart.marker import Marker
from openpyxl.chart.series import DataPoint
from openpyxl.chart.shapes import GraphicalProperties
from openpyxl.chart.text import RichText, Text
from openpyxl.chart.title import Title
from openpyxl.drawing.text import CharacterProperties, Paragraph, ParagraphProperties, RegularTextRun
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

# ---------- paleta de la marca ----------
_BOSQUE = "1F4E37"      # verde bosque
_CITRICO = "E8890C"     # cítrico: acento
_SALVIA = "5B8E7D"
_MOSTAZA = "C9A227"
_TIERRA = "8C4A2F"
_GRIS = "6C757D"
_LIENZO = "F6F3EA"      # fondo del tablero
_PALETA = [_BOSQUE, _CITRICO, _SALVIA, _MOSTAZA, _TIERRA, _GRIS]

_TITULO_FILL = PatternFill("solid", fgColor=_BOSQUE)
_ACENTO_FILL = PatternFill("solid", fgColor=_CITRICO)
_LIENZO_FILL = PatternFill("solid", fgColor=_LIENZO)
_BLANCO_FILL = PatternFill("solid", fgColor="FFFFFF")
_HEAD_FILL = PatternFill("solid", fgColor="CFE2C1")
_FILA_ALT_FILL = PatternFill("solid", fgColor="F3F8EF")
_EN_CURSO_FILL = PatternFill("solid", fgColor="FBEBD6")
_BOLD = Font(bold=True)
_HEAD_FONT = Font(bold=True, size=10, color=_BOSQUE)
_TITULO_FONT = Font(bold=True, size=13, color="FFFFFF")
_THIN = Side("thin", color="C9CEC5")
_SUAVE = Side("thin", color="DDD6C8")
_BORDE = Border(_THIN, _THIN, _THIN, _THIN)
_WRAP = Alignment(wrap_text=True, vertical="top")
_CENTRO = Alignment(horizontal="center", vertical="center", wrap_text=True)
_FMT_FECHAHORA = "DD/MM/YYYY HH:MM"
_FMT_FECHA = "DD/MM/YYYY"

_DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
_MESES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Setiembre", "Octubre", "Noviembre", "Diciembre"]

COLS = [
    ("Corrida", 20),
    ("Turno", 9),
    ("Inicio", 17),
    ("Fin", 17),
    ("Minutos", 10),
    ("Área / proceso", 16),
    ("Tipo de parada", 15),
    ("Equipo afectado", 18),
    ("Descripción de la falla", 34),
    ("Responsable", 14),
    ("Solución / observaciones", 34),
    ("Recomendación", 30),
]


def _ahora_peru():
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-5)))


def _fmt_fecha_corta(d):
    return d.strftime("%d/%m/%Y")


def _fmt_minutos(min_total):
    if min_total is None:
        return "en curso"
    h, m = divmod(int(round(min_total)), 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def _fmt_horas(min_total):
    """Duración para las tarjetas: «12 h 35 min»."""
    h, m = divmod(int(round(min_total or 0)), 60)
    return f"{h} h {m:02d} min" if h else f"{m} min"


def _poner(ws, fila, col, valor):
    """Escribe una celda. Un texto que empiece por «=» (lo escribe una persona en una descripción) se guarda como texto,
    no como fórmula."""
    c = ws.cell(row=fila, column=col, value=valor)
    if isinstance(valor, str) and valor[:1] in ("=", "+", "-", "@"):
        c.data_type = "s"
    return c


def _num(v):
    return float(v) if v is not None else None


# ===================================================================== preparación de los datos
def _preparar(paradas):
    filas = []
    for p in paradas:
        ini, fin = p.get("hora_inicio"), p.get("hora_fin")
        turno = p.get("turno")
        filas.append({
            "corrida": p.get("corrida") or "—",
            "turno": "Día" if turno == "DÍA" else "Noche" if turno == "NOCHE" else (turno or "Sin turno"),
            "inicio": ini, "fin": fin, "min": _num(p.get("min_total")),
            "area": (p.get("area_proceso") or "").strip() or "Sin área",
            "tipo": (p.get("tipo_parada") or "").strip() or "Sin tipo",
            "equipo": (p.get("equipo_afectado") or "").strip() or "Sin equipo",
            "resp": (p.get("responsable_solucion") or "").strip() or "Sin responsable",
            "falla": p.get("descripcion_falla") or "", "sol": p.get("solucion_obs") or "", "rec": p.get("recomendacion") or "",
            "dia": ini.date() if isinstance(ini, datetime) else None,
            "hora": ini.hour if isinstance(ini, datetime) else None,
            "en_curso": fin is None,
        })
    return filas


def _minutos_sin_duplicar(filas):
    """Minutos de planta parada: une los tramos que se solapan. Si JCC e ICEGEN corren en paralelo y la misma parada se
    registró en las dos corridas, la suma por corrida la cuenta dos veces; aquí cuenta una sola."""
    tramos = sorted((f["inicio"], f["fin"]) for f in filas if f["fin"] is not None and isinstance(f["inicio"], datetime))
    total, ini, fin = 0.0, None, None
    for a, b in tramos:
        if fin is None or a > fin:
            if fin is not None:
                total += (fin - ini).total_seconds() / 60
            ini, fin = a, b
        elif b > fin:
            fin = b
    if fin is not None:
        total += (fin - ini).total_seconds() / 60
    return total


def _agrupar(filas, clave):
    """[(valor, paradas, minutos)] ordenado de más a menos minutos."""
    acc = defaultdict(lambda: [0, 0.0])
    for f in filas:
        a = acc[f[clave]]
        a[0] += 1
        a[1] += f["min"] or 0.0
    return sorted(((k, v[0], v[1]) for k, v in acc.items()), key=lambda t: (-t[2], -t[1], str(t[0])))


# ===================================================================== utilidades de dibujo
def _estilo_lienzo(ws, filas, cols):
    for r in range(1, filas + 1):
        for c in range(1, cols + 1):
            ws.cell(row=r, column=c).fill = _LIENZO_FILL


def _fusionar(ws, fila, c1, c2, valor=None, *, fill=None, font=None, alinea=None, borde=None, fmt=None):
    if c2 > c1:
        ws.merge_cells(start_row=fila, start_column=c1, end_row=fila, end_column=c2)
    c = _poner(ws, fila, c1, valor)
    for col in range(c1, c2 + 1):
        cc = ws.cell(row=fila, column=col)
        if fill:
            cc.fill = fill
        if borde:
            cc.border = borde
    if font:
        c.font = font
    if alinea:
        c.alignment = alinea
    if fmt:
        c.number_format = fmt
    return c


def _tarjeta(ws, fila, col, etiqueta, valor, nota="", color=_BOSQUE):
    """Tarjeta de indicador de 4 columnas × 4 filas: etiqueta, valor grande y nota."""
    c1, c2 = col, col + 3
    top = Side("medium", color=color)
    for r in range(fila, fila + 4):
        for c in range(c1, c2 + 1):
            cc = ws.cell(row=r, column=c)
            cc.fill = _BLANCO_FILL
            cc.border = Border(left=_SUAVE if c == c1 else None, right=_SUAVE if c == c2 else None,
                               top=top if r == fila else None, bottom=_SUAVE if r == fila + 3 else None)
    _fusionar(ws, fila, c1, c2, etiqueta.upper(), font=Font(size=8, bold=True, color=_GRIS), alinea=Alignment(horizontal="left", vertical="center", indent=1))
    ws.merge_cells(start_row=fila + 1, start_column=c1, end_row=fila + 2, end_column=c2)
    v = _poner(ws, fila + 1, c1, valor)
    v.font = Font(size=20 if len(str(valor)) <= 11 else 14, bold=True, color=color)      # un texto largo (p. ej. el tipo de parada) baja de tamaño
    v.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    _fusionar(ws, fila + 3, c1, c2, nota, font=Font(size=8, color=_GRIS), alinea=Alignment(horizontal="left", vertical="center", indent=1))


def _titulo_grafico(texto):
    cp = CharacterProperties(sz=1100, b=True, solidFill=_BOSQUE)
    p = Paragraph(pPr=ParagraphProperties(defRPr=cp), r=[RegularTextRun(rPr=cp, t=texto)])
    return Title(tx=Text(rich=RichText(p=[p])), overlay=False)


def _marco(ch, ancho=19.4, alto=9.0):
    ch.width, ch.height = ancho, alto
    ch.graphical_properties = GraphicalProperties(solidFill="FFFFFF")
    ch.graphical_properties.line.solidFill = "DDD6C8"
    ch.roundedCorners = False


def _etiquetas(serie, formato="#,##0;-#,##0;;", porcentaje=False):
    serie.dLbls = DataLabelList()
    serie.dLbls.showVal = not porcentaje
    serie.dLbls.showPercent = porcentaje
    serie.dLbls.showCatName = False
    serie.dLbls.showSerName = False
    serie.dLbls.showLegendKey = False
    serie.dLbls.numFmt = formato


def _barras(titulo, ws, col_cat, col_val, fila_enc, fila_fin, *, horizontal=False, color=_BOSQUE, formato="#,##0;-#,##0;;"):
    ch = BarChart()
    ch.type = "bar" if horizontal else "col"
    ch.title = _titulo_grafico(titulo)
    ch.legend = None
    ch.add_data(Reference(ws, min_col=col_val, min_row=fila_enc, max_row=fila_fin), titles_from_data=True)
    ch.set_categories(Reference(ws, min_col=col_cat, min_row=fila_enc + 1, max_row=fila_fin))
    s = ch.series[0]
    s.graphicalProperties.solidFill = color
    s.graphicalProperties.line.solidFill = color
    ch.gapWidth = 55
    _etiquetas(s, formato)
    ch.y_axis.majorGridlines = None
    valores = [ws.cell(row=r, column=col_val).value or 0 for r in range(fila_enc + 1, fila_fin + 1)]
    ch.y_axis.scaling.min = 0
    ch.y_axis.scaling.max = (max(valores) * 1.22) if valores and max(valores) > 0 else 1      # holgura para la etiqueta de la barra más alta
    ch.y_axis.delete = False         # el eje se conserva (para respetar el máximo) pero invisible: los valores van en las etiquetas
    ch.y_axis.number_format = ";;;"           # sin números en el eje
    ch.y_axis.majorTickMark = "none"
    ch.y_axis.minorTickMark = "none"
    ch.y_axis.graphicalProperties = GraphicalProperties()
    ch.y_axis.graphicalProperties.line.noFill = True
    ch.x_axis.delete = False
    if horizontal:
        ch.x_axis.scaling.orientation = "maxMin"     # la mayor arriba
    _marco(ch)
    return ch


def _pareto(titulo, ws, fila_enc, fila_fin):
    """Barras de minutos por tipo + línea de % acumulado en el eje secundario."""
    barras = _barras(titulo, ws, 1, 3, fila_enc, fila_fin)
    barras.legend = None
    linea = LineChart()
    linea.add_data(Reference(ws, min_col=5, min_row=fila_enc, max_row=fila_fin), titles_from_data=True)
    linea.y_axis.axId = 200
    linea.y_axis.number_format = "0%"
    linea.y_axis.scaling.min, linea.y_axis.scaling.max = 0, 1
    linea.y_axis.majorGridlines = None
    linea.y_axis.crosses = "max"
    linea.y_axis.delete = False
    s = linea.series[0]
    s.graphicalProperties.line.solidFill = _CITRICO
    s.graphicalProperties.line.width = 28575
    s.marker = Marker(symbol="circle", size=7)
    s.marker.graphicalProperties = GraphicalProperties(solidFill=_CITRICO)
    s.marker.graphicalProperties.line.solidFill = _CITRICO
    s.smooth = False
    barras += linea
    return barras


def _dona(titulo, ws, fila_enc, fila_fin, n):
    ch = DoughnutChart()
    ch.title = _titulo_grafico(titulo)
    ch.holeSize = 58
    ch.add_data(Reference(ws, min_col=3, min_row=fila_enc, max_row=fila_fin), titles_from_data=True)
    ch.set_categories(Reference(ws, min_col=1, min_row=fila_enc + 1, max_row=fila_fin))
    s = ch.series[0]
    for i in range(n):
        pt = DataPoint(idx=i)
        pt.graphicalProperties = GraphicalProperties(solidFill=_PALETA[i % len(_PALETA)])
        s.dPt.append(pt)
    _etiquetas(s, "0%;;;", porcentaje=True)
    blanco = CharacterProperties(sz=1000, b=True, solidFill="FFFFFF")
    s.dLbls.txPr = RichText(p=[Paragraph(pPr=ParagraphProperties(defRPr=blanco), endParaRPr=blanco)])      # porcentaje en blanco sobre el color del turno
    ch.legend.position = "r"
    _marco(ch)
    return ch


# ===================================================================== hojas
def _tabla(ws, fila0, titulo, cabeceras, filas, formatos=None):
    """Título + encabezado + filas en la hoja de análisis. Devuelve (fila del encabezado, última fila)."""
    c = ws.cell(row=fila0, column=1, value=titulo)
    c.font = Font(bold=True, size=11, color=_BOSQUE)
    fila_enc = fila0 + 1
    for i, h in enumerate(cabeceras, start=1):
        cc = ws.cell(row=fila_enc, column=i, value=h)
        cc.font, cc.fill, cc.alignment, cc.border = _HEAD_FONT, _HEAD_FILL, _CENTRO, _BORDE
    r = fila_enc
    for fila in filas:
        r += 1
        for i, v in enumerate(fila, start=1):
            cc = _poner(ws, r, i, v)
            cc.border = _BORDE
            if formatos and formatos.get(i):
                cc.number_format = formatos[i]
    return fila_enc, r


def _hoja_analisis(wb, filas, desde, hasta):
    ws = wb.create_sheet("Análisis")
    ws.sheet_properties.tabColor = _SALVIA
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 32
    for col in "BCDE":
        ws.column_dimensions[col].width = 15
    total_min = sum(f["min"] or 0 for f in filas)
    t = {}
    r = 1
    ws.cell(row=r, column=1, value="Tablas de apoyo de los gráficos del Tablero").font = Font(bold=True, size=13, color=_BOSQUE)
    r = 3

    por_tipo = _agrupar(filas, "tipo")
    acum, lista = 0.0, []
    for k, n, m in por_tipo:
        acum += m
        lista.append((k, n, round(m, 1), (m / total_min) if total_min else 0, (acum / total_min) if total_min else 0))
    t["tipo"] = _tabla(ws, r, "Por tipo de parada (pareto)", ["Tipo de parada", "Paradas", "Minutos", "% del tiempo", "% acumulado"], lista, {3: "#,##0", 4: "0.0%", 5: "0%"})
    r = t["tipo"][1] + 3

    for clave, nombre, tit in (("area", "area", "Por área / proceso"), ("turno", "turno", "Por turno"), ("resp", "resp", "Por responsable de solución")):
        datos = [(k, n, round(m, 1)) for k, n, m in _agrupar(filas, clave)]
        if clave == "turno":
            presentes = {d[0] for d in datos}
            datos += [(tn, 0, 0) for tn in ("Día", "Noche") if tn not in presentes]
            datos.sort(key=lambda d: -d[2])
        t[nombre] = _tabla(ws, r, tit, [tit.replace("Por ", "").capitalize(), "Paradas", "Minutos"], datos, {3: "#,##0;-#,##0;"})
        r = t[nombre][1] + 3

    enc_t, fin_t = t["turno"]
    k_t = sum(1 for rr in range(enc_t + 1, fin_t + 1) if (ws.cell(row=rr, column=3).value or 0) > 0) or 1
    t["turno_graf"] = (enc_t, enc_t + k_t)

    equipos = [(k, n, round(m, 1)) for k, n, m in _agrupar(filas, "equipo")][:8]
    t["equipo"] = _tabla(ws, r, "Por equipo afectado (los 8 con más minutos)", ["Equipo afectado", "Paradas", "Minutos"], equipos, {3: "#,##0;-#,##0;"})
    r = t["equipo"][1] + 3

    # tendencia: por día, o por semana si el período es largo
    dias_periodo = (hasta - desde).days + 1
    por_dia = defaultdict(lambda: [0, 0.0])
    for f in filas:
        if f["dia"] is None:
            continue
        clave = f["dia"] if dias_periodo <= 45 else f["dia"] - timedelta(days=f["dia"].weekday())
        por_dia[clave][0] += 1
        por_dia[clave][1] += f["min"] or 0.0
    if dias_periodo <= 45:
        dias = [desde + timedelta(days=i) for i in range(dias_periodo)]
        etiqueta, cab = (lambda d: d.strftime("%d/%m")), "Día"
    else:
        lunes = desde - timedelta(days=desde.weekday())
        dias = [lunes + timedelta(weeks=i) for i in range(((hasta - lunes).days // 7) + 1)]
        etiqueta, cab = (lambda d: "Sem " + d.strftime("%d/%m")), "Semana (lunes)"
    serie = [(etiqueta(d), por_dia[d][0] if d in por_dia else 0, round(por_dia[d][1], 1) if d in por_dia else 0) for d in dias]
    t["dia"] = _tabla(ws, r, "Tendencia: minutos parados" + (" por día" if dias_periodo <= 45 else " por semana"), [cab, "Paradas", "Minutos"], serie, {3: "#,##0;-#,##0;"})
    r = t["dia"][1] + 3

    por_hora = defaultdict(lambda: [0, 0.0])
    for f in filas:
        if f["hora"] is not None:
            por_hora[f["hora"]][0] += 1
            por_hora[f["hora"]][1] += f["min"] or 0.0
    horas = [(f"{h:02d}:00", por_hora[h][0], round(por_hora[h][1], 1)) for h in range(24)]
    t["hora"] = _tabla(ws, r, "Paradas según la hora de inicio", ["Hora de inicio", "Paradas", "Minutos"], horas, {2: "#,##0;-#,##0;", 3: "#,##0;-#,##0;"})
    return ws, t


def _hoja_datos(wb, filas):
    ws = wb.create_sheet("Datos")
    ws.sheet_properties.tabColor = _CITRICO
    cols = [
        ("Corrida", 22), ("Fecha", 12), ("Turno", 11), ("Inicio", 17), ("Fin", 17), ("Duración (min)", 12), ("Duración (h)", 12),
        ("Hora de inicio", 12), ("Día de la semana", 15), ("Semana", 11), ("Mes", 12), ("Área / proceso", 18), ("Tipo de parada", 19),
        ("Equipo afectado", 21), ("Responsable", 17), ("Descripción de la falla", 40), ("Solución / observaciones", 40), ("Recomendación", 36), ("Estado", 10),
    ]
    for i, (nombre, ancho) in enumerate(cols, start=1):
        c = ws.cell(row=1, column=i, value=nombre)
        c.font, c.alignment, c.fill = Font(bold=True, color="FFFFFF"), _CENTRO, _TITULO_FILL
        ws.column_dimensions[get_column_letter(i)].width = ancho
    ws.row_dimensions[1].height = 30
    for r, f in enumerate(filas, start=2):
        ini, dia = f["inicio"], f["dia"]
        valores = [
            f["corrida"], dia, f["turno"], ini, f["fin"], f["min"], round(f["min"] / 60, 2) if f["min"] is not None else None,
            f["hora"], _DIAS[dia.weekday()] if dia else None, dia.isocalendar()[1] if dia else None, _MESES[dia.month - 1] if dia else None,
            f["area"], f["tipo"], f["equipo"], f["resp"], f["falla"], f["sol"], f["rec"], "En curso" if f["en_curso"] else "Cerrada",
        ]
        for i, v in enumerate(valores, start=1):
            c = _poner(ws, r, i, v)
            if i == 2:
                c.number_format = _FMT_FECHA
            elif i in (4, 5):
                c.number_format = _FMT_FECHAHORA
            elif i == 6:
                c.number_format = "#,##0"
            elif i == 7:
                c.number_format = "0.00"
            c.alignment = Alignment(vertical="top")      # una línea por parada (los textos completos están en «Detalle»)
    if filas:
        tabla = Table(displayName="TablaParadas", ref=f"A1:{get_column_letter(len(cols))}{len(filas) + 1}")
        tabla.tableStyleInfo = TableStyleInfo(name="TableStyleLight1", showRowStripes=True, showColumnStripes=False)
        ws.add_table(tabla)
    ws.freeze_panes = "A2"
    return ws


def _hoja_detalle(wb, desde, hasta, paradas):
    """El listado legible/imprimible de siempre."""
    ws = wb.create_sheet("Detalle")
    ws.sheet_properties.tabColor = _GRIS
    ws.sheet_view.showGridLines = False
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ncols = len(COLS)

    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncols)
    c = ws.cell(row=1, column=1, value=f"Paradas del {_fmt_fecha_corta(desde)} al {_fmt_fecha_corta(hasta)}")
    c.font, c.fill = _TITULO_FONT, _TITULO_FILL
    c.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[1].height = 26
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=ncols)
    for i in range(1, ncols + 1):
        ws.cell(row=2, column=i).fill = _ACENTO_FILL
    ws.row_dimensions[2].height = 4

    total_min = sum(float(p.get("min_total") or 0) for p in paradas)
    n = len(paradas)
    en_curso = sum(1 for p in paradas if p.get("hora_fin") is None)
    resumen = f"{n} parada{'s' if n != 1 else ''} · {_fmt_minutos(total_min)} parados en total"
    if en_curso:
        resumen += f" · {en_curso} en curso"
    ws.merge_cells(start_row=3, start_column=1, end_row=3, end_column=ncols)
    c = ws.cell(row=3, column=1, value=resumen)
    c.font = Font(italic=True, size=10, color="5B6459")
    c.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[3].height = 20

    hfila = 5
    for i, (nombre, ancho) in enumerate(COLS, start=1):
        cc = ws.cell(row=hfila, column=i, value=nombre)
        cc.font, cc.fill, cc.alignment, cc.border = _HEAD_FONT, _HEAD_FILL, _CENTRO, _BORDE
        ws.column_dimensions[get_column_letter(i)].width = ancho
    ws.row_dimensions[hfila].height = 28

    r = hfila + 1
    if not paradas:
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=ncols)
        cc = ws.cell(row=r, column=1, value="Sin paradas registradas en este período.")
        cc.font = Font(italic=True, color="8B9186")
        cc.alignment = Alignment(horizontal="center")
    for idx, p in enumerate(paradas):
        fin = p.get("hora_fin")
        # una parada sin cerrar se resalta entera en ámbar; las demás llevan cebreado sutil cada 2 filas
        fila_fill = _EN_CURSO_FILL if fin is None else (_FILA_ALT_FILL if idx % 2 == 1 else None)
        fila = [
            p.get("corrida") or "—",
            "Día" if p.get("turno") == "DÍA" else "Noche" if p.get("turno") == "NOCHE" else (p.get("turno") or "—"),
            p.get("hora_inicio"), fin, p.get("min_total"),
            p.get("area_proceso") or "—", p.get("tipo_parada") or "—", p.get("equipo_afectado") or "—", p.get("descripcion_falla") or "—",
            p.get("responsable_solucion") or "—", p.get("solucion_obs") or "—", p.get("recomendacion") or "—",
        ]
        for i, v in enumerate(fila, start=1):
            cc = _poner(ws, r, i, v)
            cc.border = _BORDE
            if fila_fill:
                cc.fill = fila_fill
            if i in (3, 4):
                cc.number_format = _FMT_FECHAHORA
                cc.alignment = Alignment(vertical="top")
            elif i == 5:
                cc.alignment = Alignment(horizontal="right", vertical="top")
                if v is None:
                    cc.value = "en curso"
                    cc.font = _BOLD
                    cc.alignment = Alignment(horizontal="center", vertical="top")
            elif i in (9, 11, 12):
                cc.alignment = _WRAP
            else:
                cc.alignment = Alignment(vertical="top", wrap_text=True)
        r += 1
    ws.freeze_panes = f"A{hfila + 1}"
    ws.print_title_rows = f"{hfila}:{hfila}"
    r += 1
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=ncols)
    cc = ws.cell(row=r, column=1, value=f"Generado el {_ahora_peru().strftime('%d/%m/%Y %H:%M')} desde el Panel de cuadre de producción.")
    cc.font = Font(italic=True, size=9, color="8B9186")
    cc.alignment = Alignment(horizontal="left", indent=1)
    return ws


def _hoja_tablero(ws, filas, desde, hasta, ws_an, t):
    ws.sheet_properties.tabColor = _BOSQUE
    ws.sheet_view.showGridLines = False
    ws.sheet_view.zoomScale = 90
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.sheet_format.defaultColWidth = 8.43
    ws.column_dimensions["A"].width = 2.5
    ws.column_dimensions["Z"].width = 2.5
    ULT = 26                                         # columnas A..Z; el contenido ocupa B..Y (24 columnas)
    _estilo_lienzo(ws, 100, ULT)

    # ---------- encabezado ----------
    ws.row_dimensions[1].height = 36
    _fusionar(ws, 1, 2, 25, f"Tablero de paradas  ·  {_fmt_fecha_corta(desde)} al {_fmt_fecha_corta(hasta)}", fill=_TITULO_FILL, font=Font(bold=True, size=16, color="FFFFFF"),
              alinea=Alignment(horizontal="left", vertical="center", indent=1))
    ws.row_dimensions[2].height = 4
    _fusionar(ws, 2, 2, 25, None, fill=_ACENTO_FILL)
    ws.row_dimensions[3].height = 20
    _fusionar(ws, 3, 2, 25, f"Generado el {_ahora_peru().strftime('%d/%m/%Y %H:%M')}  ·  Las paradas en curso se cuentan, pero no suman minutos hasta que se cierran.",
              font=Font(italic=True, size=9, color=_GRIS), alinea=Alignment(horizontal="left", vertical="center", indent=1))
    if not filas:
        _fusionar(ws, 6, 2, 25, "Sin paradas registradas en este período.", font=Font(italic=True, size=12, color=_GRIS), alinea=Alignment(horizontal="center", vertical="center"))
        return

    # ---------- indicadores ----------
    n = len(filas)
    cerradas = [f for f in filas if f["min"] is not None]
    total_min = sum(f["min"] for f in cerradas)
    prom = total_min / len(cerradas) if cerradas else 0
    mayor = max(cerradas, key=lambda f: f["min"]) if cerradas else None
    en_curso = sum(1 for f in filas if f["en_curso"])
    por_tipo = _agrupar(filas, "tipo")
    top_tipo = por_tipo[0] if por_tipo and por_tipo[0][2] > 0 else None
    corridas = len({f["corrida"] for f in filas})
    ws.row_dimensions[5].height, ws.row_dimensions[6].height, ws.row_dimensions[7].height, ws.row_dimensions[8].height = 18, 24, 24, 18
    _tarjeta(ws, 5, 2, "Paradas", n, f"en {corridas} corrida{'s' if corridas != 1 else ''}", _BOSQUE)
    sin_duplicar = _minutos_sin_duplicar(filas)
    nota_tiempo = f"{total_min / 60:,.1f} h sumando corridas"
    if sin_duplicar and total_min - sin_duplicar >= 1:
        nota_tiempo += f" · {sin_duplicar / 60:,.1f} h de planta"       # hay paradas simultáneas en varias corridas
    _tarjeta(ws, 5, 6, "Tiempo parado", _fmt_horas(total_min), nota_tiempo, _CITRICO)
    _tarjeta(ws, 5, 10, "Duración promedio", _fmt_horas(prom), "por parada cerrada", _SALVIA)
    _tarjeta(ws, 5, 14, "Parada más larga", _fmt_horas(mayor["min"]) if mayor else "—", (f"{mayor['tipo']} · {mayor['area']}")[:34] if mayor else "", _TIERRA)
    _tarjeta(ws, 5, 18, "Principal causa", top_tipo[0] if top_tipo else "—", (f"{top_tipo[2] / total_min:.0%} del tiempo parado" if top_tipo and total_min else ""), _MOSTAZA)
    _tarjeta(ws, 5, 22, "En curso", en_curso, "paradas sin cerrar" if en_curso else "todas cerradas", _GRIS if not en_curso else _CITRICO)

    # ---------- gráficos (2 por fila: columnas B y N) ----------
    pareto_enc, pareto_fin = t["tipo"]
    ws.add_chart(_pareto("Pareto de causas · minutos parados por tipo de parada", ws_an, pareto_enc, pareto_fin), "B10")
    enc, fin = t["area"]
    ws.add_chart(_barras("Minutos parados por área / proceso", ws_an, 1, 3, enc, fin, horizontal=True, color=_SALVIA), "N10")

    enc, fin = t["dia"]
    ws.add_chart(_barras("Tendencia · minutos parados", ws_an, 1, 3, enc, fin, color=_CITRICO), "B28")
    enc, fin = t["turno_graf"]
    ws.add_chart(_dona("Reparto del tiempo parado por turno", ws_an, enc, fin, fin - enc), "N28")

    enc, fin = t["resp"]
    ws.add_chart(_barras("Minutos parados según el responsable de la solución", ws_an, 1, 3, enc, fin, horizontal=True, color=_BOSQUE), "B46")
    enc, fin = t["equipo"]
    ws.add_chart(_barras("Equipos con más tiempo parado", ws_an, 1, 3, enc, fin, horizontal=True, color=_TIERRA), "N46")

    enc, fin = t["hora"]
    ws.add_chart(_barras("¿A qué hora empiezan las paradas? · cantidad", ws_an, 1, 2, enc, fin, color=_MOSTAZA), "B64")

    # ---------- matriz área × tipo (a la derecha del gráfico de horas) ----------
    tipos = [k for k, _, _ in por_tipo][:7]
    areas = [k for k, _, _ in _agrupar(filas, "area")][:9]
    mat = defaultdict(float)
    for f in filas:
        mat[(f["area"], f["tipo"])] += f["min"] or 0.0
    r0 = 64
    _fusionar(ws, r0, 15, 25, "Matriz · minutos parados por área y tipo de parada", font=Font(bold=True, size=11, color=_BOSQUE), alinea=Alignment(vertical="center", indent=1))
    ws.row_dimensions[r0 + 1].height = 34
    _fusionar(ws, r0 + 1, 15, 16, "Área / proceso", fill=_HEAD_FILL, font=_HEAD_FONT, alinea=_CENTRO, borde=_BORDE)
    for j, tp in enumerate(tipos):
        c = ws.cell(row=r0 + 1, column=17 + j, value=tp)
        c.font, c.fill, c.alignment, c.border = Font(bold=True, size=8, color=_BOSQUE), _HEAD_FILL, _CENTRO, _BORDE
    ctot = 17 + len(tipos)
    c = ws.cell(row=r0 + 1, column=ctot, value="Total")
    c.font, c.fill, c.alignment, c.border = _HEAD_FONT, _HEAD_FILL, _CENTRO, _BORDE
    for i, ar in enumerate(areas):
        r = r0 + 2 + i
        _fusionar(ws, r, 15, 16, ar, fill=_BLANCO_FILL, borde=_BORDE, font=Font(size=9))
        total_fila = 0.0
        for j, tp in enumerate(tipos):
            v = round(mat.get((ar, tp), 0.0), 0)
            total_fila += v
            c = ws.cell(row=r, column=17 + j, value=v if v else None)
            c.border, c.alignment, c.number_format = _BORDE, Alignment(horizontal="center"), "#,##0"
        c = ws.cell(row=r, column=ctot, value=total_fila)
        c.font, c.border, c.alignment, c.number_format = _BOLD, _BORDE, Alignment(horizontal="center"), "#,##0"
    if areas and tipos:
        rango = f"{get_column_letter(17)}{r0 + 2}:{get_column_letter(16 + len(tipos))}{r0 + 1 + len(areas)}"
        ws.conditional_formatting.add(rango, ColorScaleRule(start_type="min", start_color="FFFFFF", end_type="max", end_color=_CITRICO))
    ws.cell(row=r0 + 2 + len(areas), column=15, value="Minutos de paradas cerradas. Más intenso = más tiempo parado.").font = Font(italic=True, size=8, color=_GRIS)

    # ---------- lectura rápida (frases armadas con los mismos datos) ----------
    ideas = []
    if top_tipo and total_min:
        ideas.append(f"La causa principal fue «{top_tipo[0]}»: {top_tipo[2]:,.0f} min ({top_tipo[2] / total_min:.0%} del tiempo parado).")
    eq = [e for e in _agrupar(filas, "equipo") if e[2] > 0]
    if eq:
        ideas.append(f"El equipo con más tiempo parado fue «{eq[0][0]}»: {eq[0][2]:,.0f} min en {eq[0][1]} parada{'s' if eq[0][1] != 1 else ''}.")
    horas = defaultdict(int)
    for f in filas:
        if f["hora"] is not None:
            horas[f["hora"]] += 1
    if horas:
        hp = max(horas, key=lambda h: (horas[h], -h))
        ideas.append(f"Las paradas empiezan con más frecuencia a las {hp:02d}:00 ({horas[hp]} de {n}).")
    if ideas:
        r = r0 + 4 + len(areas)
        _fusionar(ws, r, 15, 25, "Lectura rápida", font=Font(bold=True, size=11, color=_BOSQUE), alinea=Alignment(vertical="center", indent=1))
        for i, texto in enumerate(ideas):
            _fusionar(ws, r + 1 + i, 15, 25, "•  " + texto, fill=_BLANCO_FILL, borde=Border(bottom=_SUAVE), font=Font(size=9, color="333333"),
                      alinea=Alignment(vertical="center", wrap_text=True, indent=1))
            ws.row_dimensions[r + 1 + i].height = 30

    # ---------- las 10 paradas más largas ----------
    r0 = 85
    _fusionar(ws, r0, 2, 25, "Las 10 paradas más largas del período", font=Font(bold=True, size=11, color=_BOSQUE), alinea=Alignment(vertical="center", indent=1))
    tramos = [("#", 2, 2), ("Corrida", 3, 5), ("Inicio", 6, 7), ("Duración", 8, 9), ("Área", 10, 11), ("Tipo", 12, 14), ("Equipo", 15, 17), ("Descripción de la falla", 18, 25)]
    ws.row_dimensions[r0 + 1].height = 22
    for nom, c1, c2 in tramos:
        _fusionar(ws, r0 + 1, c1, c2, nom, fill=_HEAD_FILL, font=_HEAD_FONT, alinea=_CENTRO, borde=_BORDE)
    for i, f in enumerate(sorted(cerradas, key=lambda f: -f["min"])[:10]):
        r = r0 + 2 + i
        relleno = _BLANCO_FILL if i % 2 == 0 else PatternFill("solid", fgColor="F3F8EF")
        valores = [i + 1, f["corrida"], f["inicio"], _fmt_horas(f["min"]), f["area"], f["tipo"], f["equipo"], (f["falla"] or "—").replace("\n", " ")[:90]]
        for (nom, c1, c2), v in zip(tramos, valores):
            c = _fusionar(ws, r, c1, c2, v, fill=relleno, borde=_BORDE, font=Font(size=9),
                          alinea=Alignment(horizontal="center" if nom in ("#", "Duración", "Inicio") else "left", vertical="center", indent=0 if nom in ("#", "Duración", "Inicio") else 1),
                          fmt=_FMT_FECHAHORA if nom == "Inicio" else None)


# ===================================================================== entrada
def generar_paradas_periodo_xlsx(desde, hasta, paradas) -> bytes:
    """desde/hasta: date. paradas: filas (corrida, turno, hora_inicio, hora_fin, min_total, area_proceso, tipo_parada,
    equipo_afectado, descripcion_falla, responsable_solucion, solucion_obs, recomendacion)."""
    filas = _preparar(paradas)
    wb = Workbook()
    ws_tab = wb.active
    ws_tab.title = "Tablero"
    ws_an, tablas = _hoja_analisis(wb, filas, desde, hasta)
    _hoja_datos(wb, filas)
    _hoja_detalle(wb, desde, hasta, paradas)
    _hoja_tablero(ws_tab, filas, desde, hasta, ws_an, tablas)
    wb.move_sheet("Análisis", offset=0)
    wb.active = 0
    wb.properties.title = f"Paradas del {_fmt_fecha_corta(desde)} al {_fmt_fecha_corta(hasta)}"
    wb.properties.creator = "Panel de cuadre de producción"
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
