"use strict";

const $ = (id) => document.getElementById(id);
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
  if (!r.ok) { texto($("correo-detalle"), r.error); return; }
  const c = r.correo;
  texto($("correo-estado"), c.activo ? "Activo, cada " + c.minutos + " min" : "Detenido");
  $("correo-estado").className = "estado " + (c.activo ? "si" : "no");
  let d = c.activo ? "Próximo envío: " + c.proximo + " (pesadas desde " + c.desde + "). Pendientes: " + c.pendientes + "."
                   : "No se envían correos. Para empezar: escribir los minutos y Activar.";
  if (!c.servicio_ok) d += " ATENCIÓN: el servicio del correo no responde.";
  texto($("correo-detalle"), d);
  texto($("correo-activar"), c.activo ? "Cambiar intervalo" : "Activar");
  poner("correo-minutos", c.minutos);
  poner("correo-para", c.para);
  poner("correo-copia", c.copia);
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

function zona(tipo) {
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
        if (tipo === "correo_cuenta" && p.estado === "OK") $("correo-clave").value = "";
        return;
      }
    }
    caja.className = "resultado error";
    texto(caja, "No hubo respuesta del servicio del correo. Revisar en la Pi: systemctl status registro1280-correo");
  } finally {
    ocupado = false;
    document.querySelectorAll("main.config button").forEach((b) => { b.disabled = false; });
    cargar();
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

cargar();
setInterval(() => { if (!ocupado) cargar(); }, 15000);
