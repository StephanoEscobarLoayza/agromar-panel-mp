"""Gráficos para los reportes en PDF (corrida y período), dibujados con el canvas de reportlab - sin imágenes ni
dependencias de sistema. Mismas reglas que los Dashboards de la web: marcas finas, extremo redondeado solo del lado del dato,
cuadrícula apenas visible, texto siempre en tinta de texto (nunca en el color de la serie), leyenda cuando hay 2 o más series y
el valor escrito junto a cada marca para que nunca dependa solo del color. Las tablas que siguen a cada gráfico llevan los
mismos números, así que el gráfico es un apoyo visual y no la única fuente.

Colores: los de reporte_base (marca) + las series de la web (--series-N de style.css)."""
import math
from datetime import datetime, timedelta

from reportlab.lib import colors
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import Flowable

from reporte_base import (
    FOREST_2, FOREST_TINT, CITRUS, SURFACE_2, BORDER, TEXT, TEXT_2, TEXT_MUTED, MESES_ES, fmt_minutos,
)

# series de la web (style.css: --series-1 ... --series-6) y un verde claro de la misma familia de la marca
SERIE_AZUL = colors.HexColor("#2a78d6")
SERIE_NARANJA = colors.HexColor("#eb6834")
SERIE_VERDE = colors.HexColor("#1baf7a")
SERIE_AMARILLO = colors.HexColor("#eda100")
SERIE_MORADO = colors.HexColor("#8457d1")
SERIE_ROSA = colors.HexColor("#d4488a")
FOREST_3 = colors.HexColor("#5C9F78")          # verde claro de la marca (p. ej. «Bines» frente a «Silo»)

# mismo color por tipo de proceso que en los Dashboards
COLOR_TIPO = {
    "JSA": SERIE_AZUL, "JCC": SERIE_NARANJA, "JCC TASTE": SERIE_MORADO,
    "JSC": SERIE_VERDE, "ICEGEN": SERIE_AMARILLO, "PRE CC": SERIE_ROSA,
}


def color_tipo(tipo):
    return COLOR_TIPO.get(tipo, TEXT_MUTED)


def recortar(texto, fuente, tam, ancho):
    """Corta el texto con «…» para que entre en `ancho` puntos."""
    texto = str(texto)
    if stringWidth(texto, fuente, tam) <= ancho:
        return texto
    while texto and stringWidth(texto + "…", fuente, tam) > ancho:
        texto = texto[:-1]
    return texto.rstrip() + "…"


def corto_proveedor(nombre):
    """Razón social abreviada para que quepa como etiqueta de un gráfico (la tabla de al lado lleva el nombre completo)."""
    n = " ".join(str(nombre or "—").split())
    for largo, corto in (
        ("SOCIEDAD ANONIMA CERRADA", "S.A.C."), ("SOCIEDAD ANÓNIMA CERRADA", "S.A.C."),
        ("COOPERATIVA AGRARIA INDUSTRIAL", "COOP. AGRARIA INDUSTRIAL"), ("COOPERATIVA AGRARIA", "COOP. AGRARIA"),
        ("SOCIEDAD ANONIMA", "S.A."),
    ):
        n = n.replace(largo, corto)
    return n


def _barra_h(c, x0, y, w, h, color, r=3):
    """Barra horizontal: cuadrada donde nace (la línea base) y redondeada del lado del dato."""
    if w <= 0:
        return
    r = min(r, h / 2, w)
    c.setFillColor(color)
    c.roundRect(x0, y, w, h, r, fill=1, stroke=0)
    c.rect(x0, y, r, h, fill=1, stroke=0)


def _leyenda(c, x, y, entradas, tam=7.5):
    """Leyenda en una fila: cuadrito de color + texto en tinta secundaria. Devuelve el x final."""
    for color, texto, *resto in entradas:
        forma = resto[0] if resto else "cuadro"          # cuadro | punto (círculo lleno) | hueco (círculo vacío)
        c.setStrokeColor(color)
        c.setFillColor(colors.white if forma == "hueco" else color)
        c.setLineWidth(1.5)
        if forma == "cuadro":
            c.roundRect(x, y - 0.4, 7, 7, 1.5, fill=1, stroke=0)
        else:
            c.circle(x + 3.5, y + 2.6, 3.2, fill=1, stroke=1)
        c.setFillColor(TEXT_2)
        c.setFont("Helvetica", tam)
        c.drawString(x + 11, y, texto)
        x += 11 + stringWidth(texto, "Helvetica", tam) + 12
    return x


# ---------------------------------------------------------------------------------------------------------------------
class BarrasH(Flowable):
    """Barras horizontales con etiqueta a la izquierda y el valor (más una nota opcional) escrito a la derecha de cada barra.
    items: [{"etiqueta", "sub" (opcional, línea chica debajo), "valor", "texto" (valor ya formateado), "nota" (opcional),
    "color"}]. leyenda: [(color, texto)] - solo cuando hay 2 o más colores con significado."""

    def __init__(self, ancho, items, ancho_etq=56, maximo=None, leyenda=None, alto_barra=11):
        super().__init__()
        self.items = items
        self.ancho_etq = ancho_etq
        self.maximo = maximo or max((float(i["valor"]) for i in items), default=0) or 1
        self.leyenda = leyenda or []
        self.alto_barra = alto_barra
        # una etiqueta larga sin línea chica se parte en dos líneas (la segunda se recorta con «…»)
        for it in items:
            it["_lineas"] = None
            if not it.get("sub") and stringWidth(it["etiqueta"], "Helvetica-Bold", 8.5) > ancho_etq:
                partes = simpleSplit(it["etiqueta"], "Helvetica-Bold", 8.5, ancho_etq)
                it["_lineas"] = [partes[0], recortar(" ".join(partes[1:]), "Helvetica-Bold", 8.5, ancho_etq)] if len(partes) > 1 else partes
        self.fila = 27 if any(i.get("sub") or i["_lineas"] for i in items) else 19
        self.alto_ley = 17 if self.leyenda else 0
        self.width = ancho
        self.height = self.alto_ley + self.fila * len(items) + 2
        self.ancho_val = max(
            (stringWidth(i.get("texto", ""), "Helvetica-Bold", 8.5)
             + (stringWidth("   " + i["nota"], "Helvetica", 7.5) if i.get("nota") else 0) for i in items),
            default=0,
        ) + 8

    def draw(self):
        c = self.canv
        x0 = self.ancho_etq + 6
        ancho_max = max(self.width - x0 - self.ancho_val, 24)
        y_tabla = self.height - self.alto_ley
        if self.leyenda:
            _leyenda(c, x0, self.height - 10, self.leyenda)
        # línea base del eje de las barras
        c.setStrokeColor(BORDER)
        c.setLineWidth(0.8)
        c.line(x0, y_tabla, x0, 2)
        for i, it in enumerate(self.items):
            yc = y_tabla - self.fila * (i + 0.5)
            # etiqueta (y línea chica) a la izquierda, siempre en tinta de texto
            if it.get("sub"):
                c.setFillColor(TEXT)
                c.setFont("Helvetica-Bold", 8.5)
                c.drawString(0, yc + 1.5, recortar(it["etiqueta"], "Helvetica-Bold", 8.5, self.ancho_etq))
                c.setFillColor(TEXT_2)
                c.setFont("Helvetica", 7)
                c.drawString(0, yc - 8, recortar(it["sub"], "Helvetica", 7, self.ancho_etq))
            elif it["_lineas"] and len(it["_lineas"]) > 1:
                c.setFillColor(TEXT)
                c.setFont("Helvetica-Bold", 8.5)
                c.drawString(0, yc + 1.5, it["_lineas"][0])
                c.drawString(0, yc - 8, it["_lineas"][1])
            else:
                c.setFillColor(TEXT)
                c.setFont("Helvetica-Bold", 8.5)
                c.drawString(0, yc - 3, recortar(it["etiqueta"], "Helvetica-Bold", 8.5, self.ancho_etq))
            w = ancho_max * min(float(it["valor"]) / self.maximo, 1.0)
            _barra_h(c, x0, yc - self.alto_barra / 2, max(w, 2) if float(it["valor"]) > 0 else 0, self.alto_barra, it["color"])
            xv = x0 + (max(w, 2) if float(it["valor"]) > 0 else 0) + 6
            c.setFillColor(TEXT)
            c.setFont("Helvetica-Bold", 8.5)
            c.drawString(xv, yc - 3, it.get("texto", ""))
            if it.get("nota"):
                c.setFillColor(TEXT_2)
                c.setFont("Helvetica", 7.5)
                c.drawString(xv + stringWidth(it.get("texto", ""), "Helvetica-Bold", 8.5) + 6, yc - 3, it["nota"])


class BarraCelda(Flowable):
    """Barra de datos dentro de una celda de tabla: la longitud es proporcional al valor (frente al máximo de la columna) y el
    número va escrito al final de la barra. Así la misma tabla sirve de gráfico y las filas se parten entre páginas sin dejar huecos."""

    def __init__(self, ancho, valor, maximo, color, texto, nota=None, alto=13):
        super().__init__()
        self.valor = float(valor or 0)
        self.maximo = float(maximo or 0) or 1.0
        self.color = color
        self.texto = texto
        self.nota = nota
        self.width = ancho
        self.height = alto

    def draw(self):
        c = self.canv
        ancho_texto = stringWidth(self.texto, "Helvetica", 9) + (stringWidth("  " + self.nota, "Helvetica", 7.5) if self.nota else 0)
        ancho_max = max(self.width - ancho_texto - 8, 10)
        w = ancho_max * min(self.valor / self.maximo, 1.0) if self.valor > 0 else 0
        alto_barra = 8
        _barra_h(c, 0, (self.height - alto_barra) / 2, max(w, 2) if w else 0, alto_barra, self.color, r=2.5)
        c.setFillColor(TEXT)
        c.setFont("Helvetica", 9)
        x = (max(w, 2) if w else 0) + 5
        c.drawString(x, (self.height - 9) / 2 + 1.5, self.texto)
        if self.nota:
            c.setFillColor(TEXT_2)
            c.setFont("Helvetica", 7.5)
            c.drawString(x + stringWidth(self.texto, "Helvetica", 9) + 5, (self.height - 9) / 2 + 1.5, self.nota)


# ---------------------------------------------------------------------------------------------------------------------
class Anillo(Flowable):
    """Un solo dato en grande (p. ej. el rendimiento): aro con la fracción llenada en color y el valor al centro."""

    def __init__(self, diametro, fraccion, valor, etiqueta, color=FOREST_2):
        super().__init__()
        self.width = diametro
        self.height = diametro
        self.fraccion = max(0.0, min(float(fraccion), 1.0))
        self.valor = valor
        self.etiqueta = etiqueta
        self.color = color

    def draw(self):
        c = self.canv
        d = self.width
        cx = cy = d / 2
        r = d / 2 - 1
        grosor = d * 0.15
        c.setFillColor(SURFACE_2)
        c.circle(cx, cy, r, fill=1, stroke=0)
        if self.fraccion > 0:
            c.setFillColor(self.color)
            if self.fraccion >= 0.9995:
                c.circle(cx, cy, r, fill=1, stroke=0)
            else:
                c.wedge(cx - r, cy - r, cx + r, cy + r, 90, -360 * self.fraccion, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.circle(cx, cy, r - grosor, fill=1, stroke=0)
        c.setFillColor(TEXT)
        tam = 19
        while stringWidth(self.valor, "Helvetica-Bold", tam) > (r - grosor) * 1.75 and tam > 9:
            tam -= 1
        c.setFont("Helvetica-Bold", tam)
        c.drawCentredString(cx, cy - 2, self.valor)
        c.setFillColor(TEXT_2)
        c.setFont("Helvetica", 7)
        c.drawCentredString(cx, cy - 12, self.etiqueta.upper())


# ---------------------------------------------------------------------------------------------------------------------
def _paso_agradable(rango, max_marcas):
    """Paso «redondo» (1, 2, 2.5, 5 × 10^k) para que el eje tenga como mucho `max_marcas` marcas."""
    if rango <= 0:
        return 1.0
    crudo = rango / max_marcas
    base = 10 ** math.floor(math.log10(crudo))
    for m in (1, 2, 2.5, 5, 10):
        if m * base >= crudo:
            return m * base
    return 10 * base


class Mancuernas(Flowable):
    """Dos mediciones por fila unidas por una línea (antes → después): sirve para ver de un vistazo cuánto cambió el Brix de cada
    tanque. Como la posición es la que habla (no la longitud de una barra), el eje no necesita partir de cero.
    filas: [{"etiqueta", "sub", "a", "b"}]; nombres: (nombre de a, nombre de b). `a` va como círculo hueco y `b` como círculo sólido."""

    def __init__(self, ancho, filas, nombres=("Brix inicial", "Brix final"), decimales=2, ancho_etq=44, color=FOREST_2):
        super().__init__()
        self.filas = filas
        self.nombres = nombres
        self.dec = decimales
        self.ancho_etq = ancho_etq
        self.color = color
        self.alto_fila = 19
        self.alto_ley = 18
        self.alto_eje = 15
        self.width = ancho
        self.height = self.alto_ley + self.alto_fila * len(filas) + self.alto_eje

    def _escala(self):
        vals = [float(v) for f in self.filas for v in (f.get("a"), f.get("b")) if v is not None]
        lo, hi = min(vals), max(vals)
        rango = max(hi - lo, 0.2)
        paso = _paso_agradable(rango * 1.3, 6)
        lo_n = math.floor((lo - rango * 0.12) / paso) * paso
        hi_n = math.ceil((hi + rango * 0.12) / paso) * paso
        if hi_n - lo_n < paso * 2:
            hi_n = lo_n + paso * 2
        marcas = []
        k = 0
        while lo_n + k * paso <= hi_n + 1e-9:
            marcas.append(round(lo_n + k * paso, 6))
            k += 1
        return lo_n, hi_n, marcas

    def draw(self):
        c = self.canv
        x0 = self.ancho_etq + 8
        x1 = self.width - 16
        lo, hi, marcas = self._escala()

        def px(v):
            return x0 + (float(v) - lo) / (hi - lo) * (x1 - x0)

        y_filas_top = self.height - self.alto_ley
        y_filas_bot = self.alto_eje
        _leyenda(c, x0, self.height - 10, [(self.color, self.nombres[0], "hueco"), (self.color, self.nombres[1], "punto")])
        # cuadrícula vertical (recesiva) y marcas del eje
        c.setLineWidth(0.5)
        dec_eje = 1 if all(abs(m * 10 - round(m * 10)) < 1e-6 for m in marcas) else 2
        for m in marcas:
            c.setStrokeColor(BORDER)
            c.line(px(m), y_filas_top, px(m), y_filas_bot)
            c.setFillColor(TEXT_MUTED)
            c.setFont("Helvetica", 7)
            c.drawCentredString(px(m), 3, f"{m:.{dec_eje}f}")
        for i, f in enumerate(self.filas):
            yc = y_filas_top - self.alto_fila * (i + 0.5)
            c.setFillColor(TEXT)
            c.setFont("Helvetica-Bold", 8.5)
            etq = recortar(f["etiqueta"], "Helvetica-Bold", 8.5, self.ancho_etq)
            c.drawString(0, yc - 3, etq)
            if f.get("sub"):                       # el dato chico (p. ej. los litros) va en la misma línea, en gris
                c.setFillColor(TEXT_2)
                c.setFont("Helvetica", 7)
                c.drawString(stringWidth(etq, "Helvetica-Bold", 8.5) + 5, yc - 2.8, recortar(f["sub"], "Helvetica", 7, self.ancho_etq - stringWidth(etq, "Helvetica-Bold", 8.5) - 5))
            a, b = f.get("a"), f.get("b")
            if a is not None and b is not None:
                c.setStrokeColor(colors.HexColor("#B7BEB2"))
                c.setLineWidth(2)
                c.line(px(a), yc, px(b), yc)
            # puntos con un anillo del color de la superficie para que no se confundan cuando se tocan
            if a is not None:
                c.setFillColor(colors.white)
                c.setStrokeColor(self.color)
                c.setLineWidth(1.6)
                c.circle(px(a), yc, 3.6, fill=1, stroke=1)
            if b is not None:
                c.setStrokeColor(colors.white)
                c.setLineWidth(1.2)
                c.setFillColor(self.color)
                c.circle(px(b), yc, 3.9, fill=1, stroke=1)
            # el número de cada punto, hacia afuera: el menor a la izquierda y el mayor a la derecha
            c.setFillColor(TEXT)
            c.setFont("Helvetica", 7.5)
            if a is not None and b is not None and abs(float(a) - float(b)) > 1e-9:
                menor, mayor = (a, b) if float(a) < float(b) else (b, a)
                c.drawRightString(px(menor) - 7, yc - 2.6, f"{float(menor):.{self.dec}f}")
                c.drawString(px(mayor) + 7, yc - 2.6, f"{float(mayor):.{self.dec}f}")
            else:
                v = b if b is not None else a
                c.drawString(px(v) + 7, yc - 2.6, f"{float(v):.{self.dec}f}")


# ---------------------------------------------------------------------------------------------------------------------
class LineaTiempo(Flowable):
    """Cuándo estuvo parada la planta durante la corrida: una banda verde (en marcha) con cada parada marcada encima en naranja,
    sobre un eje de horas. paradas: [(inicio, fin o None)]; una parada sin fin se dibuja hasta el final de la corrida."""

    def __init__(self, ancho, inicio, fin, paradas):
        super().__init__()
        ahora_del_dato = max([p[1] or p[0] for p in paradas] + [inicio]) if paradas else inicio
        t0 = min([inicio] + [p[0] for p in paradas])
        t1 = max(fin or ahora_del_dato, ahora_del_dato)
        if t1 <= t0:
            t1 = t0 + timedelta(hours=1)
        self.t0, self.t1 = t0, t1
        self.paradas = [(p[0], p[1] or t1) for p in paradas]
        self.width = ancho
        self.alto_ley = 18
        self.alto_banda = 18
        self.alto_eje = 28
        self.height = self.alto_ley + self.alto_banda + self.alto_eje + 4

    def _marcas(self):
        horas = (self.t1 - self.t0).total_seconds() / 3600
        paso = next((p for p in (1, 2, 3, 4, 6, 12, 24, 48, 96) if horas / p <= 8), 96)
        base = self.t0.replace(minute=0, second=0, microsecond=0)
        t = base if base >= self.t0 else base + timedelta(hours=1)
        # alinear al múltiplo del paso contado desde medianoche
        while t.hour % paso if paso <= 24 else False:
            t += timedelta(hours=1)
        marcas = []
        while t <= self.t1:
            marcas.append(t)
            t += timedelta(hours=paso)
        return marcas

    def draw(self):
        c = self.canv
        x0, x1 = 0, self.width
        span = (self.t1 - self.t0).total_seconds()

        def px(t):
            return x0 + (t - self.t0).total_seconds() / span * (x1 - x0)

        y_banda = self.alto_eje + 2
        _leyenda(c, x0, self.height - 11, [(FOREST_2, "Planta en marcha"), (CITRUS, "Parada")])
        c.setFillColor(FOREST_2)
        c.roundRect(x0, y_banda, x1 - x0, self.alto_banda, 3, fill=1, stroke=0)
        for ini, fin in self.paradas:
            xa, xb = px(ini), px(fin)
            w = max(xb - xa, 1.6)
            c.setFillColor(CITRUS)
            c.rect(xa, y_banda, w, self.alto_banda, fill=1, stroke=0)
            c.setStrokeColor(colors.white)
            c.setLineWidth(0.8)
            c.line(xa, y_banda, xa, y_banda + self.alto_banda)
            c.line(xa + w, y_banda, xa + w, y_banda + self.alto_banda)
            texto = fmt_minutos((fin - ini).total_seconds() / 60)
            if w >= stringWidth(texto, "Helvetica-Bold", 7) + 6:
                c.setFillColor(TEXT)
                c.setFont("Helvetica-Bold", 7)
                c.drawCentredString(xa + w / 2, y_banda + self.alto_banda / 2 - 2.4, texto)
        # eje de horas
        c.setStrokeColor(BORDER)
        c.setLineWidth(0.8)
        c.line(x0, y_banda - 3, x1, y_banda - 3)
        c.setFont("Helvetica", 7)
        marcas = self._marcas()
        for t in marcas:
            x = px(t)
            c.setStrokeColor(BORDER)
            c.line(x, y_banda - 3, x, y_banda - 6)
            c.setFillColor(TEXT_2)
            c.drawCentredString(x, y_banda - 15, t.strftime("%H:%M"))
            if t.hour == 0 or t == marcas[0]:
                mes = MESES_ES.get(t.strftime("%b"), t.strftime("%b"))
                c.setFillColor(TEXT_MUTED)
                c.drawCentredString(x, y_banda - 24, f"{t.strftime('%d')}-{mes}")


# ---------------------------------------------------------------------------------------------------------------------
class ColumnasApiladas(Flowable):
    """Columnas verticales (una por día o semana), apiladas por serie con 1 punto de separación entre segmentos. Con una sola serie
    es un gráfico de columnas simple. etiquetas: [(texto, sub o None)]; series: [(nombre, color)]; valores[i][j] = valor de la
    serie j en la columna i."""

    def __init__(self, ancho, alto, etiquetas, series, valores, unidad_eje="kg"):
        super().__init__()
        self.etiquetas = etiquetas
        self.series = series
        self.valores = valores
        self.unidad = unidad_eje
        self.width = ancho
        self.alto_ley = 17 if len(series) >= 2 else 0
        self.height = alto + self.alto_ley
        self.alto_plot = alto - 30

    def _fmt_eje(self, v):
        if self.unidad == "min":
            return fmt_minutos(v)
        return f"{v / 1000:,.0f} k" if v >= 10000 else f"{v:,.0f}"

    def draw(self):
        c = self.canv
        n = len(self.etiquetas)
        totales = [sum(f) for f in self.valores]
        maximo = max(totales) if totales else 0
        paso = _paso_agradable(maximo or 1, 4)
        tope = max(math.ceil((maximo or 1) / paso) * paso, paso)
        x0, x1 = 34, self.width - 4
        y0 = 30
        y1 = y0 + self.alto_plot - 10

        def py(v):
            return y0 + v / tope * (y1 - y0)

        if self.alto_ley:
            _leyenda(c, x0, self.height - 10, [(col, nom) for nom, col in self.series])
        c.setLineWidth(0.5)
        v = 0
        while v <= tope + 1e-9:
            c.setStrokeColor(BORDER)
            c.line(x0, py(v), x1, py(v))
            c.setFillColor(TEXT_MUTED)
            c.setFont("Helvetica", 7)
            c.drawRightString(x0 - 5, py(v) - 2.5, self._fmt_eje(v))
            v += paso
        celda = (x1 - x0) / max(n, 1)
        ancho_col = min(18, celda * 0.62)
        for i, fila in enumerate(self.valores):
            xc = x0 + celda * (i + 0.5)
            base = 0.0
            for j, val in enumerate(fila):
                if val <= 0:
                    continue
                y_a, y_b = py(base), py(base + val)
                alto = y_b - y_a
                c.setFillColor(self.series[j][1])
                c.rect(xc - ancho_col / 2, y_a + (0.5 if base > 0 else 0), ancho_col, max(alto - (0.5 if base > 0 else 0) - 0.5, 0.6), fill=1, stroke=0)
                base += val
            # solo se escribe el total en la columna más alta (o en todas si son pocas): nunca un número en cada marca
            if totales[i] > 0 and (n <= 10 or totales[i] == maximo):
                c.setFillColor(TEXT)
                c.setFont("Helvetica-Bold", 7)
                c.drawCentredString(xc, py(totales[i]) + 3, self._fmt_eje(totales[i]))
            texto, sub = self.etiquetas[i]
            c.setFillColor(TEXT_2)
            c.setFont("Helvetica", 7)
            if n <= 16 or i % max(1, round(n / 16)) == 0:
                c.drawCentredString(xc, y0 - 11, texto)
            if sub:
                c.setFillColor(TEXT_MUTED)
                c.drawCentredString(xc, y0 - 20, sub)
        c.setStrokeColor(colors.HexColor("#B7BEB2"))
        c.setLineWidth(0.8)
        c.line(x0, y0, x1, y0)
