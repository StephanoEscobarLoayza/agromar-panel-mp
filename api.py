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


@app.get("/api/lotes/sugerir-mezcla")
def sugerir_mezcla(brix_min: float, ratio_min: float):
    """Sugiere qué lotes con saldo combinar, del más antiguo al más nuevo
    (FIFO - así es como de verdad se van agarrando en planta), hasta que el
    Brix y Ratio PONDERADOS por kg lleguen al mínimo pedido. El último lote
    que hace falta se corta a la fracción justa que alcanza - no obliga a
    gastar un lote completo si con una parte ya se llega."""
    with engine.connect() as conn:
        result = conn.execute(
            text(
                """
                SELECT numero, proveedor, fecha_ingreso, tipo_almacen, kg_saldo, brix_recepcion, acidez, ratio
                FROM v_saldo_lotes
                WHERE kg_saldo > 0 AND brix_recepcion IS NOT NULL AND acidez IS NOT NULL AND acidez > 0
                ORDER BY fecha_ingreso ASC, numero ASC
                """
            )
        )
        candidatos = rows(result)

    pasos = []
    kg_acum = 0.0
    brix_pond = 0.0
    acidez_pond = 0.0
    cumplido = False

    for l in candidatos:
        kg_disponible = float(l["kg_saldo"])
        brix_lote = float(l["brix_recepcion"])
        acidez_lote = float(l["acidez"])

        kg_parcial = _kg_minimo(brix_pond, acidez_pond, kg_acum, brix_lote, acidez_lote, kg_disponible, brix_min, ratio_min)
        kg_usado = kg_parcial if kg_parcial is not None else kg_disponible

        kg_acum += kg_usado
        brix_pond += brix_lote * kg_usado
        acidez_pond += acidez_lote * kg_usado
        brix_mezcla = brix_pond / kg_acum
        acidez_mezcla = acidez_pond / kg_acum
        ratio_mezcla = brix_mezcla / acidez_mezcla if acidez_mezcla > 0 else None

        pasos.append({
            "numero": l["numero"],
            "proveedor": l["proveedor"],
            "fecha_ingreso": l["fecha_ingreso"],
            "tipo_almacen": l["tipo_almacen"],
            "kg_disponible": kg_disponible,
            "kg_usado": round(kg_usado, 2),
            "es_parcial": kg_parcial is not None and kg_parcial < kg_disponible - 0.005,
            "brix_lote": brix_lote,
            "acidez_lote": acidez_lote,
            "ratio_lote": float(l["ratio"]) if l["ratio"] is not None else None,
            "kg_acumulado": round(kg_acum, 2),
            "brix_mezcla": round(brix_mezcla, 2),
            "acidez_mezcla": round(acidez_mezcla, 3),
            "ratio_mezcla": round(ratio_mezcla, 2) if ratio_mezcla is not None else None,
        })

        if kg_parcial is not None:
            cumplido = True
            break

    return {
        "cumplido": cumplido,
        "pasos": pasos,
        "lotes_evaluados": len(candidatos),
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
# front-end estático (todo en el mismo servicio - un solo link para compartir)
# ---------------------------------------------------------------------------
app.mount("/", StaticFiles(directory=BASE_DIR / "web", html=True), name="web")
