"use strict";

const $ = (id) => document.getElementById(id);
let pagina = 1;
let paginas = 1;
let filtros = {};

async function pedir(ruta) {
  let r;
  try {
    r = await fetch(ruta, { headers: { Accept: "application/json" }, credentials: "same-origin" });
  } catch (e) {
    return { ok: false, error: "Sin conexión con la Raspberry." };
  }
  if (r.status === 401) {
    location.href = "/ingresar";
    return { ok: false, error: "La sesión terminó." };
  }
  try {
    return await r.json();
  } catch (e) {
    return { ok: false, error: "Respuesta inesperada (HTTP " + r.status + ")." };
  }
}

function texto(el, valor) { el.textContent = valor == null ? "" : String(valor); }

async function cargarResumen() {
  const r = await pedir("/api/resumen");
  if (!r.ok) { texto($("descarga-detalle"), r.error); return; }
  texto($("total"), r.total_texto);
  texto($("rango"), r.total ? "Del " + r.primera + " al " + r.ultima : "Todavía no hay pesadas guardadas");
  const d = r.ultima_descarga;
  if (d) {
    $("descarga").innerHTML = "";
    const hora = document.createElement("span");
    hora.textContent = d.inicio + " ";
    const res = document.createElement("span");
    res.className = d.resultado === "OK" ? "estado-ok" : "estado-error";
    res.textContent = d.resultado === "OK" ? "OK" : "Error";
    $("descarga").append(hora, res);
    texto($("descarga-detalle"), d.resultado === "OK"
      ? (d.nuevas || 0) + " nuevas de " + (d.leidas || 0) + " leídas, " + (d.segundos || 0).toFixed(1) + " s (" + d.origen + ")"
      : d.detalle);
  } else {
    texto($("descarga"), "Sin descargas");
    texto($("descarga-detalle"), "Usar el comando guardar o bucle en la Pi");
  }
  if (r.bucle.activo) {
    texto($("bucle"), "Cada " + r.bucle.minutos + " min");
    texto($("bucle-detalle"), r.bucle.servicio_ok ? "Activo" : "Activado, pero el servicio no responde");
    $("bucle-detalle").className = "detalle " + (r.bucle.servicio_ok ? "" : "estado-error");
  } else {
    texto($("bucle"), "Detenido");
    texto($("bucle-detalle"), "Se activa en Configuración");
    $("bucle-detalle").className = "detalle";
  }
  const c = r.correo;
  if (c.activo) {
    texto($("correo"), "Cada " + c.minutos + " min");
    let detalle = c.proximo ? "Próximo: " + c.proximo : "Activo";
    if (!c.servicio_ok) detalle = "Activado, pero el servicio no responde";
    else if (c.ultimo && c.ultimo.tipo === "tramo") {
      detalle = (c.ultimo.resultado === "OK" ? "Último: " + c.ultimo.tramo + ", " + c.ultimo.pesadas + " pesadas"
        : "Último falló: " + c.ultimo.detalle) + ". " + detalle;
    }
    texto($("correo-detalle"), detalle);
    const fallo = c.ultimo && c.ultimo.tipo === "tramo" && c.ultimo.resultado !== "OK";
    $("correo-detalle").className = "detalle " + (!c.servicio_ok || fallo ? "estado-error" : "");
  } else {
    texto($("correo"), "Detenido");
    texto($("correo-detalle"), "Se activa en Configuración");
    $("correo-detalle").className = "detalle";
  }
  texto($("controlador"), r.controlador);
  texto($("hora"), "Hora de la Pi: " + r.ahora);
}

function celda(fila, valor, clase) {
  const td = document.createElement("td");
  if (clase) td.className = clase;
  td.textContent = valor == null ? "" : String(valor);
  fila.appendChild(td);
  return td;
}

async function cargarPesadas() {
  const p = new URLSearchParams({ ...filtros, pagina: String(pagina) });
  const r = await pedir("/api/pesadas?" + p.toString());
  const cuerpo = $("filas");
  if (!r.ok) {
    $("mensaje").className = "mensaje error";
    texto($("mensaje"), r.error);
    return;
  }
  $("mensaje").className = "mensaje";
  paginas = r.paginas;
  pagina = Math.min(r.pagina, r.paginas);
  cuerpo.innerHTML = "";
  if (!r.filas.length) {
    const tr = document.createElement("tr");
    const td = celda(tr, Object.keys(filtros).length > 1 ? "No hay pesadas con esos filtros." : "Todavía no hay pesadas guardadas.", "vacio");
    td.colSpan = 12;
    cuerpo.appendChild(tr);
  }
  for (const f of r.filas) {
    const tr = document.createElement("tr");
    celda(tr, f.nro, "n");
    celda(tr, f.ID, "n");
    celda(tr, f.cor, "n");
    celda(tr, f.Producto);
    celda(tr, f.Peso, "n");
    celda(tr, f.Acum, "n");
    celda(tr, f.FechaI);
    celda(tr, f.FechaF);
    celda(tr, f.Turno, "n");
    celda(tr, f.Estado, "n");
    const g = celda(tr, f.guardado_en);
    if (f.actualizado_en) {
      g.classList.add("actualizado");
      const m = document.createElement("span");
      m.className = "marca";
      m.textContent = "actualizada " + f.actualizado_en;
      g.appendChild(m);
    }
    celda(tr, f.correo, "n");
    cuerpo.appendChild(tr);
  }
  const desde = r.total ? (pagina - 1) * r.por_pagina + 1 : 0;
  const hasta = Math.min(pagina * r.por_pagina, r.total);
  texto($("pagina-texto"), "Página " + pagina + " de " + paginas + " (" + desde.toLocaleString("en-US") + "–" +
    hasta.toLocaleString("en-US") + " de " + r.total_texto + ")");
  $("anterior").disabled = pagina <= 1;
  $("siguiente").disabled = pagina >= paginas;
}

async function cargarCorreos() {
  const r = await pedir("/api/correos");
  const cuerpo = $("correos-filas");
  cuerpo.innerHTML = "";
  if (!r.ok || !r.correos.length) {
    const tr = document.createElement("tr");
    const td = celda(tr, r.ok ? "Todavía no se envió ningún correo." : r.error, "vacio");
    td.colSpan = 8;
    cuerpo.appendChild(tr);
    return;
  }
  for (const c of r.correos) {
    const tr = document.createElement("tr");
    celda(tr, c.nro, "n");
    celda(tr, c.enviado_en);
    celda(tr, c.tipo);
    celda(tr, c.tramo);
    celda(tr, c.pesadas, "n");
    celda(tr, c.resultado, c.resultado === "OK" ? "res-ok" : "res-error");
    celda(tr, c.archivo);
    celda(tr, c.detalle, "largo");
    cuerpo.appendChild(tr);
  }
}

function leerFiltros() {
  filtros = {};
  for (const nombre of ["desde", "hasta", "producto", "id", "por_pagina"]) {
    const v = $(nombre).value.trim();
    if (v) filtros[nombre] = v;
  }
}

$("filtros").addEventListener("submit", (ev) => {
  ev.preventDefault();
  leerFiltros();
  pagina = 1;
  cargarPesadas();
});
$("limpiar").addEventListener("click", () => {
  for (const nombre of ["desde", "hasta", "producto", "id"]) $(nombre).value = "";
  $("por_pagina").value = "100";
  leerFiltros();
  pagina = 1;
  cargarPesadas();
});
$("por_pagina").addEventListener("change", () => { leerFiltros(); pagina = 1; cargarPesadas(); });
$("anterior").addEventListener("click", () => { if (pagina > 1) { pagina--; cargarPesadas(); } });
$("siguiente").addEventListener("click", () => { if (pagina < paginas) { pagina++; cargarPesadas(); } });

$("correos-caja").addEventListener("toggle", () => { if ($("correos-caja").open) cargarCorreos(); });

leerFiltros();
cargarResumen();
cargarPesadas();
setInterval(() => {
  cargarResumen();
  if (pagina === 1) cargarPesadas();   // en la primera página aparecen las nuevas solas
  if ($("correos-caja").open) cargarCorreos();
}, 30000);
