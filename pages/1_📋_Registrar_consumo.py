import streamlit as st

from db import buscar_lote, corridas_abiertas, execute, query_df

st.set_page_config(page_title="Registrar consumo", page_icon="📋")
st.title("Registrar consumo")

# contador que cambia las "keys" de los widgets tras guardar, para limpiarlos -
# no se usa st.form aqui porque el peso sugerido de bines necesita reaccionar
# EN VIVO mientras se escribe, y los widgets dentro de un form en Streamlit
# solo se actualizan al hacer submit (por eso antes se quedaba en 0.00 kg).
if "form_key" not in st.session_state:
    st.session_state.form_key = 0
k = st.session_state.form_key

corridas = corridas_abiertas()
if corridas.empty:
    st.warning("No hay corridas abiertas todavía. Crea una corrida primero.")
    st.stop()

nombre_corrida = st.selectbox("Corrida", corridas["nombre"])
corrida = corridas[corridas["nombre"] == nombre_corrida].iloc[0]
if corrida["fecha_final"] is not None:
    st.caption(f"{corrida['fecha_inicio']:%d/%m/%Y} – {corrida['fecha_final']:%d/%m/%Y}")
else:
    st.caption(f"Inicio {corrida['fecha_inicio']:%d/%m/%Y} · sigue abierta")

col1, col2 = st.columns(2)
turno = col1.selectbox("Turno", ["DÍA", "NOCHE"])
lote_numero = col2.number_input("Número de lote", min_value=0, step=1, key=f"lote_{k}")

lote = buscar_lote(int(lote_numero)) if lote_numero else None


def guardar(lote_numero, corrida_id, turno, origen, kg, bines, obs):
    try:
        execute(
            """
            INSERT INTO asignaciones
                (lote_numero, corrida_id, fecha_proceso, turno, tipo_almacen_origen, kg_asignados, bines_consumidos, observaciones)
            VALUES
                (:lote, :corrida, now(), :turno, :origen, :kg, :bines, :obs)
            """,
            {
                "lote": lote_numero, "corrida": corrida_id, "turno": turno,
                "origen": origen, "kg": kg, "bines": bines, "obs": obs,
            },
        )
        st.success(f"Asignados {kg:,.2f} kg del lote {lote_numero} a {nombre_corrida}.")
        st.session_state.form_key += 1
        st.rerun()
    except Exception as e:
        st.error(f"No se pudo guardar: {e}")


if lote_numero and lote is None:
    st.error(f"El lote {int(lote_numero)} no está en el maestro. Sincronízalo primero.")
elif lote is not None:
    c1, c2, c3 = st.columns(3)
    c1.metric("Proveedor", lote["proveedor"])
    c2.metric("Procedencia", lote["procedencia"] or "—")
    c3.metric("Saldo disponible", f"{lote['kg_saldo']:,.2f} kg")

    saldo_kg = max(float(lote["kg_saldo"]), 0.0)
    origenes = ["BINES", "SILO"] if lote["tipo_almacen"] == "MIXTO" else [lote["tipo_almacen"]]
    origen = st.selectbox("Origen", origenes, key=f"origen_{k}") if len(origenes) > 1 else origenes[0]

    if origen == "BINES":
        # bines: se anota cuantos bines se consumieron y el kg se calcula solo
        # a partir del peso neto del lote (igual que la columna "Peso sugerido
        # segun bines" del Excel original) - no se calcula a ojo.
        if not lote["bines_totales"]:
            st.error("Este lote no tiene bines_totales registrados — no se puede calcular el kg por bin. Corrígelo en Lotes.")
        else:
            kg_por_bin = float(lote["peso_neto_kg"]) / float(lote["bines_totales"])
            bines_saldo = lote["bines_saldo"]
            bines_saldo = int(bines_saldo) if bines_saldo is not None else None
            st.caption(f"{kg_por_bin:,.2f} kg por bin · saldo actual: {bines_saldo if bines_saldo is not None else '—'} bines")

            tope = bines_saldo if bines_saldo is not None else 9999
            bines_consumidos = st.number_input("Bines consumidos", min_value=0, max_value=tope, step=1, key=f"bines_{k}")
            kg_sugerido = round(min(bines_consumidos * kg_por_bin, saldo_kg), 2)
            st.markdown(f"**Peso sugerido:** {kg_sugerido:,.2f} kg")
            kg_final = st.number_input(
                "Kg a registrar (ajusta si el peso real difiere del sugerido)",
                min_value=0.0, max_value=saldo_kg, value=kg_sugerido, step=1.0,
                # la key incluye bines_consumidos: asi el campo se refresca al
                # nuevo sugerido cada vez que cambian los bines, y solo se
                # "congela" si el usuario lo edita a mano sin tocar los bines.
                key=f"kgb_{k}_{bines_consumidos}",
            )
            obs = st.text_input("Observaciones", key=f"obsb_{k}")

            if st.button("Guardar asignación", key=f"btnb_{k}"):
                if bines_consumidos <= 0:
                    st.error("Ingresa cuántos bines se consumieron.")
                elif kg_final <= 0:
                    st.error("El kg a registrar debe ser mayor a 0.")
                else:
                    guardar(int(lote_numero), int(corrida["id"]), turno, origen, kg_final, int(bines_consumidos), obs)

    else:
        # silo: no hay unidades discretas que contar, el kg se estima a ojo
        # (lo que se procesó en el turno) y se anota directo.
        kg_estimado = st.number_input("Kg a asignar (estimado)", min_value=0.0, max_value=saldo_kg, step=1.0, key=f"kgs_{k}")
        obs = st.text_input("Observaciones", key=f"obss_{k}")

        if st.button("Guardar asignación", key=f"btns_{k}"):
            if kg_estimado <= 0:
                st.error("Ingresa un peso mayor a 0.")
            else:
                guardar(int(lote_numero), int(corrida["id"]), turno, origen, kg_estimado, None, obs)

st.divider()
st.caption("Últimas asignaciones de esta corrida")
recientes = query_df(
    """
    SELECT lote_numero AS lote, kg_asignados AS kg, bines_consumidos AS bines, turno, creado_en AS registrado
    FROM asignaciones
    WHERE corrida_id = :c
    ORDER BY creado_en DESC
    LIMIT 10
    """,
    {"c": int(corrida["id"])},
)
st.dataframe(recientes, hide_index=True, use_container_width=True)
