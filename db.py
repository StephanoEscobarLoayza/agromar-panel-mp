"""Conexión y helpers de base de datos, compartidos por todas las pantallas."""
import os
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

# ruta explicita (no depende del directorio desde donde se lance streamlit -
# preview_start, por ejemplo, corre el comando desde la raiz de Agromar, no
# desde esta carpeta, y load_dotenv() sin argumento solo mira el cwd)
load_dotenv(Path(__file__).parent / ".env")


@st.cache_resource
def get_engine():
    url = os.environ.get("DATABASE_URL_POOLED") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("Falta DATABASE_URL o DATABASE_URL_POOLED en .env")
    return create_engine(url, pool_pre_ping=True)


def query_df(sql, params=None):
    with get_engine().connect() as conn:
        return pd.read_sql(text(sql), conn, params=params or {})


def execute(sql, params=None):
    """Para INSERT/UPDATE/DELETE. Deja pasar la excepción del trigger de saldo
    tal cual, para que la pantalla pueda mostrarla como mensaje de error."""
    with get_engine().begin() as conn:
        conn.execute(text(sql), params or {})


def buscar_lote(numero):
    df = query_df("SELECT * FROM v_saldo_lotes WHERE numero = :n", {"n": numero})
    return df.iloc[0] if not df.empty else None


def corridas_abiertas():
    return query_df(
        "SELECT id, nombre, fecha_inicio, fecha_final FROM corridas "
        "WHERE estado = 'abierta' ORDER BY fecha_inicio DESC"
    )
