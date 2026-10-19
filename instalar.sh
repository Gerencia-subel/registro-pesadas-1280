#!/bin/bash
# Instala o actualiza el registro de pesadas del Rice Lake 1280 en la Raspberry Pi:
# base de datos MariaDB (MySQL), comandos de la terminal, bucle automático, envío por correo y web.
# Uso (desde la carpeta copiada):   sudo bash instalar.sh
# Otro puerto para la web:          sudo PUERTO_WEB=8082 bash instalar.sh
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Hay que ejecutarlo con sudo:  sudo bash instalar.sh"
  exit 1
fi

ORIGEN="$(cd "$(dirname "$0")" && pwd)"
DESTINO=/opt/registro1280
DATOS=/var/lib/registro1280
ETC=/etc/registro1280
INI="$ETC/web.ini"
USUARIO_PI="${SUDO_USER:-}"
PY=/usr/bin/python3

# Puerto: el indicado, o el que ya tenía, o 8081.
if [ -n "${PUERTO_WEB:-}" ]; then
  PUERTO="$PUERTO_WEB"
elif [ -f "$INI" ] && grep -qE '^puerto *= *[0-9]+' "$INI"; then
  PUERTO="$(grep -E '^puerto *= *[0-9]+' "$INI" | head -1 | tr -dc '0-9')"
else
  PUERTO=8081
fi
if ! [[ "$PUERTO" =~ ^[0-9]+$ ]] || [ "$PUERTO" -lt 1 ] || [ "$PUERTO" -gt 65535 ]; then
  echo "Puerto no válido: $PUERTO"
  exit 1
fi

echo "1/7 Revisando el sistema..."
if [ ! -x "$PY" ] || ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
  echo "Se necesita Python 3.9 o superior (Raspberry Pi OS)."
  exit 1
fi
systemctl stop registro1280-web >/dev/null 2>&1 || true
if ! "$PY" -c 'import socket, sys
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("0.0.0.0", int(sys.argv[1]))); s.close()' "$PUERTO" 2>/dev/null; then
  echo "El puerto $PUERTO ya lo usa otro programa de esta Raspberry."
  echo "Instalar con otro puerto, por ejemplo:  sudo PUERTO_WEB=8082 bash instalar.sh"
  exit 1
fi

echo "2/7 Instalando MariaDB (MySQL) y librerías (necesita internet, tarda unos minutos)..."
if ! apt-get update -qq >/dev/null 2>&1 || ! DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
     mariadb-server python3-flask python3-waitress python3-mysqldb >/dev/null 2>&1; then
  echo "No se pudieron instalar los paquetes. Revisar que la Raspberry tenga internet y volver a intentar."
  exit 1
fi

echo "3/7 Creando los usuarios del sistema..."
for u in registro1280 registro1280web; do
  if ! id "$u" >/dev/null 2>&1; then
    useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin "$u"
  fi
done

echo "4/7 Copiando el programa a $DESTINO..."
mkdir -p "$DESTINO"
if [ "$ORIGEN" != "$DESTINO" ]; then
  rm -rf "$DESTINO/registro1280" "$DESTINO/pruebas"
  cp -r "$ORIGEN/registro1280" "$ORIGEN/pruebas" "$DESTINO/"
  # Se copia a un archivo nuevo y se renombra: si "actualizar" está corriendo desde
  # /opt/registro1280/actualizar.sh, no se le cambia el archivo mientras bash lo lee.
  for f in LEEME.txt instalar.sh desinstalar.sh actualizar.sh registro1280-bucle.service \
           registro1280-web.service registro1280-correo.service; do
    cp "$ORIGEN/$f" "$DESTINO/.$f.nuevo"
    mv -f "$DESTINO/.$f.nuevo" "$DESTINO/$f"
  done
fi
find "$DESTINO" -name "__pycache__" -type d -prune -exec rm -rf {} +
chown -R root:root "$DESTINO"
# Al copiar desde Windows los archivos pueden llegar solo con permiso para el dueño.
chmod 755 "$DESTINO"
chmod -R u=rwX,go=rX "$DESTINO/registro1280" "$DESTINO/pruebas"
chmod 644 "$DESTINO/LEEME.txt" "$DESTINO"/*.service
chmod 755 "$DESTINO/instalar.sh" "$DESTINO/desinstalar.sh" "$DESTINO/actualizar.sh"
mkdir -p "$DATOS/respaldos"
chown -R registro1280:registro1280 "$DATOS"
chmod 750 "$DATOS" "$DATOS/respaldos"

echo "5/7 Preparando la base de datos registro1280..."
systemctl enable mariadb >/dev/null 2>&1 || true
systemctl start mariadb
for _ in $(seq 1 30); do
  mariadb -e "SELECT 1" >/dev/null 2>&1 && break
  sleep 1
done
if ! mariadb -e "SELECT 1" >/dev/null 2>&1; then
  echo "MariaDB no arrancó. Ver el detalle con:  sudo journalctl -u mariadb -n 30"
  exit 1
fi
mariadb < "$DESTINO/registro1280/esquema.sql"
mariadb <<'SQL'
CREATE USER IF NOT EXISTS 'registro1280'@'localhost' IDENTIFIED VIA unix_socket;
GRANT SELECT, INSERT, UPDATE, LOCK TABLES, SHOW VIEW ON registro1280.* TO 'registro1280'@'localhost';
GRANT DELETE ON registro1280.descargas TO 'registro1280'@'localhost';
CREATE USER IF NOT EXISTS 'registro1280web'@'localhost' IDENTIFIED VIA unix_socket;
GRANT SELECT ON registro1280.* TO 'registro1280web'@'localhost';
SQL
if [ -n "$USUARIO_PI" ] && [ "$USUARIO_PI" != "root" ] && [[ "$USUARIO_PI" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]]; then
  mariadb -e "CREATE USER IF NOT EXISTS '$USUARIO_PI'@'localhost' IDENTIFIED VIA unix_socket;
              GRANT SELECT, INSERT, UPDATE ON registro1280.* TO '$USUARIO_PI'@'localhost';"
fi
mariadb -e "FLUSH PRIVILEGES"

echo "6/7 Creando los comandos de la terminal..."
for c in mostrar guardar visualizar bucle correo controlador cambiar-clave vaciar-base; do
  cat > "/usr/local/bin/$c" <<EOF
#!/bin/sh
# Registro de pesadas 1280: comando $c
cd /opt/registro1280 && exec /usr/bin/python3 -m registro1280 $c "\$@"
EOF
  chmod 755 "/usr/local/bin/$c"
done
cat > /usr/local/bin/actualizar <<'EOF2'
#!/bin/sh
# Registro de pesadas 1280: comando actualizar (baja la versión nueva desde GitHub)
exec bash /opt/registro1280/actualizar.sh "$@"
EOF2
chmod 755 /usr/local/bin/actualizar

echo "7/7 Creando e iniciando los servicios (bucle, correo y web)..."
mkdir -p "$ETC"
chmod 755 "$ETC"
(cd "$DESTINO" && "$PY" -m registro1280 preparar-web "$PUERTO" >/dev/null)
chown root:registro1280web "$INI"
chmod 640 "$INI"
if [ -f "$ETC/correo.ini" ]; then   # cuenta de Gmail: solo root y el servicio del correo
  chown root:registro1280 "$ETC/correo.ini"
  chmod 640 "$ETC/correo.ini"
fi
if [ -d "$ORIGEN/.git" ] && command -v git >/dev/null; then
  git -C "$ORIGEN" rev-parse HEAD > "$ETC/version_instalada" 2>/dev/null || true
fi
cp "$DESTINO/registro1280-bucle.service" "$DESTINO/registro1280-web.service" "$DESTINO/registro1280-correo.service" \
   /etc/systemd/system/
systemctl daemon-reload
systemctl enable registro1280-bucle registro1280-web registro1280-correo >/dev/null 2>&1
systemctl restart registro1280-bucle registro1280-web registro1280-correo
sleep 4

FALLA=0
for s in registro1280-bucle registro1280-web registro1280-correo; do
  if ! systemctl is-active --quiet "$s"; then
    echo
    echo "El servicio $s no arrancó. Detalle:"
    journalctl -u "$s" -n 15 --no-pager 2>/dev/null || true
    FALLA=1
  fi
done
[ "$FALLA" -eq 0 ] || exit 1

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
echo "Listo. Todo queda funcionando y arranca solo al encender la Raspberry."
echo
echo "Comandos (en esta terminal):"
echo "  mostrar \"DB.DATA#2\"     ver lo que manda el controlador"
echo "  guardar                 guardar en la base solo las pesadas nuevas"
echo "  visualizar              ver todas las pesadas guardadas"
echo "  bucle 5                 guardar cada 5 minutos, siempre (bucle detener para pararlo)"
echo "  correo 5                enviar por correo cada 5 minutos el CSV de las pesadas nuevas"
echo "  controlador             ver o cambiar la IP del controlador"
echo
echo "Web (usuario y clave: intradevco):  http://${IP:-IP-DE-LA-RASPBERRY}:$PUERTO"
echo "Para publicarla en internet con Tailscale Funnel:  sudo tailscale funnel --bg --https=443 $PUERTO"
