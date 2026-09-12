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
    estado_fuente       TEXT,                 -- "PROCESADO"/"EN PROCESO"/"EN ESPERA" tal cual llega de recepción
    estado_manual       TEXT,                 -- override de producción cuando Calidad aún no actualizó el Sheet; NULL = usar estado_fuente
    ubicacion_manual    TEXT,                 -- override de producción sobre ubicacion; NULL = usar ubicacion
    materia_prima       TEXT NOT NULL DEFAULT 'NARANJA ORGÁNICA',
    creado_en           TIMESTAMPTZ NOT NULL DEFAULT now(),
    actualizado_en      TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE lotes IS 'Maestro de lotes recibidos, sincronizado desde el Google Sheet de recepción de camiones.';
COMMENT ON COLUMN lotes.brix_recepcion IS 'Brix medido al momento de recepción del camión (distinto del brix medido en línea al procesar).';
COMMENT ON COLUMN lotes.estado_manual IS 'Override de producción sobre estado_fuente cuando Calidad aún no actualizó el Sheet. NULL = usar el del Sheet.';
COMMENT ON COLUMN lotes.ubicacion_manual IS 'Override de producción sobre ubicacion cuando Calidad aún no actualizó el Sheet. NULL = usar el del Sheet.';

-- ----------------------------------------------------------------------------
-- CORRIDAS: la unidad real de cuadre (NO la fecha). Una corrida puede cruzar
-- medianoche (fecha_inicio y fecha_final en días distintos) - esto es lo que
-- rompía el cuadre en Excel al registrar todo por "fecha del día".
-- ----------------------------------------------------------------------------
CREATE TABLE corridas (
    id                  SERIAL PRIMARY KEY,
    nombre              TEXT NOT NULL UNIQUE,        -- ej "01-Setiembre JSA Aseptic"
    tipo_proceso        TEXT,                          -- JSA / JCC / JCC TASTE / JSC / ICEGEN...
    fecha_inicio        TIMESTAMP NOT NULL,
    fecha_final         TIMESTAMP,                     -- null mientras sigue abierta
    fecha_proceso_ref   DATE,                          -- "F. Proceso" que reporta Trazabilidad, para mostrar en listas
    mp_kg_objetivo      NUMERIC(12,2),                 -- "MP kg" de Trazabilidad: contra esto se cuadra
    brix_promedio_tk    NUMERIC(5,2),
    rendimiento         NUMERIC(6,4),
    estado              TEXT NOT NULL DEFAULT 'abierta' CHECK (estado IN ('abierta', 'cerrada')),
    creado_en           TIMESTAMPTZ NOT NULL DEFAULT now(),
    stock_inicio_kg     NUMERIC(14,2),                 -- suma de kg_saldo de los lotes EN PROCESO/EN ESPERA al crear esta corrida
    stock_cierre_kg     NUMERIC(14,2)                  -- lo mismo al finalizar - NULL de nuevo si se reabre
);

COMMENT ON TABLE corridas IS 'Cada corrida de producción. Es la unidad de cuadre real, no la fecha calendario.';
COMMENT ON COLUMN corridas.mp_kg_objetivo IS 'Total "MP kg" reportado por Trazabilidad para esta corrida - el número contra el que se cuadra.';
COMMENT ON COLUMN corridas.stock_inicio_kg IS 'Foto del stock en piso (Silo+Bines) al crear la corrida - mismo criterio que el reporte de stock: solo lotes EN PROCESO/EN ESPERA, un lote PROCESADO con saldo residual es ruido de Trazabilidad, no MP disponible. NULL en corridas creadas antes de este campo existir.';
COMMENT ON COLUMN corridas.stock_cierre_kg IS 'Lo mismo al finalizar. Se limpia a NULL si se reabre, hasta que se vuelva a finalizar.';

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
    kg_asignados        NUMERIC(10,2) CHECK (kg_asignados IS NULL OR kg_asignados > 0),
                                                       -- NULL = "kg pendiente": el lote ya empezó a
                                                       -- alimentar la corrida pero todavía no se sabe
                                                       -- cuánto (típico en Silo, se calcula al cierre) -
                                                       -- se completa después con editar_asignacion
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
-- asignar más kg (o más bines) de los que el lote tiene disponibles. Esto es
-- justamente lo que falló con el lote 2333 (se asignó parcial en una corrida
-- y luego total en otra, sumando más de lo que el lote realmente pesaba) -
-- con este trigger ya no puede volver a pasar, sin importar quién cargue el
-- dato ni desde dónde.
--
-- La validación de bines se agregó después (2026-09-11): antes solo se
-- chequeaba en el código de Python (`bines_disponibles()` en api.py) antes
-- del INSERT/UPDATE - una consulta aparte, sin ninguna protección real si dos
-- corridas registran consumo del MISMO lote casi al mismo instante (dos
-- personas trabajando corridas en paralelo, por ejemplo). El chequeo de
-- Python se mantiene (da un mensaje de error más amable, sin llegar a
-- tocar la base), pero ahora este trigger es el que de verdad no deja pasar
-- el exceso, pase lo que pase del lado de la aplicación.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_validar_saldo_lote()
RETURNS TRIGGER AS $$
DECLARE
    peso_total          NUMERIC;
    ya_asignado         NUMERIC;
    saldo_disponible    NUMERIC;
    bines_total         INTEGER;
    bines_ya_asignados  INTEGER;
    bines_disponibles   INTEGER;
BEGIN
    IF NEW.kg_asignados IS NULL THEN
        RETURN NEW;  -- "kg pendiente" - nada que validar todavía
    END IF;

    SELECT peso_neto_kg, bines_totales INTO peso_total, bines_total
    FROM lotes WHERE numero = NEW.lote_numero;

    SELECT COALESCE(SUM(kg_asignados), 0) INTO ya_asignado
    FROM asignaciones
    WHERE lote_numero = NEW.lote_numero
      AND id <> COALESCE(NEW.id, -1);

    saldo_disponible := peso_total - ya_asignado;

    IF NEW.kg_asignados > saldo_disponible THEN
        RAISE EXCEPTION 'El lote % solo tiene % kg de saldo disponible (se intentó asignar % kg)',
            NEW.lote_numero, saldo_disponible, NEW.kg_asignados;
    END IF;

    -- mismo criterio para bines - solo aplica si el lote se maneja por bines
    -- (bines_totales no es null) y esta fila trae un conteo de bines.
    IF NEW.bines_consumidos IS NOT NULL AND bines_total IS NOT NULL THEN
        SELECT COALESCE(SUM(bines_consumidos), 0) INTO bines_ya_asignados
        FROM asignaciones
        WHERE lote_numero = NEW.lote_numero
          AND id <> COALESCE(NEW.id, -1);

        bines_disponibles := bines_total - bines_ya_asignados;

        IF NEW.bines_consumidos > bines_disponibles THEN
            RAISE EXCEPTION 'El lote % solo tiene % bin(es) de saldo disponible (se intentó asignar % bines)',
                NEW.lote_numero, bines_disponibles, NEW.bines_consumidos;
        END IF;
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
    l.estado_fuente,
    l.fecha_ingreso,
    l.brix_recepcion,
    l.acidez,
    l.ratio,
    l.peso_neto_kg,
    COALESCE(SUM(a.kg_asignados), 0)                     AS kg_consumidos,
    l.peso_neto_kg - COALESCE(SUM(a.kg_asignados), 0)     AS kg_saldo,
    l.bines_totales,
    l.bines_totales - COALESCE(SUM(a.bines_consumidos), 0) AS bines_saldo,
    l.estado_manual,
    l.ubicacion_manual,
    COALESCE(l.estado_manual, l.estado_fuente) AS estado_actual,
    COALESCE(l.ubicacion_manual, l.ubicacion) AS ubicacion_actual
FROM lotes l
LEFT JOIN asignaciones a ON a.lote_numero = l.numero
GROUP BY l.numero, l.proveedor, l.procedencia, l.tipo_almacen, l.ubicacion, l.estado_fuente, l.fecha_ingreso,
         l.brix_recepcion, l.acidez, l.ratio, l.peso_neto_kg, l.bines_totales, l.estado_manual, l.ubicacion_manual;

-- ----------------------------------------------------------------------------
-- CORRIDA_PRODUCTOS: cuando una corrida produce más de un producto terminado
-- en paralelo (ej. Concentrado + Aséptico desde el mismo lote de MP), NO se
-- reparte el MP crudo entre productos (los tambores de cada producto pesan
-- distinto y el rendimiento también difiere - repartir a mano el kg de MP
-- es lo que arriesgaba descuadrar). En cambio la corrida sigue siendo UNA
-- sola (un solo mp_kg_objetivo), y cada producto de salida se registra por
-- separado con lo que producción ya pesa al final: tambores y peso neto del
-- tambor. Mismos 4 campos que Trazabilidad ya trae por corrida (Tambores /
-- Peso Neto del tambor / PT kg / Rendimiento) - solo que ahora puede haber
-- más de un bloque de esos por corrida.
-- ----------------------------------------------------------------------------
CREATE TABLE corrida_productos (
    id                   SERIAL PRIMARY KEY,
    corrida_id           INTEGER NOT NULL REFERENCES corridas(id) ON DELETE CASCADE,
    producto             TEXT NOT NULL,             -- "Concentrado", "Aséptico", "Jugo Simple"...
    tipo                 TEXT NOT NULL DEFAULT 'salida' CHECK (tipo IN ('salida', 'entrada')), -- salida = lo que produjo esta corrida (Productos de salida); entrada = insumo que se metió a la corrida desde afuera, ej. reposición para subir Brix (Insumos de entrada) - nunca cuenta como PT
    tambores             INTEGER,
    peso_neto_tambor_kg  NUMERIC(8,2),
    pt_kg                NUMERIC(12,2),              -- producto terminado en kg (tambores × peso, o cargado directo)
    volumen_litros       NUMERIC(12,2),              -- litros de producto terminado, cargados directo (no se calcula con un factor aproximado)
    cuenta_como_pt       BOOLEAN NOT NULL DEFAULT TRUE, -- si suma al PT kg/rendimiento/volumen de los reportes - se desmarca a mano (enjuague, saldo de tambor sin completar, etc.); las filas "entrada" nunca cuentan, sin importar esta marca
    observaciones        TEXT,
    creado_en            TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (corrida_id, producto)
);

COMMENT ON TABLE corrida_productos IS 'Productos de salida (y, si aplica, insumos de entrada) de una corrida (soporta producción en paralelo de más de un producto desde el mismo MP).';
COMMENT ON COLUMN corrida_productos.tipo IS 'salida = lo que produjo esta corrida (aparece en Productos de salida). entrada = insumo/producto que se metió a la corrida desde afuera (ej. reposición para subir Brix) - aparece en Insumos de entrada, nunca cuenta como PT.';
COMMENT ON COLUMN corrida_productos.cuenta_como_pt IS 'Si esta fila cuenta como producto terminado real en los reportes (PT kg, rendimiento, volumen). Se marca/desmarca a mano por fila - antes se adivinaba si el nombre decía "enjuague", pero eso no cubría casos como un saldo de tambor sin completar. Las filas tipo=entrada nunca cuentan, sin importar esta marca.';

-- ----------------------------------------------------------------------------
-- PARADAS: tiempos muertos durante una corrida (falla mecánica, falta de MP,
-- limpieza, cambio de producto, etc.). Siempre ligada a la corrida que
-- estaba en curso - mismo criterio que asignaciones. hora_fin queda NULL
-- mientras la parada sigue en curso (se "cierra" cuando termina, igual que
-- una corrida se abre/cierra) o se puede cargar directo si ya se sabe.
-- ----------------------------------------------------------------------------
CREATE TABLE paradas (
    id                   SERIAL PRIMARY KEY,
    corrida_id           INTEGER NOT NULL REFERENCES corridas(id) ON DELETE CASCADE,
    turno                TEXT,              -- DÍA / NOCHE
    hora_inicio          TIMESTAMP NOT NULL,
    hora_fin             TIMESTAMP,         -- null mientras la parada sigue en curso
    area_proceso         TEXT,              -- Abastecimiento / Extracción / Estandarizado / Concentrador / Caldero
    tipo_parada          TEXT,              -- Mecánico / Eléctrico / Operativo / Limpieza-CIP / Calidad / Espera producción / Falta material
    equipo_afectado      TEXT,
    descripcion_falla    TEXT,              -- antes se llamaba "motivo"
    responsable_solucion TEXT,              -- Mantenimiento / Maquinista
    solucion_obs         TEXT,              -- "solución y/o observaciones" de la hoja de paradas
    recomendacion        TEXT,
    creado_en            TIMESTAMPTZ NOT NULL DEFAULT now(),

    CHECK (hora_fin IS NULL OR hora_fin >= hora_inicio)
);

COMMENT ON TABLE paradas IS 'Tiempos muertos registrados durante una corrida - turno, horas, área/proceso, tipo, equipo, falla y solución. Réplica de la hoja de paradas de planta.';

CREATE INDEX idx_paradas_corrida ON paradas(corrida_id);

-- ----------------------------------------------------------------------------
-- MEDICIONES_TANQUE: Brix/Acidez/pH medido de verdad con el refractómetro por
-- tanque (~3000-5000 L cada uno), durante una corrida. Existe porque el Brix
-- estimado desde los lotes de MP (recepción) sistemáticamente no coincide con
-- el real: la extracción concentra el jugo, y en estandarizado se agrega un
-- enjuague con su propio Brix/Acidez que tampoco viene de ningún lote - no
-- hay forma de calcular el real desde los lotes, solo de medirlo. Cuando una
-- corrida tiene al menos una medición, el reporte usa ESTE número, no el
-- estimado (ver reporte_corrida.py).
-- ----------------------------------------------------------------------------
CREATE TABLE mediciones_tanque (
    id            SERIAL PRIMARY KEY,
    corrida_id    INTEGER NOT NULL REFERENCES corridas(id) ON DELETE CASCADE,
    tanque        TEXT NOT NULL,          -- ej "TK1" - texto libre, no un catalogo fijo de tanques
    litros        NUMERIC(10,2),
    brix_inicial  NUMERIC(5,2),           -- al empezar a llenar el tanque (opcional)
    brix_final    NUMERIC(5,2) NOT NULL CHECK (brix_final > 0),
    acidez        NUMERIC(5,3) NOT NULL CHECK (acidez > 0),
    ph            NUMERIC(4,2),
    observaciones TEXT,
    creado_en     TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE mediciones_tanque IS 'Brix/Acidez/pH medido de verdad con el refractómetro por tanque - el número real de producción (incluye enjuague y cualquier ajuste de estandarización), no el estimado desde los lotes de MP.';

CREATE INDEX idx_mediciones_tanque_corrida ON mediciones_tanque(corrida_id);

CREATE VIEW v_cuadre_corridas AS
SELECT
    c.id,
    c.nombre,
    c.fecha_inicio,
    c.fecha_final,
    c.mp_kg_objetivo,
    c.stock_inicio_kg,
    c.stock_cierre_kg,
    COALESCE(SUM(a.kg_asignados), 0)                    AS kg_asignados_total,
    c.mp_kg_objetivo - COALESCE(SUM(a.kg_asignados), 0) AS diferencia_kg,
    CASE
        WHEN c.mp_kg_objetivo IS NULL THEN 'sin_objetivo'
        WHEN ABS(c.mp_kg_objetivo - COALESCE(SUM(a.kg_asignados), 0)) < 1 THEN 'cuadra'
        WHEN COALESCE(SUM(a.kg_asignados), 0) > c.mp_kg_objetivo THEN 'excedido'
        ELSE 'incompleto'
    END AS estado_cuadre,
    c.tipo_proceso,
    COUNT(a.id) AS n_asignaciones -- filas reales (incluye "kg pendiente") - kg_asignados_total puede ser 0 con filas de sobra
FROM corridas c
LEFT JOIN asignaciones a ON a.corrida_id = c.id
GROUP BY c.id, c.nombre, c.fecha_inicio, c.fecha_final, c.mp_kg_objetivo, c.stock_inicio_kg, c.stock_cierre_kg, c.tipo_proceso;
