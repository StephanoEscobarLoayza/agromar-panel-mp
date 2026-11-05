"""Datos ya agregados para la pantalla de Dashboards.

El servidor entrega cada pantalla con los números listos (totales, series, comparación con el período anterior) en vez
de mandar todas las corridas y todos los lotes para que el navegador los sume. Las reglas son las mismas del resumen
por período en PDF (reporte_periodo.py):

  * MP procesada de una corrida = su MP objetivo; si no lo tiene, los kg asignados.
  * Producto terminado = filas «salida» marcadas como PT menos los insumos de «entrada» que sí descuentan.
  * Rendimiento de una corrida = PT kg ÷ MP kg si tiene tambores cargados; si no, el que trae su hoja de Trazabilidad.
    Por tipo de proceso se pondera por MP (Σ rendimiento × MP ÷ Σ MP), nunca se promedian porcentajes sueltos.
  * Un lote derivado (número negativo: reproceso o PRE CC) no es materia prima nueva y no cuenta como entrega ni stock.

Aquí no hay acceso a la base: son funciones puras sobre las filas que les pasa api.py, para poder probarlas.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta

TIPOS = ["JSA", "JCC", "JCC TASTE", "JSC", "ICEGEN", "PRE CC"]
OTRO = "Otro"


def _f(valor):
    return float(valor) if valor is not None else None


def _dia(valor):
    """date de un date/datetime/texto ISO (la base devuelve fechas naive en hora de planta)."""
    if valor is None:
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    return datetime.fromisoformat(str(valor)[:19]).date()


def _tipo_de(c):
    t = (c.get("tipo_proceso") or "").strip()
    return t if t in TIPOS else OTRO


def mp_de_corrida(c) -> float:
    return float(c.get("mp_kg_objetivo") or c.get("kg_asignados_total") or 0)


# ---------------------------------------------------------------------------------------------------------- períodos
def ventana_anterior(desde: date, hasta: date):
    """Período inmediatamente anterior y de la misma duración (para comparar)."""
    dias = (hasta - desde).days + 1
    ant_hasta = desde - timedelta(days=1)
    return ant_hasta - timedelta(days=dias - 1), ant_hasta


def granularidad(desde: date, hasta: date) -> str:
    dias = (hasta - desde).days + 1
    if dias <= 45:
        return "dia"
    if dias <= 200:
        return "semana"
    return "mes"


def _inicio_bloque(d: date, gran: str) -> date:
    if gran == "semana":
        return d - timedelta(days=d.weekday())
    if gran == "mes":
        return d.replace(day=1)
    return d


def _siguiente_bloque(d: date, gran: str) -> date:
    if gran == "semana":
        return d + timedelta(days=7)
    if gran == "mes":
        return (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    return d + timedelta(days=1)


def _bloques(desde: date, hasta: date, gran: str):
    d = _inicio_bloque(desde, gran)
    while d <= hasta:
        yield d
        d = _siguiente_bloque(d, gran)


# -------------------------------------------------------------------------------------------------- producto y paradas
def pt_neto(productos) -> float | None:
    """PT kg de una corrida: lo que reportan sus filas «salida» (marcadas como PT) menos los insumos «entrada» que descuentan."""
    bruto = 0.0
    descuento = 0.0
    hay = False
    for p in productos:
        kg = _f(p.get("pt_kg"))
        if kg is None:
            continue
        if (p.get("tipo") or "salida") == "entrada":
            if p.get("cuenta_como_pt") is not False:
                descuento += kg
        elif p.get("cuenta_como_pt") is not False:
            bruto += kg
            hay = True
    if not hay:
        return None
    neto = bruto - descuento
    return neto if neto > 0 else None


def minutos_de_planta(tramos) -> float:
    """Minutos con la planta parada: une los tramos que se solapan (la misma parada anotada en dos corridas en paralelo)."""
    tramos = sorted((a, b) for a, b in tramos if a is not None and b is not None and b > a)
    total = 0.0
    ini = fin = None
    for a, b in tramos:
        if fin is None or a > fin:
            if fin is not None:
                total += (fin - ini).total_seconds() / 60
            ini, fin = a, b
        elif b > fin:
            fin = b
    if fin is not None:
        total += (fin - ini).total_seconds() / 60
    return total


def _min_parada(p) -> float:
    a, b = p.get("hora_inicio"), p.get("hora_fin")
    if a is None or b is None or b <= a:
        return 0.0
    return (b - a).total_seconds() / 60


# ----------------------------------------------------------------------------------------------------------- resumen
def _por_corrida(corridas, productos, paradas):
    pts = defaultdict(list)
    for p in productos:
        pts[p["corrida_id"]].append(p)
    paras = defaultdict(list)
    for p in paradas:
        paras[p["corrida_id"]].append(p)
    salida = []
    for c in sorted(corridas, key=lambda c: (c["fecha_inicio"], c["id"])):
        mp = mp_de_corrida(c)
        pt = pt_neto(pts.get(c["id"], []))
        rend = (pt / mp) if (pt and mp > 0) else _f(c.get("rendimiento"))
        if rend is not None and rend > 2:        # por si alguna hoja trajo el rendimiento como 52 en vez de 0.52
            rend = rend / 100
        salida.append({
            "id": c["id"], "nombre": c.get("nombre"), "fecha": _dia(c["fecha_inicio"]).isoformat(),
            "tipo": _tipo_de(c), "abierta": c.get("fecha_final") is None,
            "mp_kg": round(mp, 1), "pt_kg": round(pt, 1) if pt else None,
            "rend": round(rend, 4) if rend is not None else None,
            "rend_de_tambores": bool(pt and mp > 0),
            "min_parado": round(sum(_min_parada(p) for p in paras.get(c["id"], [])), 1),
        })
    return salida


def _kpis(filas, paradas):
    con_mp = [f for f in filas if f["mp_kg"] > 0]
    mp = sum(f["mp_kg"] for f in filas)
    ids = {f["id"] for f in filas}
    ps = [p for p in paradas if p["corrida_id"] in ids]
    return {
        "mp_kg": round(mp, 1),
        "corridas": len(filas),
        "promedio_kg": round(mp / len(con_mp), 1) if con_mp else 0.0,
        "pt_kg": round(sum(f["pt_kg"] or 0 for f in filas), 1),
        "con_pt": sum(1 for f in filas if f["pt_kg"]),
        "min_parado": round(minutos_de_planta((p.get("hora_inicio"), p.get("hora_fin")) for p in ps), 1),
        "min_parado_suma": round(sum(_min_parada(p) for p in ps), 1),
        "n_paradas": len(ps),
    }


def _por_tipo(filas):
    acc = {}
    for f in filas:
        d = acc.setdefault(f["tipo"], {"tipo": f["tipo"], "corridas": 0, "mp_kg": 0.0, "con_mp": 0,
                                       "rend_mp": 0.0, "rend_pond": 0.0, "n_rend": 0, "pt_kg": 0.0, "min_parado": 0.0})
        d["corridas"] += 1
        d["mp_kg"] += f["mp_kg"]
        d["min_parado"] += f["min_parado"]
        if f["mp_kg"] > 0:
            d["con_mp"] += 1
        if f["pt_kg"]:
            d["pt_kg"] += f["pt_kg"]
        if f["rend"] is not None and f["mp_kg"] > 0:
            d["rend_mp"] += f["mp_kg"]
            d["rend_pond"] += f["rend"] * f["mp_kg"]
            d["n_rend"] += 1
    orden = [t for t in TIPOS if t in acc] + ([OTRO] if OTRO in acc else [])
    salida = []
    for t in orden:
        d = acc[t]
        salida.append({
            "tipo": t, "corridas": d["corridas"], "mp_kg": round(d["mp_kg"], 1),
            "promedio_kg": round(d["mp_kg"] / d["con_mp"], 1) if d["con_mp"] else 0.0,
            "rend": round(d["rend_pond"] / d["rend_mp"], 4) if d["rend_mp"] else None,
            "n_rend": d["n_rend"], "pt_kg": round(d["pt_kg"], 1), "min_parado": round(d["min_parado"], 1),
        })
    return salida


def _serie(filas, desde: date, hasta: date, gran: str):
    cubos = {d: {"clave": d.isoformat(), "mp_kg": 0.0, "corridas": 0, "por_tipo": {}} for d in _bloques(desde, hasta, gran)}
    for f in filas:
        k = _inicio_bloque(date.fromisoformat(f["fecha"]), gran)
        c = cubos.get(k)
        if c is None:
            continue
        c["mp_kg"] += f["mp_kg"]
        c["corridas"] += 1
        c["por_tipo"][f["tipo"]] = round(c["por_tipo"].get(f["tipo"], 0.0) + f["mp_kg"], 1)
    salida = []
    for d in sorted(cubos):
        c = cubos[d]
        c["mp_kg"] = round(c["mp_kg"], 1)
        c["hasta"] = min(_siguiente_bloque(d, gran) - timedelta(days=1), hasta).isoformat()
        salida.append(c)
    return salida


def _top(paradas, clave, limite=8):
    acc = defaultdict(float)
    for p in paradas:
        m = _min_parada(p)
        if m > 0:
            acc[(p.get(clave) or "Sin especificar").strip() or "Sin especificar"] += m
    items = sorted(acc.items(), key=lambda kv: kv[1], reverse=True)
    visibles, resto = items[:limite], items[limite:]
    salida = [{"nombre": k, "min": round(v, 1)} for k, v in visibles]
    if resto:
        salida.append({"nombre": f"Otras ({len(resto)})", "min": round(sum(v for _, v in resto), 1)})
    return salida


def _top_proveedores(filas, ids, limite=10):
    acc = defaultdict(float)
    for r in filas:
        if r["corrida_id"] in ids and r.get("kg") is not None:
            acc[(r.get("proveedor") or "—").strip() or "—"] += float(r["kg"])
    items = sorted(acc.items(), key=lambda kv: kv[1], reverse=True)
    return [{"nombre": n, "kg": round(kg, 1)} for n, kg in items[:limite] if kg > 0]


def armar_produccion(corridas, productos, paradas, proveedores, desde: date, hasta: date, tipo: str | None, limites: dict):
    """Panel de producción de [desde, hasta], con la comparación contra el período anterior.

    `corridas`, `productos` y `paradas` traen las filas de AMBOS períodos (el actual y el anterior) y de TODOS los tipos:
    aquí se reparten. El desglose por tipo ignora el filtro de tipo (así la barra elegida se resalta entre las demás);
    todo lo demás sí lo respeta. `proveedores`: filas {corrida_id, proveedor, kg} del período actual (MP asignada por
    corrida y proveedor); aquí se filtran por tipo y se suman."""
    ant_desde, ant_hasta = ventana_anterior(desde, hasta)
    hay_anterior = limites.get("primera") is not None and ant_hasta >= _dia(limites["primera"])

    filas = _por_corrida(corridas, productos, paradas)
    actual = [f for f in filas if desde.isoformat() <= f["fecha"] <= hasta.isoformat()]
    previo = [f for f in filas if ant_desde.isoformat() <= f["fecha"] <= ant_hasta.isoformat()]
    ids_actual = {f["id"] for f in actual}
    ids_previo = {f["id"] for f in previo}
    paradas_actual = [p for p in paradas if p["corrida_id"] in ids_actual]
    paradas_previo = [p for p in paradas if p["corrida_id"] in ids_previo]

    def filtrar(lista):
        return [f for f in lista if tipo is None or f["tipo"] == tipo]

    sel, sel_prev = filtrar(actual), filtrar(previo)
    ids_sel = {f["id"] for f in sel}
    paradas_sel = [p for p in paradas_actual if p["corrida_id"] in ids_sel]
    ids_sel_prev = {f["id"] for f in sel_prev}
    paradas_sel_prev = [p for p in paradas_previo if p["corrida_id"] in ids_sel_prev]

    gran = granularidad(desde, hasta)
    tipos_prev = {t["tipo"]: t for t in _por_tipo(previo)}
    por_tipo = _por_tipo(actual)
    for t in por_tipo:
        p = tipos_prev.get(t["tipo"])
        t["rend_anterior"] = p["rend"] if p else None
        t["mp_kg_anterior"] = p["mp_kg"] if p else None

    return {
        "periodo": {"desde": desde.isoformat(), "hasta": hasta.isoformat(), "dias": (hasta - desde).days + 1, "gran": gran},
        "anterior": {"desde": ant_desde.isoformat(), "hasta": ant_hasta.isoformat()} if hay_anterior else None,
        "limites": limites,
        "tipo": tipo,
        "kpis": _kpis(sel, paradas_sel),
        "kpis_anterior": _kpis(sel_prev, paradas_sel_prev) if hay_anterior else None,
        "por_tipo": por_tipo,
        "serie": _serie(sel, desde, hasta, gran),
        "corridas": sorted(sel, key=lambda f: (f["fecha"], f["id"]), reverse=True),
        "proveedores": _top_proveedores(proveedores, ids_sel),
        "paradas": {
            "total_min": _kpis(sel, paradas_sel)["min_parado"],
            "n": len(paradas_sel),
            "por_area": _top(paradas_sel, "area_proceso"),
            "por_tipo": _top(paradas_sel, "tipo_parada"),
        },
    }


# ----------------------------------------------------------------------------------------------------- stock y lotes
def _estado(l) -> str:
    return (l.get("estado_actual") or "").strip().upper()


def armar_stock(lotes, desde: date, hasta: date, hoy: date, limites: dict):
    """Panel de stock y lotes: foto de HOY (lo que sigue en planta) + MP recibida en [desde, hasta] con su calidad.

    `lotes` son las filas de v_saldo_lotes; los derivados (número negativo) quedan fuera: no son materia prima nueva."""
    lotes = [l for l in lotes if (l.get("numero") or 0) >= 0]
    ant_desde, ant_hasta = ventana_anterior(desde, hasta)
    hay_anterior = limites.get("primera") is not None and ant_hasta >= _dia(limites["primera"])

    # ---- lo que hay en planta hoy
    en_planta = [l for l in lotes if _estado(l) in ("EN PROCESO", "EN ESPERA") and (_f(l.get("kg_saldo")) or 0) > 0.01]
    stock_kg = sum(_f(l["kg_saldo"]) for l in en_planta)

    def espera(l):
        return (hoy - _dia(l["fecha_ingreso"])).days if l.get("fecha_ingreso") else None

    tramos = [("0 a 2 días", 0, 2), ("3 a 5 días", 3, 5), ("6 a 10 días", 6, 10), ("Más de 10 días", 11, 10 ** 6)]
    antiguedad = []
    for nombre, a, b in tramos:
        sel = [l for l in en_planta if espera(l) is not None and a <= espera(l) <= b]
        antiguedad.append({"nombre": nombre, "kg": round(sum(_f(l["kg_saldo"]) for l in sel), 1), "lotes": len(sel)})
    sin_fecha = [l for l in en_planta if espera(l) is None]
    if sin_fecha:
        antiguedad.append({"nombre": "Sin fecha de ingreso", "kg": round(sum(_f(l["kg_saldo"]) for l in sin_fecha), 1), "lotes": len(sin_fecha)})

    ubic = defaultdict(lambda: {"kg": 0.0, "lotes": 0})
    for l in en_planta:
        u = (l.get("ubicacion_actual") or "Sin ubicación").strip() or "Sin ubicación"
        ubic[u]["kg"] += _f(l["kg_saldo"])
        ubic[u]["lotes"] += 1
    por_ubicacion = [{"nombre": k, "kg": round(v["kg"], 1), "lotes": v["lotes"]}
                     for k, v in sorted(ubic.items(), key=lambda kv: kv[1]["kg"], reverse=True)]

    por_estado = []
    for e, etiqueta in (("EN PROCESO", "En proceso"), ("EN ESPERA", "En espera")):
        sel = [l for l in en_planta if _estado(l) == e]
        por_estado.append({"nombre": etiqueta, "kg": round(sum(_f(l["kg_saldo"]) for l in sel), 1), "lotes": len(sel)})

    lista = sorted(en_planta, key=lambda l: (espera(l) is None, -(espera(l) or 0)))     # los más viejos primero
    lotes_saldo = [{
        "numero": l["numero"], "proveedor": l.get("proveedor"), "ubicacion": l.get("ubicacion_actual"),
        "estado": _estado(l), "kg_saldo": round(_f(l["kg_saldo"]), 1), "peso_neto_kg": round(_f(l.get("peso_neto_kg")) or 0, 1),
        "dias": espera(l), "brix": _f(l.get("brix_recepcion")), "ratio": _f(l.get("ratio")),
    } for l in lista[:60]]

    # ---- MP recibida en el período
    def recibidos(a, b):
        return [l for l in lotes if l.get("fecha_ingreso") and a <= _dia(l["fecha_ingreso"]) <= b]

    def calidad(sel):
        kg = sum(_f(l.get("peso_neto_kg")) or 0 for l in sel)

        def pond(campo):
            pares = [(_f(l.get("peso_neto_kg")) or 0, _f(l.get(campo))) for l in sel if l.get(campo) is not None]
            w = sum(p for p, _ in pares)
            return round(sum(p * v for p, v in pares) / w, 2) if w > 0 else None
        return {"kg": round(kg, 1), "lotes": len(sel), "brix": pond("brix_recepcion"), "acidez": pond("acidez"), "ratio": pond("ratio")}

    actual = recibidos(desde, hasta)
    gran = granularidad(desde, hasta)
    cubos = {d: {"clave": d.isoformat(), "kg": 0.0, "lotes": 0} for d in _bloques(desde, hasta, gran)}
    for l in actual:
        c = cubos.get(_inicio_bloque(_dia(l["fecha_ingreso"]), gran))
        if c is not None:
            c["kg"] += _f(l.get("peso_neto_kg")) or 0
            c["lotes"] += 1
    serie = []
    for d in sorted(cubos):
        c = cubos[d]
        c["kg"] = round(c["kg"], 1)
        c["hasta"] = min(_siguiente_bloque(d, gran) - timedelta(days=1), hasta).isoformat()
        serie.append(c)

    prov = defaultdict(list)
    for l in actual:
        prov[(l.get("proveedor") or "—").strip() or "—"].append(l)
    proveedores = sorted(({"nombre": k, **calidad(v)} for k, v in prov.items()), key=lambda d: d["kg"], reverse=True)[:12]

    return {
        "periodo": {"desde": desde.isoformat(), "hasta": hasta.isoformat(), "dias": (hasta - desde).days + 1, "gran": gran},
        "anterior": {"desde": ant_desde.isoformat(), "hasta": ant_hasta.isoformat()} if hay_anterior else None,
        "limites": limites,
        "stock": {"kg": round(stock_kg, 1), "lotes": len(en_planta)},
        "por_estado": por_estado, "por_ubicacion": por_ubicacion, "antiguedad": antiguedad, "lotes_saldo": lotes_saldo,
        "recibida": calidad(actual),
        "recibida_anterior": calidad(recibidos(ant_desde, ant_hasta)) if hay_anterior else None,
        "serie": serie, "proveedores": proveedores,
    }
