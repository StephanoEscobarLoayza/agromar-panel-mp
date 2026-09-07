// Helpers compartidos por las 3 páginas.

async function apiGet(path) {
  const res = await fetch(path);
  if (!res.ok) {
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
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(body.detail || `Error ${res.status}`);
  }
  return body;
}

async function apiDelete(path) {
  const res = await fetch(path, { method: "DELETE" });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(body.detail || `Error ${res.status}`);
  }
  return body;
}

function fmtKg(n) {
  if (n === null || n === undefined) return "—";
  return Number(n).toLocaleString("es-PE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
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
