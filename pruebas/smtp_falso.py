"""Servidor SMTP falso para las pruebas (no envía nada a internet).

Acepta AUTH PLAIN con un usuario y clave fijos, guarda cada mensaje recibido y
puede simular fallas: modo "limite" (Gmail 550 5.4.5 límite diario), "caido"
(cierra la conexión), "sin_respuesta" (se corta después de recibir el contenido)
o rechazar un destinatario.
"""

import base64
import email
import email.policy
import socketserver
import threading


class _Manejador(socketserver.StreamRequestHandler):
    def _decir(self, texto):
        self.wfile.write((texto + "\r\n").encode("utf-8"))

    def handle(self):
        srv = self.server
        if srv.modo == "caido":
            return
        self._decir("220 smtp.falso ESMTP")
        destinatarios, datos, en_datos = [], [], False
        while True:
            linea = self.rfile.readline()
            if not linea:
                return
            if en_datos:
                if linea in (b".\r\n", b".\n"):
                    en_datos = False
                    if srv.modo == "sin_respuesta":     # recibe el correo pero se corta antes de responder
                        return
                    if srv.modo == "limite":
                        self._decir("550 5.4.5 Daily user sending limit exceeded.")
                    else:
                        crudo = b"".join(datos)
                        srv.mensajes.append({"para": list(destinatarios),
                                             "mensaje": email.message_from_bytes(crudo, policy=email.policy.default)})
                        self._decir("250 2.0.0 OK")
                    destinatarios, datos = [], []
                else:
                    datos.append(linea[1:] if linea.startswith(b"..") else linea)
                continue
            orden = linea.decode("utf-8", "replace").strip()
            mayus = orden.upper()
            if mayus.startswith("EHLO") or mayus.startswith("HELO"):
                self.wfile.write(b"250-smtp.falso\r\n250-AUTH PLAIN\r\n250-8BITMIME\r\n250 SMTPUTF8\r\n")
            elif mayus.startswith("AUTH PLAIN"):
                partes = orden.split(" ", 2)
                try:
                    _, usuario, clave = base64.b64decode(partes[2]).decode("utf-8").split("\0")
                except Exception:
                    usuario, clave = "", ""
                srv.ingresos.append(usuario)
                if (usuario, clave) == (srv.usuario, srv.clave):
                    self._decir("235 2.7.0 Accepted")
                else:
                    self._decir("535 5.7.8 Username and Password not accepted.")
            elif mayus.startswith("MAIL FROM"):
                self._decir("250 2.1.0 OK")
            elif mayus.startswith("RCPT TO"):
                direccion = orden.split(":", 1)[1].strip().strip("<>").split(">")[0]
                if direccion in srv.rechazar:
                    self._decir("550 5.1.1 The email account that you tried to reach does not exist.")
                else:
                    destinatarios.append(direccion)
                    self._decir("250 2.1.5 OK")
            elif mayus == "DATA":
                en_datos = True
                self._decir("354 Go ahead")
            elif mayus in ("RSET", "NOOP"):
                self._decir("250 OK")
            elif mayus == "QUIT":
                self._decir("221 2.0.0 Bye")
                return
            else:
                self._decir("502 5.5.1 Unrecognized command.")


class SMTPFalso(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, usuario="prueba@gmail.com", clave="abcdefghijklmnop"):
        super().__init__(("127.0.0.1", 0), _Manejador)
        self.usuario, self.clave = usuario, clave
        self.modo = "normal"
        self.rechazar = set()
        self.mensajes = []
        self.ingresos = []
        self.puerto = self.server_address[1]
        threading.Thread(target=self.serve_forever, daemon=True).start()

    def cuenta(self, clave=None):
        """La cuenta para registro1280.correo, apuntando a este servidor (sin TLS)."""
        return {"usuario": self.usuario, "clave": clave or self.clave, "servidor": "127.0.0.1",
                "puerto": self.puerto, "seguridad": "ninguna", "nombre": "Registro de pesadas 1280"}

    def cerrar(self):
        self.shutdown()
        self.server_close()
