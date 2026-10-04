"""Subproductos de la naranja orgánica: emulsión y aceite.

Cadena: lotes -> corrida -> cilindros de emulsión -> transformación a aceite
-> barriles de aceite (ver la sección SUBPRODUCTOS de schema.sql).

Se monta desde api.py con `app.include_router(crear_router(engine))`, así la
sesión/login y el resto de la configuración (middleware) valen igual que para
los demás endpoints.
"""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import text

from reporte_subproductos import generar_subproductos_xlsx

PREFIJO_EMULSION = "EORG-2026-"
UBICACION_NUEVOS_BARRILES = "Extracción"
_LOCK_NUMERACION = 727001            # serializa quienes reparten números de cilindro/barril
_CENT = Decimal("0.01")


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal(0)


def _f(v):
    return float(v) if v is not None else None


def _a_fecha(v):
    """Las fechas que vienen dentro de un json_agg llegan como texto AAAA-MM-DD."""
    return date.fromisoformat(v[:10]) if isinstance(v, str) else v


def _codigo_cilindro(numero: int) -> str:
    return f"{PREFIJO_EMULSION}{numero}"


# ---------------------------------------------------------------------------
# lecturas - se reutilizan en la pantalla, en el resumen y en el Excel
# ---------------------------------------------------------------------------
# Cada lista se arma con UNA sola consulta (la base está en la nube: cada ida y vuelta
# cuesta casi medio segundo, así que varias consultas seguidas hacían lenta la pantalla).
_SQL_REGISTROS = """
    SELECT r.id, r.fecha, r.mp_kg_manual, r.diferencia_motivo, r.observaciones,
           COALESCE((SELECT SUM(a.kg_asignados * COALESCE(a.porcentaje_descuento, 0) / 100.0)
                     FROM emulsion_registro_corridas rc JOIN asignaciones a ON a.corrida_id = rc.corrida_id
                     WHERE rc.registro_id = r.id), 0) AS descuento_kg,
           COALESCE((SELECT SUM(a.kg_asignados)
                     FROM emulsion_registro_corridas rc JOIN asignaciones a ON a.corrida_id = rc.corrida_id
                     WHERE rc.registro_id = r.id AND a.lote_numero < 0), 0) AS precc_kg,
           COALESCE((
               SELECT json_agg(json_build_object(
                          'id', co.id, 'nombre', co.nombre,
                          'kg', COALESCE((SELECT SUM(a.kg_asignados) FROM asignaciones a WHERE a.corrida_id = co.id), 0))
                      ORDER BY co.fecha_inicio, co.id)
               FROM emulsion_registro_corridas rc JOIN corridas co ON co.id = rc.corrida_id
               WHERE rc.registro_id = r.id), '[]'::json) AS corridas,
           cs.n, cs.n_proc, cs.kg AS emulsion_kg, cs.pend_kg, cs.mn, cs.mx, ac.kg AS aceite_kg
    FROM emulsion_registros r
    LEFT JOIN LATERAL (
        SELECT count(*) AS n, count(transformacion_id) AS n_proc, COALESCE(SUM(peso_neto_kg), 0) AS kg,
               COALESCE(SUM(peso_neto_kg) FILTER (WHERE transformacion_id IS NULL), 0) AS pend_kg,
               MIN(numero) AS mn, MAX(numero) AS mx
        FROM emulsion_cilindros WHERE registro_id = r.id) cs ON TRUE
    LEFT JOIN LATERAL (SELECT SUM(kg) AS kg FROM aceite_barril_aportes WHERE registro_id = r.id) ac ON TRUE
    ORDER BY r.fecha DESC, r.id DESC
"""

_SQL_CILINDROS = """
    SELECT c.id, c.numero, c.registro_id, c.fecha, c.peso_neto_kg, c.transformacion_id,
           t.fecha AS transformacion_fecha, r.fecha AS registro_fecha,
           COALESCE((SELECT json_agg(co.nombre ORDER BY co.fecha_inicio, co.id)
                     FROM emulsion_registro_corridas rc JOIN corridas co ON co.id = rc.corrida_id
                     WHERE rc.registro_id = c.registro_id), '[]'::json) AS corridas
    FROM emulsion_cilindros c
    JOIN emulsion_registros r ON r.id = c.registro_id
    LEFT JOIN aceite_transformaciones t ON t.id = c.transformacion_id
    WHERE (CAST(:estado AS text) IS NULL
           OR (CASE WHEN c.transformacion_id IS NULL THEN 'PRECAMARA' ELSE 'PROCESADO' END) = CAST(:estado AS text))
    ORDER BY c.numero DESC
    LIMIT CAST(:lim AS integer)
"""

_SQL_BARRILES = """
    SELECT b.id, b.numero, b.codigo, b.capacidad_kg, b.ubicacion, b.nisira,
           COALESCE((
               SELECT json_agg(json_build_object(
                          'transformacion_id', ap.transformacion_id, 'registro_id', ap.registro_id, 'kg', ap.kg,
                          'fecha', t.fecha, 'registro_fecha', r.fecha,
                          'corridas', COALESCE((SELECT json_agg(co.nombre ORDER BY co.fecha_inicio, co.id)
                                                FROM emulsion_registro_corridas rc JOIN corridas co ON co.id = rc.corrida_id
                                                WHERE rc.registro_id = ap.registro_id), '[]'::json))
                      ORDER BY t.fecha, t.id, ap.id)
               FROM aceite_barril_aportes ap
               JOIN aceite_transformaciones t ON t.id = ap.transformacion_id
               LEFT JOIN emulsion_registros r ON r.id = ap.registro_id
               WHERE ap.barril_id = b.id), '[]'::json) AS aportes
    FROM aceite_barriles b
    ORDER BY b.numero DESC
"""


def _lista_registros(conn):
    regs = []
    for r in conn.execute(text(_SQL_REGISTROS)):
        co = r.corridas
        mp_corridas = sum(float(x["kg"]) for x in co)
        mp_manual = _f(r.mp_kg_manual)
        mp = mp_manual if mp_manual is not None else (mp_corridas or None)
        emulsion = float(r.emulsion_kg)
        aceite = _f(r.aceite_kg) or 0.0
        estado = "PRECAMARA" if not r.n_proc else ("TRANSFORMADO" if r.n_proc == r.n else "PARCIAL")
        listo = estado == "TRANSFORMADO"
        dif = (mp_corridas - mp_manual) if (co and mp_manual is not None) else None
        precc = float(r.precc_kg)
        # por qué difiere: marca manual; o consumos con % de descuento; o la diferencia coincide con el PRE CC que
        # se aplicó como MP (el PRE CC es producto, no pasa por extracción, así que no genera emulsión)
        if r.diferencia_motivo in ("descuento", "precc"):
            motivo = r.diferencia_motivo.upper()
        elif float(r.descuento_kg) > 0:
            motivo = "DESCUENTO"
        elif dif is not None and precc > 0 and abs(dif - precc) <= 5:
            motivo = "PRECC"
        else:
            motivo = None
        regs.append({
            "id": r.id, "fecha": r.fecha, "observaciones": r.observaciones,
            "corridas": [{"id": x["id"], "nombre": x["nombre"], "kg": float(x["kg"])} for x in co],
            "mp_corridas_kg": mp_corridas if co else None,
            "mp_manual_kg": mp_manual,
            "mp_kg": mp,
            "diferencia_kg": dif,
            "diferencia_motivo": motivo,
            "motivo_manual": r.diferencia_motivo in ("descuento", "precc"),
            "descuento_kg": float(r.descuento_kg),
            "precc_kg": precc,
            "n_cilindros": r.n, "n_procesados": r.n_proc,
            "primer_cilindro": r.mn, "ultimo_cilindro": r.mx,
            "emulsion_kg": emulsion, "pendiente_kg": float(r.pend_kg),
            "aceite_kg": aceite if (r.n_proc or aceite) else None,
            "estado": estado,
            "aceite_por_t_mp": (aceite / mp * 1000) if (listo and mp) else None,
            "conversion": (aceite / emulsion) if (listo and emulsion) else None,
        })
    return regs


def _lista_cilindros(conn, estado=None, limite=None):
    out = []
    for c in conn.execute(text(_SQL_CILINDROS), {"estado": estado, "lim": limite}):
        out.append({
            "id": c.id, "numero": c.numero, "codigo": _codigo_cilindro(c.numero),
            "fecha": c.fecha, "registro_id": c.registro_id, "registro_fecha": c.registro_fecha,
            "corridas": c.corridas, "peso_neto_kg": _f(c.peso_neto_kg),
            "estado": "PRECAMARA" if c.transformacion_id is None else "PROCESADO",
            "transformacion_id": c.transformacion_id, "transformacion_fecha": c.transformacion_fecha,
        })
    return out


def _lista_barriles(conn, ubicacion=None):
    out = []
    for b in conn.execute(text(_SQL_BARRILES)):
        if ubicacion and (b.ubicacion or "") != ubicacion:
            continue
        ap = []
        for x in b.aportes:
            previo = next((y for y in ap if y["transformacion_id"] == x["transformacion_id"] and y["registro_id"] == x["registro_id"]), None)
            if previo:                   # dos líneas de la misma transformación y corrida: un solo aporte
                previo["kg"] = round(previo["kg"] + float(x["kg"]), 2)
                continue
            ap.append({"transformacion_id": x["transformacion_id"], "fecha": _a_fecha(x["fecha"]), "registro_id": x["registro_id"],
                       "registro_fecha": _a_fecha(x["registro_fecha"]), "kg": float(x["kg"]), "corridas": x["corridas"]})
        kg = round(sum(x["kg"] for x in ap), 2)
        corridas_b = []
        for x in ap:
            for n in x["corridas"]:
                if n not in corridas_b:
                    corridas_b.append(n)
        n_origenes = len({x["registro_id"] for x in ap if x["registro_id"] is not None})
        out.append({
            "id": b.id, "numero": b.numero, "codigo": b.codigo, "capacidad_kg": _f(b.capacidad_kg),
            "ubicacion": b.ubicacion, "nisira": b.nisira, "kg": kg,
            "estado": "LLENO" if kg >= float(b.capacidad_kg) - 0.005 else "ABIERTO",
            "n_origenes": n_origenes, "mezcla": n_origenes > 1,   # aceite de más de una corrida
            "aportes": ap, "corridas": corridas_b,
        })
    return out


def _kpis(conn, registros):
    listos = [r for r in registros if r["estado"] == "TRANSFORMADO" and r["mp_kg"]]
    mp_listos = sum(r["mp_kg"] for r in listos)
    ac, nb = conn.execute(text(
        "SELECT (SELECT COALESCE(SUM(kg), 0) FROM aceite_barril_aportes), (SELECT count(*) FROM aceite_barriles)")).fetchone()
    return {
        "n_registros": len(registros), "n_cilindros": sum(r["n_cilindros"] for r in registros),
        "emulsion_kg": sum(r["emulsion_kg"] for r in registros),
        "aceite_kg": float(ac), "n_barriles": nb,
        "aceite_por_t_mp": (sum(r["aceite_kg"] for r in listos) / mp_listos * 1000) if mp_listos else None,
        "pendiente_cilindros": sum(r["n_cilindros"] - r["n_procesados"] for r in registros),
        "pendiente_kg": sum(r["pendiente_kg"] for r in registros),
    }


# ---------------------------------------------------------------------------
# escrituras
# ---------------------------------------------------------------------------
class NuevaEmulsion(BaseModel):
    fecha: str                              # AAAA-MM-DD
    corrida_ids: List[int] = []
    mp_kg_manual: Optional[float] = None
    n_cilindros: int
    peso_total_kg: float
    observaciones: Optional[str] = None


class NuevaTransformacion(BaseModel):
    fecha: str                              # AAAA-MM-DD
    aceite_kg: float
    cilindro_ids: List[int]
    observaciones: Optional[str] = None
    codigo_inicial: Optional[str] = None    # solo si aún no existe ningún barril


class EditarRegistro(BaseModel):
    diferencia_motivo: Optional[str] = None    # 'descuento', 'precc' o vacío para quitar la marca


class EditarBarril(BaseModel):
    ubicacion: Optional[str] = None
    nisira: Optional[bool] = None


def _fecha_valida(s: str) -> date:
    try:
        return date.fromisoformat((s or "").strip())
    except ValueError:
        raise HTTPException(status_code=400, detail="La fecha no es válida.")


def _repartir_por_registro(conn, ids_cilindros, aceite: Decimal):
    """Reparte el aceite de una transformación entre las corridas (registros de emulsión)
    de sus cilindros, en proporción a los kg de emulsión de cada una. Devuelve
    [(registro_id, kg)] ordenado de la corrida más antigua a la más reciente: el aceite
    de la más antigua llena primero los barriles (primero en entrar, primero en salir)."""
    filas = conn.execute(text("""
        SELECT c.registro_id, r.fecha, SUM(COALESCE(c.peso_neto_kg, 0)) AS peso, count(*) AS n
        FROM emulsion_cilindros c JOIN emulsion_registros r ON r.id = c.registro_id
        WHERE c.id = ANY(:ids) GROUP BY c.registro_id, r.fecha ORDER BY r.fecha, c.registro_id
    """), {"ids": ids_cilindros}).fetchall()
    pesos = [_d(f.peso) for f in filas]
    if sum(pesos) <= 0:
        pesos = [Decimal(f.n) for f in filas]
    total = sum(pesos)
    partes = [(aceite * w / total).quantize(_CENT, ROUND_HALF_UP) for w in pesos]
    partes[partes.index(max(partes))] += aceite - sum(partes)      # el redondeo lo absorbe la parte mayor
    return [(f.registro_id, kg) for f, kg in zip(filas, partes) if kg > 0]


def _asignar_a_barriles(conn, transformacion_id: int, partes, codigo_inicial: Optional[str]):
    """Reparte el aceite en los barriles en orden: primero completa el último barril
    si quedó abierto; luego abre barriles nuevos (códigos correlativos) hasta agotarlo.
    El último barril puede quedar abierto para la siguiente transformación."""
    ultimo = conn.execute(text("""
        SELECT b.id, b.numero, b.codigo, b.capacidad_kg, COALESCE(SUM(ap.kg), 0) AS usado
        FROM aceite_barriles b LEFT JOIN aceite_barril_aportes ap ON ap.barril_id = b.id
        GROUP BY b.id ORDER BY b.numero DESC LIMIT 1
    """)).fetchone()
    if ultimo is None:
        if not (codigo_inicial or "").strip().isdigit():
            raise HTTPException(status_code=400, detail="Todavía no hay barriles registrados: indique el código del primer barril.")
        numero, codigo, capacidad, libre, barril = 0, int(codigo_inicial) - 1, Decimal(183), Decimal(0), None
    else:
        numero, codigo, capacidad = ultimo.numero, int(ultimo.codigo), _d(ultimo.capacidad_kg)
        libre, barril = capacidad - _d(ultimo.usado), (ultimo.id, ultimo.codigo, False)

    aportes = {}    # codigo del barril -> {"codigo", "kg", "nuevo"}
    for registro_id, kg_total in partes:
        restante = kg_total
        while restante > Decimal("0.004"):
            if barril is None or libre <= Decimal("0.004"):
                numero += 1
                codigo += 1
                bid = conn.execute(text("""
                    INSERT INTO aceite_barriles (numero, codigo, capacidad_kg, ubicacion) VALUES (:n, :c, :cap, :u) RETURNING id
                """), {"n": numero, "c": str(codigo), "cap": capacidad, "u": UBICACION_NUEVOS_BARRILES}).scalar()
                barril, libre = (bid, str(codigo), True), capacidad
            kg = min(libre, restante).quantize(_CENT, ROUND_HALF_UP)
            conn.execute(text("""
                INSERT INTO aceite_barril_aportes (barril_id, transformacion_id, registro_id, kg) VALUES (:b, :t, :r, :k)
            """), {"b": barril[0], "t": transformacion_id, "r": registro_id, "k": kg})
            a = aportes.setdefault(barril[1], {"codigo": barril[1], "kg": 0.0, "nuevo": barril[2]})
            a["kg"] = round(a["kg"] + float(kg), 2)
            libre -= kg
            restante -= kg
    return list(aportes.values())


def crear_router(engine) -> APIRouter:
    router = APIRouter()

    @router.get("/api/subproductos/resumen")
    def resumen():
        with engine.connect() as conn:
            regs = _lista_registros(conn)
            return {"kpis": _kpis(conn, regs), "registros": regs}

    @router.get("/api/subproductos/corridas")
    def corridas_para_vincular(dias: int = 30):
        """Corridas recientes del panel, para vincularlas a una emulsión."""
        with engine.connect() as conn:
            res = conn.execute(text("""
                SELECT c.id, c.nombre, c.tipo_proceso, c.fecha_inicio::date AS fecha,
                       COALESCE(SUM(a.kg_asignados), 0) AS kg,
                       (SELECT rc.registro_id FROM emulsion_registro_corridas rc WHERE rc.corrida_id = c.id LIMIT 1) AS registro_id
                FROM corridas c LEFT JOIN asignaciones a ON a.corrida_id = c.id
                WHERE c.fecha_inicio >= (CURRENT_DATE - CAST(:d AS integer))
                GROUP BY c.id ORDER BY c.fecha_inicio DESC, c.id DESC
            """), {"d": max(1, min(dias, 400))})
            return [{**dict(r._mapping), "kg": _f(r.kg)} for r in res]

    @router.get("/api/emulsion/cilindros")
    def cilindros(estado: Optional[str] = None, limite: int = 0):
        est = (estado or "").upper() or None
        if est and est not in ("PRECAMARA", "PROCESADO"):
            raise HTTPException(status_code=400, detail="Estado no válido.")
        with engine.connect() as conn:
            return _lista_cilindros(conn, est, limite or None)

    @router.post("/api/emulsion")
    def registrar_emulsion(p: NuevaEmulsion):
        fecha = _fecha_valida(p.fecha)
        if not (1 <= p.n_cilindros <= 60):
            raise HTTPException(status_code=400, detail="El número de cilindros debe estar entre 1 y 60.")
        if not (p.peso_total_kg and p.peso_total_kg > 0):
            raise HTTPException(status_code=400, detail="Indique el peso neto total de la emulsión.")
        corrida_ids = sorted(set(p.corrida_ids))
        if not corrida_ids and not (p.mp_kg_manual and p.mp_kg_manual > 0):
            raise HTTPException(status_code=400, detail="Seleccione las corridas de origen o indique la MP procesada.")
        total = _d(p.peso_total_kg).quantize(_CENT, ROUND_HALF_UP)
        base = (total / p.n_cilindros).quantize(_CENT, ROUND_HALF_UP)
        pesos = [base] * (p.n_cilindros - 1) + [total - base * (p.n_cilindros - 1)]   # el último absorbe el redondeo
        with engine.begin() as conn:
            conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LOCK_NUMERACION})
            if corrida_ids:
                existentes = {r[0] for r in conn.execute(text("SELECT id FROM corridas WHERE id = ANY(:ids)"), {"ids": corrida_ids})}
                if existentes != set(corrida_ids):
                    raise HTTPException(status_code=400, detail="Alguna de las corridas indicadas no existe.")
                ya = conn.execute(text("""
                    SELECT co.nombre FROM emulsion_registro_corridas rc JOIN corridas co ON co.id = rc.corrida_id
                    WHERE rc.corrida_id = ANY(:ids) LIMIT 1
                """), {"ids": corrida_ids}).scalar()
                if ya:
                    raise HTTPException(status_code=400, detail=f"La corrida «{ya}» ya está vinculada a otro registro de emulsión.")
            rid = conn.execute(text("""
                INSERT INTO emulsion_registros (fecha, mp_kg_manual, observaciones) VALUES (:f, :mp, :o) RETURNING id
            """), {"f": fecha, "mp": p.mp_kg_manual if (p.mp_kg_manual and p.mp_kg_manual > 0) else None,
                   "o": (p.observaciones or "").strip() or None}).scalar()
            for cid in corrida_ids:
                conn.execute(text("INSERT INTO emulsion_registro_corridas (registro_id, corrida_id) VALUES (:r, :c)"), {"r": rid, "c": cid})
            sig = conn.execute(text("SELECT COALESCE(MAX(numero), 0) + 1 FROM emulsion_cilindros")).scalar()
            for i, peso in enumerate(pesos):
                conn.execute(text("""
                    INSERT INTO emulsion_cilindros (numero, registro_id, fecha, peso_neto_kg) VALUES (:n, :r, :f, :p)
                """), {"n": sig + i, "r": rid, "f": fecha, "p": peso})
        return {"ok": True, "id": rid, "cilindros": [_codigo_cilindro(sig + i) for i in range(p.n_cilindros)],
                "peso_por_cilindro_kg": float(base)}

    @router.delete("/api/emulsion/registros/{registro_id}")
    def eliminar_registro(registro_id: int):
        with engine.begin() as conn:
            if conn.execute(text("SELECT 1 FROM emulsion_registros WHERE id = :i"), {"i": registro_id}).scalar() is None:
                raise HTTPException(status_code=404, detail="Registro de emulsión no encontrado.")
            usados = conn.execute(text("SELECT count(*) FROM emulsion_cilindros WHERE registro_id = :i AND transformacion_id IS NOT NULL"),
                                  {"i": registro_id}).scalar()
            con_aceite = conn.execute(text("SELECT count(*) FROM aceite_barril_aportes WHERE registro_id = :i"), {"i": registro_id}).scalar()
            if usados or con_aceite:
                raise HTTPException(status_code=400, detail="No se puede eliminar: ya tiene cilindros transformados a aceite. Elimine primero esa transformación.")
            conn.execute(text("DELETE FROM emulsion_registros WHERE id = :i"), {"i": registro_id})
        return {"ok": True}

    @router.post("/api/emulsion/registros/{registro_id}")
    def editar_registro(registro_id: int, p: EditarRegistro):
        motivo = (p.diferencia_motivo or "").strip().lower() or None
        if motivo not in (None, "descuento", "precc"):
            raise HTTPException(status_code=400, detail="Motivo de diferencia no válido.")
        with engine.begin() as conn:
            r = conn.execute(text("UPDATE emulsion_registros SET diferencia_motivo = :m WHERE id = :i RETURNING id"), {"m": motivo, "i": registro_id})
            if r.scalar() is None:
                raise HTTPException(status_code=404, detail="Registro de emulsión no encontrado.")
        return {"ok": True}

    @router.get("/api/aceite/barriles")
    def barriles(ubicacion: Optional[str] = None):
        with engine.connect() as conn:
            return _lista_barriles(conn, (ubicacion or "").strip() or None)

    @router.post("/api/aceite/barriles/{barril_id}")
    def editar_barril(barril_id: int, p: EditarBarril):
        campos = p.model_dump(exclude_unset=True)
        sets, params = [], {"id": barril_id}
        if "ubicacion" in campos:
            sets.append("ubicacion = :u")
            params["u"] = (campos["ubicacion"] or "").strip() or None
        if "nisira" in campos and campos["nisira"] is not None:
            sets.append("nisira = :n")
            params["n"] = campos["nisira"]
        if not sets:
            raise HTTPException(status_code=400, detail="No hay nada que actualizar.")
        with engine.begin() as conn:
            r = conn.execute(text(f"UPDATE aceite_barriles SET {', '.join(sets)} WHERE id = :id RETURNING id"), params)
            if r.scalar() is None:
                raise HTTPException(status_code=404, detail="Barril no encontrado.")
        return {"ok": True}

    @router.post("/api/aceite/transformaciones")
    def registrar_transformacion(p: NuevaTransformacion):
        fecha = _fecha_valida(p.fecha)
        if not (p.aceite_kg and p.aceite_kg > 0):
            raise HTTPException(status_code=400, detail="Indique los kg de aceite obtenidos.")
        ids = sorted(set(p.cilindro_ids))
        if not ids:
            raise HTTPException(status_code=400, detail="Seleccione al menos un cilindro de emulsión.")
        aceite = _d(p.aceite_kg).quantize(_CENT, ROUND_HALF_UP)
        with engine.begin() as conn:
            conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LOCK_NUMERACION})
            cils = conn.execute(text("SELECT id, numero, transformacion_id, peso_neto_kg FROM emulsion_cilindros WHERE id = ANY(:ids) FOR UPDATE"),
                                {"ids": ids}).fetchall()
            if len(cils) != len(ids):
                raise HTTPException(status_code=400, detail="Alguno de los cilindros indicados no existe.")
            ya = [c.numero for c in cils if c.transformacion_id is not None]
            if ya:
                raise HTTPException(status_code=400, detail=f"El cilindro {_codigo_cilindro(ya[0])} ya fue transformado a aceite.")
            tid = conn.execute(text("""
                INSERT INTO aceite_transformaciones (fecha, aceite_kg, observaciones) VALUES (:f, :a, :o) RETURNING id
            """), {"f": fecha, "a": aceite, "o": (p.observaciones or "").strip() or None}).scalar()
            conn.execute(text("UPDATE emulsion_cilindros SET transformacion_id = :t WHERE id = ANY(:ids)"), {"t": tid, "ids": ids})
            aportes = _asignar_a_barriles(conn, tid, _repartir_por_registro(conn, ids, aceite), p.codigo_inicial)
        emulsion = sum(float(c.peso_neto_kg or 0) for c in cils)
        return {"ok": True, "id": tid, "aportes": aportes, "emulsion_kg": emulsion,
                "conversion": (float(aceite) / emulsion) if emulsion else None}

    @router.delete("/api/aceite/transformaciones/{transformacion_id}")
    def eliminar_transformacion(transformacion_id: int):
        """Solo se puede deshacer la última transformación: los barriles se llenan en
        orden, así que quitar una anterior obligaría a reacomodar todos los siguientes."""
        with engine.begin() as conn:
            conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LOCK_NUMERACION})
            ultima = conn.execute(text("SELECT id FROM aceite_transformaciones ORDER BY id DESC LIMIT 1")).scalar()
            if ultima is None:
                raise HTTPException(status_code=404, detail="Transformación no encontrada.")
            if ultima != transformacion_id:
                raise HTTPException(status_code=400, detail="Solo se puede deshacer la última transformación registrada.")
            conn.execute(text("DELETE FROM aceite_transformaciones WHERE id = :i"), {"i": transformacion_id})  # aportes se borran en cascada; los cilindros vuelven a precámara
            conn.execute(text("""
                DELETE FROM aceite_barriles b WHERE NOT EXISTS (SELECT 1 FROM aceite_barril_aportes ap WHERE ap.barril_id = b.id)
            """))
        return {"ok": True}

    @router.get("/api/subproductos/transformaciones")
    def transformaciones():
        """Lista de transformaciones (la más reciente primero) - la pantalla solo deja deshacer la última."""
        with engine.connect() as conn:
            res = conn.execute(text("""
                SELECT t.id, t.fecha, t.aceite_kg, count(c.id) AS n_cilindros, COALESCE(sum(c.peso_neto_kg), 0) AS emulsion_kg
                FROM aceite_transformaciones t LEFT JOIN emulsion_cilindros c ON c.transformacion_id = t.id
                GROUP BY t.id ORDER BY t.id DESC LIMIT 15
            """))
            return [{**dict(r._mapping), "aceite_kg": _f(r.aceite_kg), "emulsion_kg": _f(r.emulsion_kg)} for r in res]

    @router.get("/api/subproductos/export.xlsx")
    def exportar():
        with engine.connect() as conn:
            regs = _lista_registros(conn)
            data = generar_subproductos_xlsx(
                registros=regs, cilindros=_lista_cilindros(conn), barriles=_lista_barriles(conn), kpis=_kpis(conn, regs))
        return Response(
            content=data,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": 'attachment; filename="subproductos.xlsx"'},
        )

    return router
