"""Piezas compartidas entre los distintos reportes en PDF (reporte_corrida.py,
reporte_stock.py, y los que se agreguen después) - paleta, estilos de texto,
tarjetas KPI, badges y el mini-gráfico de barras. Todo con reportlab (puro
Python, sin dependencias de sistema como WeasyPrint - instala y corre sin
problema en Render)."""
from datetime import datetime, timedelta, timezone
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Flowable, HRFlowable

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
style_section = ParagraphStyle("section", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=13, textColor=TEXT, spaceBefore=16, spaceAfter=3)
style_kpi_label = ParagraphStyle("kpiLabel", parent=styles["Normal"], fontName="Helvetica", fontSize=8.5, textColor=TEXT_2)
style_kpi_value = ParagraphStyle("kpiValue", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=17, textColor=TEXT, leading=20)
style_cell = ParagraphStyle("cell", parent=styles["Normal"], fontName="Helvetica", fontSize=9, textColor=TEXT)
style_cell_head = ParagraphStyle("cellHead", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=8.5, textColor=TEXT_2)
style_footnote = ParagraphStyle("footnote", parent=styles["Normal"], fontName="Helvetica-Oblique", fontSize=8, textColor=TEXT_MUTED)


def fmt_kg(n):
    """Los kg casi nunca traen decimales que de verdad importen (y cuando
    sí los traen, es la báscula, no un redondeo) - mostrar ".00" en un
    número que es exacto solo confunde, como si estuviera aproximado.
    Se muestra el decimal SOLO si el número realmente lo tiene."""
    if n is None:
        return "—"
    n = float(n)
    if n == int(n):
        return f"{int(n):,}"
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


def ahora_peru():
    """Hora local de la planta (Perú, UTC-5, sin horario de verano) para los
    sellos "Generado el ..." de los reportes. En Render el servidor corre en
    UTC, así que datetime.now() a secas quedaba ~5 horas adelantado frente al
    reloj de Stephano - mismo gotcha ya corregido en paradas y en el front."""
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-5)))


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
    """El "Generado el ..." vive AQUÍ (se dibuja en el margen de cada página,
    fuera del frame de contenido) y no como Paragraph al final del story -
    antes, si la última tabla llegaba casi hasta el borde inferior, ese
    Paragraph solo no encontraba espacio y se empujaba solo a una página
    nueva, casi en blanco. Al vivir en el pie fijo nunca puede desbordar."""
    canvas_obj.saveState()
    canvas_obj.setStrokeColor(BORDER)
    canvas_obj.setLineWidth(0.5)
    canvas_obj.line(MARGIN, 14 * mm, PAGE_W - MARGIN, 14 * mm)
    canvas_obj.setFont("Helvetica", 8)
    canvas_obj.setFillColor(TEXT_MUTED)
    canvas_obj.drawString(MARGIN, 10 * mm, "Del campo a planta, cuadrado al kilo. · Agromar Industrial")
    canvas_obj.drawRightString(PAGE_W - MARGIN, 10 * mm, f"Generado {ahora_peru().strftime('%d/%m/%Y %H:%M')} · Página {doc.page}")
    canvas_obj.drawCentredString(PAGE_W / 2, 10 * mm, texto_pie)
    canvas_obj.restoreState()


def kpi_card(label, valor, ancho, color_valor=TEXT):
    """Ficha de dato tipo hoja técnica: sin caja completa (eso se ve más
    "widget web" que reporte impreso) - una línea de acento arriba (del
    mismo color que el valor, si el valor tiene un color semántico; forest
    neutro si no) y una línea delgada abajo, con la etiqueta en mayúsculas
    y algo de tracking."""
    acento = color_valor if color_valor is not TEXT else FOREST_2
    contenido = [[Paragraph(label.upper(), style_kpi_label)],
                 [Paragraph(f'<font color="#{color_valor.hexval()[2:]}">{valor}</font>', style_kpi_value)]]
    t = Table(contenido, colWidths=[ancho])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("LINEABOVE", (0, 0), (-1, 0), 2, acento),
        ("LINEBELOW", (0, -1), (-1, -1), 0.5, BORDER),
        ("TOPPADDING", (0, 0), (-1, 0), 9),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 3),
        ("TOPPADDING", (0, 1), (-1, 1), 0),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
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


def _tracked(c, x, y, text, font, size, tracking=1.0, color=None):
    """Dibuja texto con tracking (espaciado entre letras) manual - reportlab
    no lo soporta de forma nativa. Da el aire de "sello"/ficha técnica que
    tienen las etiquetas pequeñas en mayúscula de un reporte de ingeniería."""
    if color is not None:
        c.setFillColor(color)
    c.setFont(font, size)
    cx = x
    for ch in text:
        c.drawString(cx, y, ch)
        cx += c.stringWidth(ch, font, size) + tracking
    return cx


class Encabezado(Flowable):
    """Letterhead del reporte: banda forest de fondo con dos cuñas
    diagonales (forest claro + citrus) en la esquina superior derecha a modo
    de acento geométrico, wordmark, tipo de documento como eyebrow (con
    tracking, como un sello), el título real del documento en grande, y una
    fila inferior con el periodo a la izquierda y un chip de estado a la
    derecha (si aplica). Se dibuja directo con el canvas (no con una Table)
    para tener control fino sobre la geometría."""

    def __init__(self, ancho, tipo_doc, titulo, periodo, chip=None, alto=40 * mm):
        super().__init__()
        self.width = ancho
        self.height = alto
        self.tipo_doc = tipo_doc
        self.titulo = titulo
        self.periodo = periodo
        self.chip = chip

    def draw(self):
        c = self.canv
        w, h = self.width, self.height
        pad = 16
        c.saveState()

        c.setFillColor(FOREST)
        c.rect(0, 0, w, h, fill=1, stroke=0)

        # cuñas diagonales decorativas, esquina superior derecha
        c.setFillColor(FOREST_2)
        p = c.beginPath()
        p.moveTo(w * 0.66, h); p.lineTo(w, h); p.lineTo(w, h * 0.32); p.close()
        c.drawPath(p, fill=1, stroke=0)
        c.setFillColor(CITRUS)
        p2 = c.beginPath()
        p2.moveTo(w * 0.84, h); p2.lineTo(w, h); p2.lineTo(w, h * 0.6); p2.close()
        c.drawPath(p2, fill=1, stroke=0)

        # wordmark
        c.setFont("Helvetica-Bold", 13)
        c.setFillColor(colors.white)
        c.drawString(pad, h - 20, "agro")
        wm = c.stringWidth("agro", "Helvetica-Bold", 13)
        c.setFillColor(CITRUS)
        c.drawString(pad + wm, h - 20, "·")
        dot = c.stringWidth("·", "Helvetica-Bold", 13)
        c.setFillColor(colors.white)
        c.drawString(pad + wm + dot, h - 20, "mar")

        # tipo de documento - eyebrow con tracking, como un sello
        _tracked(c, pad, h - 33, self.tipo_doc.upper(), "Helvetica-Bold", 8, tracking=1.1, color=CITRUS_TINT)

        # título real del documento (se achica solo si no entra)
        size_titulo = 22
        while c.stringWidth(self.titulo, "Helvetica-Bold", size_titulo) > (w - 2 * pad - 40) and size_titulo > 13:
            size_titulo -= 1
        c.setFont("Helvetica-Bold", size_titulo)
        c.setFillColor(colors.white)
        c.drawString(pad, h - 60, self.titulo)

        # fila inferior: periodo (izq) + chip de estado (der)
        y_periodo = 14
        c.setFont("Helvetica", 10)
        c.setFillColor(colors.white)
        c.drawString(pad, y_periodo, self.periodo)

        if self.chip:
            texto, fg, bg = self.chip
            c.setFont("Helvetica-Bold", 9)
            tw = c.stringWidth(texto, "Helvetica-Bold", 9)
            chip_w, chip_h = tw + 20, 16
            chip_x, chip_y = w - pad - chip_w, y_periodo - 3
            c.setFillColor(bg)
            c.roundRect(chip_x, chip_y, chip_w, chip_h, 4, fill=1, stroke=0)
            c.setFillColor(fg)
            c.drawCentredString(chip_x + chip_w / 2, chip_y + 5, texto)

        c.restoreState()


def encabezado(tipo_doc, titulo, periodo, chip=None):
    """tipo_doc: eyebrow pequeño (ej. "Cuadre de corrida"). titulo: el
    nombre real del documento (ej. la corrida) - es el H1. periodo: fecha o
    rango debajo. chip: tupla (texto, fg, bg) opcional, ej. el estado de
    cuadre, se dibuja como chip a la derecha del periodo."""
    return Encabezado(PAGE_W - 2 * MARGIN, tipo_doc, titulo, periodo, chip)


def seccion(titulo):
    """Encabezado de sección: título en negrita + una doble regla delgada
    debajo (forest + un tramo corto citrus), en vez de solo texto en negrita
    - separa las secciones sin necesitar otra tarjeta o caja."""
    return [
        Paragraph(titulo, style_section),
        HRFlowable(width="100%", thickness=1.3, color=FOREST_2, spaceBefore=1, spaceAfter=1),
        HRFlowable(width="26%", thickness=1.3, color=CITRUS, spaceBefore=0, spaceAfter=8, hAlign="LEFT"),
    ]


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
