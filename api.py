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


ESTADOS_VALIDOS = {"PROCESADO", "EN PROCESO", "EN ESPERA"}
UBICACIONES_VALIDAS = {"Tolva", "Silo 1", "Silo 2", "Silo 1 / Silo 2", "Bines"}


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
    if valor and valor not in UBICACIONES_VALIDAS:
        raise HTTPException(status_code=400, detail=f"Ubicación inválida. Usa una de: {', '.join(UBICACIONES_VALIDAS)}.")
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
                       cp.peso_neto_tambor_kg, cp.pt_kg
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
                "SELECT id, producto, tambores, peso_neto_tambor_kg, pt_kg, observaciones "
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
                        (corrida_id, producto, tambores, peso_neto_tambor_kg, pt_kg, observaciones)
                    VALUES
                        (:corrida_id, :producto, :tambores, :peso_tambor, :pt_kg, :obs)
                    ON CONFLICT (corrida_id, producto) DO UPDATE SET
                        tambores = EXCLUDED.tambores,
                        peso_neto_tambor_kg = EXCLUDED.peso_neto_tambor_kg,
                        pt_kg = EXCLUDED.pt_kg,
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


class EditarAsignacion(BaseModel):
    kg_asignados: float


@app.post("/api/asignaciones/{asignacion_id}")
def editar_asignacion(asignacion_id: int, a: EditarAsignacion):
    """Corrige el kg de una asignacion ya registrada (ej. se tecleo mal el
    numero). Solo toca kg_asignados - bines_consumidos/observaciones no se
    piden en la UI de edicion, y sobreescribirlos con valores por defecto
    los borraria de un registro que ya tenia datos reales."""
    if a.kg_asignados <= 0:
        raise HTTPException(status_code=400, detail="El peso a asignar debe ser mayor a 0.")
    try:
        with engine.begin() as conn:
            result = conn.execute(
                text("UPDATE asignaciones SET kg_asignados = :kg WHERE id = :id RETURNING id"),
                {"id": asignacion_id, "kg": a.kg_asignados},
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
# front-end estático (todo en el mismo servicio - un solo link para compartir)
# ---------------------------------------------------------------------------
app.mount("/", StaticFiles(directory=BASE_DIR / "web", html=True), name="web")
