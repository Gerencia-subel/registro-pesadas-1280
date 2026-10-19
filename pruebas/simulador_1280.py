"""Simulador del indicador Rice Lake 1280 (servidor TCP de comandos, puerto 10001).

Responde igual que el controlador real visto en planta:
  VERSION      -> "V2.03.01  1280"
  DB.SCHEMA#2  -> "3000,<actuales>,ID,3,4,cor,2,2,Producto,7,25,..."
  DB.DATA#2    -> una fila por pesada, columnas con |, cada fila termina en CR/LF
  otro         -> "??"

Sirve para las pruebas automáticas y para probar el sistema sin el controlador:
    python3 simulador_1280.py --puerto 10001 --cada 20
(agrega una pesada cada 20 segundos).

Modos de falla (para pruebas): normal, mudo, corta, pausa, lento, desconocido.
"""

import argparse
import random
import socket
import threading
import time
from datetime import datetime, timedelta

ESQUEMA = "ID,3,4,cor,2,2,Producto,7,25,Peso,5,8,Acum,5,8,FechaI,8,8,FechaF,8,8,Turno,1,1,Estado,1,1"
PRODUCTOS = ["Sapolio Floral", "Patito Bebe", "Trome Encanto Floral", "Klintex QuitaPro", "Granel"]


def fecha_ctrl(momento):
    return momento.strftime("%m%d%Y %H%M")


class Simulador:
    def __init__(self, puerto=0, maximo=3000, iniciales=0, primer_id=188755, reloj=None, host="127.0.0.1"):
        self.maximo = maximo
        self.filas = []
        self.siguiente_id = primer_id
        self.modo = "normal"
        self.data_sin_respuesta = 0  # cuántos pedidos de DB.DATA#2 ignorar (controlador ocupado)
        self.comandos = []          # comandos recibidos (para las pruebas)
        self.conexiones = 0
        self.reloj = reloj or datetime.now
        self._cor = 0
        self._acum = 0.0
        self._producto = PRODUCTOS[0]
        self._bloqueo = threading.Lock()
        inicio = self.reloj() - timedelta(minutes=iniciales)
        for i in range(iniciales):
            self.agregar(inicio + timedelta(minutes=i))
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind((host, puerto))
        self._srv.listen(8)
        self.puerto = self._srv.getsockname()[1]
        self._activo = True
        threading.Thread(target=self._aceptar, daemon=True).start()

    # ---- datos ---------------------------------------------------------
    def agregar(self, fin=None, peso=None, producto=None, id_ctrl=None, inicio=None):
        """Agrega una pesada (y borra la más antigua si la tabla está llena, como el real)."""
        with self._bloqueo:
            fin = fin or self.reloj()
            inicio = inicio or (fin - timedelta(minutes=1))
            if producto and producto != self._producto:
                self._producto, self._cor, self._acum = producto, 0, 0.0
            peso = round(random.uniform(285, 310), 2) if peso is None else peso
            self._cor += 1
            self._acum = round(self._acum + peso, 2)
            nuevo_id = self.siguiente_id if id_ctrl is None else id_ctrl
            self.siguiente_id = nuevo_id + 1
            turno = 1 if 7 <= fin.hour < 19 else 2
            fila = "%d|%d|%s|%s|%s|%s|%s|%d|0" % (
                nuevo_id, self._cor, self._producto, _num(peso), _num(self._acum),
                fecha_ctrl(inicio), fecha_ctrl(fin), turno)
            self.filas.append(fila)
            if len(self.filas) > self.maximo:
                del self.filas[0]
            return fila

    def reiniciar_ids(self, desde=1):
        """El controlador real reinició su contador de ID dos veces en 2025."""
        self.siguiente_id = desde

    # ---- servidor ------------------------------------------------------
    def _aceptar(self):
        while self._activo:
            try:
                con, _ = self._srv.accept()
            except OSError:
                return
            self.conexiones += 1
            threading.Thread(target=self._atender, args=(con,), daemon=True).start()

    def _atender(self, con):
        buffer = b""
        with con:
            con.settimeout(30)
            while True:
                try:
                    datos = con.recv(1024)
                except OSError:
                    return
                if not datos:
                    return
                buffer += datos
                while b"\r" in buffer:
                    linea, buffer = buffer.split(b"\r", 1)
                    comando = linea.decode("ascii", "replace").strip()
                    self.comandos.append(comando)
                    try:
                        if not self._responder(con, comando):
                            return
                    except OSError:  # el cliente cerró antes de terminar
                        return

    def _responder(self, con, comando):
        modo = self.modo
        if modo == "mudo":
            return True
        if modo == "desconocido":
            con.sendall(b"??\r\n")
            return True
        if comando == "VERSION":
            con.sendall(b"V2.03.01  1280\r\n")
            return True
        with self._bloqueo:
            filas = list(self.filas)
        if comando == "DB.SCHEMA#2":
            con.sendall(("%d,%d,%s\r\n" % (self.maximo, len(filas), ESQUEMA)).encode("ascii"))
            return True
        if comando == "DB.DATA#2":
            if self.data_sin_respuesta > 0:
                self.data_sin_respuesta -= 1
                return True
            datos = "".join(f + "\r\n" for f in filas).encode("latin-1")
            if modo == "corta":       # se corta a la mitad
                con.sendall(datos[: len(datos) // 2])
                return False
            trozos = [datos[i:i + 1460] for i in range(0, len(datos), 1460)]
            for n, trozo in enumerate(trozos):
                con.sendall(trozo)
                if modo == "pausa" and n == len(trozos) // 2:
                    time.sleep(3.0)    # pausa larga a mitad de la respuesta
                elif modo == "lento":
                    time.sleep(0.01)
            return True
        con.sendall(b"??\r\n")
        return True

    def cerrar(self):
        self._activo = False
        try:
            self._srv.shutdown(socket.SHUT_RDWR)  # despierta al hilo que espera en accept()
        except OSError:
            pass
        try:
            self._srv.close()
        except OSError:
            pass


def _num(valor):
    texto = ("%.2f" % valor).rstrip("0")
    return texto + "0" if texto.endswith(".") else texto


def main():
    parser = argparse.ArgumentParser(description="Simulador del Rice Lake 1280")
    parser.add_argument("--puerto", type=int, default=10001)
    parser.add_argument("--host", default="127.0.0.1", help="0.0.0.0 para aceptar conexiones de otros equipos")
    parser.add_argument("--iniciales", type=int, default=3000, help="pesadas que ya existen al iniciar")
    parser.add_argument("--cada", type=float, default=20, help="segundos entre pesadas nuevas")
    args = parser.parse_args()
    sim = Simulador(puerto=args.puerto, iniciales=args.iniciales, host=args.host)
    print("Simulador 1280 escuchando en el puerto %d con %d pesadas. Ctrl+C para salir." % (sim.puerto, len(sim.filas)))
    try:
        while True:
            time.sleep(args.cada)
            print("Nueva pesada:", sim.agregar())
    except KeyboardInterrupt:
        sim.cerrar()


if __name__ == "__main__":
    main()
