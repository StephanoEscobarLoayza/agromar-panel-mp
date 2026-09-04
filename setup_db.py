"""
Corre esto UNA VEZ, desde tu compu (no desde el sandbox de Claude), para crear
las tablas en la base de datos de Render:

    pip install -r requirements.txt
    python setup_db.py

Lee la conexión de la variable de entorno DATABASE_URL (copia .env.example a
.env y pon tu connection string real, o expórtala directo en tu terminal).
"""
import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise SystemExit("Falta DATABASE_URL. Copia .env.example a .env y pon tu connection string.")

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")

print("Conectando a la base de datos...")
conn = psycopg2.connect(DATABASE_URL)
conn.autocommit = True
cur = conn.cursor()

with open(SCHEMA_PATH, encoding="utf-8") as f:
    sql = f.read()

print("Aplicando schema.sql...")
cur.execute(sql)

cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY table_name;")
print("Tablas creadas:", [r[0] for r in cur.fetchall()])

cur.execute("SELECT table_name FROM information_schema.views WHERE table_schema='public' ORDER BY table_name;")
print("Vistas creadas:", [r[0] for r in cur.fetchall()])

conn.close()
print("Listo.")
