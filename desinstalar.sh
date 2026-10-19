#!/bin/bash
# Quita los servicios, los comandos y el programa del registro de pesadas.
# La base de datos (las pesadas guardadas) se conserva.
# Para borrar también la base de datos:  sudo bash desinstalar.sh --borrar-datos
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Hay que ejecutarlo con sudo:  sudo bash desinstalar.sh"
  exit 1
fi

systemctl disable --now registro1280-bucle registro1280-web registro1280-correo registro1280-comunicacion \
  >/dev/null 2>&1 || true
rm -f /etc/systemd/system/registro1280-bucle.service /etc/systemd/system/registro1280-web.service \
      /etc/systemd/system/registro1280-correo.service /etc/systemd/system/registro1280-comunicacion.service
systemctl daemon-reload
for c in mostrar guardar visualizar bucle correo comunicacion controlador cambiar-clave vaciar-base actualizar; do
  if grep -q "Registro de pesadas 1280" "/usr/local/bin/$c" 2>/dev/null; then
    rm -f "/usr/local/bin/$c"
  fi
done
rm -rf /opt/registro1280 /opt/registro1280-git

if [ "${1:-}" = "--borrar-datos" ]; then
  if mariadb -e "SELECT 1" >/dev/null 2>&1; then
    mariadb -N -e "SELECT CONCAT('DROP USER ', QUOTE(user), '@', QUOTE(host), ';') FROM mysql.db WHERE db = 'registro1280'" \
      | mariadb || true
    mariadb -e "DROP USER IF EXISTS 'registro1280'@'localhost'; DROP USER IF EXISTS 'registro1280web'@'localhost';
                DROP DATABASE IF EXISTS registro1280;"
  fi
  rm -rf /etc/registro1280 /var/lib/registro1280
  userdel registro1280 >/dev/null 2>&1 || true
  userdel registro1280web >/dev/null 2>&1 || true
  echo "Programa y base de datos eliminados. MariaDB queda instalado."
else
  echo "Programa eliminado. La base de datos registro1280, los respaldos, la configuración"
  echo "y la cuenta de Gmail del correo (/var/lib/registro1280/correo.ini) se conservan."
fi
