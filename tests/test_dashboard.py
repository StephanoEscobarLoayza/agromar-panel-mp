"""Pruebas de los números que alimentan la pantalla de Dashboards (dashboard.py): son funciones puras, sin base de datos.

Se ejecutan con:  python -m unittest discover -s tests
"""
import sys
import unittest
from datetime import date, datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import dashboard  # noqa: E402


def corrida(i, dia, tipo, mp, rend=None, final=True):
    return {"id": i, "nombre": f"C{i}", "fecha_inicio": datetime(2026, 10, dia, 8, 0), "fecha_final": datetime(2026, 10, dia, 20, 0) if final else None,
            "tipo_proceso": tipo, "mp_kg_objetivo": mp, "kg_asignados_total": None, "rendimiento": rend}


def producto(cid, pt, tipo="salida", cuenta=True):
    return {"corrida_id": cid, "tipo": tipo, "pt_kg": pt, "volumen_litros": None, "cuenta_como_pt": cuenta}


def parada(cid, ini, fin, area="Extracción", tipo="Mecánico"):
    return {"corrida_id": cid, "hora_inicio": ini, "hora_fin": fin, "area_proceso": area, "tipo_parada": tipo}


LIMITES = {"primera": "2026-10-01", "ultima": "2026-10-20", "hoy": "2026-10-20"}


class Periodos(unittest.TestCase):
    def test_el_periodo_anterior_tiene_la_misma_duracion_y_termina_justo_antes(self):
        self.assertEqual(dashboard.ventana_anterior(date(2026, 10, 11), date(2026, 10, 20)), (date(2026, 10, 1), date(2026, 10, 10)))
        self.assertEqual(dashboard.ventana_anterior(date(2026, 11, 5), date(2026, 11, 5)), (date(2026, 11, 4), date(2026, 11, 4)))

    def test_la_granularidad_depende_de_la_duracion(self):
        self.assertEqual(dashboard.granularidad(date(2026, 10, 1), date(2026, 10, 30)), "dia")
        self.assertEqual(dashboard.granularidad(date(2026, 7, 13), date(2026, 11, 5)), "semana")
        self.assertEqual(dashboard.granularidad(date(2026, 1, 1), date(2026, 11, 5)), "mes")


class ProductoYParadas(unittest.TestCase):
    def test_el_producto_terminado_resta_los_insumos_de_entrada_que_descuentan(self):
        pr = [producto(1, 1000), producto(1, 500), producto(1, 200, "entrada"), producto(1, 90, "entrada", cuenta=False)]
        self.assertEqual(dashboard.pt_neto(pr), 1300)

    def test_sin_filas_de_salida_no_hay_producto_terminado(self):
        self.assertIsNone(dashboard.pt_neto([]))
        self.assertIsNone(dashboard.pt_neto([producto(1, 200, "entrada")]))
        self.assertIsNone(dashboard.pt_neto([producto(1, 300, cuenta=False)]))

    def test_las_paradas_que_se_solapan_cuentan_una_sola_vez(self):
        h = lambda hh, mm: datetime(2026, 10, 5, hh, mm)  # noqa: E731
        # la misma parada anotada en dos corridas en paralelo (10:00-11:00 y 10:30-11:30) = 90 min de planta, no 120
        self.assertEqual(dashboard.minutos_de_planta([(h(10, 0), h(11, 0)), (h(10, 30), h(11, 30)), (h(14, 0), h(14, 20))]), 110)
        self.assertEqual(dashboard.minutos_de_planta([(h(10, 0), None), (None, h(11, 0))]), 0)


class Produccion(unittest.TestCase):
    def setUp(self):
        self.corridas = [
            corrida(1, 2, "JSA", 100000), corrida(2, 3, "JCC", 200000), corrida(3, 12, "JSA", 120000), corrida(4, 13, "JCC", 100000),
            corrida(5, 14, "JCC", 100000, rend=0.1), corrida(6, 15, "ICEGEN", None, final=False),
        ]
        self.productos = [producto(1, 52000), producto(2, 17000), producto(3, 60000), producto(4, 8000)]
        self.paradas = [parada(3, datetime(2026, 10, 12, 9, 0), datetime(2026, 10, 12, 10, 0)), parada(4, datetime(2026, 10, 12, 9, 30), datetime(2026, 10, 12, 10, 30))]
        self.prov = [{"corrida_id": 3, "proveedor": "A", "kg": 120000}, {"corrida_id": 4, "proveedor": "B", "kg": 100000}, {"corrida_id": 5, "proveedor": "B", "kg": 100000}]

    def armar(self, tipo=None):
        return dashboard.armar_produccion(self.corridas, self.productos, self.paradas, self.prov, date(2026, 10, 11), date(2026, 10, 20), tipo, LIMITES)

    def test_los_totales_del_periodo_y_del_anterior(self):
        d = self.armar()
        self.assertEqual(d["kpis"]["mp_kg"], 320000)
        self.assertEqual(d["kpis"]["corridas"], 4)
        self.assertAlmostEqual(d["kpis"]["promedio_kg"], 106666.7, places=1)   # la corrida sin MP no baja el promedio
        self.assertEqual(d["kpis_anterior"]["mp_kg"], 300000)
        self.assertEqual(d["anterior"], {"desde": "2026-10-01", "hasta": "2026-10-10"})

    def test_la_serie_suma_lo_mismo_que_el_total(self):
        d = self.armar()
        self.assertEqual(sum(b["mp_kg"] for b in d["serie"]), d["kpis"]["mp_kg"])
        self.assertEqual(len(d["serie"]), 10)                             # un bloque por día, también los días sin corridas

    def test_el_rendimiento_sale_de_los_tambores_y_si_no_hay_de_la_hoja(self):
        d = self.armar()
        por = {t["tipo"]: t for t in d["por_tipo"]}
        self.assertAlmostEqual(por["JSA"]["rend"], 0.5)                   # 60000 / 120000
        self.assertAlmostEqual(por["JCC"]["rend"], (0.08 * 100000 + 0.1 * 100000) / 200000)   # ponderado por MP; una usa la hoja
        self.assertIsNone(por["ICEGEN"]["rend"])                          # sin dato: no se inventa
        self.assertAlmostEqual(por["JSA"]["rend_anterior"], 0.52)

    def test_filtrar_por_tipo_deja_intacto_el_desglose_por_tipo(self):
        d = self.armar("JCC")
        self.assertEqual(d["kpis"]["mp_kg"], 200000)
        self.assertEqual({t["tipo"] for t in d["por_tipo"]}, {"JSA", "JCC", "ICEGEN"})
        self.assertTrue(all(c["tipo"] == "JCC" for c in d["corridas"]))
        self.assertEqual([p["nombre"] for p in d["proveedores"]], ["B"])

    def test_las_paradas_simultaneas_se_cuentan_como_tiempo_de_planta(self):
        d = self.armar()
        self.assertEqual(d["kpis"]["min_parado"], 90)                     # 9:00-10:00 y 9:30-10:30 se solapan: son 90 min de planta, no 120
        self.assertEqual(d["kpis"]["min_parado_suma"], 120)
        self.assertEqual(d["paradas"]["n"], 2)

    def test_una_corrida_en_curso_se_marca_como_abierta(self):
        d = self.armar()
        self.assertEqual([c["abierta"] for c in d["corridas"] if c["id"] == 6], [True])

    def test_sin_periodo_anterior_dentro_de_la_campana_no_se_compara(self):
        d = dashboard.armar_produccion(self.corridas, self.productos, self.paradas, self.prov, date(2026, 10, 1), date(2026, 10, 20), None, LIMITES)
        self.assertIsNone(d["anterior"])
        self.assertIsNone(d["kpis_anterior"])


class Stock(unittest.TestCase):
    def lote(self, n, ing, saldo, estado="EN PROCESO", ubic="SILO 1", prov="P", neto=10000, brix=11, acidez=0.7, ratio=15):
        return {"numero": n, "fecha_ingreso": date(2026, 10, ing), "kg_saldo": saldo, "estado_actual": estado, "ubicacion_actual": ubic,
                "proveedor": prov, "peso_neto_kg": neto, "brix_recepcion": brix, "acidez": acidez, "ratio": ratio}

    def test_stock_antiguedad_y_lotes_derivados(self):
        lotes = [self.lote(1, 19, 5000), self.lote(2, 12, 8000, "EN ESPERA", "SILO 2"), self.lote(3, 1, 0, "PROCESADO"), self.lote(-1857, 19, 9999)]
        d = dashboard.armar_stock(lotes, date(2026, 10, 1), date(2026, 10, 20), date(2026, 10, 20), LIMITES)
        self.assertEqual(d["stock"], {"kg": 13000, "lotes": 2})           # el derivado (número negativo) y el procesado no cuentan
        ant = {t["nombre"]: t["kg"] for t in d["antiguedad"]}
        self.assertEqual(ant["0 a 2 días"], 5000)
        self.assertEqual(ant["6 a 10 días"], 8000)
        self.assertEqual([x["numero"] for x in d["lotes_saldo"]], [2, 1])  # los más antiguos primero

    def test_la_calidad_de_recepcion_se_pondera_por_kg(self):
        lotes = [self.lote(1, 5, 0, "PROCESADO", neto=30000, brix=12), self.lote(2, 6, 0, "PROCESADO", neto=10000, brix=10)]
        d = dashboard.armar_stock(lotes, date(2026, 10, 1), date(2026, 10, 20), date(2026, 10, 20), LIMITES)
        self.assertEqual(d["recibida"]["kg"], 40000)
        self.assertEqual(d["recibida"]["brix"], 11.5)
        self.assertEqual(sum(b["kg"] for b in d["serie"]), 40000)


if __name__ == "__main__":
    unittest.main()
