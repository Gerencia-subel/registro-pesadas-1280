"""Usuarios de la web. Los PIN se guardan como hash y nunca se devuelven al cliente."""

import re
import secrets
from contextlib import contextmanager

import MySQLdb
from MySQLdb.cursors import DictCursor
from werkzeug.security import check_password_hash, generate_password_hash

from .config_web import METODO

ROLES = ("administrador", "operador")
COLUMNAS = "nro, usuario, rol, revision, pin_seis"
# Solo hashes de los PIN iniciales elegidos por el propietario.
CUENTAS_INICIALES = (
    ("ADMINISTRADOR", "administrador", "pbkdf2:sha256:600000$TXjadL1Em3IK74HY$d094260394d5b513190404a09978a28bc99ae4aec9617bb8d938f4db0158694a"),
    ("OPERADOR", "operador", "pbkdf2:sha256:600000$Z7TWf9WqOSgZadof$e56306491862ab039040881f678c4d0771b5a86d44eecc8e58ecc54b143f0936"),
)


class ErrorAcceso(ValueError):
    def __init__(self, mensaje, estado=400):
        super().__init__(mensaje)
        self.estado = estado


@contextmanager
def _cambio(con):
    """Serializa cambios y la comprobación del último administrador, incluso entre procesos."""
    cur = con.cursor()
    cur.execute("SELECT CONCAT(DATABASE(), '_accesos')")
    candado = cur.fetchone()[0]
    cur.execute("SELECT GET_LOCK(%s, 10)", (candado,))
    if cur.fetchone()[0] != 1:
        raise ErrorAcceso("Otro cambio de accesos sigue en curso. Vuelve a intentar.", 409)
    try:
        con.commit()  # las lecturas previas no deben conservar una instantánea antigua
        yield con.cursor(DictCursor)
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        cur.execute("SELECT RELEASE_LOCK(%s)", (candado,))


def preparar(con, ajustes):
    """Migración única durante instalar/actualizar: conserva exactamente la clave anterior."""
    with _cambio(con) as cur:
        cur.execute("SELECT valor FROM configuracion WHERE clave='accesos_migrados'")
        marca = cur.fetchone()
        if marca and marca["valor"] == "1":
            return
        cur.execute("SELECT COUNT(*) AS n FROM usuarios")
        if not cur.fetchone()["n"]:
            cur.execute("INSERT INTO usuarios (usuario, clave_hash, rol, revision) VALUES (%s,%s,%s,%s)",
                        (ajustes["usuario"], ajustes["clave_hash"], "administrador", secrets.token_hex(16)))
        cur.execute("INSERT INTO configuracion (clave,valor) VALUES ('accesos_migrados','1') "
                    "ON DUPLICATE KEY UPDATE valor='1'")


def preparar_iniciales(con):
    """Crea o restablece las dos cuentas solicitadas una sola vez durante la instalación."""
    with _cambio(con) as cur:
        cur.execute("SELECT valor FROM configuracion WHERE clave='accesos_iniciales_152'")
        marca = cur.fetchone()
        if marca and marca["valor"] == "1":
            return
        for usuario, rol, clave_hash in CUENTAS_INICIALES:
            cur.execute("INSERT INTO usuarios (usuario, clave_hash, rol, revision, pin_seis) VALUES (%s,%s,%s,%s,1) "
                        "ON DUPLICATE KEY UPDATE clave_hash=VALUES(clave_hash), rol=VALUES(rol), "
                        "revision=VALUES(revision), pin_seis=1",
                        (usuario, clave_hash, rol, secrets.token_hex(16)))
        cur.execute("INSERT INTO configuracion (clave,valor) VALUES ('accesos_iniciales_152','1') "
                    "ON DUPLICATE KEY UPDATE valor='1'")


def _credenciales(con, usuario, clave):
    cur = con.cursor(DictCursor)
    cur.execute("SELECT " + COLUMNAS + ", clave_hash FROM usuarios WHERE usuario=%s", (usuario,))
    fila = cur.fetchone()
    con.commit()
    # Igual coste para un usuario inexistente: evita revelar nombres por el tiempo de respuesta.
    valido = check_password_hash(fila["clave_hash"] if fila else _HASH_VACIO, clave)
    if not fila or not valido:
        return None
    fila.pop("clave_hash")
    return fila


def autenticar(con, usuario, pin):
    if not pin_valido(pin):
        return None
    fila = _credenciales(con, usuario, pin)
    if fila and not fila["pin_seis"]:
        # Un PIN anterior que ya tiene seis dígitos se conserva sin pedir cambiarlo.
        with _cambio(con) as cur:
            revision = secrets.token_hex(16)
            cur.execute("UPDATE usuarios SET pin_seis=1, revision=%s WHERE nro=%s AND revision=%s AND pin_seis=0",
                        (revision, fila["nro"], fila["revision"]))
            if cur.rowcount != 1:
                return None
            fila.update(pin_seis=1, revision=revision)
    return fila


_HASH_VACIO = generate_password_hash(secrets.token_hex(16), method=METODO)


def vigente(con, nro, revision):
    cur = con.cursor(DictCursor)
    cur.execute("SELECT " + COLUMNAS + " FROM usuarios WHERE nro=%s AND revision=%s AND pin_seis=1", (nro, revision))
    fila = cur.fetchone()
    con.commit()
    return fila


def listar(con):
    cur = con.cursor(DictCursor)
    cur.execute("SELECT nro, usuario, rol FROM usuarios ORDER BY usuario")
    filas = list(cur.fetchall())
    con.commit()
    return filas


def _nombre(usuario):
    if not isinstance(usuario, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{3,40}", usuario):
        raise ErrorAcceso("Usuario: usa de 3 a 40 letras, números, puntos, guiones o guiones bajos.")
    return usuario


def pin_valido(pin):
    return isinstance(pin, str) and re.fullmatch(r"[0-9]{6}", pin) is not None


def _hash_pin(pin):
    if not pin_valido(pin):
        raise ErrorAcceso("El PIN debe tener exactamente 6 dígitos: ni más ni menos.")
    return generate_password_hash(pin, method=METODO)


def _rol(rol):
    if rol not in ROLES:
        raise ErrorAcceso("Selecciona Administrador u Operador.")
    return rol


def _administrador(cur, actor):
    # Se vuelve a verificar después del candado: otro administrador pudo revocar la sesión.
    cur.execute("SELECT " + COLUMNAS + " FROM usuarios WHERE nro=%s AND revision=%s",
                (actor["nro"], actor["revision"]))
    fila = cur.fetchone()
    if not fila or fila["rol"] != "administrador" or not fila["pin_seis"]:
        raise ErrorAcceso("La sesión ya no permite administrar accesos. Vuelve a ingresar.", 403)


def crear(con, actor, usuario, rol, pin):
    usuario, rol, clave_hash = _nombre(usuario), _rol(rol), _hash_pin(pin)
    try:
        with _cambio(con) as cur:
            _administrador(cur, actor)
            cur.execute("INSERT INTO usuarios (usuario, clave_hash, rol, revision, pin_seis) VALUES (%s,%s,%s,%s,1)",
                        (usuario, clave_hash, rol, secrets.token_hex(16)))
            return cur.lastrowid
    except MySQLdb.IntegrityError:
        raise ErrorAcceso("Ese usuario ya existe.", 409)


def modificar(con, actor, nro, accion, rol=None, pin=None, usuario=None):
    if accion not in ("usuario", "rol", "pin", "eliminar"):
        raise ErrorAcceso("Acción de acceso desconocida.")
    nuevo_rol = _rol(rol) if accion == "rol" else None
    clave_hash = _hash_pin(pin) if accion == "pin" else None
    nombre = _nombre(usuario) if accion == "usuario" else None
    with _cambio(con) as cur:
        _administrador(cur, actor)
        cur.execute("SELECT " + COLUMNAS + " FROM usuarios WHERE nro=%s", (nro,))
        fila = cur.fetchone()
        if not fila:
            raise ErrorAcceso("Ese usuario ya no existe.", 404)
        if accion == "eliminar" and nro == actor["nro"]:
            raise ErrorAcceso("No puedes eliminar tu propio usuario.", 409)
        if fila["rol"] == "administrador" and (accion == "eliminar" or nuevo_rol == "operador"):
            cur.execute("SELECT COUNT(*) AS n FROM usuarios WHERE rol='administrador'")
            if cur.fetchone()["n"] <= 1:
                raise ErrorAcceso("Debe quedar al menos un administrador.", 409)
        if accion == "eliminar":
            cur.execute("DELETE FROM usuarios WHERE nro=%s", (nro,))
        elif accion == "pin":
            cur.execute("UPDATE usuarios SET clave_hash=%s, revision=%s, pin_seis=1 WHERE nro=%s",
                        (clave_hash, secrets.token_hex(16), nro))
        elif accion == "usuario" and nombre != fila["usuario"]:
            try:
                cur.execute("UPDATE usuarios SET usuario=%s, revision=%s WHERE nro=%s",
                            (nombre, secrets.token_hex(16), nro))
            except MySQLdb.IntegrityError:
                raise ErrorAcceso("Ese usuario ya existe.", 409)
        elif accion == "rol" and nuevo_rol != fila["rol"]:
            cur.execute("UPDATE usuarios SET rol=%s, revision=%s WHERE nro=%s",
                        (nuevo_rol, secrets.token_hex(16), nro))


def recuperar(con, usuario, pin):
    """Recuperación local con sudo cambiar-clave, aun si se perdieron todos los accesos."""
    if not usuario or len(usuario) > 80:
        raise ErrorAcceso("El usuario debe tener entre 1 y 80 caracteres.")
    clave_hash = _hash_pin(pin)
    with _cambio(con) as cur:
        cur.execute("INSERT INTO usuarios (usuario, clave_hash, rol, revision, pin_seis) VALUES (%s,%s,'administrador',%s,1) "
                    "ON DUPLICATE KEY UPDATE clave_hash=VALUES(clave_hash), rol='administrador', revision=VALUES(revision), pin_seis=1",
                    (usuario, clave_hash, secrets.token_hex(16)))
