# Registro de pesadas – Rice Lake 1280 (Intradevco)

Programa de la Raspberry Pi que lee la tabla de pesadas del indicador Rice Lake 1280 (solo lectura, TCP 10001), la guarda en MariaDB/MySQL sin duplicar, envía por Gmail un CSV con las pesadas nuevas de cada tramo y la muestra en una web de solo lectura (puerto 8081).

- Instalación y uso: `LEEME.txt`
- Instalar o reinstalar en la Pi: `sudo bash instalar.sh`
- Actualizar desde este repositorio: `sudo actualizar` (corre las pruebas antes de instalar; `sudo actualizar volver` regresa a la versión anterior)
- Pruebas: `cd pruebas && sudo python3 -m unittest discover -s . -p "test_*.py"`

Comandos en la Pi: `mostrar`, `guardar`, `visualizar`, `bucle`, `correo`, `controlador`, `cambiar-clave`, `vaciar-base`, `actualizar`.

La cuenta de Gmail que envía se configura en la Pi con `sudo correo cuenta` y queda solo en `/etc/registro1280/correo.ini` (nunca en este repositorio).

Desde la versión 1.5.0, Configuración → Accesos permite crear usuarios Administrador u Operador, cambiar su rol, restablecer su PIN y eliminarlos. El operador ve toda la página Pesadas (incluidos Excel y correos), sin acceso a Configuración ni a sus funciones. La actualización conserva el usuario y la clave actuales como administrador inicial. Los nuevos PIN tienen de 6 a 12 dígitos y solo se almacena su hash; no se pueden consultar.

Los cambios de PIN o rol y las eliminaciones cierran las sesiones del usuario afectado. Siempre debe quedar un administrador, y nadie puede eliminar su propio usuario. Las cuentas se guardan en MariaDB y entran en los respaldos de la base. Para recuperar un administrador desde la Pi: `sudo cambiar-clave` (restablece la cuenta indicada o crea una administradora si no existe). No se crean operadores con PIN predeterminado.
