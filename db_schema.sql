-- MySQL 建表脚本
-- 创建方式: mysql -u root -p stock_analysis < db_schema.sql

CREATE TABLE IF NOT EXISTS stock_daily (
  id INT AUTO_INCREMENT PRIMARY KEY,
  code VARCHAR(10) NOT NULL,
  name VARCHAR(32) DEFAULT '',
  trade_date VARCHAR(10) NOT NULL,
  open DOUBLE DEFAULT 0,
  close DOUBLE DEFAULT 0,
  high DOUBLE DEFAULT 0,
  low DOUBLE DEFAULT 0,
  volume DOUBLE DEFAULT 0,
  amount DOUBLE DEFAULT 0,
  change_pct DOUBLE DEFAULT 0,
  UNIQUE KEY uk_code_date (code, trade_date),
  KEY idx_trade_date (trade_date),
  KEY idx_code (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS daily_limit_up (
  id INT AUTO_INCREMENT PRIMARY KEY,
  trade_date VARCHAR(10),
  code VARCHAR(10),
  name VARCHAR(32),
  price DOUBLE,
  change_pct DOUBLE,
  turnover_rate DOUBLE,
  seal_first_time VARCHAR(10),
  seal_last_time VARCHAR(10),
  board_times INT DEFAULT 1,
  bomb_times INT DEFAULT 0,
  seal_fund DOUBLE DEFAULT 0,
  industry VARCHAR(64) DEFAULT '',
  concept VARCHAR(256) DEFAULT '',
  status VARCHAR(16) DEFAULT '首板',
  source_market VARCHAR(8) DEFAULT 'A股',
  raw_json TEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  KEY idx_trade_date (trade_date),
  KEY idx_code (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS daily_picks (
  id INT AUTO_INCREMENT PRIMARY KEY,
  trade_date VARCHAR(10),
  code VARCHAR(10),
  name VARCHAR(32),
  board_times INT DEFAULT 1,
  total_score INT DEFAULT 0,
  grade VARCHAR(16) DEFAULT '',
  position_advice VARCHAR(32) DEFAULT '',
  source VARCHAR(32) DEFAULT '',
  `rank` INT DEFAULT 0,
  change_pct DOUBLE,
  next_day_change DOUBLE,
  data_tag VARCHAR(16) DEFAULT 'real',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  highlights TEXT,
  KEY idx_trade_date (trade_date),
  KEY idx_code (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS daily_snapshots (
  id INT AUTO_INCREMENT PRIMARY KEY,
  snapshot_date VARCHAR(10),
  snapshot_time VARCHAR(10),
  json_data LONGTEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS index_quotes (
  id INT AUTO_INCREMENT PRIMARY KEY,
  index_code VARCHAR(10),
  name VARCHAR(32),
  current_price DOUBLE,
  change_pct DOUBLE,
  open DOUBLE,
  high DOUBLE,
  low DOUBLE,
  volume DOUBLE,
  amount DOUBLE,
  timestamp VARCHAR(20),
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS limit_up_industry_stats (
  id INT AUTO_INCREMENT PRIMARY KEY,
  trade_date VARCHAR(10),
  industry VARCHAR(64),
  count INT DEFAULT 0,
  top_stocks TEXT,
  total_seal_fund DOUBLE DEFAULT 0,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  KEY idx_trade_date (trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS limit_up_tracking (
  id INT AUTO_INCREMENT PRIMARY KEY,
  code VARCHAR(10),
  name VARCHAR(32),
  first_limit_date VARCHAR(10),
  latest_limit_date VARCHAR(10),
  total_limit_days INT DEFAULT 1,
  max_board_count INT DEFAULT 1,
  current_board_count INT DEFAULT 1,
  first_price DOUBLE,
  latest_price DOUBLE,
  industry VARCHAR(64) DEFAULT '',
  status VARCHAR(16) DEFAULT '观察中',
  note TEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_code (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS market_summary (
  id INT AUTO_INCREMENT PRIMARY KEY,
  summary_date VARCHAR(10),
  summary_time VARCHAR(10),
  total_amount DOUBLE DEFAULT 0,
  amount_change DOUBLE DEFAULT 0,
  main_force_net_inflow DOUBLE DEFAULT 0,
  sh_main_force_inflow DOUBLE DEFAULT 0,
  sz_main_force_inflow DOUBLE DEFAULT 0,
  rise_count INT DEFAULT 0,
  fall_count INT DEFAULT 0,
  flat_count INT DEFAULT 0,
  json_data LONGTEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


CREATE TABLE IF NOT EXISTS sector_daily_history (
  id INT AUTO_INCREMENT PRIMARY KEY,
  trade_date VARCHAR(10),
  board_code VARCHAR(10),
  sector_name VARCHAR(64),
  open DOUBLE DEFAULT 0,
  close DOUBLE DEFAULT 0,
  high DOUBLE DEFAULT 0,
  low DOUBLE DEFAULT 0,
  change_pct DOUBLE DEFAULT 0,
  amount DOUBLE DEFAULT 0,
  volume DOUBLE DEFAULT 0,
  KEY idx_trade_date (trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS sector_performance (
  id INT AUTO_INCREMENT PRIMARY KEY,
  record_date VARCHAR(10),
  record_time VARCHAR(10),
  sector_name VARCHAR(64),
  change_pct DOUBLE DEFAULT 0,
  turn_over DOUBLE DEFAULT 0,
  amount DOUBLE DEFAULT 0,
  net_inflow DOUBLE DEFAULT 0,
  rise_count INT DEFAULT 0,
  fall_count INT DEFAULT 0,
  rank_type VARCHAR(16) DEFAULT 'top_gain',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  KEY idx_record_date (record_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS stock_quotes (
  id INT AUTO_INCREMENT PRIMARY KEY,
  stock_code VARCHAR(10),
  name VARCHAR(32),
  current_price DOUBLE,
  change_pct DOUBLE,
  open DOUBLE,
  high DOUBLE,
  low DOUBLE,
  pre_close DOUBLE,
  volume DOUBLE,
  amount DOUBLE,
  timestamp VARCHAR(20),
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  KEY idx_stock_code (stock_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
