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


@app.get("/api/lotes/{numero}")
def obtener_lote(numero: int):
    with engine.connect() as conn:
        result = conn.execute(text("SELECT * FROM v_saldo_lotes WHERE numero = :n"), {"n": numero})
        lote = one(result)
        if lote is None:
            raise HTTPException(status_code=404, detail=f"El lote {numero} no está en el maestro. Sincronízalo primero.")
        return lote


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


@app.post("/api/corridas/{corrida_id}/finalizar")
def finalizar_corrida(corrida_id: int):
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                UPDATE corridas SET fecha_final = now(), estado = 'cerrada'
                WHERE id = :id AND estado = 'abierta'
                RETURNING id
                """
            ),
            {"id": corrida_id},
        )
        if result.scalar() is None:
            raise HTTPException(status_code=404, detail="Corrida no encontrada o ya estaba cerrada.")
    return {"ok": True}


class NuevoObjetivo(BaseModel):
    mp_kg_objetivo: float


@app.post("/api/corridas/{corrida_id}/objetivo")
def actualizar_objetivo(corrida_id: int, o: NuevoObjetivo):
    if o.mp_kg_objetivo <= 0:
        raise HTTPException(status_code=400, detail="El objetivo debe ser mayor a 0.")
    with engine.begin() as conn:
        result = conn.execute(
            text("UPDATE corridas SET mp_kg_objetivo = :v WHERE id = :id RETURNING id"),
            {"v": o.mp_kg_objetivo, "id": corrida_id},
        )
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
                       v.peso_neto_kg, v.kg_saldo AS saldo_actual_lote
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


# ---------------------------------------------------------------------------
# asignaciones (registrar consumo)
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# front-end estático (todo en el mismo servicio - un solo link para compartir)
# ---------------------------------------------------------------------------
app.mount("/", StaticFiles(directory=BASE_DIR / "web", html=True), name="web")
