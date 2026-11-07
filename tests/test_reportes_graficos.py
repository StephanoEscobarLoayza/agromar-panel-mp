"""Pruebas de los gráficos de los PDF (corrida y período). Sin base de datos: los reportes reciben listas de diccionarios."""
import os
import sys
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
os.environ.setdefault("DATABASE_URL", "postgresql://usuario:clave@localhost:5432/prueba")

import reporte_graficos as g  # noqa: E402
from reporte_base import seccion, tabla  # noqa: E402
from reporte_corrida import generar_reporte_pdf  # noqa: E402
from reporte_periodo import generar_reporte_periodo_pdf, _columnas_de_tiempo  # noqa: E402
from reportlab.platypus import CondPageBreak  # noqa: E402


def _paginas(pdf: bytes) -> int:
    import re
    return len(re.findall(rb"/Type\s*/Page\b(?!s)", pdf))


def _corrida(**extra):
    base = {"nombre": "05-Noviembre ICEGEN", "tipo_proceso": "ICEGEN", "fecha_inicio": datetime(2026, 11, 4, 20, 0),
            "fecha_final": datetime(2026, 11, 5, 19, 0), "kg_asignados_total": Decimal("43360"),
            "stock_inicio_kg": Decimal("43360"), "stock_cierre_kg": Decimal("0")}
    base.update(extra)
    return base


def _lote(num, prov, kg, almacen="SILO", **extra):
    d = {"lote_numero": num, "proveedor": prov, "tipo_almacen_origen": almacen, "kg_asignados": kg, "bines_consumidos": None,
         "porcentaje_descuento": None, "brix_recepcion": Decimal("12.08"), "acidez": Decimal("0.62"), "ratio": Decimal("19.48"),
         "kg_saldo": Decimal("0"), "bines_saldo": None}
    d.update(extra)
    return d


class AyudasDeGraficos(unittest.TestCase):
    def test_proveedor_abreviado_para_la_etiqueta(self):
        self.assertEqual(g.corto_proveedor("AGRONEGOCIOS Y MULTISERVICIOS VILLEGAS SOCIEDAD ANONIMA CERRADA"), "AGRONEGOCIOS Y MULTISERVICIOS VILLEGAS S.A.C.")
        self.assertEqual(g.corto_proveedor("COOPERATIVA AGRARIA CAI AGRONATURE"), "COOP. AGRARIA CAI AGRONATURE")
        self.assertEqual(g.corto_proveedor(None), "—")

    def test_recortar_no_pasa_del_ancho(self):
        from reportlab.pdfbase.pdfmetrics import stringWidth
        texto = g.recortar("AGRONEGOCIOS Y MULTISERVICIOS VILLEGAS S.A.C.", "Helvetica", 7, 60)
        self.assertLessEqual(stringWidth(texto, "Helvetica", 7), 60)
        self.assertTrue(texto.endswith("…"))

    def test_paso_agradable(self):
        self.assertEqual(g._paso_agradable(385330, 4), 100000)
        self.assertEqual(g._paso_agradable(0, 4), 1.0)

    def test_columnas_por_dia_y_por_semana(self):
        claves, etq, clave_de, semanal = _columnas_de_tiempo(date(2026, 10, 7), date(2026, 11, 5))
        self.assertEqual(len(claves), 30)
        self.assertFalse(semanal)
        self.assertEqual(etq[0], ("07", "oct"))
        self.assertEqual(sum(1 for _, mes in etq if mes), 2, "el mes solo se escribe cuando cambia")
        claves, etq, clave_de, semanal = _columnas_de_tiempo(date(2026, 7, 1), date(2026, 11, 7))
        self.assertTrue(semanal)
        self.assertEqual(clave_de(date(2026, 11, 7)).weekday(), 0, "las semanas parten del lunes")

    def test_seccion_de_tabla_larga_no_deja_el_titulo_pegado_a_toda_la_tabla(self):
        piezas = seccion("Lotes", largo=True)
        self.assertIsInstance(piezas[0], CondPageBreak)
        self.assertFalse(any(p.getKeepWithNext() for p in piezas[1:]))

    def test_una_celda_puede_ser_un_grafico(self):
        celda = g.BarraCelda(100, 5, 10, g.FOREST_2, "5")
        t = tabla(["Kg"], [[celda]], [120])
        self.assertIs(t._cellvalues[1][0], celda, "la tabla no debe convertir el gráfico en texto")


class PdfDeCorridaConGraficos(unittest.TestCase):
    def test_corrida_completa(self):
        lotes = [_lote(2855, "COOPERATIVA AGRARIA CAI AGRONATURE", Decimal("22040")),
                 _lote(2856, "AGRONEGOCIOS Y MULTISERVICIOS VILLEGAS SOCIEDAD ANONIMA CERRADA", Decimal("21320"), "BINES", bines_consumidos=Decimal("12"))]
        productos = [{"producto": "Jugo Simple Congelado no Pasteurizado ICEGEN NJ ORG", "tipo": "salida", "tambores": Decimal("114"),
                      "peso_neto_tambor_kg": Decimal("200"), "pt_kg": Decimal("22800"), "volumen_litros": None, "cuenta_como_pt": True},
                     {"producto": "Enjuague", "tipo": "salida", "tambores": Decimal("1"), "peso_neto_tambor_kg": Decimal("76"),
                      "pt_kg": Decimal("76"), "volumen_litros": None, "cuenta_como_pt": False}]
        paradas = [{"hora_inicio": datetime(2026, 11, 5, 10, 22), "hora_fin": datetime(2026, 11, 5, 12, 57), "area_proceso": "Estandarizado",
                    "tipo_parada": "Espera", "equipo_afectado": None, "descripcion_falla": "x", "duracion_minutos": Decimal("155")},
                   {"hora_inicio": datetime(2026, 11, 5, 15, 0), "hora_fin": None, "area_proceso": "Extracción",
                    "tipo_parada": "Mecánico", "equipo_afectado": None, "descripcion_falla": "y", "duracion_minutos": None}]
        med = [{"tanque": "TK1", "litros": Decimal("3000"), "brix_inicial": Decimal("12.27"), "brix_final": Decimal("12.30"),
                "acidez": Decimal("0.48"), "ph": Decimal("4.16"), "ratio": Decimal("25.63")},
               {"tanque": "TK2", "litros": None, "brix_inicial": None, "brix_final": Decimal("11.64"),
                "acidez": Decimal("0.58"), "ph": None, "ratio": Decimal("20.07")}]
        pdf = generar_reporte_pdf(_corrida(), lotes, productos, paradas, med)
        self.assertTrue(pdf.startswith(b"%PDF"))

    def test_corrida_recien_creada_sin_nada(self):
        pdf = generar_reporte_pdf(_corrida(kg_asignados_total=Decimal("0"), fecha_final=None, stock_inicio_kg=None, stock_cierre_kg=None), [], [], [], [])
        self.assertTrue(pdf.startswith(b"%PDF"))

    def test_sin_brix_inicial_no_se_dibuja_el_antes_despues(self):
        import reporte_corrida as rc
        self.assertIsNone(rc._grafico_tanques([{"tanque": "TK1", "litros": 100, "brix_inicial": None, "brix_final": 12}]))

    def test_parada_con_horas_en_texto_no_rompe_el_reporte(self):
        paradas = [{"hora_inicio": "2026-11-05T10:22:00", "hora_fin": "2026-11-05T11:00:00", "area_proceso": None, "tipo_parada": None,
                    "equipo_afectado": None, "descripcion_falla": None, "duracion_minutos": 38}]
        pdf = generar_reporte_pdf(_corrida(fecha_final=None), [_lote(1, "X", Decimal("1000"))], [], paradas, [])
        self.assertTrue(pdf.startswith(b"%PDF"))

    def test_muchos_lotes_y_paradas_se_reparten_en_varias_paginas(self):
        lotes = [_lote(2000 + i, f"PROVEEDOR {i}", Decimal(1000 + i)) for i in range(40)]
        paradas = [{"hora_inicio": datetime(2026, 11, 5, 8, i), "hora_fin": datetime(2026, 11, 5, 8, i, 30), "area_proceso": "A", "tipo_parada": "B",
                    "equipo_afectado": None, "descripcion_falla": "z", "duracion_minutos": 1} for i in range(30)]
        pdf = generar_reporte_pdf(_corrida(kg_asignados_total=Decimal("60000")), lotes, [], paradas, [])
        self.assertGreater(_paginas(pdf), 2)


class PdfPorPeriodoConGraficos(unittest.TestCase):
    def test_periodo_con_datos(self):
        corridas = [{"nombre": "02-Noviembre JCC", "fecha_inicio": datetime(2026, 11, 2, 8, 0), "tipo_proceso": "JCC", "mp_kg_objetivo": None,
                     "kg_asignados_total": Decimal("39340"), "rendimiento": None},
                    {"nombre": "03-Noviembre PRE", "fecha_inicio": datetime(2026, 11, 3, 8, 0), "tipo_proceso": "PRE CC", "mp_kg_objetivo": None,
                     "kg_asignados_total": Decimal("1000"), "rendimiento": None},
                    {"nombre": "04-Noviembre ??", "fecha_inicio": datetime(2026, 11, 4, 8, 0), "tipo_proceso": "OTRO RARO", "mp_kg_objetivo": None,
                     "kg_asignados_total": Decimal("2000"), "rendimiento": Decimal("0.5")}]
        productos = [{"corrida": "02-Noviembre JCC", "producto": "x", "tipo": "salida", "pt_kg": Decimal("3975"), "volumen_litros": None, "cuenta_como_pt": True}]
        proveedores = [("AGRONEGOCIOS Y MULTISERVICIOS VILLEGAS SOCIEDAD ANONIMA CERRADA", Decimal("30000")), (None, Decimal("5"))]
        paradas = [{"hora_inicio": datetime(2026, 11, 3, 13, 53), "area_proceso": "Estandarizado", "tipo_parada": "Espera", "minutos": Decimal("74")},
                   {"hora_inicio": datetime(2026, 11, 3, 14, 53), "area_proceso": None, "tipo_parada": None, "minutos": 0}]
        pdf = generar_reporte_periodo_pdf(date(2026, 11, 2), date(2026, 11, 5), corridas, productos, proveedores, paradas)
        self.assertTrue(pdf.startswith(b"%PDF"))

    def test_periodo_vacio(self):
        pdf = generar_reporte_periodo_pdf(date(2026, 11, 6), date(2026, 11, 7), [], [], [], [])
        self.assertTrue(pdf.startswith(b"%PDF"))

    def test_periodo_largo_agrupa_por_semana(self):
        corridas = [{"nombre": "c", "fecha_inicio": datetime(2026, 8, 3, 8, 0), "tipo_proceso": "JSA", "mp_kg_objetivo": None,
                     "kg_asignados_total": Decimal("5000"), "rendimiento": None}]
        pdf = generar_reporte_periodo_pdf(date(2026, 7, 1), date(2026, 11, 7), corridas, [], [], [])
        self.assertTrue(pdf.startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main()
