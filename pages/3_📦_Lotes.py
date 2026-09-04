import streamlit as st

from db import query_df

st.set_page_config(page_title="Lotes", page_icon="📦")
st.title("Lotes")

df = query_df("SELECT * FROM v_saldo_lotes ORDER BY numero DESC")

if df.empty:
    st.info("Todavía no hay lotes cargados. Usa la pantalla Sincronizar.")
    st.stop()

filtro = st.text_input("Buscar por número, proveedor o procedencia")
if filtro:
    f = filtro.lower()
    mask = (
        df["numero"].astype(str).str.lower().str.contains(f)
        | df["proveedor"].str.lower().str.contains(f, na=False)
        | df["procedencia"].str.lower().str.contains(f, na=False)
    )
    df = df[mask]

st.dataframe(
    df[[
        "numero", "proveedor", "procedencia", "tipo_almacen",
        "peso_neto_kg", "kg_consumidos", "kg_saldo",
        "brix_recepcion", "acidez", "ratio", "fecha_ingreso",
    ]],
    hide_index=True,
    use_container_width=True,
)
