import streamlit as st

from db import query_df

st.set_page_config(page_title="Corridas", page_icon="🚚")
st.title("Corridas")

df = query_df("SELECT * FROM v_cuadre_corridas ORDER BY fecha_inicio DESC")

if df.empty:
    st.info("Todavía no hay corridas registradas.")
    st.stop()

# "sin_objetivo" es el estado normal de una corrida que recien esta pasando:
# el "MP kg" de Trazabilidad se documenta DESPUES, en base a lo que ya se
# registro aqui (no al reves) - por eso no es un error, es "en curso".
etiquetas = {
    "cuadra": "🟢 Cuadra",
    "incompleto": "🟡 Incompleto",
    "excedido": "🔴 Excedido",
    "sin_objetivo": "⚪ En curso",
}
df["estado"] = df["estado_cuadre"].map(etiquetas)

st.dataframe(
    df[["nombre", "fecha_inicio", "fecha_final", "mp_kg_objetivo", "kg_asignados_total", "diferencia_kg", "estado"]],
    hide_index=True,
    use_container_width=True,
    column_config={"mp_kg_objetivo": st.column_config.NumberColumn("MP kg objetivo", help="Se completa cuando Trazabilidad documenta la corrida")},
)

st.divider()
st.caption("Detalle por corrida")
nombre = st.selectbox("Corrida", df["nombre"])
corrida_id = int(df.loc[df["nombre"] == nombre, "id"].iloc[0])

detalle = query_df(
    """
    SELECT a.lote_numero AS lote, l.proveedor, a.turno, a.kg_asignados AS kg, a.tipo_almacen_origen AS origen
    FROM asignaciones a
    JOIN lotes l ON l.numero = a.lote_numero
    WHERE a.corrida_id = :c
    ORDER BY a.fecha_proceso
    """,
    {"c": corrida_id},
)
st.dataframe(detalle, hide_index=True, use_container_width=True)
