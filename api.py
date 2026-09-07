"""
API del panel de cuadre - Agromar. Sirve tanto los endpoints JSON (/api/...)
como las páginas estáticas del front-end (carpeta web/), en un solo servicio
con una sola URL para compartir.

Correr local:
    pip install -r requirements.txt
    python -m uvicorn api:app --reload --port 8000
"""
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import create_engine, text

from sync_lotes import sincronizar_lotes

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

DATABASE_URL = os.environ.get("DATABASE_URL_POOLED") or os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("Falta DATABASE_URL o DATABASE_URL_POOLED en .env")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)

app = FastAPI(title="Panel de cuadre - Agromar")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


def rows(result):
    return [dict(r._mapping) for r in result]


def one(result):
    row = result.fetchone()
    return dict(row._mapping) if row else None


# ---------------------------------------------------------------------------
# lotes
# ---------------------------------------------------------------------------
@app.get("/api/lotes")
def listar_lotes(q: Optional[str] = None):
    with engine.connect() as conn:
        if q:
            result = conn.execute(
                text(
                    """
                    SELECT * FROM v_saldo_lotes
                    WHERE CAST(numero AS TEXT) ILIKE :q OR proveedor ILIKE :q OR procedencia ILIKE :q
                    ORDER BY numero DESC
                    """
                ),
                {"q": f"%{q}%"},
            )
        else:
            result = conn.execute(text("SELECT * FROM v_saldo_lotes ORDER BY numero DESC"))
        return rows(result)


def _cumple_mezcla(brix_pond, acidez_pond, kg_acum, brix_lote, acidez_lote, x, brix_min, ratio_min):
    kg = kg_acum + x
    if kg <= 0:
        return False
    bp = brix_pond + brix_lote * x
    ap = acidez_pond + acidez_lote * x
    if ap <= 0:
        return False
    return (bp / kg) >= brix_min and (bp / ap) >= ratio_min


def _kg_minimo(brix_pond, acidez_pond, kg_acum, brix_lote, acidez_lote, kg_max, brix_min, ratio_min):
    """Cuánto de ESTE lote (entre 0 y su saldo kg_max) hace falta agregar a lo
    ya acumulado para llegar al Brix/Ratio mínimo - no siempre hace falta el
    lote completo. Tanto el Brix como el Ratio de una mezcla son funciones
    monótonas de x (cociente de dos funciones lineales), así que una búsqueda
    binaria simple encuentra el mínimo exacto sin álgebra propensa a errores."""
    if not _cumple_mezcla(brix_pond, acidez_pond, kg_acum, brix_lote, acidez_lote, kg_max, brix_min, ratio_min):
        return None  # ni con el lote completo alcanza
    if kg_acum == 0:
        # no hay nada acumulado todavia con que mezclar - el Brix/Ratio de
        # "una parte" de este lote es igual al del lote entero (es puro), asi
        # que no existe una fraccion "minima" con sentido: se usa completo.
        return kg_max
    lo, hi = 0.0, kg_max
    for _ in range(60):
        mid = (lo + hi) / 2
        if _cumple_mezcla(brix_pond, acidez_pond, kg_acum, brix_lote, acidez_lote, mid, brix_min, ratio_min):
            hi = mid
        else:
            lo = mid
    return hi


MAX_BINES_AJUSTE = 2  # en planta no se mete mas de 2 lotes de bines de ajuste sobre el silo


def _lote_a_dict(l):
    return {
        "numero": l["numero"],
        "proveedor": l["proveedor"],
        "fecha_ingreso": l["fecha_ingreso"],
        "tipo_almacen": l["tipo_almacen"],
        "brix_lote": float(l["brix_recepcion"]),
        "acidez_lote": float(l["acidez"]),
        "ratio_lote": float(l["ratio"]) if l["ratio"] is not None else None,
        "kg_disponible": float(l["kg_saldo"]),
    }


def _paso_mezcla(l, kg_usado, es_parcial, kg_acum, brix_pond, acidez_pond):
    brix_mezcla = brix_pond / kg_acum
    acidez_mezcla = acidez_pond / kg_acum
    ratio_mezcla = brix_mezcla / acidez_mezcla if acidez_mezcla > 0 else None
    paso = _lote_a_dict(l)
    paso.update({
        "kg_usado": round(kg_usado, 2),
        "es_parcial": es_parcial,
        "kg_acumulado": round(kg_acum, 2),
        "brix_mezcla": round(brix_mezcla, 2),
        "acidez_mezcla": round(acidez_mezcla, 3),
        "ratio_mezcla": round(ratio_mezcla, 2) if ratio_mezcla is not None else None,
    })
    return paso


def _ajustar_con_bines(bines, kg_acum, brix_pond, acidez_pond, brix_min, ratio_min, ya_cumple):
    """Agrega lotes de bines de a uno (máximo MAX_BINES_AJUSTE) sobre lo ya
    acumulado, cortando el último a la fracción exacta que hace falta - así
    es como de verdad se ajusta en planta, entrando un bin-lote a la vez."""
    pasos_bines = []
    cumplido = ya_cumple
    for l in bines:
        if cumplido or len(pasos_bines) >= MAX_BINES_AJUSTE:
            break
        kg_disponible = float(l["kg_saldo"])
        brix_lote = float(l["brix_recepcion"])
        acidez_lote = float(l["acidez"])
        kg_parcial = _kg_minimo(brix_pond, acidez_pond, kg_acum, brix_lote, acidez_lote, kg_disponible, brix_min, ratio_min)
        kg_usado = kg_parcial if kg_parcial is not None else kg_disponible
        kg_acum += kg_usado
        brix_pond += brix_lote * kg_usado
        acidez_pond += acidez_lote * kg_usado
        es_parcial = kg_parcial is not None and kg_parcial < kg_disponible - 0.005
        pasos_bines.append(_paso_mezcla(l, kg_usado, es_parcial, kg_acum, brix_pond, acidez_pond))
        if kg_parcial is not None:
            cumplido = True
    return pasos_bines, cumplido


@app.get("/api/lotes/sugerir-mezcla")
def sugerir_mezcla(brix_min: float, ratio_min: float):
    """El silo es la base fija - siempre se está alimentando de ahí, no es
    una opción a elegir - y los bines son el ajuste que se agrega encima, de
    a uno, hasta un máximo de 2 (así se mete en planta). Solo se consideran
    lotes 'EN ESPERA' o, para el silo, el que ya esté 'EN PROCESO' (el que
    de verdad está alimentando en este momento):
    - Si hay un lote de Silo EN PROCESO -> esa es la base, sin ambigüedad.
    - Si no, y hay un solo lote de Silo EN ESPERA -> se usa ese.
    - Si no, y hay 2+ lotes de Silo EN ESPERA -> no se sabe cuál arranca
      primero, así que se devuelve una OPCIÓN por cada uno (no se adivina).
    - Si no hay ningún lote de Silo disponible -> se resuelve solo con bines.
    Sobre esa base (la que sea), se agregan bines EN ESPERA de a uno, máximo
    2, cortando el último a la fracción exacta que hace falta."""
    with engine.connect() as conn:
        silo_en_proceso = rows(conn.execute(text(
            """
            SELECT numero, proveedor, fecha_ingreso, tipo_almacen, kg_saldo, brix_recepcion, acidez, ratio
            FROM v_saldo_lotes
            WHERE kg_saldo > 0 AND brix_recepcion IS NOT NULL AND acidez IS NOT NULL AND acidez > 0
                  AND tipo_almacen = 'SILO' AND UPPER(TRIM(estado_actual)) = 'EN PROCESO'
            ORDER BY fecha_ingreso ASC, numero ASC
            """
        )))
        silo_en_espera = rows(conn.execute(text(
            """
            SELECT numero, proveedor, fecha_ingreso, tipo_almacen, kg_saldo, brix_recepcion, acidez, ratio
            FROM v_saldo_lotes
            WHERE kg_saldo > 0 AND brix_recepcion IS NOT NULL AND acidez IS NOT NULL AND acidez > 0
                  AND tipo_almacen = 'SILO' AND UPPER(TRIM(estado_actual)) = 'EN ESPERA'
            ORDER BY fecha_ingreso ASC, numero ASC
            """
        )))
        bines = rows(conn.execute(text(
            """
            SELECT numero, proveedor, fecha_ingreso, tipo_almacen, kg_saldo, brix_recepcion, acidez, ratio
            FROM v_saldo_lotes
            WHERE kg_saldo > 0 AND brix_recepcion IS NOT NULL AND acidez IS NOT NULL AND acidez > 0
                  AND tipo_almacen = 'BINES' AND UPPER(TRIM(estado_actual)) = 'EN ESPERA'
            ORDER BY fecha_ingreso ASC, numero ASC
            """
        )))

    def escenario(silo_base):
        """silo_base: un lote de silo (dict de la consulta) o None."""
        if silo_base is None:
            kg_acum = brix_pond = acidez_pond = 0.0
            ya_cumple = False
            silo_info = None
        else:
            kg_acum = float(silo_base["kg_saldo"])
            brix_pond = float(silo_base["brix_recepcion"]) * kg_acum
            acidez_pond = float(silo_base["acidez"]) * kg_acum
            ya_cumple = _cumple_mezcla(0, 0, 0, float(silo_base["brix_recepcion"]), float(silo_base["acidez"]), kg_acum, brix_min, ratio_min)
            silo_info = _paso_mezcla(silo_base, kg_acum, False, kg_acum, brix_pond, acidez_pond)
        pasos_bines, cumplido = _ajustar_con_bines(bines, kg_acum, brix_pond, acidez_pond, brix_min, ratio_min, ya_cumple)
        return {"silo_base": silo_info, "pasos_bines": pasos_bines, "cumplido": cumplido}

    if silo_en_proceso:
        # puede (raro) haber mas de un lote de silo en proceso a la vez -
        # se toman todos juntos como una sola base, ya que ambos estan
        # alimentando de verdad ahora mismo
        kg_acum = brix_pond = acidez_pond = 0.0
        silo_info = None
        for l in silo_en_proceso:
            kg = float(l["kg_saldo"])
            kg_acum += kg
            brix_pond += float(l["brix_recepcion"]) * kg
            acidez_pond += float(l["acidez"]) * kg
            silo_info = _paso_mezcla(l, kg, False, kg_acum, brix_pond, acidez_pond) if silo_info is None else silo_info
        # si hay mas de uno, se guarda el detalle de cada uno para mostrarlos todos
        silo_bases_multiples = [_paso_mezcla(l, float(l["kg_saldo"]), False, float(l["kg_saldo"]),
                                              float(l["brix_recepcion"]) * float(l["kg_saldo"]),
                                              float(l["acidez"]) * float(l["kg_saldo"])) for l in silo_en_proceso]
        ya_cumple = kg_acum > 0 and (brix_pond / kg_acum) >= brix_min and acidez_pond > 0 and (brix_pond / acidez_pond) >= ratio_min
        pasos_bines, cumplido = _ajustar_con_bines(bines, kg_acum, brix_pond, acidez_pond, brix_min, ratio_min, ya_cumple)
        return {
            "modo": "unico",
            "origen_base": "en_proceso",
            "escenarios": [{"silo_base": silo_bases_multiples, "pasos_bines": pasos_bines, "cumplido": cumplido}],
        }

    if len(silo_en_espera) == 0:
        e = escenario(None)
        return {"modo": "unico", "origen_base": "sin_silo", "escenarios": [{"silo_base": None, "pasos_bines": e["pasos_bines"], "cumplido": e["cumplido"]}]}

    if len(silo_en_espera) == 1:
        e = escenario(silo_en_espera[0])
        return {"modo": "unico", "origen_base": "en_espera_unico", "escenarios": [{"silo_base": [e["silo_base"]], "pasos_bines": e["pasos_bines"], "cumplido": e["cumplido"]}]}

    # 2+ lotes de silo en espera y ninguno en proceso: no se sabe cual va a
    # arrancar primero - se da una opcion independiente por cada uno
    escenarios = []
    for base in silo_en_espera:
        e = escenario(base)
        escenarios.append({"silo_base": [e["silo_base"]], "pasos_bines": e["pasos_bines"], "cumplido": e["cumplido"]})
    return {"modo": "opciones", "origen_base": "en_espera_multiple", "escenarios": escenarios}


def _paso_ya_alimentado(r, kg_usado, kg_acum, brix_pond, acidez_pond):
    brix_mezcla = brix_pond / kg_acum
    acidez_mezcla = acidez_pond / kg_acum
    ratio_mezcla = brix_mezcla / acidez_mezcla if acidez_mezcla > 0 else None
    return {
        "numero": r["lote_numero"],
        "proveedor": r["proveedor"],
        "fecha_ingreso": r["fecha_ingreso"],
        "tipo_almacen": r["tipo_almacen_origen"],
        "brix_lote": float(r["brix_recepcion"]),
        "acidez_lote": float(r["acidez"]),
        "ratio_lote": float(r["ratio"]) if r["ratio"] is not None else None,
        "kg_usado": round(kg_usado, 2),
        "kg_acumulado": round(kg_acum, 2),
        "brix_mezcla": round(brix_mezcla, 2),
        "acidez_mezcla": round(acidez_mezcla, 3),
        "ratio_mezcla": round(ratio_mezcla, 2) if ratio_mezcla is not None else None,
    }


@app.get("/api/corridas/{corrida_id}/sugerir-siguiente-bin")
def sugerir_siguiente_bin(corrida_id: int, brix_min: float, ratio_min: float):
    """Para cuando ya estás a mitad de una corrida y un lote de bines se te
    acaba: a diferencia del sugeridor general (que arranca de cero asumiendo
    que vas a usar el lote de Silo completo), este parte de lo que YA
    registraste de verdad en 'Registrar consumo' para esta corrida (Silo +
    bines ya usados) - ese es el Brix/Ratio acumulado real que ya está
    mezclado en la máquina - y sugiere el/los siguiente(s) bin(es) que hacen
    falta para llegar al mínimo, de a uno, máximo 2, igual que en planta."""
    with engine.connect() as conn:
        corrida = one(conn.execute(text("SELECT id, nombre FROM corridas WHERE id = :id"), {"id": corrida_id}))
        if corrida is None:
            raise HTTPException(status_code=404, detail="Corrida no encontrada.")

        registrado = rows(conn.execute(
            text(
                """
                SELECT a.lote_numero, a.kg_asignados, a.tipo_almacen_origen, a.creado_en,
                       l.proveedor, l.fecha_ingreso, l.brix_recepcion, l.acidez, l.ratio
                FROM asignaciones a
                JOIN lotes l ON l.numero = a.lote_numero
                WHERE a.corrida_id = :c
                ORDER BY a.creado_en ASC
                """
            ),
            {"c": corrida_id},
        ))

        bines = rows(conn.execute(text(
            """
            SELECT numero, proveedor, fecha_ingreso, tipo_almacen, kg_saldo, brix_recepcion, acidez, ratio
            FROM v_saldo_lotes
            WHERE kg_saldo > 0 AND brix_recepcion IS NOT NULL AND acidez IS NOT NULL AND acidez > 0
                  AND tipo_almacen = 'BINES' AND UPPER(TRIM(estado_actual)) = 'EN ESPERA'
            ORDER BY fecha_ingreso ASC, numero ASC
            """
        )))

    kg_acum = brix_pond = acidez_pond = 0.0
    ya_alimentado = []
    lotes_sin_calidad = []
    for r in registrado:
        kg = float(r["kg_asignados"] or 0)
        if kg <= 0:
            continue
        if r["brix_recepcion"] is None or r["acidez"] is None or float(r["acidez"]) <= 0:
            lotes_sin_calidad.append(r["lote_numero"])
            continue
        kg_acum += kg
        brix_pond += float(r["brix_recepcion"]) * kg
        acidez_pond += float(r["acidez"]) * kg
        ya_alimentado.append(_paso_ya_alimentado(r, kg, kg_acum, brix_pond, acidez_pond))

    if kg_acum == 0:
        return {
            "corrida_nombre": corrida["nombre"],
            "tiene_registros": False,
            "lotes_sin_calidad": lotes_sin_calidad,
        }

    ya_cumple = (brix_pond / kg_acum) >= brix_min and acidez_pond > 0 and (brix_pond / acidez_pond) >= ratio_min
    pasos_bines, cumplido = _ajustar_con_bines(bines, kg_acum, brix_pond, acidez_pond, brix_min, ratio_min, ya_cumple)

    return {
        "corrida_nombre": corrida["nombre"],
        "tiene_registros": True,
        "ya_alimentado": ya_alimentado,
        "lotes_sin_calidad": lotes_sin_calidad,
        "brix_actual": round(brix_pond / kg_acum, 2),
        "ratio_actual": round(brix_pond / acidez_pond, 2) if acidez_pond > 0 else None,
        "cumplido_ya": ya_cumple,
        "pasos_bines": pasos_bines,
        "cumplido": cumplido,
    }


@app.get("/api/lotes/{numero}")
def obtener_lote(numero: int):
    with engine.connect() as conn:
        result = conn.execute(text("SELECT * FROM v_saldo_lotes WHERE numero = :n"), {"n": numero})
        lote = one(result)
        if lote is None:
            raise HTTPException(status_code=404, detail=f"El lote {numero} no está en el maestro. Sincronízalo primero.")
        return lote


ESTADOS_VALIDOS = {"PROCESADO", "EN PROCESO", "EN ESPERA"}

# un lote puede estar repartido entre varios sitios a la vez (dos lotes
# combinados en un mismo silo, o un lote entre silo 1 y silo 2) - por eso
# la ubicación se guarda como una combinación de estos, unida con " / ",
# en vez de limitarla a una sola opción de una lista fija.
UBICACIONES_BASE = ["Tolva", "Silo 1", "Silo 2", "Bines"]


def ubicacion_es_valida(valor: str) -> bool:
    partes = [p.strip() for p in valor.split("/")]
    return bool(partes) and len(set(partes)) == len(partes) and all(p in UBICACIONES_BASE for p in partes)


class ActualizarEstadoLote(BaseModel):
    estado: Optional[str] = None  # None = volver a usar el que trae el Sheet


@app.post("/api/lotes/{numero}/estado")
def actualizar_estado_lote(numero: int, body: ActualizarEstadoLote):
    """Override manual de producción sobre el estado del lote, para cuando
    Calidad todavía no actualizó el Sheet. Mandar estado=null vuelve a usar
    el valor del Sheet."""
    valor = body.estado.strip().upper() if body.estado else None
    if valor and valor not in ESTADOS_VALIDOS:
        raise HTTPException(status_code=400, detail=f"Estado inválido. Usa uno de: {', '.join(ESTADOS_VALIDOS)}.")
    with engine.begin() as conn:
        result = conn.execute(
            text("UPDATE lotes SET estado_manual = :v WHERE numero = :n RETURNING numero"),
            {"v": valor, "n": numero},
        )
        if result.scalar() is None:
            raise HTTPException(status_code=404, detail=f"El lote {numero} no está en el maestro.")
    return {"ok": True}


class ActualizarUbicacionLote(BaseModel):
    ubicacion: Optional[str] = None  # None = volver a usar la que trae el Sheet


@app.post("/api/lotes/{numero}/ubicacion")
def actualizar_ubicacion_lote(numero: int, body: ActualizarUbicacionLote):
    """Override manual de producción sobre la ubicación del lote."""
    valor = body.ubicacion.strip() if body.ubicacion else None
    if valor and not ubicacion_es_valida(valor):
        raise HTTPException(status_code=400, detail=f"Ubicación inválida. Usa una combinación de: {', '.join(UBICACIONES_BASE)}.")
    with engine.begin() as conn:
        result = conn.execute(
            text("UPDATE lotes SET ubicacion_manual = :v WHERE numero = :n RETURNING numero"),
            {"v": valor, "n": numero},
        )
        if result.scalar() is None:
            raise HTTPException(status_code=404, detail=f"El lote {numero} no está en el maestro.")
    return {"ok": True}


@app.post("/api/sync/lotes")
def sync_lotes_endpoint():
    """Trae los lotes nuevos/actualizados desde el Google Sheet de
    recepción - lo mismo que hace `python sync_lotes.py`, pero desde un
    botón en la página en vez de la terminal."""
    try:
        return sincronizar_lotes(engine)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"No se pudo sincronizar: {e}")


# ---------------------------------------------------------------------------
# corridas
# ---------------------------------------------------------------------------
@app.get("/api/corridas")
def listar_corridas(abiertas: bool = False):
    with engine.connect() as conn:
        if abiertas:
            result = conn.execute(
                text(
                    "SELECT id, nombre, fecha_inicio, fecha_final, mp_kg_objetivo "
                    "FROM corridas WHERE estado = 'abierta' ORDER BY fecha_inicio DESC"
                )
            )
        else:
            result = conn.execute(text("SELECT * FROM v_cuadre_corridas ORDER BY fecha_inicio DESC"))
        return rows(result)


class NuevaCorrida(BaseModel):
    nombre: str
    tipo_proceso: Optional[str] = None
    fecha_inicio: str  # viene de <input type="datetime-local">, ej "2026-09-03T14:30"


@app.post("/api/corridas")
def crear_corrida(c: NuevaCorrida):
    nombre = c.nombre.strip()
    if not nombre:
        raise HTTPException(status_code=400, detail="El nombre de la corrida no puede estar vacío.")
    try:
        with engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    INSERT INTO corridas (nombre, tipo_proceso, fecha_inicio, estado)
                    VALUES (:nombre, :tipo_proceso, :fecha_inicio, 'abierta')
                    RETURNING id
                    """
                ),
                {"nombre": nombre, "tipo_proceso": c.tipo_proceso, "fecha_inicio": c.fecha_inicio},
            )
            new_id = result.scalar()
        return {"id": new_id, "ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e).split("\n")[0])


class FinalizarCorrida(BaseModel):
    fecha_final: str


@app.post("/api/corridas/{corrida_id}/finalizar")
def finalizar_corrida(corrida_id: int, f: FinalizarCorrida):
    """fecha_final la manda el navegador (hora local de la planta) - NO se
    usa now() del servidor: fecha_inicio se guarda como hora local "de
    pared" (viene de un <input datetime-local>, sin zona horaria) y Render
    corre en UTC, así que un now() del servidor quedaría ~5 horas adelantado
    frente a fecha_inicio - mismo gotcha ya corregido en paradas/cerrar."""
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                UPDATE corridas SET fecha_final = :fecha_final, estado = 'cerrada'
                WHERE id = :id AND estado = 'abierta'
                RETURNING id
                """
            ),
            {"id": corrida_id, "fecha_final": f.fecha_final},
        )
        if result.scalar() is None:
            raise HTTPException(status_code=404, detail="Corrida no encontrada o ya estaba cerrada.")
    return {"ok": True}


@app.post("/api/corridas/{corrida_id}/reabrir")
def reabrir_corrida(corrida_id: int):
    """Vuelve a abrir una corrida ya finalizada - para cuando se olvidó
    registrar el consumo de un lote antes de cerrarla (una vez cerrada,
    deja de aparecer en el selector de 'Registrar consumo'). Se puede
    volver a finalizar normal después."""
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                UPDATE corridas SET fecha_final = NULL, estado = 'abierta'
                WHERE id = :id AND estado = 'cerrada'
                RETURNING id
                """
            ),
            {"id": corrida_id},
        )
        if result.scalar() is None:
            raise HTTPException(status_code=404, detail="Corrida no encontrada o ya estaba abierta.")
    return {"ok": True}


@app.delete("/api/corridas/{corrida_id}")
def eliminar_corrida(corrida_id: int):
    """Solo se puede borrar una corrida sin asignaciones - para corregir una
    creada por error, no para descartar consumo ya registrado."""
    with engine.begin() as conn:
        n_asignaciones = conn.execute(
            text("SELECT count(*) FROM asignaciones WHERE corrida_id = :id"), {"id": corrida_id}
        ).scalar()
        if n_asignaciones > 0:
            raise HTTPException(
                status_code=400,
                detail=f"Esta corrida ya tiene {n_asignaciones} asignación(es) de consumo - no se puede eliminar para no perder ese registro.",
            )
        result = conn.execute(text("DELETE FROM corridas WHERE id = :id RETURNING id"), {"id": corrida_id})
        if result.scalar() is None:
            raise HTTPException(status_code=404, detail="Corrida no encontrada.")
    return {"ok": True}


@app.get("/api/corridas/{corrida_id}/asignaciones")
def listar_asignaciones(corrida_id: int):
    with engine.connect() as conn:
        result = conn.execute(
            text(
                """
                SELECT a.id, a.lote_numero, l.proveedor, a.turno, a.kg_asignados,
                       a.bines_consumidos, a.tipo_almacen_origen, a.observaciones, a.creado_en,
                       v.peso_neto_kg, v.kg_saldo AS saldo_actual_lote, v.bines_totales, v.bines_saldo
                FROM asignaciones a
                JOIN lotes l ON l.numero = a.lote_numero
                JOIN v_saldo_lotes v ON v.numero = a.lote_numero
                WHERE a.corrida_id = :c
                ORDER BY a.creado_en DESC
                """
            ),
            {"c": corrida_id},
        )
        return rows(result)


@app.get("/api/productos")
def listar_todos_productos():
    """Productos de salida de todas las corridas, con el nombre y el MP kg
    de su corrida ya incluidos - para los dashboards de rendimiento y
    producto terminado (evita pedir corrida por corrida)."""
    with engine.connect() as conn:
        result = conn.execute(
            text(
                """
                SELECT cp.id, cp.corrida_id, c.nombre AS corrida_nombre, c.fecha_inicio,
                       c.tipo_proceso, c.mp_kg_objetivo, cp.producto, cp.tambores,
                       cp.peso_neto_tambor_kg, cp.pt_kg, cp.volumen_litros
                FROM corrida_productos cp
                JOIN corridas c ON c.id = cp.corrida_id
                ORDER BY c.fecha_inicio DESC
                """
            )
        )
        return rows(result)


@app.get("/api/corridas/{corrida_id}/productos")
def listar_productos_corrida(corrida_id: int):
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT id, producto, tambores, peso_neto_tambor_kg, pt_kg, volumen_litros, observaciones "
                "FROM corrida_productos WHERE corrida_id = :c ORDER BY producto"
            ),
            {"c": corrida_id},
        )
        return rows(result)


class NuevoProductoCorrida(BaseModel):
    producto: str
    tambores: Optional[int] = None
    peso_neto_tambor_kg: Optional[float] = None
    pt_kg: Optional[float] = None
    volumen_litros: Optional[float] = None  # se carga directo (medido/conocido), no se calcula con un factor
    observaciones: Optional[str] = ""


@app.post("/api/corridas/{corrida_id}/productos")
def guardar_producto_corrida(corrida_id: int, p: NuevoProductoCorrida):
    producto = p.producto.strip()
    if not producto:
        raise HTTPException(status_code=400, detail="El nombre del producto no puede estar vacío.")
    pt_kg = p.pt_kg
    if pt_kg is None and p.tambores and p.peso_neto_tambor_kg:
        pt_kg = p.tambores * p.peso_neto_tambor_kg
    try:
        with engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    INSERT INTO corrida_productos
                        (corrida_id, producto, tambores, peso_neto_tambor_kg, pt_kg, volumen_litros, observaciones)
                    VALUES
                        (:corrida_id, :producto, :tambores, :peso_tambor, :pt_kg, :volumen, :obs)
                    ON CONFLICT (corrida_id, producto) DO UPDATE SET
                        tambores = EXCLUDED.tambores,
                        peso_neto_tambor_kg = EXCLUDED.peso_neto_tambor_kg,
                        pt_kg = EXCLUDED.pt_kg,
                        volumen_litros = EXCLUDED.volumen_litros,
                        observaciones = EXCLUDED.observaciones
                    RETURNING id
                    """
                ),
                {
                    "corrida_id": corrida_id,
                    "producto": producto,
                    "tambores": p.tambores,
                    "peso_tambor": p.peso_neto_tambor_kg,
                    "pt_kg": pt_kg,
                    "volumen": p.volumen_litros,
                    "obs": p.observaciones,
                },
            )
            new_id = result.scalar()
        return {"id": new_id, "ok": True}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e).split("\n")[0])


@app.delete("/api/corridas/{corrida_id}/productos/{producto_id}")
def eliminar_producto_corrida(corrida_id: int, producto_id: int):
    with engine.begin() as conn:
        result = conn.execute(
            text("DELETE FROM corrida_productos WHERE id = :id AND corrida_id = :c RETURNING id"),
            {"id": producto_id, "c": corrida_id},
        )
        if result.scalar() is None:
            raise HTTPException(status_code=404, detail="Producto no encontrado.")
    return {"ok": True}


# ---------------------------------------------------------------------------
# asignaciones (registrar consumo)
# ---------------------------------------------------------------------------
def bines_disponibles(conn, lote_numero: int, excluir_asignacion_id: Optional[int] = None):
    """(bines_totales, bines_disponibles) de un lote. bines_disponibles ya sumó
    de vuelta los bines de la propia asignación que se está editando (si
    aplica) - mismo criterio que el trigger de saldo en kg, que excluye la
    fila propia. El trigger de la base solo protege el saldo en KG; los
    bines se validan aquí porque son un conteo aparte (kg y bines pueden
    desalinearse si alguien ajusta el kg sugerido a mano)."""
    row = conn.execute(
        text("SELECT bines_totales, bines_saldo FROM v_saldo_lotes WHERE numero = :n"),
        {"n": lote_numero},
    ).mappings().first()
    if row is None or row["bines_totales"] is None:
        return (None, None)
    disponibles = row["bines_saldo"] or 0
    if excluir_asignacion_id is not None:
        propios = conn.execute(
            text("SELECT COALESCE(bines_consumidos, 0) FROM asignaciones WHERE id = :id"),
            {"id": excluir_asignacion_id},
        ).scalar()
        disponibles += propios or 0
    return (row["bines_totales"], disponibles)


class NuevaAsignacion(BaseModel):
    lote_numero: int
    corrida_id: int
    turno: str
    tipo_almacen_origen: str
    kg_asignados: float
    bines_consumidos: Optional[int] = None
    observaciones: Optional[str] = ""


@app.post("/api/asignaciones")
def crear_asignacion(a: NuevaAsignacion):
    if a.kg_asignados <= 0:
        raise HTTPException(status_code=400, detail="El peso a asignar debe ser mayor a 0.")
    try:
        with engine.begin() as conn:
            if a.tipo_almacen_origen == "BINES" and a.bines_consumidos:
                totales, disponibles = bines_disponibles(conn, a.lote_numero)
                if disponibles is not None and a.bines_consumidos > disponibles:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Ese lote solo tiene {disponibles} bin(es) disponibles (de {totales} en total).",
                    )
            result = conn.execute(
                text(
                    """
                    INSERT INTO asignaciones
                        (lote_numero, corrida_id, fecha_proceso, turno, tipo_almacen_origen, kg_asignados, bines_consumidos, observaciones)
                    VALUES
                        (:lote_numero, :corrida_id, now(), :turno, :origen, :kg, :bines, :obs)
                    RETURNING id
                    """
                ),
                {
                    "lote_numero": a.lote_numero,
                    "corrida_id": a.corrida_id,
                    "turno": a.turno,
                    "origen": a.tipo_almacen_origen,
                    "kg": a.kg_asignados,
                    "bines": a.bines_consumidos,
                    "obs": a.observaciones,
                },
            )
            new_id = result.scalar()
        return {"id": new_id, "ok": True}
    except HTTPException:
        raise
    except Exception as e:
        # el trigger de saldo insuficiente (trg_validar_saldo_lote) llega aqui
        # como excepcion de la base - se traduce a un 400 con el mensaje tal cual.
        raise HTTPException(status_code=400, detail=str(e).split("\n")[0])


class EditarAsignacion(BaseModel):
    kg_asignados: float
    bines_consumidos: Optional[int] = None  # solo aplica si el lote es de bines - el front recalcula el kg a partir de esto


@app.post("/api/asignaciones/{asignacion_id}")
def editar_asignacion(asignacion_id: int, a: EditarAsignacion):
    """Corrige una asignacion ya registrada. Si el origen es BINES, el front
    manda la cantidad de bines corregida junto con el kg ya recalculado
    (bines x kg-por-bin) - no le pide a producción que calcule el kg a mano.
    Si es SILO, bines_consumidos llega en null y se guarda así (nunca tuvo
    bines)."""
    if a.kg_asignados <= 0:
        raise HTTPException(status_code=400, detail="El peso a asignar debe ser mayor a 0.")
    try:
        with engine.begin() as conn:
            lote_numero = conn.execute(
                text("SELECT lote_numero FROM asignaciones WHERE id = :id"), {"id": asignacion_id}
            ).scalar()
            if lote_numero is None:
                raise HTTPException(status_code=404, detail="Asignación no encontrada.")
            if a.bines_consumidos:
                totales, disponibles = bines_disponibles(conn, lote_numero, excluir_asignacion_id=asignacion_id)
                if disponibles is not None and a.bines_consumidos > disponibles:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Ese lote solo tiene {disponibles} bin(es) disponibles (de {totales} en total).",
                    )
            result = conn.execute(
                text(
                    "UPDATE asignaciones SET kg_asignados = :kg, bines_consumidos = :bines "
                    "WHERE id = :id RETURNING id"
                ),
                {"id": asignacion_id, "kg": a.kg_asignados, "bines": a.bines_consumidos},
            )
            if result.scalar() is None:
                raise HTTPException(status_code=404, detail="Asignación no encontrada.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e).split("\n")[0])
    return {"ok": True}


@app.delete("/api/asignaciones/{asignacion_id}")
def eliminar_asignacion(asignacion_id: int):
    with engine.begin() as conn:
        result = conn.execute(
            text("DELETE FROM asignaciones WHERE id = :id RETURNING id"), {"id": asignacion_id}
        )
        if result.scalar() is None:
            raise HTTPException(status_code=404, detail="Asignación no encontrada.")
    return {"ok": True}


# ---------------------------------------------------------------------------
# paradas (tiempos muertos de una corrida)
# ---------------------------------------------------------------------------
@app.get("/api/corridas/{corrida_id}/paradas")
def listar_paradas(corrida_id: int):
    with engine.connect() as conn:
        result = conn.execute(
            text(
                """
                SELECT id, corrida_id, motivo, hora_inicio, hora_fin, observaciones, creado_en,
                       CASE WHEN hora_fin IS NULL THEN NULL
                            ELSE ROUND(EXTRACT(EPOCH FROM (hora_fin - hora_inicio)) / 60)
                       END AS duracion_minutos
                FROM paradas
                WHERE corrida_id = :c
                ORDER BY hora_inicio DESC
                """
            ),
            {"c": corrida_id},
        )
        return rows(result)


class NuevaParada(BaseModel):
    corrida_id: int
    motivo: str
    hora_inicio: str
    hora_fin: Optional[str] = None
    observaciones: Optional[str] = ""


@app.post("/api/paradas")
def crear_parada(p: NuevaParada):
    motivo = p.motivo.strip()
    if not motivo:
        raise HTTPException(status_code=400, detail="El motivo de la parada no puede estar vacío.")
    try:
        with engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    INSERT INTO paradas (corrida_id, motivo, hora_inicio, hora_fin, observaciones)
                    VALUES (:corrida_id, :motivo, :hora_inicio, :hora_fin, :obs)
                    RETURNING id
                    """
                ),
                {
                    "corrida_id": p.corrida_id,
                    "motivo": motivo,
                    "hora_inicio": p.hora_inicio,
                    "hora_fin": p.hora_fin,
                    "obs": p.observaciones,
                },
            )
            new_id = result.scalar()
        return {"id": new_id, "ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e).split("\n")[0])


class CerrarParada(BaseModel):
    hora_fin: str


@app.post("/api/paradas/{parada_id}/cerrar")
def cerrar_parada(parada_id: int, p: CerrarParada):
    """Marca el fin de una parada que sigue en curso (hora_fin quedó en NULL
    al crearla porque todavía no se sabía cuánto iba a durar). hora_fin la
    manda el navegador (hora local de la planta) - NO se usa now() del
    servidor: hora_inicio se guarda como hora local "de pared" (viene de un
    <input datetime-local>, sin zona horaria) y el servidor de Render corre
    en UTC, así que un now() del servidor quedaría ~5 horas adelantado
    frente a hora_inicio y la duración calculada saldría mal."""
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                UPDATE paradas SET hora_fin = :hora_fin
                WHERE id = :id AND hora_fin IS NULL
                RETURNING id
                """
            ),
            {"id": parada_id, "hora_fin": p.hora_fin},
        )
        if result.scalar() is None:
            raise HTTPException(status_code=404, detail="Parada no encontrada o ya estaba cerrada.")
    return {"ok": True}


class EditarParada(BaseModel):
    motivo: str
    hora_inicio: str
    hora_fin: Optional[str] = None
    observaciones: Optional[str] = ""


@app.post("/api/paradas/{parada_id}")
def editar_parada(parada_id: int, p: EditarParada):
    motivo = p.motivo.strip()
    if not motivo:
        raise HTTPException(status_code=400, detail="El motivo de la parada no puede estar vacío.")
    try:
        with engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    UPDATE paradas SET motivo = :motivo, hora_inicio = :hora_inicio,
                                        hora_fin = :hora_fin, observaciones = :obs
                    WHERE id = :id RETURNING id
                    """
                ),
                {
                    "id": parada_id,
                    "motivo": motivo,
                    "hora_inicio": p.hora_inicio,
                    "hora_fin": p.hora_fin,
                    "obs": p.observaciones,
                },
            )
            if result.scalar() is None:
                raise HTTPException(status_code=404, detail="Parada no encontrada.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e).split("\n")[0])
    return {"ok": True}


@app.delete("/api/paradas/{parada_id}")
def eliminar_parada(parada_id: int):
    with engine.begin() as conn:
        result = conn.execute(
            text("DELETE FROM paradas WHERE id = :id RETURNING id"), {"id": parada_id}
        )
        if result.scalar() is None:
            raise HTTPException(status_code=404, detail="Parada no encontrada.")
    return {"ok": True}


# ---------------------------------------------------------------------------
# front-end estático (todo en el mismo servicio - un solo link para compartir)
# ---------------------------------------------------------------------------
app.mount("/", StaticFiles(directory=BASE_DIR / "web", html=True), name="web")
