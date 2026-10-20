"""Página web del registro de pesadas: consulta de pesadas y configuración.

Corre como el usuario de Linux registro1280web, que puede leer, agregar pedidos y administrar
cuentas de acceso: las pesadas no se pueden editar ni borrar desde la web. Los cambios de
configuración (correo, bucle, controlador) y "guardar ahora" se piden con un pedido que
hace el servicio del correo, igual que los comandos de la terminal.
"""

import hmac
import json
import os
import secrets
import threading
import time
from datetime import datetime, timedelta

import MySQLdb
from flask import Flask, g, jsonify, redirect, render_template, request, send_file, session, url_for

from . import VERSION, accesos, acciones, basedatos, comunicacion, correo, excel, formato
from .basedatos import ErrorBase
from .horario import leer_fecha, minutos_config
from .reloj import ahora

INTENTOS_MAXIMOS = 5
BLOQUEO_SEGUNDOS = 300
LATIDO_MAXIMO = 200
TAMANOS = (5, 25, 50, 100, 500, 1000)


class Intentos:
    """Bloquea el ingreso por 5 minutos después de 5 claves incorrectas seguidas."""

    def __init__(self):
        self._datos = {}
        self._candado = threading.Lock()

    def bloqueado(self, origen):
        with self._candado:
            fallos, hasta = self._datos.get(origen, (0, 0.0))
            return hasta > time.monotonic()

    def fallo(self, origen):
        with self._candado:
            fallos, hasta = self._datos.get(origen, (0, 0.0))
            if hasta and hasta <= time.monotonic():
                fallos = 0
            fallos += 1
            hasta = time.monotonic() + BLOQUEO_SEGUNDOS if fallos >= INTENTOS_MAXIMOS else 0.0
            self._datos[origen] = (fallos, hasta)

    def exito(self, origen):
        with self._candado:
            self._datos.pop(origen, None)


def _fecha_param(nombre, limite_superior=False):
    valor = (request.args.get(nombre) or "").strip()
    if not valor:
        return None
    # Se conservan las consultas antiguas por día completo. Con hora, se incluye
    # todo el minuto elegido en Hasta (también los segundos de ese minuto).
    formato_fecha = "%Y-%m-%dT%H:%M" if "T" in valor else "%Y-%m-%d"
    try:
        momento = datetime.strptime(valor, formato_fecha)
        if limite_superior:
            momento += timedelta(minutes=1) if "T" in valor else timedelta(days=1)
        return momento
    except (ValueError, OverflowError):
        raise ValueError("Fecha u hora no válida: %s" % valor)


def _paginacion_param():
    pagina = max(1, int(request.args.get("pagina") or 1))
    por_pagina = int(request.args.get("por_pagina") or 5)
    return pagina, por_pagina if por_pagina in TAMANOS else 5


def _filtros_pesadas_param():
    desde, hasta = _fecha_param("desde"), _fecha_param("hasta", limite_superior=True)
    if desde and hasta and desde >= hasta:
        raise ValueError("Fecha y hora Desde deben ser anteriores o iguales a Hasta.")
    producto = (request.args.get("producto") or "").strip()[:60] or None
    id_texto = (request.args.get("id") or "").strip()
    try:
        id_ctrl = int(id_texto) if id_texto else None
    except ValueError:
        raise ValueError("El ID debe ser un número.")
    return dict(desde=desde, hasta=hasta, producto=producto, id_ctrl=id_ctrl, hasta_exclusivo=True)


def _num(valor):
    return None if valor is None else float(valor)


def _vivo(latido):
    try:
        edad = (ahora() - datetime.strptime(latido or "", "%Y-%m-%d %H:%M:%S")).total_seconds()
    except ValueError:
        return False
    return edad <= LATIDO_MAXIMO


def _lista(texto):
    return [d.strip() for d in (texto or "").split(",") if d.strip()]


def _hora_config(texto):
    try:
        return formato.fecha(datetime.strptime(texto or "", "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return ""


def _correo_json(c):
    tramo = correo.rango(c["desde"], c["hasta"]) if c["desde"] and c["hasta"] else ""
    detalle = c["detalle"] or ""
    if c["tipo"] in ("tramo", "prueba") and c["resultado"] == "OK":
        # También simplifica los registros anteriores sin modificar su historial.
        detalle = correo.detalle_enviado(c.get("para"), c.get("copia"))
    if c["tipo"] == "alerta":
        tramo = ""
    return {"nro": c["nro"], "enviado_en": formato.fecha_segundos(c["enviado_en"]), "tipo": c["tipo"],
            "tramo": tramo, "pesadas": c["pesadas"], "resultado": c["resultado"],
            "archivo": c["archivo"] or "", "detalle": detalle}


def crear_app(ajustes, conectar=basedatos.conectar):
    """ajustes: dict con usuario, clave_hash, secreto (de config_web.leer())."""
    carpeta = os.path.dirname(os.path.abspath(__file__))
    app = Flask(__name__, template_folder=os.path.join(carpeta, "templates"),
                static_folder=os.path.join(carpeta, "static"))
    app.secret_key = ajustes["secreto"]
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=12))
    try:
        app.json.ensure_ascii = False
    except AttributeError:
        pass
    intentos = Intentos()

    def origen_del_pedido():
        # Con Tailscale Funnel los pedidos llegan desde la misma Pi (127.0.0.1); en ese caso
        # se usa la IP real que informa el proxy, para no bloquear a todos por un solo intento.
        remoto = request.remote_addr or "?"
        if remoto in ("127.0.0.1", "::1"):
            reenviado = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
            if reenviado:
                return "funnel:" + reenviado[:60]
        return remoto

    def usuario_actual():
        if not isinstance(session.get("cuenta"), int) or not isinstance(session.get("revision"), str):
            return None
        con = conectar()
        try:
            return accesos.vigente(con, session["cuenta"], session["revision"])
        finally:
            con.close()

    @app.before_request
    def proteger():
        if request.endpoint in ("ingresar", "actualizar_pin", "static"):
            return None
        try:
            g.cuenta = usuario_actual()
        except (ErrorBase, MySQLdb.Error):
            return jsonify({"ok": False, "error": "No se pudo verificar el acceso. Intenta de nuevo."}), 503
        if g.cuenta:
            if g.cuenta["rol"] != "administrador" and request.endpoint not in (
                    "inicio", "salir", "api_resumen", "api_pesadas", "api_excel", "api_correos"):
                if request.path.startswith("/api/"):
                    return jsonify({"ok": False, "error": "Solo un administrador puede acceder a esta sección."}), 403
                return render_template("sin_acceso.html", version=VERSION), 403
            if "csrf" not in session:
                session["csrf"] = secrets.token_hex(16)
            if request.method == "POST" and not hmac.compare_digest(
                    request.headers.get("X-CSRF-Token", ""), session["csrf"]):
                return jsonify({"ok": False, "error": "La página está desactualizada. Recargarla."}), 403
            return None
        # No borrar el token de conversión por un recurso anónimo solicitado en paralelo.
        if any(clave in session for clave in ("cuenta", "revision", "usuario")):
            session.clear()
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "La sesión terminó. Volver a ingresar."}), 401
        return redirect(url_for("ingresar"))

    @app.after_request
    def cabeceras(respuesta):
        respuesta.headers["X-Content-Type-Options"] = "nosniff"
        respuesta.headers["X-Frame-Options"] = "DENY"
        respuesta.headers["Referrer-Policy"] = "no-referrer"
        if request.endpoint != "static":
            respuesta.headers["Cache-Control"] = "no-store"
        return respuesta

    def iniciar_sesion(cuenta, origen):
        intentos.exito(origen)
        session.clear()
        session.permanent = True
        session["cuenta"] = cuenta["nro"]
        session["revision"] = cuenta["revision"]
        session["csrf"] = secrets.token_hex(16)
        return redirect(url_for("inicio"))

    def error_ingreso(origen):
        intentos.fallo(origen)
        return ("Demasiados intentos. Esperar 5 minutos y volver a probar."
                if intentos.bloqueado(origen) else "Usuario o PIN incorrectos.")

    @app.route("/ingresar", methods=["GET", "POST"])
    def ingresar():
        error = None
        if request.method == "POST":
            origen = origen_del_pedido()
            usuario = (request.form.get("usuario") or "").strip()
            clave = request.form.get("clave") or ""
            if intentos.bloqueado(origen):
                error = "Demasiados intentos. Esperar 5 minutos y volver a probar."
            elif not accesos.pin_valido(clave):
                intentos.fallo(origen)
                error = "El PIN debe tener exactamente 6 dígitos: ni más ni menos. Si usabas otra clave, elige Actualizar acceso anterior."
            else:
                try:
                    con = conectar()
                    try:
                        cuenta = accesos.autenticar(con, usuario, clave) if len(usuario) <= 80 and len(clave) <= 1024 else None
                    finally:
                        con.close()
                except (ErrorBase, MySQLdb.Error):
                    return render_template("ingresar.html", error="No se pudo verificar el acceso. Intenta de nuevo.", version=VERSION), 503
                if cuenta:
                    return iniciar_sesion(cuenta, origen)
                else:
                    error = error_ingreso(origen)
        return render_template("ingresar.html", error=error, version=VERSION), (401 if error else 200)

    @app.route("/actualizar-pin", methods=["GET", "POST"])
    def actualizar_pin():
        error, estado = None, 200
        if "migrar_csrf" not in session:
            session["migrar_csrf"] = secrets.token_hex(16)
        if request.method == "POST":
            origen = origen_del_pedido()
            usuario = (request.form.get("usuario") or "").strip()
            anterior = request.form.get("anterior") or ""
            pin = request.form.get("pin") or ""
            token = request.form.get("csrf") or ""
            if not hmac.compare_digest(token.encode("utf-8"), session["migrar_csrf"].encode("utf-8")):
                error, estado = "Recarga la página y vuelve a intentar.", 403
            elif intentos.bloqueado(origen):
                error, estado = "Demasiados intentos. Esperar 5 minutos y volver a probar.", 401
            elif not accesos.pin_valido(pin):
                error, estado = "El PIN debe tener exactamente 6 dígitos: ni más ni menos.", 400
            elif pin != request.form.get("repetir"):
                error, estado = "Los PIN no coinciden.", 400
            else:
                try:
                    con = conectar()
                    try:
                        cuenta = accesos.convertir_pin(con, usuario, anterior, pin) if len(usuario) <= 80 and len(anterior) <= 1024 else None
                    finally:
                        con.close()
                except (ErrorBase, MySQLdb.Error):
                    error, estado = "No se pudo actualizar el acceso. Intenta de nuevo.", 503
                else:
                    if cuenta:
                        return iniciar_sesion(cuenta, origen)
                    error_ingreso(origen)
                    error, estado = ("Demasiados intentos. Esperar 5 minutos y volver a probar."
                                     if intentos.bloqueado(origen) else
                                     "Los datos anteriores no son válidos o este acceso ya usa un PIN de 6 dígitos."), 401
        return render_template("actualizar_pin.html", error=error, csrf=session["migrar_csrf"], version=VERSION), estado

    @app.get("/salir")
    def salir():
        session.clear()
        return redirect(url_for("ingresar"))

    @app.get("/")
    def inicio():
        return render_template("panel.html", vista="pesadas", usuario=g.cuenta["usuario"], administrador=g.cuenta["rol"] == "administrador", version=VERSION,
                               tamanos=TAMANOS, csrf=session.get("csrf", ""))

    @app.get("/configuracion")
    def configuracion():
        return render_template("panel.html", vista="configuracion", usuario=g.cuenta["usuario"], administrador=True, version=VERSION,
                               tamanos=TAMANOS, csrf=session.get("csrf", ""))

    def con_base(funcion):
        try:
            con = conectar()
        except ErrorBase as e:
            return jsonify({"ok": False, "error": str(e)}), 503
        try:
            return funcion(con)
        except ErrorBase as e:
            return jsonify({"ok": False, "error": str(e)}), 503
        except accesos.ErrorAcceso as e:
            return jsonify({"ok": False, "error": str(e)}), e.estado
        except MySQLdb.Error:
            return jsonify({"ok": False, "error": "No se pudo consultar o guardar en la base. Intenta de nuevo."}), 503
        finally:
            try:
                con.close()
            except Exception:
                pass

    @app.get("/api/resumen")
    def api_resumen():
        def hacer(con):
            r = basedatos.resumen(con)
            cfg = r["config"]
            d = r["ultima_descarga"]
            c = r["ultimo_correo"]
            servicio_ok = _vivo(cfg.get("latido"))
            return jsonify({
                "ok": True,
                "ahora": formato.fecha(ahora()),
                "total": r["total"],
                "total_texto": formato.numero(r["total"]),
                "primera": formato.fecha(r["primera"]),
                "ultima": formato.fecha(r["ultima"]),
                "rechazadas": r["rechazadas"],
                "controlador": "%s:%s" % (cfg.get("ip", ""), cfg.get("puerto", "")),
                "bucle": {"activo": cfg.get("bucle_activo") == "1", "minutos": cfg.get("bucle_minutos"),
                          "servicio_ok": servicio_ok},
                "ultima_descarga": None if not d else {
                    "inicio": formato.fecha_segundos(d["inicio"]), "origen": d["origen"],
                    "resultado": d["resultado"], "nuevas": d["nuevas"], "leidas": d["leidas"],
                    "segundos": _num(d["segundos"]), "detalle": d["detalle"] or "",
                },
                "correo": {
                    "activo": cfg.get("correo_activo") == "1", "minutos": cfg.get("correo_minutos"),
                    "servicio_ok": _vivo(cfg.get("correo_latido")),
                    "proximo": _hora_config(cfg.get("correo_proximo")),
                    "ultimo": None if not c else _correo_json(c),
                },
            })
        return con_base(hacer)

    @app.get("/api/pesadas")
    def api_pesadas():
        try:
            filtros = _filtros_pesadas_param()
            pagina, por_pagina = _paginacion_param()
        except ValueError as e:
            mensaje = str(e) if str(e).startswith(("Fecha", "El ID")) else "La página y las filas por página deben ser números enteros."
            return jsonify({"ok": False, "error": mensaje}), 400

        def hacer(con):
            filas, cantidad = basedatos.listar(con, pagina=pagina, por_pagina=por_pagina, **filtros)
            paginas = max(1, (cantidad + por_pagina - 1) // por_pagina)
            return jsonify({
                "ok": True, "total": cantidad, "total_texto": formato.numero(cantidad),
                "pagina": pagina, "paginas": paginas, "por_pagina": por_pagina,
                "filas": [{
                    "nro": f["nro"], "ID": f["ID"], "cor": f["cor"], "Producto": f["Producto"],
                    "Peso": formato.kg(f["Peso"]), "Acum": formato.kg(f["Acum"]),
                    "FechaI": formato.fecha(f["FechaI"]), "FechaF": formato.fecha(f["FechaF"]),
                    "Turno": f["Turno"], "Estado": f["Estado"],
                    "guardado_en": formato.fecha_segundos(f["guardado_en"]),
                    "actualizado_en": formato.fecha_segundos(f["actualizado_en"]),
                    "correo": f["correo"] or None,
                } for f in filas],
            })
        return con_base(hacer)

    @app.get("/api/pesadas/excel")
    def api_excel():
        try:
            filtros = _filtros_pesadas_param()
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        sin_filtros = all(filtros[k] is None for k in ("desde", "hasta", "producto", "id_ctrl"))
        if sin_filtros and request.args.get("confirmar_todo") != "1":
            return jsonify({"ok": False, "error": "No has seleccionado filtros. Confirma si deseas exportar toda la base de datos."}), 400

        def hacer(con):
            archivo = excel.crear(basedatos.exportar_pesadas(con, **filtros))
            try:
                return send_file(archivo, as_attachment=True,
                                 download_name="pesadas_%s.xlsx" % ahora().strftime("%Y%m%d_%H%M%S"),
                                 mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            except Exception:
                archivo.close()
                raise
        return con_base(hacer)

    @app.get("/api/descargas")
    def api_descargas():
        def hacer(con):
            return jsonify({"ok": True, "descargas": [{
                "inicio": formato.fecha_segundos(d["inicio"]), "origen": d["origen"],
                "resultado": d["resultado"], "segundos": _num(d["segundos"]), "leidas": d["leidas"],
                "nuevas": d["nuevas"], "actualizadas": d["actualizadas"], "total": d["total"],
                "detalle": d["detalle"] or "",
            } for d in basedatos.descargas(con, 20)]})
        return con_base(hacer)

    @app.get("/api/configuracion")
    def api_configuracion():
        def hacer(con):
            cfg = basedatos.config(con)
            return jsonify({
                "ok": True,
                "ahora": formato.fecha(ahora()),
                "controlador": {"ip": cfg.get("ip", ""), "puerto": cfg.get("puerto", "")},
                "bucle": {"activo": cfg.get("bucle_activo") == "1", "minutos": cfg.get("bucle_minutos") or "5",
                          "servicio_ok": _vivo(cfg.get("latido"))},
                "correo": {
                    "activo": cfg.get("correo_activo") == "1", "minutos": str(minutos_config(cfg)),
                    "servicio_ok": _vivo(cfg.get("correo_latido")),
                    "cuenta": cfg.get("correo_cuenta", ""), "para": _lista(cfg.get("correo_para")),
                    "copia": _lista(cfg.get("correo_copia")),
                    "proximo": formato.fecha(leer_fecha(cfg.get("correo_proximo"))),
                    "desde": formato.fecha(leer_fecha(cfg.get("correo_desde"))),
                    "pendientes": basedatos.contar_pendientes(con) if cfg.get("correo_activo") == "1" else 0,
                },
                "pedidos": [{
                    "nro": p["nro"], "creado": formato.fecha_segundos(p["creado"]), "origen": p["origen"],
                    "tipo": acciones.TIPOS.get(p["tipo"], p["tipo"]), "estado": p["estado"],
                    "resultado": p["resultado"] or "",
                } for p in basedatos.ultimos_pedidos(con, 15)],
            })
        return con_base(hacer)

    @app.post("/api/pedidos")
    def api_crear_pedido():
        cuerpo = request.get_json(silent=True) or {}
        tipo = str(cuerpo.get("tipo") or "")
        if tipo not in acciones.TIPOS:
            return jsonify({"ok": False, "error": "Pedido desconocido."}), 400
        if tipo == "correo_cuenta":
            datos = json.dumps({"usuario": str(cuerpo.get("usuario") or "").strip(),
                                "clave": str(cuerpo.get("clave") or "")})
        elif tipo == "correo_destinatarios":
            datos = json.dumps({c: [str(x).strip() for x in (cuerpo.get(c) or []) if str(x).strip()]
                                for c in ("para", "copia")})
        else:
            datos = str(cuerpo.get("datos") or "").strip()
        if len(datos) > 6000:
            return jsonify({"ok": False, "error": "Demasiado texto (máximo 6000 caracteres)."}), 400

        def hacer(con):
            nro = basedatos.crear_pedido(con, tipo, datos, ahora(), origen_del_pedido())
            return jsonify({"ok": True, "nro": nro})
        return con_base(hacer)

    @app.get("/api/comunicacion")
    def api_comunicacion():
        def hacer(con):
            cfg = basedatos.config(con)
            e = comunicacion.leer_estado(cfg)
            return jsonify({"ok": True, "activo": cfg.get("comunicacion_activo") == "1",
                            "segundos": str(comunicacion.segundos_config(cfg)), "estado": e["estado"],
                            "momento": e["momento"].strftime("%H:%M:%S") if e["momento"] else ""})
        return con_base(hacer)

    @app.get("/api/configuracion/clave")
    def api_clave():
        def hacer(con):
            return jsonify({"ok": True, "clave": basedatos.config(con).get("correo_clave", "")})
        return con_base(hacer)

    @app.get("/api/pedidos/<int:nro>")
    def api_pedido(nro):
        def hacer(con):
            p = basedatos.pedido(con, nro)
            if p is None:
                return jsonify({"ok": False, "error": "No existe ese pedido."}), 404
            return jsonify({"ok": True, "estado": p["estado"], "resultado": p["resultado"] or ""})
        return con_base(hacer)

    @app.get("/api/correos")
    def api_correos():
        try:
            pagina, por_pagina = _paginacion_param()
        except ValueError:
            return jsonify({"ok": False, "error": "La página y las filas por página deben ser números enteros."}), 400

        def hacer(con):
            filas, cantidad, actual = basedatos.listar_correos(con, pagina, por_pagina)
            return jsonify({"ok": True, "correos": [_correo_json(c) for c in filas],
                            "total": cantidad, "total_texto": formato.numero(cantidad),
                            "pagina": actual, "paginas": max(1, (cantidad + por_pagina - 1) // por_pagina),
                            "por_pagina": por_pagina})
        return con_base(hacer)

    @app.get("/api/accesos")
    def api_accesos():
        return con_base(lambda con: jsonify({"ok": True, "usuarios": accesos.listar(con), "actual": g.cuenta["nro"]}))

    @app.post("/api/accesos")
    def api_crear_acceso():
        cuerpo = request.get_json(silent=True)
        if not isinstance(cuerpo, dict):
            return jsonify({"ok": False, "error": "Datos de usuario no válidos."}), 400

        def hacer(con):
            nro = accesos.crear(con, g.cuenta, cuerpo.get("usuario"), cuerpo.get("rol"), cuerpo.get("pin"))
            return jsonify({"ok": True, "nro": nro})
        return con_base(hacer)

    @app.post("/api/accesos/<int:nro>")
    def api_modificar_acceso(nro):
        cuerpo = request.get_json(silent=True)
        if not isinstance(cuerpo, dict):
            return jsonify({"ok": False, "error": "Datos de usuario no válidos."}), 400

        def hacer(con):
            accesos.modificar(con, g.cuenta, nro, cuerpo.get("accion"), cuerpo.get("rol"), cuerpo.get("pin"), cuerpo.get("usuario"))
            propia_sesion = nro == g.cuenta["nro"] and (cuerpo.get("accion") == "pin" or
                           (cuerpo.get("accion") == "usuario" and cuerpo.get("usuario") != g.cuenta["usuario"]) or
                           (cuerpo.get("accion") == "rol" and cuerpo.get("rol") != g.cuenta["rol"]))
            if propia_sesion:
                session.clear()
            return jsonify({"ok": True, "volver_ingresar": propia_sesion})
        return con_base(hacer)

    return app


def servir():
    """Servicio registro1280-web (lo arranca systemd)."""
    import logging
    import sys
    from waitress import serve
    from . import config_web
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stdout)
    ajustes = config_web.leer()
    app = crear_app(ajustes)
    logging.getLogger("registro1280").info("Web del registro de pesadas en el puerto %d", ajustes["puerto"])
    serve(app, host="0.0.0.0", port=ajustes["puerto"], threads=6, ident="registro1280")
