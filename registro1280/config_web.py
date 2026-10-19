"""Configuración de la página web: /etc/registro1280/web.ini (puerto, usuario, clave cifrada)."""

import configparser
import os
import secrets

from werkzeug.security import generate_password_hash

RUTA = os.environ.get("REGISTRO1280_WEB_INI", "/etc/registro1280/web.ini")
METODO = "pbkdf2:sha256:600000"


def leer(ruta=RUTA):
    ini = configparser.ConfigParser(interpolation=None)
    if not ini.read(ruta, encoding="utf-8") or not ini.has_section("web"):
        raise FileNotFoundError("No existe la configuración de la web (%s). Ejecutar: sudo bash instalar.sh" % ruta)
    s = ini["web"]
    return {"puerto": s.getint("puerto", 8081), "usuario": s.get("usuario", "intradevco"),
            "clave_hash": s.get("clave_hash", ""), "secreto": s.get("secreto", "")}


def _escribir(datos, ruta):
    ini = configparser.ConfigParser(interpolation=None)
    ini["web"] = {k: str(v) for k, v in datos.items()}
    temporal = ruta + ".tmp"
    with open(temporal, "w", encoding="utf-8") as f:
        f.write("# Configuración de la web del registro de pesadas. La clave está cifrada.\n"
                "# Para cambiar usuario o clave: sudo cambiar-clave\n")
        ini.write(f)
    if os.path.exists(ruta):
        st = os.stat(ruta)
        try:
            os.chown(temporal, st.st_uid, st.st_gid)
        except PermissionError:
            pass
        os.chmod(temporal, st.st_mode & 0o777)
    else:
        os.chmod(temporal, 0o640)
    os.replace(temporal, ruta)


def preparar(puerto, usuario="intradevco", clave="intradevco", ruta=RUTA):
    """Crea el archivo si no existe. Si existe, conserva usuario y clave y solo cambia el puerto."""
    if os.path.exists(ruta):
        datos = leer(ruta)
        datos["puerto"] = int(puerto)
        if not datos["secreto"]:
            datos["secreto"] = secrets.token_hex(32)
        if not datos["clave_hash"]:
            datos["clave_hash"] = generate_password_hash(clave, method=METODO)
        _escribir(datos, ruta)
        return False
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    _escribir({"puerto": int(puerto), "usuario": usuario,
               "clave_hash": generate_password_hash(clave, method=METODO),
               "secreto": secrets.token_hex(32)}, ruta)
    return True


def cambiar_clave(usuario, clave, ruta=RUTA):
    datos = leer(ruta)
    datos["usuario"] = usuario
    datos["clave_hash"] = generate_password_hash(clave, method=METODO)
    datos["secreto"] = secrets.token_hex(32)   # cierra las sesiones abiertas
    _escribir(datos, ruta)
