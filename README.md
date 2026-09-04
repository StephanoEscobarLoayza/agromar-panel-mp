# Panel de Cuadre de Producción — Agromar

App para reemplazar el cuadre manual en Excel (CONSUMOS vs Trazabilidad) por un
sistema con validación en vivo. Diseñado para el equipo administrativo de
producción, uso en tablet.

## Por qué esto y no Excel

- Las corridas pueden cruzar medianoche (empiezan un día, terminan de
  madrugada al siguiente) — registrar por "fecha" en vez de por "corrida" es
  lo que rompía el cuadre. Aquí la corrida es la unidad central.
- Un lote puede repartirse entre varias corridas — el modelo es
  muchos-a-muchos desde el principio, no una hoja lineal.
- La base de datos bloquea automáticamente si se intenta asignar más kg de
  los que un lote tiene disponibles (ver `schema.sql`, trigger
  `trg_validar_saldo_lote`) — el problema del lote 2333 (asignado dos veces,
  sumando más de lo que pesaba) no puede volver a pasar.

## Estructura

- `schema.sql` — esquema completo de PostgreSQL: tablas `lotes`, `corridas`,
  `asignaciones`, vistas `v_saldo_lotes` y `v_cuadre_corridas`, y el trigger
  de validación de saldo.
- `requirements.txt` — dependencias Python 3.11 para la app (Streamlit).

## Modelo de datos (resumen)

- **lotes** — maestro de recepción: proveedor, procedencia, fecha de
  ingreso, tipo de almacén (Silo/Bines/Mixto), peso neto, brix/pH/acidez/
  ratio de recepción. Se sincroniza desde el Google Sheet de recepción de
  camiones.
- **corridas** — cada corrida de producción: nombre, tipo de proceso
  (JSA/JCC/JSC/ICEGEN), fecha de inicio y fin (pueden ser días distintos),
  y el "MP kg" objetivo que reporta Trazabilidad.
- **asignaciones** — reemplaza la hoja CONSUMOS: qué lote alimentó qué
  corrida, cuántos kg, en qué turno, con qué brix de línea. Un lote puede
  tener varias filas (una por corrida a la que alimentó).

## Base de datos

Proyecto Neon: `agromar-cuadre` (org `org-jolly-lake-94342129`, separado del
proyecto FireMuscle — cada uno con su propio límite gratis de 0.5 GB /
100 horas de cómputo, sin vencimiento). Conexión en `.env`. El esquema ya
está cargado y probado (el trigger de saldo bloquea correctamente sobre-
asignaciones).

Usa `DATABASE_URL_POOLED` (no `DATABASE_URL`) para la app en producción —
es la conexión con pgbouncer de Neon, pensada para muchas conexiones cortas
como las de una app web. `DATABASE_URL` (directa) es para migraciones y
scripts puntuales como `setup_db.py`.

## Arquitectura: API + front-end propio (no Streamlit)

Se evaluaron dos caminos — vestir Streamlit con CSS propio, o un front-end
HTML/CSS/JS aparte con una API chiquita en FastAPI — y se eligió el segundo:
control total del diseño (identidad de marca real, no "tablas en cajas"),
mejor para tablet (se puede agregar a pantalla de inicio, botones táctiles a
la medida), y sigue siendo un solo servicio con un solo link para compartir
(la API sirve también los archivos estáticos del front-end).

- `api.py` — FastAPI: endpoints `/api/lotes`, `/api/lotes/{numero}`,
  `/api/corridas`, `/api/corridas/{id}/asignaciones`,
  `POST /api/asignaciones` (el trigger de saldo insuficiente llega como
  HTTP 400 con el mensaje). Sirve además `web/` como archivos estáticos.
- `web/index.html` — Corridas: semáforo de cuadre en vivo. Contempla el
  estado "en curso" (corrida nueva, sin `mp_kg_objetivo` todavía — ver nota
  de flujo de trabajo más abajo).
- `web/registrar.html` — Registrar consumo: autocompleta proveedor/
  procedencia/saldo al escribir el lote; si es BINES pide bines consumidos
  y calcula el kg solo (con opción de ajustar); si es SILO, kg estimado
  directo.
- `web/lotes.html` — maestro de lotes con saldo en vivo y buscador.
- `web/style.css` / `web/app.js` — estilos e (helpers) compartidos por las
  3 páginas (fetch, formato de números, tema claro/oscuro).

Probado end-to-end contra la base real (autocompletado, cálculo de bines,
guardado, bloqueo de sobre-asignación) — funciona.

Para probarlo local: desde esta carpeta,
```
pip install -r requirements.txt
python -m uvicorn api:app --reload --port 8000
```
y abre `http://localhost:8000`.

### Prototipo anterior en Streamlit (`app.py`, `pages/`)

Se queda en la carpeta como referencia de la lógica de negocio (mismas
consultas SQL, mismo cálculo de bines), pero ya NO es el camino que se va a
seguir — el front-end de verdad es el de arriba. No hace falta mantenerlo
actualizado.

## Sincronización con Google Sheets (lotes)

`sync_lotes.py` trae los lotes desde la hoja de recepción de camiones
("23082024 DATOS DE FRUTA PARA BALANZA NJ ORGANICA 2026", pestaña
"NJ ORGANICA VAL") hacia la tabla `lotes`. Se lee vía el link público de la
hoja (CSV export de Google), sin credenciales. Corrida:

```
python sync_lotes.py
```

Se puede correr las veces que haga falta — hace upsert por número de lote
(los nuevos se agregan, los existentes se actualizan), nunca borra nada. Los
lotes "EN ESPERA" (todavía sin peso/proveedor/fecha en la hoja) se omiten
hasta que la próxima corrida ya los traiga completos.

Mapeo de columnas importante: la columna "BINES/TOLVA" de la hoja trae un
número (bines contados) o la letra "T" (tolva, a granel) — se traduce a
`tipo_almacen`: número → `BINES` (+ `bines_totales`), "T" → `SILO` (se trata
igual que silo: sin conteo de unidades, el kg se estima a ojo). La columna
"UBICACIÓN" de la hoja ("Silo 1", "Silo 2", "tolva"...) se guarda tal cual
en `lotes.ubicacion`, solo informativo.

## Próximos pasos sugeridos

1. ~~Levantar Postgres y correr `schema.sql`~~ — hecho (Neon).
2. ~~Sincronizar lotes desde Google Sheets~~ — hecho, ver arriba.
3. Sincronizar `corridas`: leer las hojas de corrida de Trazabilidad Nar
   2026.xlsx → poblar `corridas` (nombre, fecha_inicio, fecha_final,
   mp_kg_objetivo) — para las corridas ya cerradas/documentadas. Las
   corridas nuevas se crean directo desde la app cuando arrancan (sin
   `mp_kg_objetivo` todavía, ver nota de flujo de trabajo en el código de
   `web/index.html`).
4. Considerar correr `sync_lotes.py` automáticamente cada cierto tiempo
   (cron / tarea programada) en vez de a mano.
