CREATE DATABASE IF NOT EXISTS priority_storage CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE priority_storage;

CREATE TABLE IF NOT EXISTS modules (
  module_id INT PRIMARY KEY,
  module_name VARCHAR(100) NOT NULL,
  description VARCHAR(500),
  is_enabled BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS data_records (
  data_id BIGINT AUTO_INCREMENT PRIMARY KEY,
  module_id INT NOT NULL,
  source_port VARCHAR(64),
  raw_message TEXT,
  received_at DATETIME(6) NOT NULL,
  payload_json JSON NOT NULL,
  priority ENUM('HIGH','MEDIUM','LOW') NOT NULL,
  CONSTRAINT fk_record_module FOREIGN KEY (module_id) REFERENCES modules(module_id),
  INDEX idx_records_received(received_at),
  INDEX idx_records_priority(priority)
);

CREATE TABLE IF NOT EXISTS readings (
  reading_id BIGINT AUTO_INCREMENT PRIMARY KEY,
  data_id BIGINT NOT NULL,
  reading_index INT NOT NULL,
  reading_value DOUBLE NOT NULL,
  CONSTRAINT fk_reading_record FOREIGN KEY (data_id) REFERENCES data_records(data_id) ON DELETE CASCADE,
  UNIQUE KEY uq_record_reading(data_id, reading_index)
);

CREATE TABLE IF NOT EXISTS processes (
  process_id BIGINT AUTO_INCREMENT PRIMARY KEY,
  data_id BIGINT NOT NULL,
  status ENUM('PENDING','QUEUED','PROCESSING','RETRY_WAIT','COMPLETED','FAILED') NOT NULL DEFAULT 'PENDING',
  attempt_count INT NOT NULL DEFAULT 0,
  max_attempts INT NOT NULL DEFAULT 3,
  last_error TEXT,
  submitted_at DATETIME(6) NOT NULL,
  started_at DATETIME(6),
  completed_at DATETIME(6),
  next_retry_at DATETIME(6),
  CONSTRAINT fk_process_record FOREIGN KEY (data_id) REFERENCES data_records(data_id) ON DELETE CASCADE,
  UNIQUE KEY uq_process_data(data_id),
  INDEX idx_process_status(status)
);

CREATE TABLE IF NOT EXISTS processing_attempts (
  attempt_id BIGINT AUTO_INCREMENT PRIMARY KEY,
  data_id BIGINT NOT NULL,
  attempt_number INT NOT NULL,
  outcome VARCHAR(30) NOT NULL,
  error_message TEXT,
  started_at DATETIME(6) NOT NULL,
  ended_at DATETIME(6),
  CONSTRAINT fk_attempt_record FOREIGN KEY (data_id) REFERENCES data_records(data_id) ON DELETE CASCADE
);

INSERT IGNORE INTO modules(module_id,module_name,description) VALUES
(101,'Environmental Module','Illustrative environmental readings'),
(102,'Motion Module','Illustrative motion and device readings');
