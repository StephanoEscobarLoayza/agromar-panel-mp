-- ============================================================================
-- ESQUEMA: Panel de Cuadre de Producción (Agromar - Naranja)
-- Motor: PostgreSQL (Render Postgres, Supabase, o cualquier Postgres 13+)
-- ============================================================================

-- ----------------------------------------------------------------------------
-- LOTES: maestro de lotes recibidos (sincronizado desde tu Google Sheet de
-- recepción de camiones). Incluye toda la calidad de recepción: brix, pH,
-- acidez, ratio, procedencia, fecha de ingreso.
-- ----------------------------------------------------------------------------
CREATE TABLE lotes (
    numero              INTEGER PRIMARY KEY,
    proveedor           TEXT NOT NULL,
    procedencia         TEXT,
    guia                TEXT,
    placa               TEXT,
    fecha_ingreso       DATE NOT NULL,
    tipo_almacen        TEXT NOT NULL CHECK (tipo_almacen IN ('SILO', 'BINES', 'MIXTO')),
    peso_neto_kg        NUMERIC(10,2) NOT NULL,
    bines_totales       INTEGER,              -- solo aplica si tipo_almacen incluye BINES
    brix_recepcion      NUMERIC(5,2),
    ph_recepcion        NUMERIC(4,2),
    acidez              NUMERIC(5,2),
    ratio               NUMERIC(6,2),
    ubicacion           TEXT,                 -- "Silo 1", "Silo 2", "Tolva"... (tal cual llega de recepción)
    materia_prima       TEXT NOT NULL DEFAULT 'NARANJA ORGÁNICA',
    creado_en           TIMESTAMPTZ NOT NULL DEFAULT now(),
    actualizado_en      TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE lotes IS 'Maestro de lotes recibidos, sincronizado desde el Google Sheet de recepción de camiones.';
COMMENT ON COLUMN lotes.brix_recepcion IS 'Brix medido al momento de recepción del camión (distinto del brix medido en línea al procesar).';

-- ----------------------------------------------------------------------------
-- CORRIDAS: la unidad real de cuadre (NO la fecha). Una corrida puede cruzar
-- medianoche (fecha_inicio y fecha_final en días distintos) - esto es lo que
-- rompía el cuadre en Excel al registrar todo por "fecha del día".
-- ----------------------------------------------------------------------------
CREATE TABLE corridas (
    id                  SERIAL PRIMARY KEY,
    nombre              TEXT NOT NULL UNIQUE,        -- ej "01-Setiembre JSA Aseptic"
    tipo_proceso        TEXT,                          -- JSA / JCC / JSC / ICEGEN...
    fecha_inicio        TIMESTAMP NOT NULL,
    fecha_final         TIMESTAMP,                     -- null mientras sigue abierta
    fecha_proceso_ref   DATE,                          -- "F. Proceso" que reporta Trazabilidad, para mostrar en listas
    mp_kg_objetivo      NUMERIC(12,2),                 -- "MP kg" de Trazabilidad: contra esto se cuadra
    brix_promedio_tk    NUMERIC(5,2),
    rendimiento         NUMERIC(6,4),
    estado              TEXT NOT NULL DEFAULT 'abierta' CHECK (estado IN ('abierta', 'cerrada')),
    creado_en           TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE corridas IS 'Cada corrida de producción. Es la unidad de cuadre real, no la fecha calendario.';
COMMENT ON COLUMN corridas.mp_kg_objetivo IS 'Total "MP kg" reportado por Trazabilidad para esta corrida - el número contra el que se cuadra.';

-- ----------------------------------------------------------------------------
-- ASIGNACIONES: reemplaza la hoja CONSUMOS. Cada fila = "este lote alimentó
-- esta corrida por tantos kg". Relación muchos-a-muchos: un lote puede
-- repartirse entre varias corridas (como 2318 y 2291, partidos entre dos),
-- y una corrida jala de varios lotes.
-- ----------------------------------------------------------------------------
CREATE TABLE asignaciones (
    id                  SERIAL PRIMARY KEY,
    lote_numero         INTEGER NOT NULL REFERENCES lotes(numero),
    corrida_id          INTEGER NOT NULL REFERENCES corridas(id),
    fecha_proceso       TIMESTAMP NOT NULL,            -- momento real en que se metió este lote a producción
    turno               TEXT CHECK (turno IN ('DÍA', 'NOCHE')),
    tipo_almacen_origen TEXT CHECK (tipo_almacen_origen IN ('SILO', 'BINES')),
    kg_asignados        NUMERIC(10,2) NOT NULL CHECK (kg_asignados > 0),
    bines_consumidos    INTEGER,
    brix_produccion     NUMERIC(5,2),                  -- brix medido EN LÍNEA al procesar (puede diferir del de recepción)
    observaciones       TEXT,
    usuario             TEXT,
    creado_en           TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (lote_numero, corrida_id, fecha_proceso, turno)
);

COMMENT ON TABLE asignaciones IS 'Registro de consumo: qué lote alimentó qué corrida, y cuánto. Reemplaza la hoja CONSUMOS de Excel.';

CREATE INDEX idx_asignaciones_lote ON asignaciones(lote_numero);
CREATE INDEX idx_asignaciones_corrida ON asignaciones(corrida_id);

-- ----------------------------------------------------------------------------
-- VALIDACIÓN AUTOMÁTICA: bloquea a nivel de base de datos si se intenta
-- asignar más kg de los que el lote tiene disponibles. Esto es justamente lo
-- que falló con el lote 2333 (se asignó parcial en una corrida y luego total
-- en otra, sumando más de lo que el lote realmente pesaba) - con este trigger
-- ya no puede volver a pasar, sin importar quién cargue el dato ni desde dónde.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_validar_saldo_lote()
RETURNS TRIGGER AS $$
DECLARE
    peso_total       NUMERIC;
    ya_asignado       NUMERIC;
    saldo_disponible  NUMERIC;
BEGIN
    SELECT peso_neto_kg INTO peso_total FROM lotes WHERE numero = NEW.lote_numero;

    SELECT COALESCE(SUM(kg_asignados), 0) INTO ya_asignado
    FROM asignaciones
    WHERE lote_numero = NEW.lote_numero
      AND id <> COALESCE(NEW.id, -1);

    saldo_disponible := peso_total - ya_asignado;

    IF NEW.kg_asignados > saldo_disponible THEN
        RAISE EXCEPTION 'El lote % solo tiene % kg de saldo disponible (se intentó asignar % kg)',
            NEW.lote_numero, saldo_disponible, NEW.kg_asignados;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_validar_saldo_lote
BEFORE INSERT OR UPDATE ON asignaciones
FOR EACH ROW EXECUTE FUNCTION fn_validar_saldo_lote();

-- ----------------------------------------------------------------------------
-- VISTAS: saldo por lote y cuadre por corrida, calculados solos - nada de
-- fórmulas de array que se rompen con un guardado.
-- ----------------------------------------------------------------------------
CREATE VIEW v_saldo_lotes AS
SELECT
    l.numero,
    l.proveedor,
    l.procedencia,
    l.tipo_almacen,
    l.ubicacion,
    l.fecha_ingreso,
    l.brix_recepcion,
    l.acidez,
    l.ratio,
    l.peso_neto_kg,
    COALESCE(SUM(a.kg_asignados), 0)                     AS kg_consumidos,
    l.peso_neto_kg - COALESCE(SUM(a.kg_asignados), 0)     AS kg_saldo,
    l.bines_totales,
    l.bines_totales - COALESCE(SUM(a.bines_consumidos), 0) AS bines_saldo
FROM lotes l
LEFT JOIN asignaciones a ON a.lote_numero = l.numero
GROUP BY l.numero, l.proveedor, l.procedencia, l.tipo_almacen, l.ubicacion, l.fecha_ingreso,
         l.brix_recepcion, l.acidez, l.ratio, l.peso_neto_kg, l.bines_totales;

CREATE VIEW v_cuadre_corridas AS
SELECT
    c.id,
    c.nombre,
    c.fecha_inicio,
    c.fecha_final,
    c.mp_kg_objetivo,
    COALESCE(SUM(a.kg_asignados), 0)                    AS kg_asignados_total,
    c.mp_kg_objetivo - COALESCE(SUM(a.kg_asignados), 0) AS diferencia_kg,
    CASE
        WHEN c.mp_kg_objetivo IS NULL THEN 'sin_objetivo'
        WHEN ABS(c.mp_kg_objetivo - COALESCE(SUM(a.kg_asignados), 0)) < 1 THEN 'cuadra'
        WHEN COALESCE(SUM(a.kg_asignados), 0) > c.mp_kg_objetivo THEN 'excedido'
        ELSE 'incompleto'
    END AS estado_cuadre
FROM corridas c
LEFT JOIN asignaciones a ON a.corrida_id = c.id
GROUP BY c.id, c.nombre, c.fecha_inicio, c.fecha_final, c.mp_kg_objetivo;
