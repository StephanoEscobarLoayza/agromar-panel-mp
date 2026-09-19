# Panel de Cuadre de Producción — Agromar Industrial

Sistema de trazabilidad de materia prima (naranja) para una planta de jugos:
reemplaza el cuadre manual en Excel (hoja de recepción de camiones +
Trazabilidad) por una aplicación web con validación en vivo, un solo login
compartido y reportes que replican el formato físico que ya usa el equipo de
producción.

## Por qué esto y no Excel

- Las corridas pueden cruzar medianoche (empiezan un día, terminan de
  madrugada al siguiente) — registrar por "fecha" en vez de por "corrida" es
  lo que rompía el cuadre en la hoja. Aquí la corrida es la unidad central.
- Un lote puede repartirse entre varias corridas — el modelo es
  muchos-a-muchos desde el principio, no una hoja lineal.
- La base de datos bloquea automáticamente si se intenta asignar más kg de
  los que un lote tiene disponibles (`schema.sql`, trigger
  `trg_validar_saldo_lote`) — un lote asignado dos veces por error, sumando
  más de lo que pesaba, ya no puede pasar desapercibido.
- El peso, Brix, Acidez y Ratio pueden corregirse a mano cuando difieren de
  lo que trae la hoja de recepción (ej. una báscula distinta, un dato viejo),
  y esa corrección queda protegida contra la siguiente sincronización.

## Qué hace cada página

| Página | Para qué sirve |
|---|---|
| **Tablero** (`/`, página de entrada) | Vista tipo kiosco de la corrida en curso — kg registrados, últimos lotes — se actualiza sola cada 45s, pensada para quedar abierta en una pantalla fija. |
| **Dashboards** (`index.html`) | Indicadores generales de producción: corridas activas, kg del día, lotes con saldo, MP procesada total, reporte por período (PDF). |
| **Corridas** | Crear/editar/finalizar corridas, registrar productos de salida e insumos de entrada (con checkbox para si cuentan como PT o si descuentan del total), descargar el reporte PDF y el Excel de Trazabilidad de cada una. |
| **Registrar consumo** | Asignar un lote a una corrida: autocompleta proveedor/procedencia/saldo, calcula el kg a partir de los bines consumidos (con opción de ajustar a mano si el peso real difiere del promedio), o pide el kg directo si es Silo. |
| **Lotes** | Maestro con saldo en vivo, buscador y filtro por estado. Estado, ubicación y peso neto se pueden corregir a mano ahí mismo, protegidos contra la próxima sincronización. |
| **Trazabilidad de lote** | Búsqueda en reversa: dado un número de lote, muestra a qué corridas alimentó y qué salió de cada una. |
| **Calidad → Siguiente bin** | Si se acaba un bin a media corrida, calcula cuál conviene reponer (por fecha o por el que más ayuda a mantener Brix/Ratio) sin pasarse de la Acidez máxima. |
| **Medición de tanques** | Brix/Acidez/pH medidos de verdad con refractómetro por tanque — manda sobre el estimado de los lotes en los reportes en cuanto hay al menos una medición. |
| **Paradas** | Registro de paradas de planta durante una corrida (motivo, duración). |
| **Sincronizar** | Trae los lotes nuevos/actualizados desde el Google Sheet de recepción (además de correr sola cada 20 min), y descarga un respaldo completo en Excel (una hoja por tabla). |

Todas las páginas comparten un login único (usuario/contraseña compartidos
por el equipo, cookie firmada) y están pensadas para usarse desde tablet
además de PC/celular.

## Reportes

- **PDF de corrida** — cuadre completo de una corrida: MP consumida, stock de
  piso al inicio/cierre, Brix/Acidez/Ratio (medido si hay tanques, estimado
  si no), rendimiento, producto terminado, tambores, volumen, lotes
  consumidos y su estado al cierre, insumos de entrada, paradas.
- **PDF de período** — lo mismo agregado entre dos fechas, con desglose por
  tipo de proceso y por proveedor.
- **PDF de stock** — foto del stock de MP en piso en un momento dado (Silo +
  Bines), solo lotes con saldo real disponible.
- **Excel de Trazabilidad por corrida** — replica el formato físico que ya
  usa producción (una hoja con lotes, fórmulas en vivo, HR de pulpeado,
  MP kg/hr, PT, rendimiento) para que se pueda seguir editando igual que
  antes si hace falta.
- **Excel plano** (`Sincronizar`) — todas las tablas (lotes, corridas,
  consumos, mediciones, productos, paradas) tal cual están en la base, para
  respaldo o para cuadrar sin abrir la base de datos.

Todos los kg se muestran redondeados a entero en toda la app y los reportes
(sin decimales), aunque la base los guarda con precisión completa.

## Arquitectura

- **Backend**: FastAPI (`api.py`) + SQLAlchemy con SQL directo (sin ORM)
  contra Postgres. Sirve también `web/` como archivos estáticos — un solo
  servicio, un solo link para compartir.
- **Front-end**: HTML/CSS/JS propio (sin framework), una página por
  pantalla, compartiendo `web/app.js` (helpers de formato/fetch) y
  `web/style.css` (tema claro/oscuro).
- **Reportes**: ReportLab para PDF (`reporte_*.py`), openpyxl para Excel con
  fórmulas reales (`reporte_trazabilidad.py`) y exportación plana
  (`_hoja_xlsx` en `api.py`).
- **Base de datos**: Postgres en Neon, con vistas (`v_saldo_lotes`,
  `v_cuadre_corridas`) y un trigger de validación de saldo — ver
  `schema.sql`.

## Modelo de datos (resumen)

- **lotes** — maestro de recepción: proveedor, procedencia, fecha de
  ingreso, tipo de almacén (Silo/Bines/Mixto), peso neto, Brix/Acidez/Ratio
  de recepción. Se sincroniza desde Google Sheets; peso, estado y ubicación
  se pueden congelar contra esa sincronización si se corrigen a mano.
- **corridas** — cada corrida de producción: nombre, tipo de proceso
  (JSA/JCC/JSC/ICEGEN...), fecha de inicio y fin (pueden ser días distintos).
- **asignaciones** — qué lote alimentó qué corrida, cuántos kg, en qué
  turno, con qué origen (Silo/Bines) y cuántos bines. Un lote puede tener
  varias filas, una por corrida a la que alimentó.
- **corrida_productos** — productos de salida (lo que produjo la corrida) e
  insumos de entrada (lo que se le metió desde afuera, ej. para subir Brix)
  de cada corrida.
- **mediciones_tanque** — Brix/Acidez/pH/litros medidos de verdad por tanque
  al cerrar una corrida.
- **paradas** — paradas de planta registradas por corrida.

## Base de datos

Proyecto Neon (Postgres serverless), con el esquema completo en
`schema.sql`. La app usa `DATABASE_URL_POOLED` (conexión con pgbouncer,
pensada para muchas conexiones cortas como las de una app web) en vez de
`DATABASE_URL` (directa, para migraciones y scripts puntuales).

La base es **la misma en local y en producción** — no hay entorno de
pruebas separado, así que cualquier prueba local corre contra datos reales
(tener cuidado al probar cosas destructivas).

## Sincronización con Google Sheets (lotes)

`sync_lotes.py` trae los lotes desde el Google Sheet de recepción de
camiones hacia la tabla `lotes` (upsert por número de lote — nunca borra
nada). Corre sola cada 20 minutos desde un loop en segundo plano dentro de
la propia app (ver `_sync_loop` en `api.py`), y también se puede disparar a
mano desde "Sincronizar".

## Autenticación

Login único compartido por el equipo (usuario/contraseña + cookie firmada).
Se activa poniendo las variables de entorno `APP_USER`, `APP_PASSWORD` y
`APP_SESSION_SECRET` — si no están puestas (ej. desarrollo local), la app
corre sin login.

## Cómo correr localmente

```
pip install -r requirements.txt
python -m uvicorn api:app --reload --port 8000
```

y abre `http://localhost:8000`. Hace falta un archivo `.env` con
`DATABASE_URL` (o `DATABASE_URL_POOLED`) apuntando a la base — ver
`.env.example`.

## Despliegue

Pensado para correr como un solo servicio (build: `pip install -r
requirements.txt`, start: `uvicorn api:app --host 0.0.0.0 --port $PORT`).
Incluye `Dockerfile` para desplegar en cualquier plataforma que soporte
contenedores.
