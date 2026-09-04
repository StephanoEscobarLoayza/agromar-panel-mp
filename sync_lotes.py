"""
Trae los lotes desde la hoja de Google Sheets de recepción de camiones
("23082024 DATOS DE FRUTA PARA BALANZA NJ ORGANICA 2026", pestaña
"NJ ORGANICA VAL") hacia la tabla `lotes` de la base de datos.

La hoja tiene que seguir compartida como "cualquiera con el enlace puede
ver" - así se lee directo, sin ninguna credencial de Google.

Se puede correr las veces que haga falta: los lotes que ya existen se
actualizan (por ejemplo cuando un lote que estaba "EN ESPERA" ya se pesó),
los nuevos se agregan. Nunca borra nada.

`sincronizar_lotes(engine)` es la función reusable — la usa tanto este
script por línea de comandos como el botón "Sincronizar" de la página web
(POST /api/sync/lotes en api.py), para no tener la lógica duplicada.

Correr por línea de comandos:
    python sync_lotes.py
"""
import csv
import io
import os
import urllib.request
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

SHEET_ID = "13RhflCqsQexgJVqLBHB28YVc52V-vy9bXRpuvKX4zbM"
TAB_NAME = "NJ ORGANICA VAL"
CSV_URL = (
    f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq"
    f"?tqx=out:csv&sheet={TAB_NAME.replace(' ', '%20')}"
)

# columnas de la hoja, en orden (A..N):
# UBICACIÓN, LOTE NISIRA, Proveedor, PLACA, MATERIA PRIMA, FECHA INGRESO A
# PLANTA, BRIX REFRACTÓMETRO, BINES/TOLVA, pH, ACIDEZ, RATIO, PROCEDENCIA,
# PESO, ESTADO


def num(s):
    """'10,04' / '  14,76 ' -> 10.04 / 14.76 (decimal con coma). '' -> None."""
    if s is None:
        return None
    s = s.strip().replace(",", ".")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def fecha(s):
    """'01/08/2026' (día/mes/año) -> date. '' -> None."""
    if not s or not s.strip():
        return None
    try:
        return datetime.strptime(s.strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


def tipo_almacen_y_bines(bines_tolva):
    """BINES/TOLVA trae un número (bines contados) o 'T' (tolva - a granel,
    sin conteo, igual que silo: el kg se estima a ojo, no por unidad)."""
    b = (bines_tolva or "").strip()
    if b.upper() == "T":
        return "SILO", None
    try:
        return "BINES", int(b)
    except ValueError:
        return "SILO", None


def sincronizar_lotes(engine):
    """Descarga la hoja y hace upsert en `lotes`. Devuelve un dict con los
    contadores de resultado (para mostrar en la respuesta de la API o en
    la salida de consola)."""
    with urllib.request.urlopen(CSV_URL, timeout=30) as resp:
        contenido = resp.read().decode("utf-8")

    filas = list(csv.reader(io.StringIO(contenido)))
    datos = filas[1:]  # sin encabezado

    insertados = actualizados = omitidos = 0

    with engine.begin() as conn:
        for fila in datos:
            if len(fila) < 14:
                continue
            (ubicacion, lote_s, proveedor, placa, materia_prima, fecha_s,
             brix_s, bines_tolva, ph_s, acidez_s, ratio_s, procedencia,
             peso_s, estado) = fila[:14]

            lote_s = lote_s.strip()
            if not lote_s.isdigit():
                continue
            numero = int(lote_s)

            peso = num(peso_s)
            proveedor = proveedor.strip()
            fecha_ingreso = fecha(fecha_s)

            # lotes "EN ESPERA" que aun no llegaron/pesaron no tienen estos
            # datos todavia - se importan solos la proxima vez que se corra
            # esto, una vez que ya los hayan pesado.
            if peso is None or not proveedor or fecha_ingreso is None:
                omitidos += 1
                continue

            tipo_almacen, bines_totales = tipo_almacen_y_bines(bines_tolva)

            data = {
                "numero": numero,
                "proveedor": proveedor,
                "procedencia": procedencia.strip() or None,
                "placa": placa.strip() or None,
                "fecha_ingreso": fecha_ingreso,
                "tipo_almacen": tipo_almacen,
                "peso_neto_kg": peso,
                "bines_totales": bines_totales,
                "brix_recepcion": num(brix_s),
                "ph_recepcion": num(ph_s),
                "acidez": num(acidez_s),
                "ratio": num(ratio_s),
                "materia_prima": materia_prima.strip() or "NARANJA ORGÁNICA",
                "ubicacion": ubicacion.strip() or None,
                "estado_fuente": estado.strip() or None,
            }

            resultado = conn.execute(
                text(
                    """
                    INSERT INTO lotes
                        (numero, proveedor, procedencia, placa, fecha_ingreso, tipo_almacen,
                         peso_neto_kg, bines_totales, brix_recepcion, ph_recepcion, acidez,
                         ratio, materia_prima, ubicacion, estado_fuente)
                    VALUES
                        (:numero, :proveedor, :procedencia, :placa, :fecha_ingreso, :tipo_almacen,
                         :peso_neto_kg, :bines_totales, :brix_recepcion, :ph_recepcion, :acidez,
                         :ratio, :materia_prima, :ubicacion, :estado_fuente)
                    ON CONFLICT (numero) DO UPDATE SET
                        proveedor = EXCLUDED.proveedor,
                        procedencia = EXCLUDED.procedencia,
                        placa = EXCLUDED.placa,
                        fecha_ingreso = EXCLUDED.fecha_ingreso,
                        tipo_almacen = EXCLUDED.tipo_almacen,
                        peso_neto_kg = EXCLUDED.peso_neto_kg,
                        bines_totales = EXCLUDED.bines_totales,
                        brix_recepcion = EXCLUDED.brix_recepcion,
                        ph_recepcion = EXCLUDED.ph_recepcion,
                        acidez = EXCLUDED.acidez,
                        ratio = EXCLUDED.ratio,
                        materia_prima = EXCLUDED.materia_prima,
                        ubicacion = EXCLUDED.ubicacion,
                        estado_fuente = EXCLUDED.estado_fuente,
                        actualizado_en = now()
                    RETURNING (xmax = 0) AS es_insercion
                    """
                ),
                data,
            )
            if resultado.scalar():
                insertados += 1
            else:
                actualizados += 1

    return {"insertados": insertados, "actualizados": actualizados, "omitidos": omitidos}


def main():
    DATABASE_URL = os.environ.get("DATABASE_URL_POOLED") or os.environ.get("DATABASE_URL")
    if not DATABASE_URL:
        raise SystemExit("Falta DATABASE_URL en .env")
    engine = create_engine(DATABASE_URL)
    r = sincronizar_lotes(engine)
    print(
        f"Listo: {r['insertados']} lotes nuevos, {r['actualizados']} actualizados, "
        f"{r['omitidos']} omitidos (todavía sin peso/proveedor/fecha - lotes en espera)."
    )


if __name__ == "__main__":
    main()
