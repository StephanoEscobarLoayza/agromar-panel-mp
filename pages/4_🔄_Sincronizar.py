import streamlit as st

st.set_page_config(page_title="Sincronizar", page_icon="🔄")
st.title("Sincronizar")

st.info(
    "PENDIENTE: traer lotes desde el Google Sheet de recepción de camiones "
    "hacia la tabla `lotes`, y las corridas desde Trazabilidad hacia la tabla "
    "`corridas`. Queda anotado como próximo paso — ver README.md."
)
