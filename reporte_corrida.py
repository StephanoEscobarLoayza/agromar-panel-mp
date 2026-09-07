"""Genera el PDF de cuadre de una corrida (botón "Reporte PDF" en Corridas).

Usa reportlab (puro Python, sin dependencias de sistema - a diferencia de
WeasyPrint, esto instala y corre sin problema en Render). La paleta de
colores es la misma que la del panel web (ver web/style.css :root) para que
el PDF se sienta parte de la misma app, no un documento aparte.
"""
from datetime import datetime
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable, Flowable, PageBreak,
)

# ---------------------------------------------------------------------------
# paleta - copiada 1:1 de las variables --root de web/style.css
# ---------------------------------------------------------------------------
FOREST = colors.HexColor("#123524")
FOREST_2 = colors.HexColor("#1E6B45")
FOREST_TINT = colors.HexColor("#E7EFE9")
CITRUS = colors.HexColor("#E8890C")
CITRUS_TINT = colors.HexColor("#FBE9CE")
BG = colors.HexColor("#FBF7EE")
SURFACE_2 = colors.HexColor("#F3EDE0")
BORDER = colors.HexColor("#E4DBC7")
TEXT = colors.HexColor("#1B231D")
TEXT_2 = colors.HexColor("#5B6459")
TEXT_MUTED = colors.HexColor("#8B9186")
OK = colors.HexColor("#2F9E5B")
OK_BG = colors.HexColor("#E4F3EA")
WARN = colors.HexColor("#C77A0E")
WARN_BG = colors.HexColor("#FBEBD6")
BAD = colors.HexColor("#C7433B")
BAD_BG = colors.HexColor("#FBE4E2")

PAGE_W, PAGE_H = A4
MARGIN = 16 * mm

styles = getSampleStyleSheet()
style_h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontName="Helvetica-Bold", fontSize=20, textColor=colors.white, spaceAfter=2)
style_eyebrow = ParagraphStyle("eyebrow", parent=styles["Normal"], fontName="Helvetica-Oblique", fontSize=9.5, textColor=CITRUS_TINT, spaceAfter=4)
style_sub = ParagraphStyle("sub", parent=styles["Normal"], fontName="Helvetica", fontSize=10, textColor=colors.white)
style_section = ParagraphStyle("section", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=13, textColor=TEXT, spaceBefore=14, spaceAfter=6)
style_kpi_label = ParagraphStyle("kpiLabel", parent=styles["Normal"], fontName="Helvetica", fontSize=8.5, textColor=TEXT_2)
style_kpi_value = ParagraphStyle("kpiValue", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=17, textColor=TEXT, leading=20)
style_cell = ParagraphStyle("cell", parent=styles["Normal"], fontName="Helvetica", fontSize=9, textColor=TEXT)
style_cell_head = ParagraphStyle("cellHead", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=8.5, textColor=TEXT_2)
style_footnote = ParagraphStyle("footnote", parent=styles["Normal"], fontName="Helvetica-Oblique", fontSize=8, textColor=TEXT_MUTED)


def fmt_kg(n):
    if n is None:
        return "—"
    return f"{n:,.2f}"


def fmt_num(n, dec=2):
    if n is None:
        return "—"
    return f"{n:,.{dec}f}"


MESES_ES = {
    "Jan": "ene", "Feb": "feb", "Mar": "mar", "Apr": "abr", "May": "may", "Jun": "jun",
    "Jul": "jul", "Aug": "ago", "Sep": "set", "Oct": "oct", "Nov": "nov", "Dec": "dic",
}


def _parse_dt(dt):
    if isinstance(dt, str):
        try:
            return datetime.fromisoformat(dt)
        except ValueError:
            return None
    return dt


def fmt_fecha(dt):
    dt = _parse_dt(dt)
    if dt is None:
        return "—"
    mes = MESES_ES.get(dt.strftime("%b"), dt.strftime("%b"))
    return f"{dt.strftime('%d')}-{mes} {dt.strftime('%Y')}"


def fmt_hora(dt):
    dt = _parse_dt(dt)
    if dt is None:
        return "—"
    mes = MESES_ES.get(dt.strftime("%b"), dt.strftime("%b"))
    return f"{dt.strftime('%d')}-{mes} {dt.strftime('%H:%M')}"


def fmt_minutos(m):
    if m is None:
        return "—"
    h, mi = divmod(int(round(m)), 60)
    return f"{h}h {mi}m" if h else f"{mi}m"


class Banda(Flowable):
    """Barra de color simple con una etiqueta - usada para el mini-grafico
    Silo vs Bines del dashboard (misma idea que un bar chart horizontal,
    con las dos series en orden fijo forest/citrus, nunca un color al azar)."""

    def __init__(self, ancho_total, valor, maximo, color, etiqueta, alto=16):
        super().__init__()
        self.ancho_total = ancho_total
        self.valor = valor
        self.maximo = max(maximo, 0.0001)
        self.color = color
        self.etiqueta = etiqueta
        self.alto = alto
        self.width = ancho_total
        self.height = alto + 4

    def draw(self):
        c = self.canv
        w = self.ancho_total * min(self.valor / self.maximo, 1.0)
        c.setFillColor(SURFACE_2)
        c.roundRect(0, 2, self.ancho_total, self.alto, 3, fill=1, stroke=0)
        c.setFillColor(self.color)
        c.roundRect(0, 2, max(w, 4), self.alto, 3, fill=1, stroke=0)
        c.setFillColor(TEXT)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(6, 2 + self.alto / 2 - 3.5, self.etiqueta)


def _header_footer(canvas_obj, doc, nombre_corrida):
    canvas_obj.saveState()
    # banda verde del header (solo en la primera pagina la dibuja SimpleDocTemplate
    # via el flowable de arriba - aqui solo va el pie de pagina, comun a todas)
    canvas_obj.setStrokeColor(BORDER)
    canvas_obj.setLineWidth(0.5)
    canvas_obj.line(MARGIN, 14 * mm, PAGE_W - MARGIN, 14 * mm)
    canvas_obj.setFont("Helvetica", 8)
    canvas_obj.setFillColor(TEXT_MUTED)
    canvas_obj.drawString(MARGIN, 10 * mm, "Del campo a planta, cuadrado al kilo. · Agromar Industrial")
    canvas_obj.drawRightString(PAGE_W - MARGIN, 10 * mm, f"Página {doc.page}")
    canvas_obj.drawCentredString(PAGE_W / 2, 10 * mm, nombre_corrida)
    canvas_obj.restoreState()


def _kpi_card(label, valor, ancho, color_valor=TEXT, badge=None):
    contenido = [[Paragraph(label, style_kpi_label)],
                 [Paragraph(f'<font color="#{color_valor.hexval()[2:]}">{valor}</font>', style_kpi_value)]]
    if badge:
        contenido.append([badge])
    t = Table(contenido, colWidths=[ancho], cornerRadii=[6, 6, 6, 6])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("BOX", (0, 0), (-1, -1), 0.75, BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
    ]))
    return t


def _badge(texto, fg, bg):
    p = Paragraph(f'<font color="#{fg.hexval()[2:]}"><b>{texto}</b></font>', ParagraphStyle(
        "badge", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=9, textColor=fg,
    ))
    t = Table([[p]], colWidths=[None], cornerRadii=[6, 6, 6, 6])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
    ]))
    return t


ESTADO_CUADRE_INFO = {
    "cuadra": ("✓ CUADRA", OK, OK_BG),
    "incompleto": ("△ INCOMPLETO", WARN, WARN_BG),
    "excedido": ("✕ EXCEDIDO", BAD, BAD_BG),
    "sin_objetivo": ("SIN OBJETIVO MP", TEXT_2, SURFACE_2),
}


def _tabla_lotes(lotes):
    data = [[Paragraph(h, style_cell_head) for h in ["Lote", "Proveedor", "Almacén", "Kg usado", "Brix", "Acidez", "Ratio"]]]
    for l in lotes:
        data.append([
            Paragraph(f"#{l['lote_numero']}", style_cell),
            Paragraph(l.get("proveedor") or "—", style_cell),
            Paragraph("Bines" if l.get("tipo_almacen_origen") == "BINES" else "Silo" if l.get("tipo_almacen_origen") == "SILO" else "—", style_cell),
            Paragraph(fmt_kg(l["kg_asignados"]), style_cell),
            Paragraph(fmt_num(l.get("brix_recepcion"), 2), style_cell),
            Paragraph(fmt_num(l.get("acidez"), 2), style_cell),
            Paragraph(fmt_num(l.get("ratio"), 2), style_cell),
        ])
    t = Table(data, colWidths=[22 * mm, 55 * mm, 20 * mm, 26 * mm, 16 * mm, 18 * mm, 16 * mm], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), FOREST_TINT),
        ("LINEBELOW", (0, 0), (-1, 0), 0.75, FOREST_2),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, SURFACE_2]),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
    ]))
    return t


def _tabla_productos(productos):
    data = [[Paragraph(h, style_cell_head) for h in ["Producto", "Tambores", "Peso/tambor", "PT kg", "Litros"]]]
    for p in productos:
        data.append([
            Paragraph(p.get("producto") or "—", style_cell),
            Paragraph(fmt_num(p.get("tambores"), 0), style_cell),
            Paragraph(fmt_kg(p.get("peso_neto_tambor_kg")), style_cell),
            Paragraph(fmt_kg(p.get("pt_kg")), style_cell),
            Paragraph(fmt_kg(p.get("volumen_litros")), style_cell),
        ])
    t = Table(data, colWidths=[45 * mm, 25 * mm, 30 * mm, 30 * mm, 30 * mm], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), FOREST_TINT),
        ("LINEBELOW", (0, 0), (-1, 0), 0.75, FOREST_2),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, SURFACE_2]),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
    ]))
    return t


def _tabla_paradas(paradas):
    data = [[Paragraph(h, style_cell_head) for h in ["Motivo", "Inicio", "Fin", "Duración"]]]
    for p in paradas:
        data.append([
            Paragraph(p.get("motivo") or "—", style_cell),
            Paragraph(fmt_hora(p.get("hora_inicio")), style_cell),
            Paragraph(fmt_hora(p.get("hora_fin")) if p.get("hora_fin") else "en curso", style_cell),
            Paragraph(fmt_minutos(p.get("duracion_minutos")), style_cell),
        ])
    t = Table(data, colWidths=[70 * mm, 35 * mm, 35 * mm, 20 * mm], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), FOREST_TINT),
        ("LINEBELOW", (0, 0), (-1, 0), 0.75, FOREST_2),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, SURFACE_2]),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
    ]))
    return t


def generar_reporte_pdf(corrida: dict, lotes: list, productos: list, paradas: list) -> bytes:
    """corrida: fila de v_cuadre_corridas (+ nombre/fechas/tipo_proceso).
    lotes: filas de asignaciones + join a lotes (numero, proveedor, tipo_almacen_origen, kg_asignados, brix_recepcion, acidez, ratio).
    productos: filas de corrida_productos.
    paradas: filas de paradas (+ duracion_minutos)."""
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN, bottomMargin=20 * mm,
        title=f"Cuadre - {corrida['nombre']}",
    )

    kg_total = float(corrida.get("kg_asignados_total") or 0)
    kg_objetivo = corrida.get("mp_kg_objetivo")
    kg_silo = sum(float(l["kg_asignados"]) for l in lotes if l.get("tipo_almacen_origen") == "SILO")
    kg_bines = sum(float(l["kg_asignados"]) for l in lotes if l.get("tipo_almacen_origen") == "BINES")

    brix_pond = sum(float(l["kg_asignados"]) * float(l["brix_recepcion"]) for l in lotes if l.get("brix_recepcion") is not None)
    acidez_pond = sum(float(l["kg_asignados"]) * float(l["acidez"]) for l in lotes if l.get("acidez") is not None)
    kg_con_calidad = sum(float(l["kg_asignados"]) for l in lotes if l.get("brix_recepcion") is not None)
    brix_prom = brix_pond / kg_con_calidad if kg_con_calidad > 0 else None
    ratio_prom = brix_pond / acidez_pond if acidez_pond > 0 else None

    pt_total = sum(float(p["pt_kg"]) for p in productos if p.get("pt_kg") is not None)
    litros_total = sum(float(p["volumen_litros"]) for p in productos if p.get("volumen_litros") is not None)
    rendimiento = pt_total / kg_total if kg_total > 0 and pt_total > 0 else None

    paradas_cerradas = [p for p in paradas if p.get("duracion_minutos") is not None]
    min_parado = sum(p["duracion_minutos"] for p in paradas_cerradas)
    hay_en_curso = any(p.get("duracion_minutos") is None for p in paradas)

    estado = corrida.get("estado_cuadre") or "sin_objetivo"
    badge_txt, badge_fg, badge_bg = ESTADO_CUADRE_INFO.get(estado, ESTADO_CUADRE_INFO["sin_objetivo"])

    story = []

    # ---------------- encabezado (banda verde) ----------------
    header_tbl = Table(
        [[Paragraph("agro<font color='#E8890C'>·</font>mar", ParagraphStyle("wordmark", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=15, textColor=colors.white))],
         [Paragraph("Panel de cuadre — planta de jugos", style_eyebrow)],
         [Paragraph("Cuadre de corrida", style_h1)],
         [Paragraph(f"{corrida['nombre']} · {fmt_fecha(corrida.get('fecha_inicio'))}" + (f" — {fmt_fecha(corrida.get('fecha_final'))}" if corrida.get("fecha_final") else " · en curso"), style_sub)]],
        colWidths=[PAGE_W - 2 * MARGIN],
    )
    header_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), FOREST),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 0),
        ("LEFTPADDING", (0, 0), (-1, -1), 16),
        ("RIGHTPADDING", (0, 0), (-1, -1), 16),
        ("TOPPADDING", (0, 2), (-1, 2), 6),
        ("BOTTOMPADDING", (0, 3), (-1, 3), 14),
    ]))
    story.append(header_tbl)
    story.append(Spacer(1, 10 * mm))

    # ---------------- badge de estado ----------------
    story.append(_badge(badge_txt, badge_fg, badge_bg))
    story.append(Spacer(1, 6 * mm))

    # ---------------- KPIs ----------------
    story.append(Paragraph("Resumen", style_section))
    ancho_kpi = (PAGE_W - 2 * MARGIN - 2 * 6) / 3
    fila1 = [
        _kpi_card("MP consumida", f"{fmt_kg(kg_total)} kg", ancho_kpi),
        _kpi_card("MP objetivo (Trazabilidad)", f"{fmt_kg(float(kg_objetivo))} kg" if kg_objetivo else "—", ancho_kpi),
        _kpi_card("Diferencia", f"{fmt_kg(float(kg_objetivo) - kg_total)} kg" if kg_objetivo else "—", ancho_kpi,
                  color_valor=(OK if estado == "cuadra" else WARN if estado == "incompleto" else BAD if estado == "excedido" else TEXT)),
    ]
    fila2 = [
        _kpi_card("Brix ponderado", fmt_num(brix_prom, 2), ancho_kpi),
        _kpi_card("Ratio ponderado", fmt_num(ratio_prom, 2), ancho_kpi),
        _kpi_card("Rendimiento", f"{rendimiento * 100:.1f} %" if rendimiento else "—", ancho_kpi),
    ]
    fila3 = [
        _kpi_card("Producto terminado", f"{fmt_kg(pt_total)} kg" if pt_total else "—", ancho_kpi),
        _kpi_card("Volumen", f"{fmt_kg(litros_total)} L" if litros_total else "—", ancho_kpi),
        _kpi_card("Tiempo parado", (fmt_minutos(min_parado) + (" · hay una en curso" if hay_en_curso else "")) if paradas else "sin paradas", ancho_kpi,
                  color_valor=(BAD if min_parado > 60 else TEXT)),
    ]
    kpi_grid = Table([fila1, fila2, fila3], colWidths=[ancho_kpi] * 3, spaceBefore=4)
    kpi_grid.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(kpi_grid)
    story.append(Spacer(1, 6 * mm))

    # ---------------- mini dashboard: Silo vs Bines ----------------
    if kg_silo > 0 or kg_bines > 0:
        story.append(Paragraph("MP por almacén de origen", style_section))
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

    story.append(HRFlowable(width="100%", thickness=0.5, color=BORDER, spaceBefore=4, spaceAfter=10))

    # ---------------- detalle: lotes ----------------
    story.append(Paragraph(f"Lotes de MP consumidos ({len(lotes)})", style_section))
    if lotes:
        story.append(_tabla_lotes(lotes))
    else:
        story.append(Paragraph("Sin lotes registrados todavía.", style_footnote))

    # ---------------- detalle: productos ----------------
    if productos:
        story.append(Spacer(1, 8 * mm))
        story.append(Paragraph(f"Productos de salida ({len(productos)})", style_section))
        story.append(_tabla_productos(productos))

    # ---------------- detalle: paradas ----------------
    if paradas:
        story.append(Spacer(1, 8 * mm))
        story.append(Paragraph(f"Paradas registradas ({len(paradas)})", style_section))
        story.append(_tabla_paradas(paradas))

    story.append(Spacer(1, 10 * mm))
    story.append(Paragraph(f"Generado el {datetime.now().strftime('%d/%m/%Y %H:%M')} desde el Panel de cuadre de producción.", style_footnote))

    doc.build(
        story,
        onFirstPage=lambda c, d: _header_footer(c, d, corrida["nombre"]),
        onLaterPages=lambda c, d: _header_footer(c, d, corrida["nombre"]),
    )
    return buf.getvalue()
