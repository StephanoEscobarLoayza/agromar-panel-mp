"""Conexión a la base de datos (Neon / PostgreSQL).

Se usa psycopg2, como siempre. Pero si Windows lo bloquea (Smart App Control o el Control de
aplicaciones rechazan el archivo sin firmar `_psycopg*.pyd` con «Una directiva de Control de
aplicaciones bloqueó este archivo»), el panel no podía ni arrancar. En ese caso se usa pg8000,
que está escrito solo en Python (no carga ningún archivo binario sin firmar) y hace lo mismo.
"""
import ssl

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url


def crear_motor(url: str, **opciones):
    try:
        import psycopg2  # noqa: F401 - si Windows bloquea el archivo, falla aquí con ImportError
        connect_args = dict(opciones.pop("connect_args", {}))
        for clave, valor in (("keepalives", 1), ("keepalives_idle", 30), ("keepalives_interval", 10), ("keepalives_count", 5)):
            connect_args.setdefault(clave, valor)          # avisos de «sigo aquí»: la conexión inactiva no se corta en silencio
        return create_engine(url, connect_args=connect_args, **opciones)
    except ImportError:
        u = make_url(url)
        sslmode = u.query.get("sslmode", "require")
        consulta = {k: v for k, v in u.query.items() if k not in ("sslmode", "channel_binding")}   # pg8000 no los entiende
        connect_args = dict(opciones.pop("connect_args", {}))
        if sslmode != "disable":
            connect_args["ssl_context"] = ssl.create_default_context()
        return create_engine(u.set(drivername="postgresql+pg8000", query=consulta), connect_args=connect_args, **opciones)
