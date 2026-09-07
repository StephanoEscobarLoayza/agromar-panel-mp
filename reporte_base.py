"""Piezas compartidas entre los distintos reportes en PDF (reporte_corrida.py,
reporte_stock.py, y los que se agreguen después) - paleta, estilos de texto,
tarjetas KPI, badges y el mini-gráfico de barras. Todo con reportlab (puro
Python, sin dependencias de sistema como WeasyPrint - instala y corre sin
problema en Render)."""
from datetime import datetime
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Flowable

# ---------------------------------------------------------------------------
# paleta - copiada 1:1 de las variables --root de web/style.css, para que el
# PDF se sienta parte de la misma app, no un documento aparte.
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
    """Barra de color simple con una etiqueta - usada para mini-graficos tipo
    Silo vs Bines (misma idea que un bar chart horizontal, con las series en
    orden fijo forest/citrus, nunca un color al azar)."""

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


def header_footer(canvas_obj, doc, texto_pie):
    canvas_obj.saveState()
    canvas_obj.setStrokeColor(BORDER)
    canvas_obj.setLineWidth(0.5)
    canvas_obj.line(MARGIN, 14 * mm, PAGE_W - MARGIN, 14 * mm)
    canvas_obj.setFont("Helvetica", 8)
    canvas_obj.setFillColor(TEXT_MUTED)
    canvas_obj.drawString(MARGIN, 10 * mm, "Del campo a planta, cuadrado al kilo. · Agromar Industrial")
    canvas_obj.drawRightString(PAGE_W - MARGIN, 10 * mm, f"Página {doc.page}")
    canvas_obj.drawCentredString(PAGE_W / 2, 10 * mm, texto_pie)
    canvas_obj.restoreState()


def kpi_card(label, valor, ancho, color_valor=TEXT):
    contenido = [[Paragraph(label, style_kpi_label)],
                 [Paragraph(f'<font color="#{color_valor.hexval()[2:]}">{valor}</font>', style_kpi_value)]]
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


def badge(texto, fg, bg):
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


def encabezado(titulo, subtitulo):
    """Banda verde del header - misma para todos los reportes, solo cambia
    el titulo (H1) y la linea de subtitulo debajo del wordmark."""
    header_tbl = Table(
        [[Paragraph("agro<font color='#E8890C'>·</font>mar", ParagraphStyle("wordmark", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=15, textColor=colors.white))],
         [Paragraph("Panel de cuadre — planta de jugos", style_eyebrow)],
         [Paragraph(titulo, style_h1)],
         [Paragraph(subtitulo, style_sub)]],
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
    return header_tbl


def tabla(headers, filas, col_widths, align_derecha_desde=None):
    """Tabla generica con el mismo look en todos los reportes: header verde
    claro, filas alternadas, linea inferior sutil. `filas` ya viene como
    lista de listas de Paragraph (o texto plano, se envuelve solo)."""
    data = [[Paragraph(h, style_cell_head) for h in headers]]
    for fila in filas:
        data.append([c if isinstance(c, Paragraph) else Paragraph(str(c), style_cell) for c in fila])
    t = Table(data, colWidths=col_widths, repeatRows=1)
    estilo = [
        ("BACKGROUND", (0, 0), (-1, 0), FOREST_TINT),
        ("LINEBELOW", (0, 0), (-1, 0), 0.75, FOREST_2),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, SURFACE_2]),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
    ]
    if align_derecha_desde is not None:
        estilo.append(("ALIGN", (align_derecha_desde, 0), (-1, -1), "RIGHT"))
    t.setStyle(TableStyle(estilo))
    return t


def nuevo_doc(buf, titulo_documento):
    return SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN, bottomMargin=20 * mm,
        title=titulo_documento,
    )
