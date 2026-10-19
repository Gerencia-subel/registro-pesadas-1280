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

-- Configuración: IP y puerto del controlador, y el bucle.
CREATE TABLE IF NOT EXISTS configuracion (
  clave  VARCHAR(40)  NOT NULL,
  valor  VARCHAR(200) NOT NULL,
  PRIMARY KEY (clave)
) ENGINE=InnoDB;

INSERT IGNORE INTO configuracion (clave, valor) VALUES
  ('ip', '10.30.211.141'),
  ('puerto', '10001'),
  ('bucle_activo', '0'),
  ('bucle_minutos', '5'),
  ('bucle_cambiado', ''),
  ('latido', ''),
  ('ultimo_respaldo', '');
