#!/bin/bash
# Actualiza el registro de pesadas desde GitHub. La Pi solo LEE el repositorio (llave de solo lectura).
#   sudo actualizar             baja la última versión, corre las pruebas y, si pasan, la instala
#   sudo actualizar estado      versión instalada y cambios disponibles
#   sudo actualizar volver      vuelve a la versión anterior
#   sudo actualizar configurar  crea la llave de solo lectura para GitHub (una sola vez)
# Las pesadas guardadas, la configuración y la clave de la web nunca se tocan.
#
# Todo el script va entre { } y termina con exit: bash lo lee completo antes de empezar,
# así no le afecta que la instalación reemplace este mismo archivo mientras corre.
{
set -euo pipefail

REPO_URL="${REGISTRO1280_REPO:-ssh://git@ssh.github.com:443/Gerencia-subel/registro-pesadas-1280.git}"
RAMA="${REGISTRO1280_RAMA:-main}"
COPIA=/opt/registro1280-git
ETC=/etc/registro1280
LLAVE="$ETC/github_llave"
INSTALADA="$ETC/version_instalada"
ANTERIOR="$ETC/version_anterior"
REGISTRO=/var/lib/registro1280/ultima_actualizacion.log

if [ "$(id -u)" -ne 0 ]; then
  echo "Hay que ejecutarlo con sudo:  sudo actualizar"
  exit 1
fi
export GIT_SSH_COMMAND="ssh -i $LLAVE -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=$ETC/github_known_hosts -o ConnectTimeout=20"
export GIT_TERMINAL_PROMPT=0

corto() { git -C "$COPIA" log -1 --format='%h  %ad  %s' --date=format:'%d/%m/%Y %H:%M' "$1" 2>/dev/null || echo "${1:0:7}"; }

necesita_git() {
  if ! command -v git >/dev/null; then
    echo "Instalando git..."
    apt-get update -qq >/dev/null 2>&1 || true
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git >/dev/null 2>&1 || {
      echo "No se pudo instalar git. Revisar internet."; exit 1; }
  fi
}

necesita_llave() {
  if [[ "$REPO_URL" == ssh://* ]] && [ ! -f "$LLAVE" ]; then
    echo "Primero hay que crear la llave de GitHub:  sudo actualizar configurar"
    exit 1
  fi
}

bajar() {
  if [ ! -d "$COPIA/.git" ]; then
    rm -rf "$COPIA"
    git clone -q --branch "$RAMA" "$REPO_URL" "$COPIA" 2>/dev/null || {
      echo "No se pudo bajar el repositorio de GitHub."
      echo "Revisar internet y que la llave esté agregada en GitHub (sudo actualizar configurar)."
      exit 1; }
  else
    git -C "$COPIA" fetch -q origin "$RAMA" 2>/dev/null || {
      echo "No se pudo conectar con GitHub. Revisar internet y la llave (sudo actualizar configurar)."
      exit 1; }
  fi
}

instalar_version() {   # $1 = commit
  git -C "$COPIA" checkout -q --detach "$1"
  git -C "$COPIA" reset -q --hard "$1"
  git -C "$COPIA" clean -q -fdx
  (cd "$COPIA" && bash instalar.sh)
  quitar_lo_que_no_tiene "$COPIA"
}

# Al volver a una versión anterior al envío por correo (1.1), su instalador no conoce el
# servicio del correo: se detiene y se quita aquí. La cuenta de Gmail (correo.ini) se conserva.
quitar_lo_que_no_tiene() {   # $1 = carpeta de la versión instalada
  local nombre
  for nombre in correo comunicacion; do
    if [ ! -f "$1/registro1280-$nombre.service" ] && [ -f "/etc/systemd/system/registro1280-$nombre.service" ]; then
      systemctl disable --now "registro1280-$nombre" >/dev/null 2>&1 || true
      rm -f "/etc/systemd/system/registro1280-$nombre.service"
      grep -q "Registro de pesadas 1280" "/usr/local/bin/$nombre" 2>/dev/null && rm -f "/usr/local/bin/$nombre"
      systemctl daemon-reload
    fi
  done
}

accion="${1:-}"
case "$accion" in
  configurar)
    necesita_git
    mkdir -p "$ETC"
    if [ ! -f "$LLAVE" ]; then
      ssh-keygen -q -t ed25519 -N "" -C "registro1280@$(hostname)" -f "$LLAVE"
      chmod 600 "$LLAVE"
    fi
    echo "Llave de solo lectura de esta Raspberry (copiar la línea completa):"
    echo
    cat "$LLAVE.pub"
    echo
    echo "En GitHub: repositorio registro-pesadas-1280 > Settings > Deploy keys > Add deploy key."
    echo "Title: Pi intradevco. Key: pegar la línea de arriba. NO marcar 'Allow write access'. Add key."
    echo
    echo "Probando la conexión con GitHub..."
    if git ls-remote "$REPO_URL" >/dev/null 2>&1; then
      echo "Conexión correcta. Ya se puede usar:  sudo actualizar"
    else
      echo "Todavía no hay acceso. Agregar la llave en GitHub y volver a probar con:  sudo actualizar configurar"
    fi
    ;;

  estado)
    necesita_git; necesita_llave; bajar
    nueva="$(git -C "$COPIA" rev-parse "origin/$RAMA")"
    actual="$(cat "$INSTALADA" 2>/dev/null || true)"
    if [ -n "$actual" ]; then echo "Instalada:  $(corto "$actual")"; else echo "Instalada:  (sin datos)"; fi
    echo "En GitHub:  $(corto "$nueva")"
    if [ "$actual" = "$nueva" ]; then
      echo "Está al día."
    else
      echo
      echo "Cambios que se instalarían con 'sudo actualizar':"
      if [ -n "$actual" ] && git -C "$COPIA" cat-file -e "$actual" 2>/dev/null; then
        git -C "$COPIA" log --format='  %h  %s' "$actual..$nueva"
      else
        git -C "$COPIA" log -5 --format='  %h  %s' "$nueva"
      fi
    fi
    ;;

  volver)
    necesita_git
    previa="$(cat "$ANTERIOR" 2>/dev/null || true)"
    actual="$(cat "$INSTALADA" 2>/dev/null || true)"
    if [ -z "$previa" ] || [ ! -d "$COPIA/.git" ] || ! git -C "$COPIA" cat-file -e "$previa" 2>/dev/null; then
      echo "No hay una versión anterior guardada."
      exit 1
    fi
    echo "Volviendo a: $(corto "$previa")"
    instalar_version "$previa" > "$REGISTRO" 2>&1 || { cat "$REGISTRO"; exit 1; }
    echo "$previa" > "$INSTALADA"
    [ -n "$actual" ] && echo "$actual" > "$ANTERIOR"
    echo "Listo. Quedó instalada la versión anterior. Las pesadas guardadas no cambiaron."
    ;;

  "")
    necesita_git; necesita_llave
    echo "1/4 Bajando la última versión de GitHub..."
    bajar
    nueva="$(git -C "$COPIA" rev-parse "origin/$RAMA")"
    actual="$(cat "$INSTALADA" 2>/dev/null || true)"
    if [ "$actual" = "$nueva" ]; then
      echo "Ya está instalada la última versión: $(corto "$nueva")"
      exit 0
    fi
    echo "    Versión nueva: $(corto "$nueva")"
    git -C "$COPIA" checkout -q --detach "$nueva"
    git -C "$COPIA" reset -q --hard "$nueva"
    git -C "$COPIA" clean -q -fdx

    echo "2/4 Corriendo las pruebas automáticas (unos 3 minutos)..."
    if ! (cd "$COPIA/pruebas" && python3 -m unittest discover -s . -p "test_*.py") > "$REGISTRO" 2>&1; then
      echo
      tail -n 25 "$REGISTRO"
      echo
      echo "Las pruebas fallaron: NO se instaló nada. El sistema sigue funcionando con la versión que tenía."
      [ -n "$actual" ] && git -C "$COPIA" checkout -q --detach "$actual" 2>/dev/null || true
      exit 1
    fi
    echo "    Pruebas OK."

    echo "3/4 Instalando..."
    if ! (cd "$COPIA" && bash instalar.sh) >> "$REGISTRO" 2>&1; then
      echo "La instalación falló. Detalle:"
      tail -n 20 "$REGISTRO"
      if [ -n "$actual" ] && git -C "$COPIA" cat-file -e "$actual" 2>/dev/null; then
        echo "Volviendo a la versión que estaba instalada..."
        instalar_version "$actual" >> "$REGISTRO" 2>&1 && echo "Se restauró la versión anterior."
      fi
      exit 1
    fi
    [ -n "$actual" ] && echo "$actual" > "$ANTERIOR"
    echo "$nueva" > "$INSTALADA"

    echo "4/4 Listo. Versión instalada: $(corto "$nueva")"
    echo "Si algo no funciona bien:  sudo actualizar volver"
    ;;

  *)
    echo "Uso: sudo actualizar | sudo actualizar estado | sudo actualizar volver | sudo actualizar configurar"
    exit 1
    ;;
esac
exit 0
}
