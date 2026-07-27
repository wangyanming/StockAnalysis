-- ============================================
-- backtest_picks 表 - 回测交易明细
-- 版本: v1.0
-- 日期: 2026-07-23
-- 说明: 独立于生产表的回测结果存储表
-- ============================================

-- 补全旧表可能缺失的字段
ALTER TABLE backtest_picks ADD COLUMN IF NOT EXISTS strategy_group VARCHAR(32) DEFAULT '' COMMENT '策略组: baseline' AFTER source;
ALTER TABLE backtest_picks ADD COLUMN IF NOT EXISTS sh_change DECIMAL(6,2) DEFAULT NULL COMMENT '大盘当日涨跌幅(%)' AFTER stop_reason;
ALTER TABLE backtest_picks ADD COLUMN IF NOT EXISTS consecutive_loss INT DEFAULT 0 COMMENT '买入前连续亏损次数' AFTER sh_change;

-- 修改batch_id字段长度（如需要）
ALTER TABLE backtest_picks MODIFY COLUMN batch_id VARCHAR(32) NOT NULL COMMENT '回测批次标识';
