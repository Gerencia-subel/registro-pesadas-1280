(() => {
  "use strict";

  const vistas = {
    pesadas: { ruta: "/", titulo: "Registro de pesadas – Rice Lake 1280" },
    configuracion: { ruta: "/configuracion", titulo: "Configuración – Registro de pesadas" },
  };
  const posiciones = { pesadas: 0, configuracion: 0 };
  for (const nombre of Object.keys(vistas)) {
    if (!document.getElementById("vista-" + nombre)) delete vistas[nombre];
  }
  if ("scrollRestoration" in history) history.scrollRestoration = "manual";

  function mostrar(vista, guardarHistoria) {
    const anterior = document.body.dataset.vista;
    if (!vistas[vista] || vista === anterior) return;
    posiciones[anterior] = window.scrollY;
    document.body.dataset.vista = vista;
    for (const nombre of Object.keys(vistas)) {
      document.getElementById("vista-" + nombre).hidden = nombre !== vista;
    }
    document.querySelectorAll(".menu a[data-vista]").forEach((enlace) => {
      if (enlace.dataset.vista === vista) enlace.setAttribute("aria-current", "page");
      else enlace.removeAttribute("aria-current");
    });
    document.title = vistas[vista].titulo;
    if (guardarHistoria) history.pushState({ vista: vista }, "", vistas[vista].ruta);
    window.scrollTo(0, posiciones[vista]);
    document.dispatchEvent(new CustomEvent("registro1280:vista", { detail: vista }));
  }

  document.addEventListener("click", (ev) => {
    const enlace = ev.target.closest("a[data-vista]");
    if (!enlace || ev.defaultPrevented || ev.button !== 0 || ev.ctrlKey || ev.metaKey || ev.shiftKey || ev.altKey) return;
    if (!vistas[enlace.dataset.vista]) return;
    ev.preventDefault();
    mostrar(enlace.dataset.vista, true);
  });
  window.addEventListener("popstate", () => {
    mostrar(location.pathname === "/configuracion" ? "configuracion" : "pesadas", false);
  });
})();
