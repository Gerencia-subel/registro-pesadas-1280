(() => {
"use strict";

const vista = document.getElementById("vista-configuracion");
const $ = (id) => vista.querySelector("#" + id);
const CSRF = document.querySelector('meta[name="csrf"]').content;
let ocupado = false;

async function pedir(ruta, opciones) {
  let r;
  try {
    r = await fetch(ruta, Object.assign({ headers: { Accept: "application/json" }, credentials: "same-origin" }, opciones || {}));
  } catch (e) {
    return { ok: false, error: "Sin conexión con la Raspberry." };
  }
  if (r.status === 401) { location.href = "/ingresar"; return { ok: false, error: "La sesión terminó." }; }
  try { return await r.json(); } catch (e) { return { ok: false, error: "Respuesta inesperada (HTTP " + r.status + "). Volver a intentar." }; }
}

function texto(el, valor) { el.textContent = valor == null ? "" : String(valor); }

// Los campos se llenan con lo guardado, salvo el que se está editando.
function poner(id, valor) {
  const el = $(id);
  if (document.activeElement !== el && !el.dataset.editado) el.value = valor == null ? "" : valor;
}

async function cargar() {
  const r = await pedir("/api/configuracion");
  if (!r.ok) { texto($("correo-config-detalle"), r.error); return; }
  const c = r.correo;
  texto($("correo-estado"), c.activo ? "Activo, cada " + c.minutos + " min" : "Detenido");
  $("correo-estado").className = "estado " + (c.activo ? "si" : "no");
  let d = c.activo ? "Próximo envío: " + c.proximo + " (pesadas desde " + c.desde + "). Pendientes: " + c.pendientes + "."
                   : "No se envían correos. Para empezar: escribir los minutos y Activar.";
  if (!c.servicio_ok) d += " ATENCIÓN: el servicio del correo no responde.";
  texto($("correo-config-detalle"), d);
  texto($("correo-activar"), c.activo ? "Cambiar intervalo" : "Activar");
  poner("correo-minutos", c.minutos);
  if (!listasEditadas) { pintarLista("para", c.para); pintarLista("copia", c.copia); }
  poner("correo-cuenta", c.cuenta);

  const b = r.bucle;
  texto($("bucle-estado"), b.activo ? "Activo, cada " + b.minutos + " min" + (b.servicio_ok ? "" : " (el servicio no responde)") : "Detenido");
  $("bucle-estado").className = "estado " + (b.activo ? "si" : "no");
  poner("bucle-minutos", b.minutos);
  poner("ctrl-ip", r.controlador.ip);
  poner("ctrl-puerto", r.controlador.puerto);

  const cuerpo = $("pedidos");
  cuerpo.innerHTML = "";
  if (!r.pedidos.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 6; td.className = "vacio"; td.textContent = "Todavía no se hizo ningún cambio desde la web.";
    tr.appendChild(td); cuerpo.appendChild(tr);
  }
  for (const p of r.pedidos) {
    const tr = document.createElement("tr");
    for (const [valor, clase] of [[p.nro, "n"], [p.creado, ""], [p.tipo, ""],
                                  [p.estado === "PENDIENTE" ? "En proceso" : p.estado, p.estado === "OK" ? "res-ok" : (p.estado === "ERROR" ? "res-error" : "")],
                                  [p.resultado, "largo"], [p.origen, ""]]) {
      const td = document.createElement("td");
      if (clase) td.className = clase;
      td.textContent = valor == null ? "" : String(valor);
      tr.appendChild(td);
    }
    cuerpo.appendChild(tr);
  }
}

// ---------------------------------------------------------------- destinatarios (uno por casilla)
let listasEditadas = false;

function itemCorreo(lista, valor) {
  const fila = document.createElement("div");
  fila.className = "item";
  const entrada = document.createElement("input");
  entrada.type = "email";
  entrada.maxLength = 254;
  entrada.placeholder = "nombre@empresa.com";
  entrada.value = valor || "";
  entrada.setAttribute("aria-label", lista === "para" ? "Correo para" : "Correo en copia");
  entrada.addEventListener("input", () => { listasEditadas = true; });
  const quitar = document.createElement("button");
  quitar.type = "button";
  quitar.textContent = "Quitar";
  quitar.addEventListener("click", () => {
    fila.remove();
    listasEditadas = true;
    revisarVacia(lista);
  });
  fila.append(entrada, quitar);
  return fila;
}

function revisarVacia(lista) {
  const caja = $("lista-" + lista);
  const aviso = caja.querySelector(".vacio-lista");
  const hay = caja.querySelector(".item");
  if (!hay && !aviso) {
    const p = document.createElement("p");
    p.className = "vacio-lista";
    p.textContent = lista === "para" ? "Sin destinatarios." : "Sin copia.";
    caja.prepend(p);
  } else if (hay && aviso) {
    aviso.remove();
  }
}

function pintarLista(lista, correos) {
  const caja = $("lista-" + lista);
  caja.innerHTML = "";
  for (const c of correos) caja.appendChild(itemCorreo(lista, c));
  revisarVacia(lista);
}

function leerLista(lista) {
  return Array.from($("lista-" + lista).querySelectorAll("input")).map((i) => i.value.trim()).filter((v) => v);
}

document.querySelectorAll("button.agregar").forEach((b) => {
  b.addEventListener("click", () => {
    const lista = b.dataset.lista;
    const fila = itemCorreo(lista, "");
    $("lista-" + lista).appendChild(fila);
    revisarVacia(lista);
    listasEditadas = true;
    fila.querySelector("input").focus();
  });
});

$("form-destinatarios").addEventListener("submit", (ev) => {
  ev.preventDefault();
  enviar("correo_destinatarios", { para: leerLista("para"), copia: leerLista("copia") });
});

// ---------------------------------------------------------------- contraseña: ver / ocultar
async function cargarClave() {
  const r = await pedir("/api/configuracion/clave");
  if (r.ok && !$("correo-clave").dataset.editado) $("correo-clave").value = r.clave || "";
}

$("ver-clave").addEventListener("click", () => {
  const campo = $("correo-clave");
  const ver = campo.type === "password";
  campo.type = ver ? "text" : "password";
  $("ver-clave").textContent = ver ? "Ocultar" : "Ver";
  $("ver-clave").setAttribute("aria-pressed", ver ? "true" : "false");
});

// ---------------------------------------------------------------- estado de comunicación
async function cargarComunicacion() {
  const r = await pedir("/api/comunicacion");
  if (!r.ok) return;
  const el = $("com-estado");
  texto(el, r.estado);
  el.title = r.momento ? "Última revisión: " + r.momento : "";
  el.className = "estado " + (r.estado === "Activo" ? "si" : (r.estado === "Falla de comunicación" ? "falla" : "no"));
  poner("com-segundos", r.segundos);
}

function zona(tipo) {
  if (tipo === "comunicacion") return $("res-comunicacion");
  if (tipo.startsWith("correo")) return $("res-correo");
  if (tipo === "bucle" || tipo === "guardar") return $("res-bucle");
  return $("res-controlador");
}

async function enviar(tipo, cuerpo) {
  if (ocupado) return;
  const caja = zona(tipo);
  caja.className = "resultado espera";
  texto(caja, tipo === "guardar" || tipo === "correo_probar" || tipo === "correo" ? "Procesando… (puede tardar hasta un minuto)" : "Procesando…");
  ocupado = true;
  document.querySelectorAll("main.config button").forEach((b) => { b.disabled = true; });
  try {
    const r = await pedir("/api/pedidos", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json", "X-CSRF-Token": CSRF },
      body: JSON.stringify(Object.assign({ tipo: tipo }, cuerpo)),
    });
    if (!r.ok) { caja.className = "resultado error"; texto(caja, r.error); return; }
    const limite = Date.now() + 180000;
    while (Date.now() < limite) {
      await new Promise((ok) => setTimeout(ok, 1000));
      const p = await pedir("/api/pedidos/" + r.nro);
      if (p.ok && p.estado !== "PENDIENTE") {
        caja.className = "resultado " + (p.estado === "OK" ? "bien" : "error");
        texto(caja, p.resultado);
        document.querySelectorAll("[data-editado]").forEach((el) => { delete el.dataset.editado; });
        if (tipo === "correo_destinatarios" && p.estado === "OK") listasEditadas = false;
        if (tipo === "correo_cuenta") cargarClave();
        return;
      }
    }
    caja.className = "resultado error";
    texto(caja, "No hubo respuesta del servicio del correo. Revisar en la Pi: systemctl status registro1280-correo");
  } finally {
    ocupado = false;
    document.querySelectorAll("main.config button").forEach((b) => { b.disabled = false; });
    cargar();
    cargarComunicacion();
  }
}

document.querySelectorAll("main.config input").forEach((el) => {
  el.addEventListener("input", () => { el.dataset.editado = "1"; });
});

document.querySelectorAll("form[data-tipo]").forEach((form) => {
  form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const tipo = form.dataset.tipo;
    if (tipo === "correo_cuenta") {
      enviar(tipo, { usuario: $("correo-cuenta").value.trim(), clave: $("correo-clave").value });
    } else if (tipo === "controlador") {
      enviar(tipo, { datos: $("ctrl-ip").value.trim() + " " + $("ctrl-puerto").value.trim() });
    } else {
      enviar(tipo, { datos: form.querySelector('[name="datos"]').value.trim() });
    }
  });
});

document.querySelectorAll("button[data-tipo]").forEach((b) => {
  b.addEventListener("click", () => {
    if (b.dataset.datos === "detener" && !confirm("¿Detener? Se puede volver a activar cuando se quiera.")) return;
    enviar(b.dataset.tipo, { datos: b.dataset.datos });
  });
});

function actualizar() {
  if (!ocupado) { cargar(); cargarClave(); }
  cargarComunicacion();
}
document.addEventListener("registro1280:vista", (ev) => {
  if (ev.detail === "configuracion") actualizar();
});
if (!vista.hidden) actualizar();
setInterval(() => { if (!vista.hidden) cargarComunicacion(); }, 3000);
setInterval(() => { if (!vista.hidden && !ocupado) cargar(); }, 15000);
})();
