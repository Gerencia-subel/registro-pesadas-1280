# Registro de pesadas – Rice Lake 1280 (Intradevco)

Programa de la Raspberry Pi que lee la tabla de pesadas del indicador Rice Lake 1280 (solo lectura, TCP 10001), la guarda en MariaDB/MySQL sin duplicar, envía por Gmail un CSV con las pesadas nuevas de cada tramo y la muestra en una web de solo lectura (puerto 8081).

- Instalación y uso: `LEEME.txt`
- Instalar o reinstalar en la Pi: `sudo bash instalar.sh`
- Actualizar desde este repositorio: `sudo actualizar` (corre las pruebas antes de instalar; `sudo actualizar volver` regresa a la versión anterior)
- Pruebas: `cd pruebas && sudo python3 -m unittest discover -s . -p "test_*.py"`

Comandos en la Pi: `mostrar`, `guardar`, `visualizar`, `bucle`, `correo`, `controlador`, `cambiar-clave`, `vaciar-base`, `actualizar`.

La cuenta de Gmail que envía se configura en la Pi con `sudo correo cuenta` y queda solo en `/etc/registro1280/correo.ini` (nunca en este repositorio).
