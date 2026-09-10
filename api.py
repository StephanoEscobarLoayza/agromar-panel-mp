"""
API del panel de cuadre - Agromar. Sirve tanto los endpoints JSON (/api/...)
como las páginas estáticas del front-end (carpeta web/), en un solo servicio
con una sola URL para compartir.

Correr local:
    pip install -r requirements.txt
    python -m uvicorn api:app --reload --port 8000
"""
import asyncio
import hashlib
import hmac
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import create_engine, text

from sync_lotes import sincronizar_lotes
from reporte_base import ahora_peru
from reporte_corrida import generar_reporte_pdf
from reporte_periodo import generar_reporte_periodo_pdf
from reporte_stock import generar_reporte_stock_pdf
from reporte_trazabilidad import generar_trazabilidad_xlsx

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

# ---------------------------------------------------------------------------
# autenticación - una sola contraseña compartida para todo el equipo.
# Se activa SOLO si están seteadas APP_PASSWORD y APP_SESSION_SECRET (env vars
# en Render). Si no están, la app funciona sin login como antes - así se puede
# desplegar el código sin dejar a nadie afuera hasta decidir prender el candado.
# ---------------------------------------------------------------------------
APP_USER = (os.environ.get("APP_USER") or "").strip()
APP_PASSWORD = (os.environ.get("APP_PASSWORD") or "").strip()
_SESSION_SECRET = (os.environ.get("APP_SESSION_SECRET") or "").strip()
AUTH_ON = bool(APP_USER and APP_PASSWORD and _SESSION_SECRET)
_COOKIE_NAME = "agromar_auth"
_COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 días sin volver a entrar
_COOKIE_SECURE = os.environ.get("APP_INSECURE_COOKIE") != "1"  # =1 solo para probar en http local
# rutas visibles sin login (para que la pantalla de login cargue y pueda enviar)
_RUTAS_LIBRES = {"/login.html", "/login-bg.jpg", "/login-bg-mobile.jpg", "/api/login", "/api/sesion", "/style.css", "/app.js", "/favicon.svg"}


def _token_sesion():
    return hmac.new(_SESSION_SECRET.encode(), b"agromar-ok", hashlib.sha256).hexdigest()


@app.middleware("http")
async def _guardia_auth(request, call_next):
    if not AUTH_ON:
        return await call_next(request)
    path = request.url.path
    if path in _RUTAS_LIBRES:
        return await call_next(request)
    cookie = request.cookies.get(_COOKIE_NAME, "")
    if cookie and hmac.compare_digest(cookie, _token_sesion()):
        return await call_next(request)
    if path.startswith("/api/"):
        return JSONResponse({"detail": "No autenticado"}, status_code=401)
    return RedirectResponse(url="/login.html", status_code=303)


class LoginPayload(BaseModel):
    usuario: str = ""
    password: str


@app.get("/api/sesion")
def estado_sesion():
    return {"auth": AUTH_ON}


@app.post("/api/login")
def login(p: LoginPayload):
    if not AUTH_ON:
        return {"ok": True}
    ok_usuario = hmac.compare_digest(p.usuario.strip(), APP_USER)
    ok_clave = hmac.compare_digest(p.password.strip(), APP_PASSWORD)
    if not (ok_usuario and ok_clave):
        raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos.")
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        _COOKIE_NAME, _token_sesion(), max_age=_COOKIE_MAX_AGE,
        httponly=True, samesite="lax", secure=_COOKIE_SECURE, path="/",
    )
    return resp


@app.post("/api/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(_COOKIE_NAME, path="/")
    return resp


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


@app.get("/api/lotes/reporte-stock.pdf")
def reporte_stock_pdf():
    """Foto en PDF del stock que el sistema calcula en vivo (Silo + Bines) -
    no es un conteo físico, es exactamente lo que ya muestra v_saldo_lotes.
    Solo cuenta lotes "EN PROCESO" o "EN ESPERA" (mismo criterio que el
    Sugeridor de mezcla) - un lote "PROCESADO" con algo de saldo casi
    siempre es ruido de medición de Trazabilidad, no MP real disponible."""
    with engine.connect() as conn:
        lotes = rows(conn.execute(text(
            """
            SELECT numero, proveedor, tipo_almacen, fecha_ingreso, estado_actual, kg_saldo, bines_saldo, peso_neto_kg
            FROM v_saldo_lotes
            WHERE kg_saldo > 0 AND UPPER(TRIM(estado_actual)) IN ('EN PROCESO', 'EN ESPERA')
            ORDER BY tipo_almacen, fecha_ingreso ASC, numero ASC
            """
        )))
    pdf_bytes = generar_reporte_stock_pdf(lotes)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'inline; filename="stock-materia-prima.pdf"',
            "Cache-Control": "no-store",
        },
    )


MAX_BINES_AJUSTE = 2  # en planta siempre entran 2 lotes de bines a la vez sobre el silo


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


def _ajustar_con_bines(bines, kg_acum, brix_pond, acidez_pond, brix_min, ratio_min):
    """Agrega hasta MAX_BINES_AJUSTE lotes de bines COMPLETOS (nunca corta a
    una fracción) - en planta siempre entran 2 lotes de bines a la vez, sin
    importar si con uno solo ya alcanzaría el mínimo: la prioridad es
    procesar más materia prima, no la mínima posible para llegar al
    Brix/Ratio (Stephano lo aclaró explícitamente: quedarse corto con un
    bin parcial cuando podría meter más es ineficiente para la producción)."""
    pasos_bines = []
    for l in bines:
        if len(pasos_bines) >= MAX_BINES_AJUSTE:
            break
        kg_disponible = float(l["kg_saldo"])
        brix_lote = float(l["brix_recepcion"])
        acidez_lote = float(l["acidez"])
        kg_acum += kg_disponible
        brix_pond += brix_lote * kg_disponible
        acidez_pond += acidez_lote * kg_disponible
        pasos_bines.append(_paso_mezcla(l, kg_disponible, False, kg_acum, brix_pond, acidez_pond))
    cumplido = kg_acum > 0 and (brix_pond / kg_acum) >= brix_min and acidez_pond > 0 and (brix_pond / acidez_pond) >= ratio_min
    return pasos_bines, cumplido


def _ajustar_con_bines_calidad(bines, kg_acum, brix_pond, acidez_pond, brix_min, ratio_min):
    """Igual que _ajustar_con_bines (hasta MAX_BINES_AJUSTE lotes COMPLETOS,
    nunca a fracción) pero en vez de ir por orden de llegada, en cada uno de
    los cupos elige - de lo que queda disponible - el bin que deja la MEJOR
    mezcla resultante, probando de verdad cada candidato contra lo que ya se
    lleva acumulado (no solo comparando el brix/ratio propio de cada lote
    suelto). Stephano pidió esto como alternativa a "el que sigue por
    fecha" - acá la prioridad es ayudar a mantener o subir el Brix Y el
    Ratio, no solo uno de los dos a costa del otro - por eso se compara qué
    tan por encima de CADA mínimo queda (brix/brix_min y ratio/ratio_min) y
    se elige el candidato cuyo peor de los dos sea el más alto (maximin) -
    así no gana un bin que dispara el ratio pero hunde el brix, ni al revés."""
    disponibles = list(bines)
    pasos_bines = []
    while disponibles and len(pasos_bines) < MAX_BINES_AJUSTE:
        mejor = mejor_score = None
        for l in disponibles:
            kg_l = float(l["kg_saldo"])
            brix_l = float(l["brix_recepcion"])
            acidez_l = float(l["acidez"])
            kg_test = kg_acum + kg_l
            brix_pond_test = brix_pond + brix_l * kg_l
            acidez_pond_test = acidez_pond + acidez_l * kg_l
            brix_test = brix_pond_test / kg_test
            ratio_test = brix_test / (acidez_pond_test / kg_test) if acidez_pond_test > 0 else 0
            score = (min(brix_test / brix_min, ratio_test / ratio_min), brix_test, ratio_test)
            if mejor is None or score > mejor_score:
                mejor, mejor_score = l, score
        kg_disponible = float(mejor["kg_saldo"])
        brix_lote = float(mejor["brix_recepcion"])
        acidez_lote = float(mejor["acidez"])
        kg_acum += kg_disponible
        brix_pond += brix_lote * kg_disponible
        acidez_pond += acidez_lote * kg_disponible
        pasos_bines.append(_paso_mezcla(mejor, kg_disponible, False, kg_acum, brix_pond, acidez_pond))
        disponibles.remove(mejor)
    cumplido = kg_acum > 0 and (brix_pond / kg_acum) >= brix_min and acidez_pond > 0 and (brix_pond / acidez_pond) >= ratio_min
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
            silo_info = None
        else:
            kg_acum = float(silo_base["kg_saldo"])
            brix_pond = float(silo_base["brix_recepcion"]) * kg_acum
            acidez_pond = float(silo_base["acidez"]) * kg_acum
            silo_info = _paso_mezcla(silo_base, kg_acum, False, kg_acum, brix_pond, acidez_pond)
        pasos_bines, cumplido = _ajustar_con_bines(bines, kg_acum, brix_pond, acidez_pond, brix_min, ratio_min)
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
        pasos_bines, cumplido = _ajustar_con_bines(bines, kg_acum, brix_pond, acidez_pond, brix_min, ratio_min)
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
def sugerir_siguiente_bin(corrida_id: int, brix_min: float, ratio_min: float, modo: str = "fecha"):
    """Para cuando ya estás a mitad de una corrida y un lote de bines se te
    acaba: a diferencia del sugeridor general (que arranca de cero asumiendo
    que vas a usar el lote de Silo completo), este parte de lo que YA
    registraste de verdad en 'Registrar consumo' para esta corrida (Silo +
    bines ya usados) - ese es el Brix/Ratio acumulado real que ya está
    mezclado en la máquina - y sugiere el/los siguiente(s) bin(es) que hacen
    falta para llegar al mínimo, de a uno, máximo 2, igual que en planta.

    modo="fecha" (por defecto): el siguiente en la cola por orden de
    llegada - fácil de calcular a mano con solo mirar la fecha, se usa para
    no dejar ningún lote esperando demasiado.
    modo="calidad": en vez de por fecha, elige el/los bin(es) que más
    ayudan a mantener o subir el Brix/Ratio de la mezcla resultante - esto
    sí hace falta calcularlo, no es solo mirar una fecha."""
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

    # en planta SIEMPRE corren 2 lotes de bines a la vez, sin importar si la
    # mezcla ya llegó al mínimo o no (regla operativa, no de calidad) - por
    # eso siempre se muestra el siguiente bin (o los 2 siguientes), completos,
    # aunque ya no "haga falta" para la calidad. Por fecha (de a uno, el que
    # sigue en la cola) o por calidad (el que más ayuda al Brix/Ratio),
    # según lo que haya pedido el front en `modo`.
    if modo == "calidad":
        pasos_bines, cumplido = _ajustar_con_bines_calidad(bines, kg_acum, brix_pond, acidez_pond, brix_min, ratio_min)
    else:
        pasos_bines, cumplido = _ajustar_con_bines(bines, kg_acum, brix_pond, acidez_pond, brix_min, ratio_min)

    return {
        "corrida_nombre": corrida["nombre"],
        "modo": modo,
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


SYNC_INTERVAL_SEGUNDOS = 20 * 60  # cada 20 minutos

# Estado en memoria del último sync (manual o automático) - se reinicia con
# cada despliegue, pero eso solo significa que el mensaje "última sincronización"
# queda en blanco hasta el próximo sync (a lo sumo 20 min) - no se pierde nada
# de la tabla lotes, que sigue intacta.
_estado_sync = {"ultima": None, "resultado": None, "error": None}


def _correr_sync(marcar_error_como_502=False):
    try:
        resultado = sincronizar_lotes(engine)
        _estado_sync["ultima"] = datetime.now(timezone.utc).isoformat()
        _estado_sync["resultado"] = resultado
        _estado_sync["error"] = None
        return resultado
    except Exception as e:
        _estado_sync["ultima"] = datetime.now(timezone.utc).isoformat()
        _estado_sync["error"] = str(e)
        if marcar_error_como_502:
            raise HTTPException(status_code=502, detail=f"No se pudo sincronizar: {e}")
        raise


async def _sync_periodico():
    """Corre sincronizar_lotes() solo, cada SYNC_INTERVAL_SEGUNDOS - primero
    apenas arranca el servicio (o después de cada deploy), y de ahí en
    adelante en bucle. Usa to_thread porque sincronizar_lotes() es una
    función normal (bloqueante: descarga el CSV y hace queries síncronas) -
    sin esto, trabarla en el loop de asyncio congelaría el resto de la app
    mientras dura la descarga."""
    while True:
        try:
            await asyncio.to_thread(_correr_sync)
        except Exception as e:
            print(f"[sync automático] error: {e}")
        await asyncio.sleep(SYNC_INTERVAL_SEGUNDOS)


@app.on_event("startup")
async def iniciar_sync_periodico():
    asyncio.create_task(_sync_periodico())


@app.get("/api/sync/estado")
def sync_estado():
    """Para que la página muestre 'última sincronización: hace X min' y
    confirme que el sync automático de verdad está corriendo, no solo que
    se supone que corre."""
    return _estado_sync


@app.post("/api/sync/lotes")
def sync_lotes_endpoint():
    """Trae los lotes nuevos/actualizados desde el Google Sheet de
    recepción ahora mismo, sin esperar al próximo sync automático - lo mismo
    que hace `python sync_lotes.py`, pero desde un botón en la página."""
    return _correr_sync(marcar_error_como_502=True)


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
            result = conn.execute(
                text("SELECT * FROM v_cuadre_corridas ORDER BY fecha_inicio DESC")
            )
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
                    INSERT INTO corridas (nombre, tipo_proceso, fecha_inicio, estado, stock_inicio_kg)
                    VALUES (:nombre, :tipo_proceso, :fecha_inicio, 'abierta',
                            (SELECT COALESCE(SUM(kg_saldo), 0) FROM v_saldo_lotes
                             WHERE kg_saldo > 0 AND UPPER(TRIM(estado_actual)) IN ('EN PROCESO', 'EN ESPERA')))
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
                UPDATE corridas SET fecha_final = :fecha_final, estado = 'cerrada',
                       stock_cierre_kg = (SELECT COALESCE(SUM(kg_saldo), 0) FROM v_saldo_lotes
                                          WHERE kg_saldo > 0 AND UPPER(TRIM(estado_actual)) IN ('EN PROCESO', 'EN ESPERA'))
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
                UPDATE corridas SET fecha_final = NULL, estado = 'abierta', stock_cierre_kg = NULL
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


@app.get("/api/corridas/{corrida_id}/reporte.pdf")
def reporte_corrida_pdf(corrida_id: int):
    """Genera el PDF de cuadre de una corrida: KPIs, lotes de MP consumidos,
    productos de salida y paradas - todo lo que hoy vive repartido en varias
    páginas, junto en un documento para imprimir o compartir."""
    with engine.connect() as conn:
        corrida = one(conn.execute(text("SELECT * FROM v_cuadre_corridas WHERE id = :id"), {"id": corrida_id}))
        if corrida is None:
            raise HTTPException(status_code=404, detail="Corrida no encontrada.")

        lotes = rows(conn.execute(
            text(
                """
                SELECT a.lote_numero, l.proveedor, a.tipo_almacen_origen, a.kg_asignados,
                       l.brix_recepcion, l.acidez, l.ratio, v.kg_saldo
                FROM asignaciones a
                JOIN lotes l ON l.numero = a.lote_numero
                JOIN v_saldo_lotes v ON v.numero = a.lote_numero
                WHERE a.corrida_id = :c
                ORDER BY a.creado_en ASC
                """
            ),
            {"c": corrida_id},
        ))
        productos = rows(conn.execute(
            text(
                "SELECT producto, tambores, peso_neto_tambor_kg, pt_kg, volumen_litros "
                "FROM corrida_productos WHERE corrida_id = :c ORDER BY producto"
            ),
            {"c": corrida_id},
        ))
        paradas = rows(conn.execute(
            text(
                """
                SELECT hora_inicio, hora_fin, area_proceso, tipo_parada,
                       equipo_afectado, descripcion_falla,
                       CASE WHEN hora_fin IS NULL THEN NULL
                            ELSE ROUND(EXTRACT(EPOCH FROM (hora_fin - hora_inicio)) / 60)
                       END AS duracion_minutos
                FROM paradas WHERE corrida_id = :c ORDER BY hora_inicio ASC
                """
            ),
            {"c": corrida_id},
        ))
        mediciones = rows(conn.execute(
            text(
                """
                SELECT tanque, litros, brix_inicial, brix_final, acidez, ph,
                       ROUND(brix_final / acidez, 2) AS ratio
                FROM mediciones_tanque WHERE corrida_id = :c ORDER BY creado_en ASC
                """
            ),
            {"c": corrida_id},
        ))

    pdf_bytes = generar_reporte_pdf(corrida, lotes, productos, paradas, mediciones)
    nombre_archivo = f"cuadre-{corrida['nombre']}.pdf".replace(" ", "-").replace("/", "-")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{nombre_archivo}"',
            # Sin esto, el navegador puede quedarse con una copia vieja del PDF
            # para esta misma URL (mismo link "Reporte PDF" siempre) y mostrar
            # datos desactualizados aunque la corrida ya tenga registros nuevos
            # (ej. tanques medidos agregados después de la ultima vez que se abrio).
            "Cache-Control": "no-store",
        },
    )


@app.get("/api/reporte-periodo.pdf")
def reporte_periodo_pdf(desde: str, hasta: str):
    """Resumen de producción de un rango de fechas: MP procesada, corridas,
    tipos de proceso, producto terminado, proveedores y paradas. Las corridas
    se agrupan por su fecha de inicio; `hasta` cuenta el día completo."""
    try:
        d_desde = datetime.strptime(desde, "%Y-%m-%d").date()
        d_hasta = datetime.strptime(hasta, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Fechas inválidas (usa AAAA-MM-DD).")
    if d_desde > d_hasta:
        raise HTTPException(status_code=400, detail="La fecha 'desde' no puede ser posterior a 'hasta'.")
    p = {"d": d_desde, "h": d_hasta + timedelta(days=1)}

    with engine.connect() as conn:
        corridas = rows(conn.execute(text(
            """
            SELECT v.*, c.rendimiento
            FROM v_cuadre_corridas v JOIN corridas c ON c.id = v.id
            WHERE v.fecha_inicio >= :d AND v.fecha_inicio < :h
            ORDER BY v.fecha_inicio
            """
        ), p))
        productos = rows(conn.execute(text(
            """
            SELECT c.nombre AS corrida, p.producto, p.pt_kg, p.volumen_litros
            FROM corrida_productos p JOIN corridas c ON c.id = p.corrida_id
            WHERE c.fecha_inicio >= :d AND c.fecha_inicio < :h
            """
        ), p))
        proveedores = list(conn.execute(text(
            """
            SELECT l.proveedor, SUM(a.kg_asignados) AS kg
            FROM asignaciones a
            JOIN corridas c ON c.id = a.corrida_id
            JOIN lotes l ON l.numero = a.lote_numero
            WHERE c.fecha_inicio >= :d AND c.fecha_inicio < :h AND a.kg_asignados IS NOT NULL
            GROUP BY l.proveedor ORDER BY kg DESC LIMIT 12
            """
        ), p))
        paradas = rows(conn.execute(text(
            """
            SELECT p.hora_inicio, p.area_proceso, p.tipo_parada,
                   CASE WHEN p.hora_fin IS NULL THEN 0
                        ELSE EXTRACT(EPOCH FROM (p.hora_fin - p.hora_inicio)) / 60 END AS minutos
            FROM paradas p JOIN corridas c ON c.id = p.corrida_id
            WHERE c.fecha_inicio >= :d AND c.fecha_inicio < :h
            ORDER BY p.hora_inicio
            """
        ), p))

    pdf_bytes = generar_reporte_periodo_pdf(d_desde, d_hasta, corridas, productos, proveedores, paradas)
    nombre = f"resumen-{d_desde.strftime('%Y%m%d')}-a-{d_hasta.strftime('%Y%m%d')}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{nombre}"',
            "Cache-Control": "no-store",
        },
    )


@app.get("/api/corridas/{corrida_id}/trazabilidad.xlsx")
def trazabilidad_corrida_xlsx(corrida_id: int):
    """Exporta UNA corrida al formato de la hoja de Trazabilidad de planta:
    encabezado de fechas, tabla de lotes de MP con totales, bloque de resumen
    (MP kg, rendimiento, masa de jugo simple, ratio...) y saldos por lote.
    Lo que la app no guarda (GRP, N° Guía, descuentos por brix, cáscara/semilla,
    GNC) sale en blanco para llenarlo a mano al cierre."""
    with engine.connect() as conn:
        corrida = one(conn.execute(text(
            """
            SELECT v.*, c.tipo_proceso, c.brix_promedio_tk, c.rendimiento, c.fecha_proceso_ref
            FROM v_cuadre_corridas v JOIN corridas c ON c.id = v.id
            WHERE v.id = :id
            """
        ), {"id": corrida_id}))
        if corrida is None:
            raise HTTPException(status_code=404, detail="Corrida no encontrada.")

        lotes = rows(conn.execute(text(
            """
            SELECT a.lote_numero, a.kg_asignados, a.bines_consumidos, a.brix_produccion,
                   a.tipo_almacen_origen, a.fecha_proceso,
                   l.proveedor, l.procedencia, l.guia, l.fecha_ingreso, l.brix_recepcion,
                   l.peso_neto_kg, l.bines_totales,
                   s.kg_saldo, s.bines_saldo, s.kg_consumidos, s.estado_actual
            FROM asignaciones a
            JOIN lotes l ON l.numero = a.lote_numero
            LEFT JOIN v_saldo_lotes s ON s.numero = a.lote_numero
            WHERE a.corrida_id = :c
            ORDER BY a.fecha_proceso, a.lote_numero
            """
        ), {"c": corrida_id}))
        productos = rows(conn.execute(text(
            "SELECT producto, tambores, peso_neto_tambor_kg, pt_kg, volumen_litros "
            "FROM corrida_productos WHERE corrida_id = :c ORDER BY producto"
        ), {"c": corrida_id}))
        mediciones = rows(conn.execute(text(
            "SELECT litros, brix_inicial, brix_final FROM mediciones_tanque "
            "WHERE corrida_id = :c ORDER BY creado_en"
        ), {"c": corrida_id}))
        # todo el stock de MP con saldo disponible (mismo criterio que el
        # reporte de stock) - el módulo lo parte en dos: lo que dejó ESTA
        # corrida (saldo parcial) y los demás lotes completos que siguen en piso.
        stock = rows(conn.execute(text(
            """
            SELECT numero, proveedor, procedencia, fecha_ingreso, peso_neto_kg,
                   kg_consumidos, kg_saldo, bines_saldo, bines_totales, estado_actual
            FROM v_saldo_lotes
            WHERE kg_saldo > 0.01
              AND UPPER(TRIM(estado_actual)) IN ('EN PROCESO', 'EN ESPERA')
            ORDER BY numero
            """
        )))

    xlsx_bytes = generar_trazabilidad_xlsx(corrida, lotes, productos, mediciones, stock)
    nombre = f"trazabilidad-{corrida['nombre']}.xlsx".replace(" ", "-").replace("/", "-")
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{nombre}"',
            "Cache-Control": "no-store",
        },
    )


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


def _marcar_estado_por_consumo(conn, lote_numero: int):
    """Se llama SIEMPRE que se crea, corrige o BORRA un consumo. Antes esto
    ponía "EN PROCESO" a ciegas, sin importar si esa misma asignación dejaba
    el lote en 0 kg de saldo - un lote que se termina de golpe se quedaba
    pegado en "En proceso" para siempre, porque nada más volvía a tocar
    estado_manual después. Y como estado_manual TAPA a estado_fuente (el
    Sheet de Trazabilidad), la sincronización automática de cada 20 min no
    lo arreglaba sola aunque el Sheet sí dijera "Procesado" del otro lado -
    Stephano lo notó ("el sincronizador jala de Drive y lo pone en proceso
    de nuevo"). Ahora se recalcula el saldo real después del cambio y se
    guarda el estado que corresponde de verdad.

    Si al lote ya no le queda NINGUNA asignación (se borró la única que
    tenía), no tiene sentido dejarlo con un override "EN PROCESO" ni
    "PROCESADO" a la fuerza - se le quita el override (estado_manual=NULL)
    para que vuelva a mandar estado_fuente, el de la hoja de Trazabilidad
    (normalmente "En espera" si nunca se había tocado). Encontrado por
    Stephano: anotó un lote, lo borró, y se quedó pegado en "En proceso"."""
    tiene_asignaciones = conn.execute(
        text("SELECT 1 FROM asignaciones WHERE lote_numero = :n LIMIT 1"), {"n": lote_numero}
    ).scalar()
    if not tiene_asignaciones:
        conn.execute(text("UPDATE lotes SET estado_manual = NULL WHERE numero = :n"), {"n": lote_numero})
        return
    saldo = conn.execute(
        text("SELECT kg_saldo FROM v_saldo_lotes WHERE numero = :n"), {"n": lote_numero}
    ).scalar()
    nuevo_estado = "PROCESADO" if saldo is not None and float(saldo) <= 0.01 else "EN PROCESO"
    conn.execute(
        text("UPDATE lotes SET estado_manual = :v WHERE numero = :n"),
        {"v": nuevo_estado, "n": lote_numero},
    )


class NuevaAsignacion(BaseModel):
    lote_numero: int
    corrida_id: int
    turno: str
    tipo_almacen_origen: str
    kg_asignados: Optional[float] = None  # None = "kg pendiente" (ver comentario en schema.sql)
    bines_consumidos: Optional[int] = None
    observaciones: Optional[str] = ""


@app.post("/api/asignaciones")
def crear_asignacion(a: NuevaAsignacion):
    if a.kg_asignados is not None and a.kg_asignados <= 0:
        raise HTTPException(status_code=400, detail="El peso a asignar debe ser mayor a 0.")
    try:
        with engine.begin() as conn:
            # Un mismo lote no puede quedar registrado 2 veces en la misma
            # corrida - antes no había ninguna validación y era fácil
            # duplicarlo sin darse cuenta (typeas el número de nuevo,
            # doble clic en Guardar), lo que infla el kg consumido y deja el
            # lote repetido como 2 filas en "Lotes de MP consumidos" del
            # reporte. Si de verdad hay más kg que sumarle a un lote que ya
            # está en esta corrida, se hace editando esa fila (✎ en
            # "Últimas asignaciones"), no creando otra.
            ya_existe = conn.execute(
                text(
                    "SELECT kg_asignados, turno FROM asignaciones "
                    "WHERE lote_numero = :n AND corrida_id = :c"
                ),
                {"n": a.lote_numero, "c": a.corrida_id},
            ).mappings().first()
            if ya_existe is not None:
                kg_txt = f"{float(ya_existe['kg_asignados']):,.2f} kg" if ya_existe["kg_asignados"] is not None else "kg pendiente"
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"El lote {a.lote_numero} ya está registrado en esta corrida "
                        f"({kg_txt}, turno {ya_existe['turno']}) - para agregar más kg, "
                        'edítalo en "Últimas asignaciones" (✎) en vez de crear otro registro.'
                    ),
                )
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
            # Registrar consumo y el "estado" del lote (En proceso/En espera/
            # Procesado) eran dos cosas totalmente desconectadas - el saldo se
            # actualizaba solo, pero el estado se quedaba en lo que decía la
            # última sincronización de Trazabilidad (o en nada), así que un
            # lote podía llevar miles de kg ya registrados y seguir viéndose
            # "En espera", o (el caso real que encontró Stephano) la hoja de
            # Google podía marcarlo "Procesado" mientras acá casi no se le
            # había registrado nada. Ahora, apenas se registra CUALQUIER
            # consumo de un lote, se marca su estado a mano - mismo campo
            # que ya se podía tocar desde Lotes, solo que ahora se dispara solo.
            _marcar_estado_por_consumo(conn, a.lote_numero)
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
            # mismo criterio que al crear: recalcula si el lote quedó en 0
            # kg de saldo tras la corrección (Procesado) o si le sigue
            # quedando algo (En proceso) - una edición también puede ser la
            # que complete el "kg pendiente" que lo termina de golpe.
            _marcar_estado_por_consumo(conn, lote_numero)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e).split("\n")[0])
    return {"ok": True}


@app.delete("/api/asignaciones/{asignacion_id}")
def eliminar_asignacion(asignacion_id: int):
    with engine.begin() as conn:
        lote_numero = conn.execute(
            text("SELECT lote_numero FROM asignaciones WHERE id = :id"), {"id": asignacion_id}
        ).scalar()
        if lote_numero is None:
            raise HTTPException(status_code=404, detail="Asignación no encontrada.")
        conn.execute(text("DELETE FROM asignaciones WHERE id = :id"), {"id": asignacion_id})
        _marcar_estado_por_consumo(conn, lote_numero)
    return {"ok": True}


# ---------------------------------------------------------------------------
# paradas (tiempos muertos de una corrida)
# ---------------------------------------------------------------------------
_PARADA_COLS = (
    "turno, area_proceso, tipo_parada, equipo_afectado, descripcion_falla, "
    "responsable_solucion, solucion_obs, recomendacion"
)


@app.get("/api/corridas/{corrida_id}/paradas")
def listar_paradas(corrida_id: int):
    with engine.connect() as conn:
        result = conn.execute(
            text(
                f"""
                SELECT id, corrida_id, hora_inicio, hora_fin, {_PARADA_COLS}, creado_en,
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


class DatosParada(BaseModel):
    turno: Optional[str] = None
    hora_inicio: str
    hora_fin: Optional[str] = None
    area_proceso: Optional[str] = None
    tipo_parada: Optional[str] = None
    equipo_afectado: Optional[str] = None
    descripcion_falla: Optional[str] = None
    responsable_solucion: Optional[str] = None
    solucion_obs: Optional[str] = None
    recomendacion: Optional[str] = None


class NuevaParada(DatosParada):
    corrida_id: int


def _params_parada(p: DatosParada):
    limpio = lambda s: (s.strip() or None) if isinstance(s, str) else s
    return {
        "turno": limpio(p.turno),
        "hora_inicio": p.hora_inicio,
        "hora_fin": p.hora_fin or None,
        "area_proceso": limpio(p.area_proceso),
        "tipo_parada": limpio(p.tipo_parada),
        "equipo_afectado": limpio(p.equipo_afectado),
        "descripcion_falla": limpio(p.descripcion_falla),
        "responsable_solucion": limpio(p.responsable_solucion),
        "solucion_obs": limpio(p.solucion_obs),
        "recomendacion": limpio(p.recomendacion),
    }


@app.post("/api/paradas")
def crear_parada(p: NuevaParada):
    if not p.hora_inicio:
        raise HTTPException(status_code=400, detail="La hora de inicio es obligatoria.")
    try:
        with engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    INSERT INTO paradas
                        (corrida_id, turno, hora_inicio, hora_fin, area_proceso, tipo_parada,
                         equipo_afectado, descripcion_falla, responsable_solucion, solucion_obs, recomendacion)
                    VALUES
                        (:corrida_id, :turno, :hora_inicio, :hora_fin, :area_proceso, :tipo_parada,
                         :equipo_afectado, :descripcion_falla, :responsable_solucion, :solucion_obs, :recomendacion)
                    RETURNING id
                    """
                ),
                {"corrida_id": p.corrida_id, **_params_parada(p)},
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


@app.post("/api/paradas/{parada_id}")
def editar_parada(parada_id: int, p: DatosParada):
    if not p.hora_inicio:
        raise HTTPException(status_code=400, detail="La hora de inicio es obligatoria.")
    try:
        with engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    UPDATE paradas SET
                        turno = :turno, hora_inicio = :hora_inicio, hora_fin = :hora_fin,
                        area_proceso = :area_proceso, tipo_parada = :tipo_parada,
                        equipo_afectado = :equipo_afectado, descripcion_falla = :descripcion_falla,
                        responsable_solucion = :responsable_solucion, solucion_obs = :solucion_obs,
                        recomendacion = :recomendacion
                    WHERE id = :id RETURNING id
                    """
                ),
                {"id": parada_id, **_params_parada(p)},
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
# mediciones de tanque (Brix/Acidez real medido, no el estimado de los lotes)
# ---------------------------------------------------------------------------
@app.get("/api/corridas/{corrida_id}/mediciones-tanque")
def listar_mediciones_tanque(corrida_id: int):
    with engine.connect() as conn:
        result = conn.execute(
            text(
                """
                SELECT id, corrida_id, tanque, litros, brix_inicial, brix_final, acidez, ph, observaciones, creado_en,
                       ROUND(brix_final / acidez, 2) AS ratio
                FROM mediciones_tanque
                WHERE corrida_id = :c
                ORDER BY creado_en ASC
                """
            ),
            {"c": corrida_id},
        )
        return rows(result)


class NuevaMedicionTanque(BaseModel):
    corrida_id: int
    tanque: str
    litros: Optional[float] = None
    brix_inicial: float  # obligatorio - el equipo usa el promedio del inicial como referencia
    brix_final: float
    acidez: float
    ph: Optional[float] = None
    observaciones: Optional[str] = ""


@app.post("/api/mediciones-tanque")
def crear_medicion_tanque(m: NuevaMedicionTanque):
    tanque = m.tanque.strip()
    if not tanque:
        raise HTTPException(status_code=400, detail="Escribe qué tanque es (ej. TK1).")
    if m.brix_inicial <= 0:
        raise HTTPException(status_code=400, detail="El Brix inicial debe ser mayor a 0.")
    if m.brix_final <= 0:
        raise HTTPException(status_code=400, detail="El Brix final debe ser mayor a 0.")
    if m.acidez <= 0:
        raise HTTPException(status_code=400, detail="La acidez debe ser mayor a 0.")
    try:
        with engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    INSERT INTO mediciones_tanque
                        (corrida_id, tanque, litros, brix_inicial, brix_final, acidez, ph, observaciones)
                    VALUES
                        (:corrida_id, :tanque, :litros, :brix_inicial, :brix_final, :acidez, :ph, :obs)
                    RETURNING id
                    """
                ),
                {
                    "corrida_id": m.corrida_id,
                    "tanque": tanque,
                    "litros": m.litros,
                    "brix_inicial": m.brix_inicial,
                    "brix_final": m.brix_final,
                    "acidez": m.acidez,
                    "ph": m.ph,
                    "obs": m.observaciones,
                },
            )
            new_id = result.scalar()
        return {"id": new_id, "ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e).split("\n")[0])


class EditarMedicionTanque(BaseModel):
    tanque: str
    litros: Optional[float] = None
    brix_inicial: float
    brix_final: float
    acidez: float
    ph: Optional[float] = None
    observaciones: Optional[str] = ""


@app.post("/api/mediciones-tanque/{medicion_id}")
def editar_medicion_tanque(medicion_id: int, m: EditarMedicionTanque):
    tanque = m.tanque.strip()
    if not tanque:
        raise HTTPException(status_code=400, detail="Escribe qué tanque es (ej. TK1).")
    if m.brix_inicial <= 0:
        raise HTTPException(status_code=400, detail="El Brix inicial debe ser mayor a 0.")
    if m.brix_final <= 0:
        raise HTTPException(status_code=400, detail="El Brix final debe ser mayor a 0.")
    if m.acidez <= 0:
        raise HTTPException(status_code=400, detail="La acidez debe ser mayor a 0.")
    try:
        with engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    UPDATE mediciones_tanque
                    SET tanque = :tanque, litros = :litros, brix_inicial = :brix_inicial,
                        brix_final = :brix_final, acidez = :acidez, ph = :ph, observaciones = :obs
                    WHERE id = :id RETURNING id
                    """
                ),
                {
                    "id": medicion_id,
                    "tanque": tanque,
                    "litros": m.litros,
                    "brix_inicial": m.brix_inicial,
                    "brix_final": m.brix_final,
                    "acidez": m.acidez,
                    "ph": m.ph,
                    "obs": m.observaciones,
                },
            )
            if result.scalar() is None:
                raise HTTPException(status_code=404, detail="Medición no encontrada.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e).split("\n")[0])
    return {"ok": True}


@app.delete("/api/mediciones-tanque/{medicion_id}")
def eliminar_medicion_tanque(medicion_id: int):
    with engine.begin() as conn:
        result = conn.execute(
            text("DELETE FROM mediciones_tanque WHERE id = :id RETURNING id"), {"id": medicion_id}
        )
        if result.scalar() is None:
            raise HTTPException(status_code=404, detail="Medición no encontrada.")
    return {"ok": True}


# ---------------------------------------------------------------------------
# exportar todo a Excel (.xlsx) - respaldo y para cuadrar contra Trazabilidad
# sin abrir la base. Una hoja por tabla, con los datos tal cual están ahora.
# ---------------------------------------------------------------------------
def _celda_xlsx(v):
    """Ajusta un valor de la DB para que Excel lo entienda bien: Decimal -> float,
    y las fechas con zona (creado_en, TIMESTAMPTZ en UTC) a hora de Perú sin zona
    (mismo criterio que los reportes PDF)."""
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime) and v.tzinfo is not None:
        return v.astimezone(timezone(timedelta(hours=-5))).replace(tzinfo=None)
    if isinstance(v, (datetime, date)):
        return v
    return v


def _hoja_xlsx(wb, nombre, result):
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    ws = wb.create_sheet(nombre[:31])  # Excel: máximo 31 chars por nombre de hoja
    cols = list(result.keys())
    ws.append(cols)
    n = 0
    for row in result:
        ws.append([_celda_xlsx(v) for v in row])
        n += 1
    for c in ws[1]:
        c.font = Font(bold=True)
    ws.freeze_panes = "A2"
    for i, col in enumerate(cols, 1):
        ws.column_dimensions[get_column_letter(i)].width = min(42, max(12, len(str(col)) + 3))
    return n


@app.get("/api/export.xlsx")
def exportar_xlsx():
    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.active)
    with engine.connect() as conn:
        _hoja_xlsx(wb, "Lotes", conn.execute(text(
            "SELECT * FROM v_saldo_lotes ORDER BY numero"
        )))
        _hoja_xlsx(wb, "Corridas", conn.execute(text(
            """
            SELECT v.*, c.estado, c.rendimiento, c.brix_promedio_tk
            FROM v_cuadre_corridas v JOIN corridas c ON c.id = v.id
            ORDER BY v.fecha_inicio
            """
        )))
        _hoja_xlsx(wb, "Consumos", conn.execute(text(
            """
            SELECT a.id, c.nombre AS corrida, a.lote_numero, l.proveedor,
                   a.turno, a.tipo_almacen_origen, a.bines_consumidos, a.kg_asignados,
                   a.creado_en, a.observaciones
            FROM asignaciones a
            JOIN corridas c ON c.id = a.corrida_id
            JOIN lotes l ON l.numero = a.lote_numero
            ORDER BY a.creado_en
            """
        )))
        _hoja_xlsx(wb, "Mediciones de tanque", conn.execute(text(
            """
            SELECT c.nombre AS corrida, m.tanque, m.litros, m.brix_inicial, m.brix_final,
                   m.acidez, m.ph, ROUND(m.brix_final / m.acidez, 2) AS ratio,
                   m.creado_en, m.observaciones
            FROM mediciones_tanque m
            JOIN corridas c ON c.id = m.corrida_id
            ORDER BY c.fecha_inicio, m.creado_en
            """
        )))
        _hoja_xlsx(wb, "Productos de salida", conn.execute(text(
            """
            SELECT c.nombre AS corrida, p.producto, p.tambores, p.peso_neto_tambor_kg,
                   p.pt_kg, p.volumen_litros, p.observaciones
            FROM corrida_productos p
            JOIN corridas c ON c.id = p.corrida_id
            ORDER BY c.fecha_inicio, p.producto
            """
        )))
        _hoja_xlsx(wb, "Paradas", conn.execute(text(
            """
            SELECT c.nombre AS corrida, p.turno, p.hora_inicio, p.hora_fin,
                   CASE WHEN p.hora_fin IS NULL THEN NULL
                        ELSE ROUND(EXTRACT(EPOCH FROM (p.hora_fin - p.hora_inicio)) / 60)
                   END AS min_total,
                   p.area_proceso, p.tipo_parada, p.equipo_afectado, p.descripcion_falla,
                   p.responsable_solucion, p.solucion_obs, p.recomendacion
            FROM paradas p
            JOIN corridas c ON c.id = p.corrida_id
            ORDER BY c.fecha_inicio, p.hora_inicio
            """
        )))

    buf = BytesIO()
    wb.save(buf)
    nombre = f"agromar-datos-{ahora_peru().strftime('%Y-%m-%d')}.xlsx"
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{nombre}"',
            "Cache-Control": "no-store",
        },
    )


# ---------------------------------------------------------------------------
# front-end estático (todo en el mismo servicio - un solo link para compartir)
# ---------------------------------------------------------------------------
app.mount("/", StaticFiles(directory=BASE_DIR / "web", html=True), name="web")
