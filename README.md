# Registro de pesadas – Rice Lake 1280 (Intradevco)

Programa de la Raspberry Pi que lee la tabla de pesadas del indicador Rice Lake 1280 (solo lectura, TCP 10001), la guarda en MariaDB/MySQL sin duplicar, envía por Gmail un XLSX con la tabla TablaBalanzacoches con las pesadas nuevas de cada tramo y la muestra en una web de solo lectura (puerto 8081).

- Instalación y uso: `LEEME.txt`
- Instalar o reinstalar en la Pi: `sudo bash instalar.sh`
- Actualizar desde este repositorio: `sudo actualizar` (corre las pruebas antes de instalar; `sudo actualizar volver` regresa a la versión anterior)
- Pruebas: `cd pruebas && sudo python3 -m unittest discover -s . -p "test_*.py"`

Comandos en la Pi: `mostrar`, `guardar`, `visualizar`, `bucle`, `correo`, `controlador`, `cambiar-clave`, `vaciar-base`, `actualizar`.

La cuenta de Gmail que envía se configura en la Pi con `sudo correo cuenta` y queda solo en `/etc/registro1280/correo.ini` (nunca en este repositorio).

Configuración → Accesos permite crear usuarios Administrador u Operador, cambiar su nombre (incluidas mayúsculas), cambiar su rol, restablecer su PIN y eliminarlos. El operador ve toda la página Pesadas (incluidos Excel y correos), sin acceso a Configuración ni a sus funciones. Desde la versión 1.5.1, todos los PIN deben tener exactamente 6 números: ni más ni menos. Solo se almacena su hash; no se pueden consultar. Los nombres distinguen mayúsculas y minúsculas.

La versión 1.5.2 crea o restablece una sola vez las cuentas ADMINISTRADOR y OPERADOR con los PIN solicitados por el propietario. Se ingresa directamente; se retiró el formulario de actualización del acceso anterior. Las cuentas adicionales se conservan. Las futuras actualizaciones respetan los cambios de nombre, rol o PIN y las cuentas eliminadas.

Los cambios de nombre, PIN o rol y las eliminaciones cierran las sesiones del usuario afectado. Siempre debe quedar un administrador, y nadie puede eliminar su propio usuario. Las cuentas se guardan en MariaDB y entran en los respaldos de la base. Para recuperar un administrador desde la Pi: `sudo cambiar-clave` (pide un PIN de exactamente seis dígitos y restablece la cuenta indicada o crea una administradora si no existe).

Desde la versión 1.5.3, los correos automáticos y de prueba adjuntan un archivo .xlsx con la misma estructura, números, fechas y tabla TablaBalanzacoches que la descarga de la web. Cada envío mantiene sus pesadas pendientes y el tramo correspondiente; los tramos sin pesadas y las alertas siguen sin adjunto.
