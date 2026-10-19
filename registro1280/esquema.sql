-- Base de datos del registro de pesadas del Rice Lake 1280.
-- Se puede ejecutar varias veces: no borra nada que ya exista.

CREATE DATABASE IF NOT EXISTS registro1280 CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE registro1280;

-- Pesadas guardadas. Las columnas ID..Estado son iguales a la tabla 2 del controlador.
-- Cada pesada se identifica por ID + FechaI: el controlador reinicia su contador de ID,
-- así que el ID solo puede repetirse en otra fecha.
CREATE TABLE IF NOT EXISTS pesadas (
  nro            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Número correlativo en esta base',
  ID             INT            NOT NULL COMMENT 'ID del controlador',
  cor            INT            NOT NULL COMMENT 'cor del controlador',
  Producto       VARCHAR(60)    NOT NULL COMMENT 'Producto del controlador',
  Peso           DECIMAL(14,4)  NOT NULL COMMENT 'Peso del controlador (kg)',
  Acum           DECIMAL(16,4)  NOT NULL COMMENT 'Acum del controlador (kg)',
  FechaI         DATETIME       NOT NULL COMMENT 'FechaI del controlador',
  FechaF         DATETIME       NOT NULL COMMENT 'FechaF del controlador',
  Turno          INT            NOT NULL COMMENT 'Turno del controlador',
  Estado         INT            NOT NULL COMMENT 'Estado del controlador',
  guardado_en    DATETIME       NOT NULL COMMENT 'Cuándo se guardó en esta base (hora de Lima)',
  actualizado_en DATETIME       NULL     COMMENT 'Si el controlador cambió algún dato después de guardarla',
  PRIMARY KEY (nro),
  UNIQUE KEY pesada (ID, FechaI),
  KEY por_fechai (FechaI),
  KEY por_producto (Producto)
) ENGINE=InnoDB;

-- Cada descarga del controlador (manual con "guardar" o automática con "bucle").
CREATE TABLE IF NOT EXISTS descargas (
  nro          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  inicio       DATETIME      NOT NULL,
  origen       VARCHAR(10)   NOT NULL COMMENT 'manual o bucle',
  resultado    VARCHAR(10)   NOT NULL COMMENT 'OK o ERROR',
  segundos     DECIMAL(8,2)  NULL,
  leidas       INT           NULL COMMENT 'Filas que entregó el controlador',
  nuevas       INT           NULL,
  actualizadas INT           NULL,
  sin_cambio   INT           NULL,
  rechazadas   INT           NULL COMMENT 'Filas con datos inválidos (ver tabla rechazadas)',
  total        INT           NULL COMMENT 'Total en la base después de guardar',
  detalle      VARCHAR(500)  NULL,
  PRIMARY KEY (nro),
  KEY por_inicio (inicio)
) ENGINE=InnoDB;

-- Filas del controlador que no se pudieron interpretar (se guardan tal cual llegaron).
CREATE TABLE IF NOT EXISTS rechazadas (
  nro          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  linea        VARCHAR(500)  NOT NULL,
  motivo       VARCHAR(200)  NOT NULL,
  primera_vez  DATETIME      NOT NULL,
  ultima_vez   DATETIME      NOT NULL,
  veces        INT           NOT NULL DEFAULT 1,
  PRIMARY KEY (nro),
  UNIQUE KEY por_linea (linea)
) ENGINE=InnoDB;

-- Envío por correo: cada pesada se marca con el número del correo que la envió, así
-- ninguna se envía dos veces. NULL = todavía no se envió. 0 = ya estaba guardada cuando
-- se activó el correo (no se envía). Las que llegan tarde van en el siguiente correo.
ALTER TABLE pesadas ADD COLUMN IF NOT EXISTS correo BIGINT UNSIGNED NULL
  COMMENT 'N° del correo que la envió (0 = ya estaba al activar el correo; NULL = pendiente)';
ALTER TABLE pesadas ADD INDEX IF NOT EXISTS por_correo (correo);

-- Cada correo enviado (o intentado) con el CSV de un tramo, y los correos de prueba.
CREATE TABLE IF NOT EXISTS correos (
  nro          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  enviado_en   DATETIME      NOT NULL COMMENT 'Cuándo se envió o intentó',
  tipo         VARCHAR(10)   NOT NULL COMMENT 'tramo o prueba',
  desde        DATETIME      NULL     COMMENT 'Inicio del tramo',
  hasta        DATETIME      NULL     COMMENT 'Fin del tramo',
  pesadas      INT           NOT NULL DEFAULT 0 COMMENT 'Pesadas en el CSV',
  resultado    VARCHAR(10)   NOT NULL COMMENT 'OK o ERROR',
  para         VARCHAR(500)  NULL,
  copia        VARCHAR(500)  NULL,
  archivo      VARCHAR(100)  NULL     COMMENT 'Nombre del CSV adjunto',
  detalle      VARCHAR(500)  NULL,
  PRIMARY KEY (nro),
  KEY por_enviado (enviado_en)
) ENGINE=InnoDB;

-- Pedidos de la web (cambiar la configuración, guardar ahora, probar). La web solo puede
-- agregar pedidos; los hace el servicio del correo, igual que los comandos de la terminal.
CREATE TABLE IF NOT EXISTS pedidos (
  nro        BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  creado     DATETIME       NOT NULL,
  origen     VARCHAR(80)    NOT NULL DEFAULT '' COMMENT 'IP de quien lo pidió',
  tipo       VARCHAR(30)    NOT NULL,
  datos      VARCHAR(6000)  NOT NULL DEFAULT '' COMMENT 'Se borra al atenderlo si lleva la contraseña',
  estado     VARCHAR(10)    NOT NULL DEFAULT 'PENDIENTE' COMMENT 'PENDIENTE, OK o ERROR',
  resultado  VARCHAR(2000)  NULL,
  atendido   DATETIME       NULL,
  PRIMARY KEY (nro),
  KEY por_estado (estado)
) ENGINE=InnoDB;

-- Configuración: IP y puerto del controlador, el bucle y el correo.
CREATE TABLE IF NOT EXISTS configuracion (
  clave  VARCHAR(40)   NOT NULL,
  valor  VARCHAR(5000) NOT NULL,
  PRIMARY KEY (clave)
) ENGINE=InnoDB;
-- Versiones anteriores: más espacio para listas largas de destinatarios.
ALTER TABLE configuracion MODIFY valor VARCHAR(5000) NOT NULL;
ALTER TABLE pedidos MODIFY datos VARCHAR(6000) NOT NULL DEFAULT '';
ALTER TABLE correos MODIFY para TEXT NULL, MODIFY copia TEXT NULL;

INSERT IGNORE INTO configuracion (clave, valor) VALUES
  ('ip', '10.30.211.141'),
  ('puerto', '10001'),
  ('bucle_activo', '0'),
  ('bucle_minutos', '5'),
  ('bucle_cambiado', ''),
  ('latido', ''),
  ('ultimo_respaldo', ''),
  ('correo_activo', '0'),
  ('correo_minutos', '5'),
  ('correo_para', ''),
  ('correo_copia', ''),
  ('correo_cuenta', ''),
  ('correo_clave', ''),
  ('correo_desde', ''),
  ('correo_proximo', ''),
  ('correo_detenido', ''),
  ('correo_latido', '');

-- Versión 1.3.5: las activaciones del correo anotadas antes con el texto largo quedan solo
-- con la primera línea ("Correo activado: cada N minutos. Primer envío a las ...").
UPDATE pedidos SET resultado = SUBSTRING_INDEX(resultado, CHAR(10), 1)
 WHERE tipo = 'correo' AND resultado LIKE 'Correo activado:%' AND resultado LIKE CONCAT('%', CHAR(10), '%');
