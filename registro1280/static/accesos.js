(() => {
  "use strict";
  const seccion = document.getElementById("seccion-accesos");
  const $ = (id) => seccion.querySelector("#" + id);
  const csrf = document.querySelector('meta[name="csrf"]').content;
  let ocupado = false;
  let edicion = null;

  function mensaje(texto, error = false) {
    $("res-accesos").textContent = texto;
    $("res-accesos").className = "resultado " + (error ? "error" : "bien");
  }

  async function pedir(ruta, cuerpo) {
    const opciones = { credentials: "same-origin", headers: { Accept: "application/json" } };
    if (cuerpo) {
      opciones.method = "POST";
      opciones.headers["Content-Type"] = "application/json";
      opciones.headers["X-CSRF-Token"] = csrf;
      opciones.body = JSON.stringify(cuerpo);
    }
    try {
      const respuesta = await fetch(ruta, opciones);
      if (respuesta.status === 401) { location.href = "/ingresar"; return { ok: false }; }
      return await respuesta.json();
    } catch (e) {
      return { ok: false, error: "No se pudo conectar con la Raspberry. Vuelve a intentar." };
    }
  }

  function cerrarEdicion() {
    $("editar-acceso").reset();
    $("editar-acceso").hidden = true;
    edicion = null;
  }

  function editar(usuario, accion) {
    if (ocupado) return;
    cerrarEdicion();
    edicion = { nro: usuario.nro, accion: accion };
    const pin = accion === "pin";
    $("editar-rol-campo").hidden = pin;
    $("editar-rol").disabled = pin;
    $("editar-pin-campo").hidden = !pin;
    $("editar-repetir-campo").hidden = !pin;
    for (const id of ["editar-pin", "editar-repetir"]) {
      $(id).disabled = !pin;
      $(id).required = pin;
    }
    $("editar-rol").value = usuario.rol;
    $("acceso-editar-titulo").textContent = (pin ? "Restablecer PIN: " : "Cambiar rol: ") + usuario.usuario;
    $("editar-acceso").hidden = false;
    $(pin ? "editar-pin" : "editar-rol").focus();
  }

  async function cargar() {
    const r = await pedir("/api/accesos");
    if (!r.ok) { mensaje(r.error || "No se pudo cargar los accesos.", true); return; }
    $("usuarios").replaceChildren();
    for (const usuario of r.usuarios) {
      const tr = document.createElement("tr");
      for (const texto of [usuario.usuario + (usuario.nro === r.actual ? " (tú)" : ""),
                            usuario.rol === "administrador" ? "Administrador" : "Operador"]) {
        const td = document.createElement("td");
        td.textContent = texto;
        tr.append(td);
      }
      const td = document.createElement("td");
      const acciones = document.createElement("div");
      acciones.className = "botones";
      for (const [accion, nombre] of [["rol", "Cambiar rol"], ["pin", "Restablecer PIN"], ["eliminar", "Eliminar"]]) {
        const boton = document.createElement("button");
        boton.type = "button";
        boton.textContent = nombre;
        boton.setAttribute("aria-label", nombre + " de " + usuario.usuario);
        boton.disabled = accion === "eliminar" && usuario.nro === r.actual;
        boton.addEventListener("click", () => {
          if (ocupado) return;
          if (accion !== "eliminar") editar(usuario, accion);
          else if (confirm("¿Eliminar el usuario " + usuario.usuario + "? Sus sesiones se cerrarán.")) {
            guardar("/api/accesos/" + usuario.nro, { accion: accion }, "Usuario eliminado.");
          }
        });
        acciones.append(boton);
      }
      td.append(acciones);
      tr.append(td);
      $("usuarios").append(tr);
    }
  }

  async function guardar(ruta, cuerpo, exito) {
    if (ocupado) return;
    ocupado = true;
    const botones = Array.from(seccion.querySelectorAll("button")).map((b) => [b, b.disabled]);
    botones.forEach(([b]) => { b.disabled = true; });
    try {
      const r = await pedir(ruta, cuerpo);
      if (!r.ok) { mensaje(r.error || "No se pudo guardar el acceso.", true); return; }
      cerrarEdicion();
      $("crear-acceso").reset();
      if (r.volver_ingresar) {
        alert(exito + " Vuelve a ingresar con tu usuario.");
        location.href = "/ingresar";
        return;
      }
      mensaje(exito);
      await cargar();
    } finally {
      ocupado = false;
      botones.forEach(([b, desactivado]) => { b.disabled = desactivado; });
    }
  }

  $("crear-acceso").addEventListener("submit", (ev) => {
    ev.preventDefault();
    if ($("acceso-pin").value !== $("acceso-repetir").value) { mensaje("Los PIN no coinciden.", true); return; }
    guardar("/api/accesos", { usuario: $("acceso-usuario").value.trim(), rol: $("acceso-rol").value,
                            pin: $("acceso-pin").value }, "Usuario creado.");
  });
  $("editar-acceso").addEventListener("submit", (ev) => {
    ev.preventDefault();
    if (!edicion) return;
    if (edicion.accion === "pin" && $("editar-pin").value !== $("editar-repetir").value) {
      mensaje("Los PIN no coinciden.", true); return;
    }
    guardar("/api/accesos/" + edicion.nro,
            { accion: edicion.accion, rol: $("editar-rol").value, pin: $("editar-pin").value },
            edicion.accion === "pin" ? "PIN restablecido." : "Rol actualizado.");
  });
  $("cancelar-acceso").addEventListener("click", cerrarEdicion);
  document.addEventListener("registro1280:vista", (ev) => {
    if (ev.detail === "configuracion" && !ocupado) cargar();
  });
  if (!document.getElementById("vista-configuracion").hidden) cargar();
})();
