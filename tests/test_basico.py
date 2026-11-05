"""Pruebas de las piezas que no dependen de la base de datos.

Se ejecutan con:  python -m unittest discover -s tests
No se abre ninguna conexión: el motor de SQLAlchemy se crea sin conectarse.
"""
import os
import re
import sys
import time
import logging
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
logging.disable(logging.CRITICAL)   # los errores simulados no deben ensuciar la salida
sys.path.insert(0, str(RAIZ))
os.environ.setdefault("DATABASE_URL", "postgresql://usuario:clave@localhost:5432/prueba")
os.environ["APP_USER"] = os.environ["APP_PASSWORD"] = os.environ["APP_SESSION_SECRET"] = "prueba"

import api  # noqa: E402
import repartir  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from reporte_trazabilidad import _dia_mes_del_nombre  # noqa: E402


class _ErrorBD(Exception):
    """Imita el error que entrega el controlador de Postgres (pg8000 lo entrega como diccionario)."""

    def __init__(self, codigo, mensaje):
        super().__init__({"C": codigo, "M": mensaje})


class _Envuelto(Exception):
    """Imita el error de SQLAlchemy, que guarda el original en .orig."""

    def __init__(self, orig):
        super().__init__(str(orig))
        self.orig = orig


class ErroresDeBaseDeDatos(unittest.TestCase):
    def test_regla_escrita_en_la_base_se_muestra_tal_cual(self):
        e = api._error_http(_Envuelto(_ErrorBD("P0001", "El lote 2730 solo tiene 2220 kg de saldo.")))
        self.assertEqual((e.status_code, e.detail), (400, "El lote 2730 solo tiene 2220 kg de saldo."))

    def test_duplicado_da_mensaje_claro(self):
        e = api._error_http(_Envuelto(_ErrorBD("23505", "duplicate key value violates unique constraint")))
        self.assertEqual(e.status_code, 409)
        self.assertNotIn("constraint", e.detail)

    def test_error_inesperado_no_filtra_detalles_internos(self):
        e = api._error_http(_Envuelto(_ErrorBD("XX000", "relation \"secreta\" does not exist")))
        self.assertEqual(e.status_code, 500)
        self.assertNotIn("secreta", e.detail)


class Sesion(unittest.TestCase):
    def test_token_recien_emitido_es_valido(self):
        self.assertTrue(api._sesion_valida(api._token_sesion()))

    def test_token_alterado_no_vale(self):
        exp, azar, firma = api._token_sesion().split(".")
        self.assertFalse(api._sesion_valida(f"{int(exp) + 3600}.{azar}.{firma}"))
        self.assertFalse(api._sesion_valida(f"{exp}.{azar}.{'0' * len(firma)}"))

    def test_token_vencido_no_vale(self):
        exp = int(time.time()) - 10
        self.assertFalse(api._sesion_valida(f"{exp}.abc.{api._firma(exp, 'abc')}"))

    def test_basura_no_vale(self):
        for token in ("", "sin-punto", "abc.def", "a.b.c", None):
            self.assertFalse(api._sesion_valida(token), token)

    def test_cerrar_sesion_invalida_solo_ese_token(self):
        uno, otro = api._token_sesion(), api._token_sesion()
        api._sesiones_cerradas.add(uno.rsplit(".", 1)[1])
        self.assertFalse(api._sesion_valida(uno))
        self.assertTrue(api._sesion_valida(otro))      # la sesión de otra persona sigue abierta


class FechaEnElNombreDeLaCorrida(unittest.TestCase):
    def test_nombres_validos(self):
        self.assertEqual(_dia_mes_del_nombre("17-Octubre JCC"), (17, 10))
        self.assertEqual(_dia_mes_del_nombre("19-Octubre JCC TASTE"), (19, 10))
        self.assertEqual(_dia_mes_del_nombre("5 septiembre ICE"), (5, 9))

    def test_nombres_sin_fecha(self):
        for nombre in ("Corrida de prueba", "32-Octubre", "", None):
            self.assertIsNone(_dia_mes_del_nombre(nombre), nombre)


class ExcelConVariasCorridas(unittest.TestCase):
    def _hoja(self, nombre, tipo):
        from datetime import datetime
        corrida = {"nombre": nombre, "tipo_proceso": tipo, "fecha_inicio": datetime(2026, 10, 17, 8, 0), "fecha_final": None, "saldo_por_secuencia": True}
        return {"corrida": corrida, "lotes": [], "productos": [], "mediciones": [], "stock": []}

    def test_nombres_de_hoja_validos_y_unicos(self):
        from reporte_trazabilidad import _nombres_de_hoja
        res = _nombres_de_hoja(["17-Octubre JCC", "17-Octubre JCC", "A/B:C*D?[E]", "", "X" * 50])
        self.assertEqual(res[0], "17-Octubre JCC")
        self.assertEqual(res[1], "17-Octubre JCC (2)")
        self.assertNotRegex(res[2], r"[\[\]:*?/\\]")
        self.assertEqual(res[3], "Corrida")
        self.assertTrue(all(len(n) <= 31 for n in res))
        self.assertEqual(len({n.lower() for n in res}), len(res))

    def test_solo_la_ultima_hoja_lleva_el_cuadro_de_stock(self):
        import io
        import openpyxl
        from reporte_trazabilidad import generar_trazabilidad_grupo_xlsx
        libro = generar_trazabilidad_grupo_xlsx([self._hoja("16-Octubre ICEGEN", "ICEGEN"), self._hoja("17-Octubre ICEGEN", "ICEGEN"), self._hoja("17-Octubre JCC", "JCC")])
        wb = openpyxl.load_workbook(io.BytesIO(libro))
        self.assertEqual(wb.sheetnames, ["16-Octubre ICEGEN", "17-Octubre ICEGEN", "17-Octubre JCC"])
        textos = [{str(c.value) for fila in ws.iter_rows() for c in fila if isinstance(c.value, str)} for ws in wb.worksheets]
        con_cuadro = [any(t.startswith("Stock inicial") for t in hoja) for hoja in textos]
        self.assertEqual(con_cuadro, [False, False, True])
        self.assertTrue(all(any("está en la hoja «17-Octubre JCC»" in t for t in hoja) for hoja in textos[:2]))

    def test_el_reproceso_va_en_un_cuadro_aparte_y_no_en_la_tabla_de_lotes(self):
        import io
        import openpyxl
        from reporte_trazabilidad import generar_trazabilidad_grupo_xlsx
        hoja = self._hoja("02-Noviembre JCC", "JCC")
        hoja["lotes"] = [
            {"lote_numero": 2840, "proveedor": "AGRONEGOCIOS", "kg_asignados": 1450, "tipo_almacen_origen": "SILO"},
            {"lote_numero": -1857, "proveedor": "REPROCESO (JUGO OBSERVADO ENE-2025)", "kg_asignados": 8100, "tipo_almacen_origen": "SILO"},
            {"lote_numero": -2597, "proveedor": "COOPERATIVA X", "kg_asignados": 500, "tipo_almacen_origen": "SILO"},   # un PRE CC sigue en la tabla
        ]
        ws = openpyxl.load_workbook(io.BytesIO(generar_trazabilidad_grupo_xlsx([hoja]))).worksheets[0]
        tabla = [ws.cell(fila, 3).value for fila in range(8, 11)]
        self.assertEqual(tabla, [2840, "2597 PRE CC", None])
        textos = {str(c.value) for fila in ws.iter_rows() for c in fila if c.value is not None}
        self.assertIn("Reproceso (informativo)", textos)
        self.assertIn("1857 REPROCESO", textos)
        self.assertNotIn("1857 REPROCESO", {str(ws.cell(fila, 3).value) for fila in range(8, 11)})


class ExcelDeParadas(unittest.TestCase):
    def _parada(self, **extra):
        from datetime import datetime
        base = {"corrida": "03-Noviembre JCC", "turno": "DÍA", "hora_inicio": datetime(2026, 11, 3, 10, 33), "hora_fin": datetime(2026, 11, 3, 12, 18), "min_total": 105,
                "area_proceso": "Estandarizado", "tipo_parada": "Espera producción", "equipo_afectado": "Tanque", "descripcion_falla": "Tanque lleno",
                "responsable_solucion": "Maquinista", "solucion_obs": "", "recomendacion": ""}
        base.update(extra)
        return base

    def _libro(self, paradas):
        import io
        from datetime import date
        import openpyxl
        from reporte_paradas import generar_paradas_periodo_xlsx
        return openpyxl.load_workbook(io.BytesIO(generar_paradas_periodo_xlsx(date(2026, 11, 1), date(2026, 11, 7), paradas)))

    def test_el_libro_trae_tablero_analisis_datos_y_detalle(self):
        from datetime import datetime
        en_curso = self._parada(hora_inicio=datetime(2026, 11, 4, 8, 0), hora_fin=None, min_total=None, tipo_parada="Mecánico", turno="NOCHE")
        wb = self._libro([self._parada(), en_curso])
        self.assertEqual(wb.sheetnames, ["Tablero", "Análisis", "Datos", "Detalle"])
        self.assertEqual(len(wb["Tablero"]._charts), 7)
        self.assertIn("TablaParadas", wb["Datos"].tables)
        self.assertEqual(wb["Datos"].max_row, 3)                       # encabezado + 2 paradas
        self.assertEqual(wb["Datos"]["S3"].value, "En curso")

    def test_un_texto_que_empieza_con_igual_no_se_guarda_como_formula(self):
        wb = self._libro([self._parada(descripcion_falla="=1+1")])
        celdas = [c for fila in wb["Datos"].iter_rows(min_row=2) for c in fila if c.value == "=1+1"]
        self.assertTrue(celdas)
        self.assertTrue(all(c.data_type == "s" for c in celdas))

    def test_sin_paradas_el_libro_se_genera_sin_graficos(self):
        wb = self._libro([])
        self.assertEqual(wb.sheetnames, ["Tablero", "Análisis", "Datos", "Detalle"])
        self.assertEqual(len(wb["Tablero"]._charts), 0)
        self.assertEqual(len(wb["Datos"].tables), 0)


class LoteDeReproceso(unittest.TestCase):
    def _post(self, **datos):
        from unittest import mock
        from fastapi.testclient import TestClient
        base = {"numero": 1857, "descripcion": "Jugo observado ene-2025", "kg": 8100, "brix": 11.5}
        base.update(datos)
        with mock.patch.object(api, "AUTH_ON", False):          # la prueba no depende de si el login está activo en este equipo
            return TestClient(api.app).post("/api/lotes/reproceso", json=base)

    def test_rechaza_datos_invalidos_sin_tocar_la_base(self):
        for cambio, texto in (({"numero": 0}, "número"), ({"kg": 0}, "cantidad"), ({"brix": 150}, "Brix"), ({"acidez": 80}, "acidez"),
                              ({"fecha_ingreso": "05/11/2026"}, "fecha"), ({"descripcion": "x" * 81}, "descripción")):
            r = self._post(**cambio)
            self.assertEqual(r.status_code, 400, cambio)
            self.assertIn(texto.lower(), r.json()["detail"].lower())

    def test_el_lote_se_guarda_en_negativo_con_proveedor_reproceso_y_ratio(self):
        class Res:
            def __init__(self, v): self.v = v
            def scalar(self): return self.v

        class Conexion:
            def __init__(self, existe): self.existe, self.llamadas = existe, []
            def execute(self, sql, params=None):
                self.llamadas.append((str(sql), params))
                return Res(1 if self.existe and "SELECT 1" in str(sql) else None)

        from datetime import date
        c = Conexion(existe=False)
        self.assertEqual(api._insertar_lote_reproceso(c, 1857, "REPROCESO (JUGO)", date(2026, 11, 5), 8100.0, 11.5, 0.5), -1857)
        sql, params = c.llamadas[-1]
        self.assertIn("INSERT INTO lotes", sql)
        self.assertEqual((params["d"], params["prov"], params["kg"], params["r"]), (-1857, "REPROCESO (JUGO)", 8100.0, 23.0))
        sin_acidez = Conexion(existe=False)
        api._insertar_lote_reproceso(sin_acidez, 1858, "REPROCESO", date(2026, 11, 5), 100.0, 11.5, None)
        self.assertIsNone(sin_acidez.llamadas[-1][1]["r"])
        with self.assertRaises(api.HTTPException) as e:                # un número que ya usa otro lote
            api._insertar_lote_reproceso(Conexion(existe=True), 2831, "REPROCESO", date(2026, 11, 5), 100.0, None, None)
        self.assertEqual(e.exception.status_code, 409)


class ConexionConLaBase(unittest.TestCase):
    def test_una_conexion_cortada_responde_503_en_español_y_marcada_para_reintentar(self):
        from sqlalchemy.exc import OperationalError
        e = OperationalError("SELECT 1", {}, Exception("server closed the connection unexpectedly"))
        r = api._error_http(e)
        self.assertEqual(r.status_code, 503)
        self.assertIn("conexión", r.detail.lower())
        self.assertEqual(r.headers.get("X-Reintentable"), "1")

    def _llamar(self, metodo, respuestas):
        """Pasa una petición por el reintento de lecturas con una app falsa que contesta, en orden, lo de `respuestas`."""
        import asyncio
        llamadas = []

        async def app_falsa(scope, receive, send):
            estado, cabeceras = respuestas[min(len(llamadas), len(respuestas) - 1)]
            llamadas.append(1)
            await send({"type": "http.response.start", "status": estado, "headers": cabeceras})
            await send({"type": "http.response.body", "body": b"{}"})

        enviados = []

        async def enviar(mensaje):
            enviados.append(mensaje)

        async def recibir():
            return {"type": "http.request", "body": b""}

        scope = {"type": "http", "method": metodo, "path": "/api/x", "headers": []}
        asyncio.run(api._ReintentoLecturas(app_falsa)(scope, recibir, enviar))
        return len(llamadas), [m["status"] for m in enviados if m["type"] == "http.response.start"]

    def test_una_lectura_se_repite_una_vez_si_la_base_cortó_la_conexion(self):
        reintentable = [(b"x-reintentable", b"1")]
        self.assertEqual(self._llamar("GET", [(503, reintentable), (200, [])]), (2, [200]))
        self.assertEqual(self._llamar("GET", [(503, reintentable)]), (2, [503]))          # si sigue fallando, se muestra el error (una sola vez)
        self.assertEqual(self._llamar("GET", [(500, [])]), (1, [500]))                     # un error de otro tipo no se repite

    def test_una_escritura_nunca_se_repite_sola(self):
        self.assertEqual(self._llamar("POST", [(503, [(b"x-reintentable", b"1")]), (200, [])]), (1, [503]))

    def test_la_hora_de_fin_no_puede_ser_anterior_a_la_de_inicio(self):
        api._validar_horas_parada("2026-11-03T13:53", "2026-11-03T15:07")
        api._validar_horas_parada("2026-11-03T13:53", None)
        with self.assertRaises(api.HTTPException) as e:
            api._validar_horas_parada("2026-11-03T13:53", "2026-11-03T12:00")
        self.assertEqual(e.exception.status_code, 400)
        with self.assertRaises(api.HTTPException):
            api._instante("no es una fecha", "hora de inicio")

    def test_el_aviso_de_posible_repetido_pide_confirmacion(self):
        import json
        r = api._pedir_confirmacion("Ya hay un TK3 en esta corrida.")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(json.loads(r.body), {"detail": "Ya hay un TK3 en esta corrida.", "requiere_confirmacion": True})


class ReprocesoFueraDeLaMP(unittest.TestCase):
    """El jugo de REPROCESO no es materia prima: el PDF lo informa aparte y no lo suma a la MP."""

    def test_se_reconoce_el_lote_de_reproceso(self):
        import reporte_corrida
        self.assertTrue(reporte_corrida._es_reproceso({"lote_numero": -1857, "proveedor": "REPROCESO (JUGO)"}))
        self.assertFalse(reporte_corrida._es_reproceso({"lote_numero": -2593, "proveedor": "AGRONEGOCIOS"}))     # PRE CC: sí es MP
        self.assertFalse(reporte_corrida._es_reproceso({"lote_numero": 1857, "proveedor": "REPROCESO"}))         # un lote real nunca es reproceso

    def test_el_pdf_se_genera_con_un_lote_de_reproceso(self):
        import reporte_corrida
        corrida = {"nombre": "02-Noviembre JCC", "fecha_inicio": None, "fecha_final": None, "kg_asignados_total": 1000}
        lotes = [
            {"lote_numero": 2835, "proveedor": "COOPERATIVA", "tipo_almacen_origen": "SILO", "kg_asignados": 1000, "kg_saldo": 0, "brix_recepcion": 11, "acidez": 0.7, "ratio": 15},
            {"lote_numero": -1857, "proveedor": "REPROCESO (JUGO OBSERVADO)", "tipo_almacen_origen": "SILO", "kg_asignados": 8100, "kg_saldo": 0, "brix_recepcion": 11, "acidez": 0.5, "ratio": 20},
        ]
        pdf = reporte_corrida.generar_reporte_pdf(corrida, lotes, [], [], [])
        self.assertTrue(pdf.startswith(b"%PDF"))


class TanqueOlvidado(unittest.TestCase):
    def test_numero_de_tanque(self):
        self.assertEqual(api._numero_tanque("TK12"), 12)
        self.assertEqual(api._numero_tanque(" tk 07 "), 7)
        for nombre in ("Tanque 1", "TK12-B", "", None):
            self.assertIsNone(api._numero_tanque(nombre), nombre)

    def test_queda_entre_el_anterior_y_el_siguiente(self):
        from datetime import datetime, timezone
        h = lambda hh, mm: datetime(2026, 10, 24, hh, mm, tzinfo=timezone.utc)
        vecinos = [("TK10", h(2, 0)), ("TK11", h(2, 10)), ("TK13", h(2, 40)), ("TK14", h(3, 0))]
        hora = api._instante_para_tanque(12, vecinos)
        self.assertTrue(h(2, 10) < hora < h(2, 40))

    def test_el_ultimo_tanque_usa_la_hora_de_ahora(self):
        from datetime import datetime, timezone
        vecinos = [("TK1", datetime(2026, 10, 24, 2, 0, tzinfo=timezone.utc))]
        self.assertIsNone(api._instante_para_tanque(2, vecinos))
        self.assertIsNone(api._instante_para_tanque(1, []))

    def test_el_primero_que_falta_queda_antes_del_siguiente(self):
        from datetime import datetime, timezone
        h = datetime(2026, 10, 24, 2, 0, tzinfo=timezone.utc)
        self.assertTrue(api._instante_para_tanque(1, [("TK2", h)]) < h)


class OrdenDeUnReparto(unittest.TestCase):
    def test_la_jsa_jcc_queda_al_final_en_los_mismos_lugares(self):
        seq = [160, 171, 172, 173, 174]                      # 171 ICEGEN, 172 JCC, 173 ICEGEN
        tipos = {171: "ICEGEN", 172: "JCC", 173: "ICEGEN"}
        orden, nueva = api._secuencia_con_principal_al_final(seq, [172, 171, 173], tipos)
        self.assertEqual(orden, [171, 173, 172])
        self.assertEqual(nueva, [160, 171, 173, 172, 174])

    def test_si_ya_estaba_ordenado_no_cambia(self):
        seq = [1, 2, 3]
        orden, nueva = api._secuencia_con_principal_al_final(seq, [2, 3], {2: "ICEGEN", 3: "JSA"})
        self.assertEqual((orden, nueva), ([2, 3], seq))

    def test_con_dos_principales_se_respeta_el_orden_de_la_lista(self):
        seq = [10, 11, 12]
        orden, _ = api._secuencia_con_principal_al_final(seq, [12, 11], {11: "JSA", 12: "JCC"})
        self.assertEqual(orden, [11, 12])


class ListaDeCorridasParaRepartir(unittest.TestCase):
    def test_ids_validos(self):
        self.assertEqual(repartir._ids("3,1,3"), [1, 3])

    def test_ids_invalidos(self):
        for cadena in ("1", "a,b", "1,2,3,4,5", ""):
            with self.assertRaises(HTTPException, msg=cadena):
                repartir._ids(cadena)


class PaginasWeb(unittest.TestCase):
    """Reglas de presentación que no deben volver a romperse."""

    paginas = sorted((RAIZ / "web").glob("*.html"))

    def test_no_se_usan_cuadros_nativos_del_navegador(self):
        for p in self.paginas:
            self.assertIsNone(re.search(r"(?<![\w.])alert\(", p.read_text(encoding="utf-8")), p.name)

    def test_el_zoom_no_esta_bloqueado(self):
        for p in self.paginas:
            self.assertNotIn("maximum-scale", p.read_text(encoding="utf-8"), p.name)

    def test_las_paginas_con_avisos_cargan_app_js(self):
        for p in self.paginas:
            texto = p.read_text(encoding="utf-8")
            if "avisar(" in texto:
                self.assertIn("app.js", texto, p.name)


if __name__ == "__main__":
    unittest.main()
