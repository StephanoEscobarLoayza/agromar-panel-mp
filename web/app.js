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

// Si la sesión expiró (o nunca se entró), el backend responde 401 - se manda
// al login sin dejar que la página siga intentando con datos a medias.
function _siNoAutenticado(res) {
  if (res.status === 401) {
    location.href = "/login.html?next=" + encodeURIComponent(location.pathname);
    return new Promise(() => {});  // nunca resuelve: corta la cadena
  }
  return null;
}

async function apiGet(path) {
  const res = await fetch(path);
  if (!res.ok) {
    const corte = _siNoAutenticado(res);
    if (corte) return corte;
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Error ${res.status}`);
  }
  return res.json();
}

async function apiPost(path, data) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const corte = _siNoAutenticado(res);
    if (corte) return corte;
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(body.detail || `Error ${res.status}`);
  }
  return body;
}

async function apiDelete(path) {
  const res = await fetch(path, { method: "DELETE" });
  if (!res.ok) {
    const corte = _siNoAutenticado(res);
    if (corte) return corte;
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(body.detail || `Error ${res.status}`);
  }
  return body;
}

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
  // Pedido explicito de Stephano: ningun kg se muestra con decimales, nunca
  // (antes solo se redondeaba el "saldo" - lo corrigio: quiere esto en TODA
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
  // el bug es que JS igual le inventa una). Se parsean los números
  // directo, sin darle a JS la oportunidad de reinterpretar la zona horaria.
  if (!iso) return "—";
  const [anio, mes, dia] = iso.split("-").map(Number);
  return `${String(dia).padStart(2, "0")}-${MESES_CORTOS[mes - 1]}`;
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s ?? "";
  return div.innerHTML;
}

// Hora local "de pared" en formato para <input type="datetime-local"> -
// úsala siempre que el backend necesite "la hora de ahora": el servidor
// (Render) corre en UTC pero las columnas de fecha de esta app son TIMESTAMP
// sin zona, guardadas como hora local de Perú tal cual la escribe el
// navegador. Si el servidor pusiera su propio now() en vez de esto, quedaría
// ~5 horas adelantado frente a cualquier otra hora de la misma fila puesta
// por el usuario (así se descubrió el bug de las paradas - ver memoria).
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
// única forma de moverse (Stephano lo notó). Si el gesto ya es horizontal
// (trackpad/mouse con rueda lateral) se deja pasar tal cual. ----------
document.addEventListener("wheel", (ev) => {
  const wrap = ev.target.closest(".table-wrap");
  if (!wrap || wrap.scrollWidth <= wrap.clientWidth) return;
  if (Math.abs(ev.deltaY) <= Math.abs(ev.deltaX)) return;
  wrap.scrollLeft += ev.deltaY;
  ev.preventDefault();
}, { passive: false });
