// Helpers compartidos por las 3 páginas.

// Reemplazo de confirm() nativo: en algunos navegadores (sobre todo tablet)
// el diálogo nativo es fácil de pasar por alto o no se ve bien, y parece que
// el botón "no hizo nada". Este es propio de la app, siempre visible igual
// en cualquier dispositivo. Uso: if (!(await confirmar("¿Seguro?"))) return;
function confirmar(mensaje) {
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:999;display:flex;align-items:center;justify-content:center;padding:20px";
    overlay.innerHTML = `
      <div class="panel" style="max-width:420px;width:100%;margin:0">
        <div class="form-body">
          <p style="margin:0 0 18px;color:var(--text);font-size:14.5px;line-height:1.45">${escapeHtml(mensaje)}</p>
          <div style="display:flex;gap:10px;justify-content:flex-end">
            <button type="button" class="btn-link" data-accion="cancelar" style="padding:9px 16px">Cancelar</button>
            <button type="button" class="btn" data-accion="confirmar" style="width:auto;padding:9px 20px">Confirmar</button>
          </div>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);
    const cerrar = (valor) => { overlay.remove(); resolve(valor); };
    overlay.addEventListener("click", (ev) => {
      if (ev.target === overlay) cerrar(false);
      const btn = ev.target.closest("button[data-accion]");
      if (btn) cerrar(btn.dataset.accion === "confirmar");
    });
  });
}

// Aviso que no bloquea la pantalla (reemplaza a alert()): aparece abajo, se cierra solo y también al tocarlo.
// Uso: avisar("Elija una corrida.") o avisar("Guardado.", "ok").
function avisar(mensaje, tipo) {
  let caja = document.getElementById("avisosGlobales");
  if (!caja) {
    caja = document.createElement("div");
    caja.id = "avisosGlobales";
    caja.setAttribute("aria-live", "assertive");
    document.body.appendChild(caja);
  }
  const el = document.createElement("div");
  el.className = "aviso-global" + (tipo === "ok" ? " ok" : "");
  el.setAttribute("role", "alert");
  el.textContent = String(mensaje);
  el.addEventListener("click", () => el.remove());
  caja.appendChild(el);
  setTimeout(() => el.remove(), Math.max(5000, String(mensaje).length * 70));
}

// Si la sesión expiró (o nunca se entró), el backend responde 401 - se manda
// al login sin dejar que la página siga intentando con datos a medias.
function _siNoAutenticado(res) {
  if (res.status === 401) {
    location.href = "/login.html?next=" + encodeURIComponent(location.pathname);
    return new Promise(() => {});  // nunca resuelve: corta la cadena
  }
  return null;
}

// El servidor responde `detail` como texto, o como una lista de objetos cuando rechaza los datos enviados (antes se veía «[object Object]»).
function _mensajeError(body, res) {
  const d = body && body.detail;
  if (Array.isArray(d)) return d.map((x) => (x && x.msg) || JSON.stringify(x)).join(" ");
  return d || `Error ${res.status}`;
}

// ---------- conexión con el servidor ----------
// Mensajes claros en español cuando falla la red (antes salía «Failed to fetch»), tiempo máximo de espera, reintento
// automático de las LECTURAS y protección contra el doble envío de las escrituras. Una escritura nunca se repite sola:
// podría quedar duplicada si la respuesta se perdió pero el dato sí llegó.
class ErrorApi extends Error {
  constructor(mensaje, { estado = 0, sinConexion = false, confirmable = false } = {}) {
    super(mensaje);
    this.name = "ErrorApi";
    this.estado = estado;
    this.sinConexion = sinConexion;     // el navegador no pudo hablar con el servidor
    this.confirmable = confirmable;     // el servidor pide confirmar («parece repetido», «se cruza con otra»)
  }
}
const MSG_SIN_CONEXION = "No hay conexión con el servidor. Revise su internet: lo que escribió sigue en pantalla; vuelva a intentarlo cuando regrese la conexión.";
const MSG_LENTO = "El servidor tardó demasiado en responder. Espere unos segundos y vuelva a intentarlo; antes de repetir, revise si el registro ya se guardó.";
const MSG_NO_DISPONIBLE = "El servidor no está disponible por un momento. Intente de nuevo en unos segundos.";
const PLAZO_MS = 30000;

const _espera = (ms) => new Promise((r) => setTimeout(r, ms));

async function _fetch(path, opciones = {}, reintentos = 0) {
  for (let intento = 0; ; intento++) {
    const control = new AbortController();
    const temporizador = setTimeout(() => control.abort(), PLAZO_MS);
    try {
      const res = await fetch(path, { ...opciones, signal: control.signal });
      clearTimeout(temporizador);
      if ([502, 503, 504].includes(res.status) && intento < reintentos) { await _espera(900 * (intento + 1)); continue; }
      _servidorRespondio(true);
      return res;
    } catch (e) {
      clearTimeout(temporizador);
      if (intento < reintentos) { await _espera(900 * (intento + 1)); continue; }
      const lento = !!(e && e.name === "AbortError");
      if (!lento) _servidorRespondio(false);
      throw new ErrorApi(lento ? MSG_LENTO : MSG_SIN_CONEXION, { sinConexion: !lento });
    }
  }
}

async function _leer(res, tolerante) {
  if (!res.ok) {
    const corte = _siNoAutenticado(res);
    if (corte) return corte;
  }
  if (res.ok && !tolerante) return res.json();
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const sinDetalle = !(body && body.detail);
    const mensaje = sinDetalle && [502, 503, 504].includes(res.status) ? MSG_NO_DISPONIBLE : _mensajeError(body, res);
    throw new ErrorApi(mensaje, { estado: res.status, confirmable: !!(body && body.requiere_confirmacion) });
  }
  return body;
}

// Un envío idéntico mientras el primero sigue en camino (o terminó hace un instante) no se manda otra vez: es el doble clic.
const _enVuelo = new Map();
const _recientes = new Map();
function _unico(clave, enviar) {
  const ahora = Date.now();
  for (const [k, v] of _recientes) if (ahora - v.cuando > 5000) _recientes.delete(k);
  const reciente = _recientes.get(clave);
  if (reciente && ahora - reciente.cuando < 1200) return Promise.resolve(reciente.resultado);
  if (_enVuelo.has(clave)) return _enVuelo.get(clave);
  const promesa = enviar()
    .then((r) => { _recientes.set(clave, { cuando: Date.now(), resultado: r }); return r; })
    .finally(() => _enVuelo.delete(clave));
  _enVuelo.set(clave, promesa);
  return promesa;
}

async function apiGet(path) {
  return _leer(await _fetch(path, {}, 2));
}

function apiPost(path, data) {
  return _unico("POST " + path + " " + JSON.stringify(data), async () =>
    _leer(await _fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) }), true));
}

function apiDelete(path) {
  return _unico("DELETE " + path, async () => _leer(await _fetch(path, { method: "DELETE" }), true));
}

// Guarda y, si el servidor avisa de un posible repetido o cruce («requiere confirmación»), pregunta antes de insistir.
// Devuelve null si la persona decide no registrarlo; en ese caso no hay que limpiar el formulario.
async function apiPostConfirmando(path, data) {
  try {
    return await apiPost(path, data);
  } catch (e) {
    if (e instanceof ErrorApi && e.confirmable) {
      if (await confirmar(e.message + " ¿Desea registrarlo de todos modos?")) return apiPost(path, { ...data, forzar: true });
      return null;
    }
    throw e;
  }
}

// ---------- aviso permanente cuando se pierde la conexión ----------
let _barraConexion = null;
let _vigilaConexion = null;
function _mostrarBarraConexion(texto) {
  if (!_barraConexion) {
    _barraConexion = document.createElement("div");
    _barraConexion.id = "barraSinConexion";
    _barraConexion.setAttribute("role", "status");
    _barraConexion.setAttribute("aria-live", "polite");
    document.body.appendChild(_barraConexion);
    // mientras el aviso está, se revisa cada pocos segundos si el servidor ya volvió
    _vigilaConexion = setInterval(() => {
      fetch("/api/sesion", { cache: "no-store" }).then(() => _servidorRespondio(true)).catch(() => {});
    }, 6000);
  }
  _barraConexion.textContent = texto;
}
function _servidorRespondio(ok) {
  if (!ok) { _mostrarBarraConexion("No se puede contactar con el servidor. Lo que escribió se conserva; vuelva a intentar cuando regrese la conexión."); return; }
  if (_barraConexion) {
    _barraConexion.remove();
    _barraConexion = null;
    clearInterval(_vigilaConexion);
    avisar("Conexión restablecida.", "ok");
  }
}
window.addEventListener("offline", () => _mostrarBarraConexion("Sin conexión a internet. Lo que escribió se conserva; no se guardará nada hasta que regrese la conexión."));
window.addEventListener("online", () => { fetch("/api/sesion", { cache: "no-store" }).then(() => _servidorRespondio(true)).catch(() => {}); });
document.addEventListener("DOMContentLoaded", () => { if (!navigator.onLine) _mostrarBarraConexion("Sin conexión a internet. Lo que escribió se conserva; no se guardará nada hasta que regrese la conexión."); });

// "Cerrar sesión" en el pie de cada página - solo aparece si el login está activo.
document.addEventListener("DOMContentLoaded", () => {
  fetch("/api/sesion").then(r => r.json()).then(s => {
    if (!s || !s.auth) return;
    const foot = document.querySelector(".foot");
    if (!foot) return;
    const link = document.createElement("button");
    link.type = "button";
    link.className = "btn-link";
    link.textContent = "Cerrar sesión";
    link.style.cssText = "margin-top:14px;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--text-muted)";
    link.addEventListener("click", async () => {
      await fetch("/api/logout", { method: "POST" }).catch(() => {});
      location.href = "/login.html";
    });
    foot.appendChild(link);
  }).catch(() => {});
});

function fmtKg(n) {
  // Criterio de la planta: ningun kg se muestra con decimales, nunca
  // (antes solo se redondeaba el "saldo"; ahora aplica en TODA
  // la app). Redondea solo la VISTA, el numero real en la base no se toca.
  if (n === null || n === undefined) return "—";
  return Math.round(Number(n)).toLocaleString("es-PE");
}

// alias - el saldo usa la misma regla que cualquier otro kg ahora.
const fmtSaldo = fmtKg;

// Si un numero de KPI no entra en su tarjeta (ej. el total de MP de toda la
// campaña, que va creciendo), el navegador lo cortaba o lo partia en
// cualquier lado (a veces justo despues de un solo digito - se ve peor que
// simplemente achicar la letra). Se llama despues de pintar cada tanda de
// tarjetas KPI - achica el font-size del numero hasta que quepa en una
// sola linea, nunca lo envuelve.
function ajustarKpiValues(root) {
  (root || document).querySelectorAll(".kpi-value").forEach(el => {
    el.style.fontSize = "";
    let size = parseFloat(getComputedStyle(el).fontSize);
    while (el.scrollWidth > el.clientWidth + 1 && size > 13) {
      size -= 1;
      el.style.fontSize = size + "px";
    }
  });
}

function fmtInt(n) {
  if (n === null || n === undefined) return "—";
  return Number(n).toLocaleString("es-PE");
}

function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleDateString("es-PE", { day: "2-digit", month: "short" }) + " " +
         d.toLocaleTimeString("es-PE", { hour: "2-digit", minute: "2-digit" });
}

const MESES_CORTOS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "set", "oct", "nov", "dic"];

function fmtFechaSola(iso) {
  // Para columnas DATE puras (fecha_ingreso de un lote, sin hora) - nunca
  // pasarlas por fmtDate()/new Date() a secas: un string "2026-09-08" sin
  // hora se interpreta como medianoche UTC, y en Perú (UTC-5) se muestra
  // como el día ANTERIOR a las 7pm (mismo problema ya documentado con
  // TIMESTAMP vs hora local, pero al revés: acá no hay hora que mezclar,
  // el problema es que JS igual le inventa una). Se parsean los números
  // directo, sin darle a JS la oportunidad de reinterpretar la zona horaria.
  if (!iso) return "—";
  const [anio, mes, dia] = iso.split("-").map(Number);
  return `${String(dia).padStart(2, "0")}-${MESES_CORTOS[mes - 1]}`;
}

// Un lote derivado (número negativo) es, por defecto, producto de una corrida PRE CC. Si su proveedor empieza por
// "REPROCESO", es jugo (p. ej. cilindros observados) que se vuelve a procesar: se rotula REPROCESO.
function etiquetaDerivado(proveedor) {
  return /^\s*reproceso/i.test(proveedor || "") ? "REPROCESO" : "PRE CC";
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s ?? "";
  return div.innerHTML;
}

// Hora local "de pared" en formato para <input type="datetime-local"> -
// úsala siempre que el backend necesite "la hora de ahora": el servidor
// puede correr en UTC pero las columnas de fecha de esta app son TIMESTAMP
// sin zona, guardadas como hora local de Perú tal cual la escribe el
// navegador. Si el servidor pusiera su propio now() en vez de esto, quedaría
// ~5 horas adelantado frente a cualquier otra hora de la misma fila puesta
// por el usuario (así se detectó el desfase en las paradas).
function ahoraLocal() {
  const d = new Date();
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 16);
}

// ---------- menús desplegables del nav (Registro / Calidad, y los que se
// agreguen después) - un botón .nav-dropdown-btn[data-menu="idDelMenu"] abre
// el <div class="nav-dropdown-menu" id="idDelMenu"> correspondiente, que vive
// al final del <body> (fuera del hero, que tiene overflow:hidden) y se
// posiciona con position:fixed según el botón que lo abrió ----------
document.addEventListener("DOMContentLoaded", () => {
  const botones = document.querySelectorAll(".nav-dropdown-btn[data-menu]");
  if (botones.length === 0) return;

  function cerrarTodos() {
    document.querySelectorAll(".nav-dropdown-menu").forEach((m) => { m.hidden = true; });
  }

  botones.forEach((btn) => {
    const menu = document.getElementById(btn.dataset.menu);
    if (!menu) return;
    btn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const abrir = menu.hidden;
      cerrarTodos();
      if (abrir) {
        const r = btn.getBoundingClientRect();
        menu.style.left = Math.round(r.left) + "px";
        menu.style.top = Math.round(r.bottom + 6) + "px";
        menu.hidden = false;
        // en el celular el botón de la columna derecha dejaba el menú fuera de la pantalla: se acota al ancho visible
        const maxIzq = document.documentElement.clientWidth - menu.getBoundingClientRect().width - 8;
        menu.style.left = Math.max(8, Math.min(Math.round(r.left), maxIzq)) + "px";
      }
    });
  });

  document.addEventListener("click", (ev) => {
    document.querySelectorAll(".nav-dropdown-menu").forEach((menu) => {
      const btn = document.querySelector(`[data-menu="${menu.id}"]`);
      if (!menu.hidden && !menu.contains(ev.target) && ev.target !== btn && btn && !btn.contains(ev.target)) {
        menu.hidden = true;
      }
    });
  });
  document.addEventListener("scroll", cerrarTodos, true);
  window.addEventListener("resize", cerrarTodos);
});

// ---------- tablas anchas (Lotes, Registrar, etc.): la rueda del mouse las
// mueve de lado a lado mientras el cursor está encima, en vez de tener que
// bajar hasta el final de una tabla larga para encontrar la barra de scroll
// horizontal - en una PC sin trackpad no hay gesto de swipe lateral, era la
// única forma de moverse. Si el gesto ya es horizontal
// (trackpad/mouse con rueda lateral) se deja pasar tal cual. ----------
document.addEventListener("wheel", (ev) => {
  const wrap = ev.target.closest(".table-wrap");
  if (!wrap || wrap.scrollWidth <= wrap.clientWidth) return;
  if (Math.abs(ev.deltaY) <= Math.abs(ev.deltaX)) return;
  wrap.scrollLeft += ev.deltaY;
  ev.preventDefault();
}, { passive: false });

// Tablas anchas en el celular: cada celda lleva el nombre de su columna (se muestra al verse como tarjeta)
// y las tablas que se desbordan se marcan para avisar que se pueden deslizar hacia un lado.
(function () {
  function rotular(tabla) {
    const etiquetas = [...tabla.querySelectorAll("thead th")].map((th) => th.textContent.trim());
    tabla.querySelectorAll("tbody tr, tfoot tr").forEach((tr) => {
      let col = 0;
      [...tr.children].forEach((td) => {
        if (td.dataset.label === undefined) td.dataset.label = etiquetas[col] || "";
        col += td.colSpan || 1;
      });
    });
  }
  function marcarDesborde() {
    document.querySelectorAll(".table-wrap").forEach((w) => {
      w.classList.toggle("desborda", w.clientWidth > 0 && w.scrollWidth > w.clientWidth + 4);
    });
  }
  const vistas = new WeakSet();
  let pendiente = null;
  function revisar() {
    document.querySelectorAll("table.tabla-tarjetas").forEach((t) => {
      rotular(t);
      if (!vistas.has(t)) { vistas.add(t); new MutationObserver(() => rotular(t)).observe(t, { childList: true, subtree: true }); }
    });
    marcarDesborde();
  }
  function programar() { clearTimeout(pendiente); pendiente = setTimeout(revisar, 150); }
  document.addEventListener("DOMContentLoaded", () => {
    revisar();
    new MutationObserver(programar).observe(document.body, { childList: true, subtree: true });
    window.addEventListener("resize", programar);
  });
})();
