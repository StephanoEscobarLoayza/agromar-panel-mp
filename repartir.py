"""Reparto de lotes entre corridas que trabajan en paralelo.

Cuando dos corridas (p. ej. JSA y JCC) corren al mismo tiempo con los mismos lotes,
los lotes se registran juntos y después se reparten entre las corridas según los
tanques que llenó cada línea. El cálculo se hace en la pantalla (web/repartir.html);
acá se entregan los datos y se aplica el resultado, validando que cada lote conserve
exactamente los mismos kg y bines en total (el saldo del lote no cambia).

El PRE CC (lotes derivados, número negativo) no pasa por extracción: no entra al
reparto y se queda en la corrida donde está.

Se monta desde api.py con `app.include_router(crear_router(engine, sync_pre_cc, ordenar_grupo))`.
"""
import statistics
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

_CENT = Decimal("0.01")
_NOTA = "Repartido entre corridas en paralelo"


def _f(v):
    return float(v) if v is not None else None


def _ids(cadena: str) -> List[int]:
    try:
        ids = sorted({int(x) for x in cadena.split(",") if x.strip()})
    except ValueError:
        raise HTTPException(status_code=400, detail="Las corridas indicadas no son válidas.")
    if len(ids) < 2:
        raise HTTPException(status_code=400, detail="Indique al menos dos corridas.")
    if len(ids) > 4:
        raise HTTPException(status_code=400, detail="Se pueden repartir hasta 4 corridas a la vez.")
    return ids


# producto terminado neto de una corrida: salidas que cuentan como PT menos los insumos de entrada que descuentan
_SQL_PT = """
    COALESCE((SELECT SUM(CASE WHEN p.tipo = 'salida' AND p.cuenta_como_pt THEN p.pt_kg
                              WHEN p.tipo = 'entrada' AND p.cuenta_como_pt THEN -p.pt_kg ELSE 0 END)
              FROM corrida_productos p WHERE p.corrida_id = c.id AND p.pt_kg IS NOT NULL), 0)
"""


def _rendimientos_tipicos(conn):
    """Mediana histórica del rendimiento (PT / MP) por tipo de proceso."""
    filas = conn.execute(text(f"""
        SELECT c.tipo_proceso AS tipo,
               COALESCE((SELECT SUM(a.kg_asignados) FROM asignaciones a WHERE a.corrida_id = c.id), 0) AS mp,
               {_SQL_PT} AS pt
        FROM corridas c WHERE c.tipo_proceso IS NOT NULL
    """)).fetchall()
    por_tipo: Dict[str, list] = {}
    for f in filas:
        mp, pt = float(f.mp), float(f.pt)
        if mp > 0 and pt > 0 and pt / mp < 1:        # se descartan registros claramente descuadrados
            por_tipo.setdefault(f.tipo, []).append(pt / mp)
    return {t: statistics.median(v) for t, v in por_tipo.items() if len(v) >= 3}


def aplicar_reparto(conn, corrida_ids: List[int], reparto: Dict[int, Dict[int, Decimal]], sync_pre_cc=None):
    """Aplica el reparto dentro de la transacción `conn`.
    reparto: {lote_numero: {corrida_id: kg}}. Devuelve un resumen."""
    corridas = conn.execute(text("SELECT id, tipo_proceso FROM corridas WHERE id = ANY(:ids)"), {"ids": corrida_ids}).fetchall()
    if len(corridas) != len(corrida_ids):
        raise HTTPException(status_code=400, detail="Alguna de las corridas indicadas no existe.")

    filas = conn.execute(text("""
        SELECT id, lote_numero, corrida_id, fecha_proceso, turno, tipo_almacen_origen, kg_asignados,
               bines_consumidos, brix_produccion, porcentaje_descuento, observaciones, usuario
        FROM asignaciones
        WHERE corrida_id = ANY(:ids) AND lote_numero > 0
        ORDER BY id FOR UPDATE
    """), {"ids": corrida_ids}).fetchall()
    por_lote: Dict[int, list] = {}
    for f in filas:
        por_lote.setdefault(f.lote_numero, []).append(f)
    # las filas con kg pendiente cuentan como cero y el reparto las llena; un lote sin ningún kg en estas corridas se omite
    # si sus kg están en otra corrida, y bloquea si no tiene kg en ninguna parte
    sin_kg = [l for l, fs in por_lote.items() if all(f.kg_asignados is None for f in fs)]
    if sin_kg:
        en_otras = {r.lote_numero for r in conn.execute(text("""
            SELECT DISTINCT lote_numero FROM asignaciones
            WHERE lote_numero = ANY(:n) AND kg_asignados IS NOT NULL AND NOT (corrida_id = ANY(:ids))
        """), {"n": sin_kg, "ids": corrida_ids})}
        sin_ninguno = sorted(set(sin_kg) - en_otras)
        if sin_ninguno:
            nums = ", ".join(str(l) for l in sin_ninguno)
            raise HTTPException(status_code=400, detail=f"{'El lote' if len(sin_ninguno) == 1 else 'Los lotes'} {nums} {'tiene' if len(sin_ninguno) == 1 else 'tienen'} kg pendiente: complételo en «Registrar consumo» antes de repartir.")
        for l in sin_kg:
            del por_lote[l]
    # lotes que todavía tienen saldo: su marca de "pendiente" en una corrida se conserva si esa corrida no recibe kg
    abiertos = {r.numero for r in conn.execute(text("SELECT numero FROM v_saldo_lotes WHERE numero = ANY(:n) AND kg_saldo > 0"), {"n": list(por_lote)})} if por_lote else set()

    if set(reparto) != set(por_lote):
        faltan = sorted(set(por_lote) - set(reparto))
        sobran = sorted(set(reparto) - set(por_lote))
        raise HTTPException(status_code=400, detail=f"El reparto no coincide con los lotes registrados (faltan {faltan}, sobran {sobran}).")

    bajadas, subidas = [], []      # primero se reduce y se borra, después se aumenta o se inserta: el saldo nunca se excede a medias
    partidos = 0
    for lote, filas_lote in por_lote.items():
        total_viejo = sum(Decimal(f.kg_asignados) for f in filas_lote if f.kg_asignados is not None)
        nuevo = {int(c): Decimal(str(k)).quantize(_CENT, ROUND_HALF_UP) for c, k in reparto[lote].items()}
        if any(c not in corrida_ids for c in nuevo) or any(k < 0 for k in nuevo.values()):
            raise HTTPException(status_code=400, detail=f"El reparto del lote {lote} no es válido.")
        total_nuevo = sum(nuevo.values())
        if abs(total_nuevo - total_viejo) > Decimal("0.02"):
            raise HTTPException(status_code=400, detail=f"El lote {lote} no conserva sus kg: tenía {total_viejo} y el reparto suma {total_nuevo}.")
        # el redondeo lo absorbe la corrida que más recibe, para que el total quede exacto
        mayor = max(nuevo, key=lambda c: nuevo[c])
        nuevo[mayor] += total_viejo - total_nuevo
        if sum(1 for k in nuevo.values() if k > 0) > 1:
            partidos += 1

        # bines: se reparten en enteros, en proporción a los kg y conservando el total
        bines_total = sum(f.bines_consumidos or 0 for f in filas_lote)
        bines = {c: 0 for c in nuevo}
        if bines_total > 0 and total_viejo > 0:
            exacto = {c: Decimal(bines_total) * k / total_viejo for c, k in nuevo.items()}
            bines = {c: int(v) for c, v in exacto.items()}
            resto = bines_total - sum(bines.values())
            for c in sorted(nuevo, key=lambda c: exacto[c] - bines[c], reverse=True)[:resto]:
                bines[c] += 1

        plantilla = next(f for f in filas_lote if f.kg_asignados is not None)
        for c, kg in nuevo.items():
            existentes = sorted((f for f in filas_lote if f.corrida_id == c), key=lambda f: f.kg_asignados is None)      # primero la que tiene kg
            if kg <= 0:
                for f in existentes:
                    if f.kg_asignados is None and lote in abiertos:
                        continue
                    bajadas.append(("DELETE FROM asignaciones WHERE id = :id", {"id": f.id}))
                continue
            if existentes:
                principal = existentes[0]
                for extra in existentes[1:]:
                    bajadas.append(("DELETE FROM asignaciones WHERE id = :id", {"id": extra.id}))
                obs = principal.observaciones or ""
                obs = obs if _NOTA in obs else (obs + " | " if obs else "") + _NOTA
                sql = "UPDATE asignaciones SET kg_asignados = :kg, bines_consumidos = :b, observaciones = :o WHERE id = :id"
                par = {"kg": kg, "b": (bines[c] if bines_total > 0 else None), "o": obs, "id": principal.id}
                (bajadas if principal.kg_asignados is not None and kg < Decimal(principal.kg_asignados) else subidas).append((sql, par))
            else:
                obs = plantilla.observaciones or ""
                obs = obs if _NOTA in obs else (obs + " | " if obs else "") + _NOTA
                subidas.append(("""
                    INSERT INTO asignaciones (lote_numero, corrida_id, fecha_proceso, turno, tipo_almacen_origen, kg_asignados,
                                              bines_consumidos, brix_produccion, porcentaje_descuento, observaciones, usuario)
                    VALUES (:lote, :c, :fp, :t, :ta, :kg, :b, :bx, :pd, :o, :u)
                """, {"lote": lote, "c": c, "fp": plantilla.fecha_proceso, "t": plantilla.turno, "ta": plantilla.tipo_almacen_origen,
                      "kg": kg, "b": (bines[c] if bines_total > 0 else None), "bx": plantilla.brix_produccion,
                      "pd": plantilla.porcentaje_descuento, "o": obs, "u": plantilla.usuario}))
    for sql, par in bajadas + subidas:
        conn.execute(text(sql), par)

    # si alguna de las corridas es PRE CC, el producto derivado de sus lotes hay que recalcularlo
    if sync_pre_cc and any(c.tipo_proceso == "PRE CC" for c in corridas):
        for lote in por_lote:
            sync_pre_cc(conn, lote)
    return {"lotes": len(por_lote), "lotes_partidos": partidos}


class AplicarReparto(BaseModel):
    corridas: List[int]
    reparto: Dict[str, Dict[str, float]]          # {"2506": {"117": 11180.5, "119": 4019.5}}


def crear_router(engine, sync_pre_cc=None, ordenar_grupo=None) -> APIRouter:
    router = APIRouter()

    @router.get("/api/repartir/datos")
    def datos(corridas: str):
        ids = _ids(corridas)
        with engine.connect() as conn:
            cor = conn.execute(text(f"""
                SELECT c.id, c.nombre, c.tipo_proceso, c.fecha_inicio, {_SQL_PT} AS pt
                FROM corridas c WHERE c.id = ANY(:ids) ORDER BY c.fecha_inicio, c.id
            """), {"ids": ids}).fetchall()
            if len(cor) != len(ids):
                raise HTTPException(status_code=404, detail="Alguna de las corridas indicadas no existe.")
            asig = conn.execute(text("""
                SELECT a.corrida_id, a.lote_numero, a.kg_asignados, a.bines_consumidos, a.creado_en,
                       l.proveedor, l.tipo_almacen
                FROM asignaciones a JOIN lotes l ON l.numero = a.lote_numero
                WHERE a.corrida_id = ANY(:ids) ORDER BY a.creado_en, a.id
            """), {"ids": ids}).fetchall()
            tanques = conn.execute(text("""
                SELECT m.id, m.corrida_id, m.tanque, m.litros, m.creado_en
                FROM mediciones_tanque m WHERE m.corrida_id = ANY(:ids) ORDER BY m.creado_en, m.id
            """), {"ids": ids}).fetchall()
            tipicos = _rendimientos_tipicos(conn)
            # lotes que alimentaron cada tanque (se anotan en «Medición de tanques»); la tabla se crea sola al usarla por primera vez
            lotes_tanque: Dict[int, List[int]] = {}
            if tanques and conn.execute(text("SELECT to_regclass('public.tanque_lotes')")).scalar():
                for r in conn.execute(text("SELECT medicion_id, lote_numero FROM tanque_lotes WHERE medicion_id = ANY(:m) ORDER BY lote_numero"),
                                      {"m": [t.id for t in tanques]}):
                    lotes_tanque.setdefault(r.medicion_id, []).append(r.lote_numero)

            # Un lote con kg en alguna de las corridas marcadas y "pendiente" en otra no bloquea el reparto: el reparto llena
            # la parte pendiente. Un lote sin kg en ninguna de las marcadas se omite si sus kg están en otra corrida
            # (se avisa), y solo bloquea si no tiene kg en ninguna parte.
            con_kg = {a.lote_numero for a in asig if a.kg_asignados is not None and a.lote_numero > 0}
            sin_kg = sorted({a.lote_numero for a in asig if a.kg_asignados is None} - con_kg)
            en_otras: Dict[int, List[str]] = {}
            if sin_kg:
                for f in conn.execute(text("""
                    SELECT a.lote_numero, c.nombre FROM asignaciones a JOIN corridas c ON c.id = a.corrida_id
                    WHERE a.lote_numero = ANY(:n) AND a.kg_asignados IS NOT NULL AND NOT (a.corrida_id = ANY(:ids))
                    GROUP BY a.lote_numero, c.id, c.nombre, c.fecha_inicio ORDER BY c.fecha_inicio, c.id
                """), {"n": sin_kg, "ids": ids}):
                    en_otras.setdefault(f.lote_numero, []).append(f.nombre)
            en_proceso = []
            if con_kg:
                en_proceso = [f.numero for f in conn.execute(text(
                    "SELECT numero FROM v_saldo_lotes WHERE numero = ANY(:n) AND kg_saldo > 0 ORDER BY numero"), {"n": sorted(con_kg)})]

        precc: Dict[int, float] = {}
        lotes: Dict[int, dict] = {}
        for a in asig:
            if a.kg_asignados is None:
                continue
            if a.lote_numero < 0:                              # PRE CC: producto, no pasa por extracción
                precc[a.corrida_id] = precc.get(a.corrida_id, 0.0) + float(a.kg_asignados)
                continue
            l = lotes.setdefault(a.lote_numero, {"numero": a.lote_numero, "proveedor": a.proveedor, "almacen": a.tipo_almacen,
                                                 "kg": 0.0, "bines": 0, "orden": a.creado_en.isoformat(), "actual": {}})
            l["kg"] += float(a.kg_asignados)
            l["bines"] += a.bines_consumidos or 0
            l["actual"][str(a.corrida_id)] = l["actual"].get(str(a.corrida_id), 0.0) + float(a.kg_asignados)
        mp_por_corrida: Dict[int, float] = {}
        for a in asig:
            if a.kg_asignados is not None:
                mp_por_corrida[a.corrida_id] = mp_por_corrida.get(a.corrida_id, 0.0) + float(a.kg_asignados)

        return {
            "corridas": [{
                "id": c.id, "nombre": c.nombre, "tipo_proceso": c.tipo_proceso, "fecha_inicio": c.fecha_inicio,
                "pt_kg": float(c.pt), "mp_kg": mp_por_corrida.get(c.id, 0.0), "precc_kg": precc.get(c.id, 0.0),
                "tipico": tipicos.get(c.tipo_proceso),
                "tanques": [{"id": t.id, "tanque": t.tanque, "litros": _f(t.litros), "registrado": t.creado_en.isoformat(),
                             "lotes": lotes_tanque.get(t.id, [])}
                            for t in tanques if t.corrida_id == c.id],
            } for c in cor],
            "lotes": sorted(lotes.values(), key=lambda x: (x["orden"], x["numero"])),
            "pendientes": [l for l in sin_kg if l not in en_otras],
            "omitidos": [{"lote": l, "en": en_otras[l]} for l in sin_kg if l in en_otras],
            "en_proceso": en_proceso,
        }

    @router.post("/api/repartir/aplicar")
    def aplicar(p: AplicarReparto):
        ids = sorted(set(p.corridas))
        if not (2 <= len(ids) <= 4):
            raise HTTPException(status_code=400, detail="Indique entre dos y cuatro corridas.")
        try:
            reparto = {int(l): {int(c): Decimal(str(k)) for c, k in d.items()} for l, d in p.reparto.items()}
        except Exception:
            raise HTTPException(status_code=400, detail="El reparto enviado no es válido.")
        with engine.begin() as conn:
            res = aplicar_reparto(conn, ids, reparto, sync_pre_cc)
            if ordenar_grupo:       # las corridas quedan en la lista con la JSA/JCC al final (misma secuencia para el Excel y el PDF)
                ordenar_grupo(conn, ids)
        return {"ok": True, **res}

    return router
