-- stock_daily: A股个股日K线数据
ALTER TABLE stock_daily MODIFY code VARCHAR(10) COMMENT '股票代码(6位)';
ALTER TABLE stock_daily MODIFY name VARCHAR(32) COMMENT '股票名称';
ALTER TABLE stock_daily MODIFY trade_date VARCHAR(10) COMMENT '交易日(YYYYMMDD)';
ALTER TABLE stock_daily MODIFY open DOUBLE COMMENT '开盘价';
ALTER TABLE stock_daily MODIFY close DOUBLE COMMENT '收盘价';
ALTER TABLE stock_daily MODIFY high DOUBLE COMMENT '最高价';
ALTER TABLE stock_daily MODIFY low DOUBLE COMMENT '最低价';
ALTER TABLE stock_daily MODIFY volume DOUBLE COMMENT '成交量(股,腾讯手×100)';
ALTER TABLE stock_daily MODIFY amount DOUBLE COMMENT '成交额(元,腾讯万元×10000)';
ALTER TABLE stock_daily MODIFY change_pct DOUBLE COMMENT '涨跌幅(%)';

-- daily_limit_up: 每日涨停板数据
ALTER TABLE daily_limit_up MODIFY trade_date VARCHAR(10) COMMENT '交易日(YYYYMMDD)';
ALTER TABLE daily_limit_up MODIFY code VARCHAR(10) COMMENT '股票代码';
ALTER TABLE daily_limit_up MODIFY name VARCHAR(32) COMMENT '股票名称';
ALTER TABLE daily_limit_up MODIFY price DOUBLE COMMENT '涨停价';
ALTER TABLE daily_limit_up MODIFY change_pct DOUBLE COMMENT '涨停日涨幅(%)';
ALTER TABLE daily_limit_up MODIFY turnover_rate DOUBLE COMMENT '换手率(%)';
ALTER TABLE daily_limit_up MODIFY seal_first_time VARCHAR(10) COMMENT '首次封板时间(HHMMSS)';
ALTER TABLE daily_limit_up MODIFY seal_last_time VARCHAR(10) COMMENT '最后封板时间(HHMMSS)';
ALTER TABLE daily_limit_up MODIFY board_times INT COMMENT '涨停连板数';
ALTER TABLE daily_limit_up MODIFY bomb_times INT COMMENT '炸板次数';
ALTER TABLE daily_limit_up MODIFY seal_fund DOUBLE COMMENT '封单资金(元)';
ALTER TABLE daily_limit_up MODIFY industry VARCHAR(64) COMMENT '所属行业';
ALTER TABLE daily_limit_up MODIFY concept VARCHAR(256) COMMENT '所属概念(逗号分隔)';
ALTER TABLE daily_limit_up MODIFY status VARCHAR(16) COMMENT '涨停状态:首板/连板/炸板';
ALTER TABLE daily_limit_up MODIFY source_market VARCHAR(8) COMMENT '市场: A股/港股';
ALTER TABLE daily_limit_up MODIFY raw_json TEXT COMMENT '原始JSON数据';

-- daily_picks: 每日选股推荐记录
ALTER TABLE daily_picks MODIFY trade_date VARCHAR(10) COMMENT '选股交易日(YYYYMMDD)';
ALTER TABLE daily_picks MODIFY code VARCHAR(10) COMMENT '股票代码';
ALTER TABLE daily_picks MODIFY name VARCHAR(32) COMMENT '股票名称';
ALTER TABLE daily_picks MODIFY board_times INT COMMENT '连板天数';
ALTER TABLE daily_picks MODIFY total_score INT COMMENT '综合评分(百分制)';
ALTER TABLE daily_picks MODIFY grade VARCHAR(16) COMMENT '评级: S/A/B/C';
ALTER TABLE daily_picks MODIFY position_advice VARCHAR(32) COMMENT '仓位建议';
ALTER TABLE daily_picks MODIFY source VARCHAR(32) COMMENT '选股策略来源';
ALTER TABLE daily_picks MODIFY `rank` INT COMMENT '排名';
ALTER TABLE daily_picks MODIFY change_pct DOUBLE COMMENT '次日涨跌幅(%)';
ALTER TABLE daily_picks MODIFY next_day_change DOUBLE COMMENT '下一个交易日涨跌幅(%)';
ALTER TABLE daily_picks MODIFY data_tag VARCHAR(16) COMMENT '数据标签: real/predict';
ALTER TABLE daily_picks MODIFY highlights TEXT COMMENT '选股亮点说明';

-- daily_snapshots: 每日盘中快照
ALTER TABLE daily_snapshots MODIFY snapshot_date VARCHAR(10) COMMENT '快照日期(YYYYMMDD)';
ALTER TABLE daily_snapshots MODIFY snapshot_time VARCHAR(10) COMMENT '快照时间(HHMMSS)';
ALTER TABLE daily_snapshots MODIFY json_data LONGTEXT COMMENT '快照JSON数据';

-- index_quotes: 指数实时行情
ALTER TABLE index_quotes MODIFY index_code VARCHAR(10) COMMENT '指数代码';
ALTER TABLE index_quotes MODIFY name VARCHAR(32) COMMENT '指数名称';
ALTER TABLE index_quotes MODIFY current_price DOUBLE COMMENT '当前点位';
ALTER TABLE index_quotes MODIFY change_pct DOUBLE COMMENT '涨跌幅(%)';
ALTER TABLE index_quotes MODIFY open DOUBLE COMMENT '开盘点位';
ALTER TABLE index_quotes MODIFY high DOUBLE COMMENT '最高点位';
ALTER TABLE index_quotes MODIFY low DOUBLE COMMENT '最低点位';
ALTER TABLE index_quotes MODIFY volume DOUBLE COMMENT '成交量(手)';
ALTER TABLE index_quotes MODIFY amount DOUBLE COMMENT '成交额(元,腾讯万元×10000)';
ALTER TABLE index_quotes MODIFY timestamp VARCHAR(20) COMMENT '数据时间戳';

-- limit_up_industry_stats: 涨停板块统计
ALTER TABLE limit_up_industry_stats MODIFY trade_date VARCHAR(10) COMMENT '交易日(YYYYMMDD)';
ALTER TABLE limit_up_industry_stats MODIFY industry VARCHAR(64) COMMENT '行业名称';
ALTER TABLE limit_up_industry_stats MODIFY count INT COMMENT '该行业涨停数';
ALTER TABLE limit_up_industry_stats MODIFY top_stocks TEXT COMMENT '代表涨停股(逗号分隔)';
ALTER TABLE limit_up_industry_stats MODIFY total_seal_fund DOUBLE COMMENT '板块封单总额(元)';

-- limit_up_tracking: 涨停板跟踪
ALTER TABLE limit_up_tracking MODIFY code VARCHAR(10) COMMENT '股票代码';
ALTER TABLE limit_up_tracking MODIFY name VARCHAR(32) COMMENT '股票名称';
ALTER TABLE limit_up_tracking MODIFY first_limit_date VARCHAR(10) COMMENT '首次涨停日期';
ALTER TABLE limit_up_tracking MODIFY latest_limit_date VARCHAR(10) COMMENT '最近涨停日期';
ALTER TABLE limit_up_tracking MODIFY total_limit_days INT COMMENT '累计涨停天数';
ALTER TABLE limit_up_tracking MODIFY max_board_count INT COMMENT '最大连板数';
ALTER TABLE limit_up_tracking MODIFY current_board_count INT COMMENT '当前连板数';
ALTER TABLE limit_up_tracking MODIFY first_price DOUBLE COMMENT '首次涨停价';
ALTER TABLE limit_up_tracking MODIFY latest_price DOUBLE COMMENT '最新价';
ALTER TABLE limit_up_tracking MODIFY industry VARCHAR(64) COMMENT '所属行业';
ALTER TABLE limit_up_tracking MODIFY status VARCHAR(16) COMMENT '跟踪状态: 观察中/关注/剔除';
ALTER TABLE limit_up_tracking MODIFY note TEXT COMMENT '备注';

-- market_summary: 盘中行情汇总
ALTER TABLE market_summary MODIFY summary_date VARCHAR(10) COMMENT '汇总日期(YYYYMMDD)';
ALTER TABLE market_summary MODIFY summary_time VARCHAR(10) COMMENT '汇总时间(HHMMSS)';
ALTER TABLE market_summary MODIFY total_amount DOUBLE COMMENT '全市场成交额(元,腾讯万元×10000)';
ALTER TABLE market_summary MODIFY amount_change DOUBLE COMMENT '成交额变化';
ALTER TABLE market_summary MODIFY main_force_net_inflow DOUBLE COMMENT '主力净流入(元)';
ALTER TABLE market_summary MODIFY sh_main_force_inflow DOUBLE COMMENT '沪市主力净流入(元)';
ALTER TABLE market_summary MODIFY sz_main_force_inflow DOUBLE COMMENT '深市主力净流入(元)';
ALTER TABLE market_summary MODIFY rise_count INT COMMENT '上涨家数';
ALTER TABLE market_summary MODIFY fall_count INT COMMENT '下跌家数';
ALTER TABLE market_summary MODIFY flat_count INT COMMENT '平盘家数';
ALTER TABLE market_summary MODIFY json_data LONGTEXT COMMENT '完整JSON汇总';


-- sector_daily_history: 板块日K线历史
ALTER TABLE sector_daily_history MODIFY trade_date VARCHAR(10) COMMENT '交易日(YYYYMMDD)';
ALTER TABLE sector_daily_history MODIFY board_code VARCHAR(10) COMMENT '板块代码';
ALTER TABLE sector_daily_history MODIFY sector_name VARCHAR(64) COMMENT '板块名称';
ALTER TABLE sector_daily_history MODIFY open DOUBLE COMMENT '开盘点位';
ALTER TABLE sector_daily_history MODIFY close DOUBLE COMMENT '收盘点位';
ALTER TABLE sector_daily_history MODIFY high DOUBLE COMMENT '最高点位';
ALTER TABLE sector_daily_history MODIFY low DOUBLE COMMENT '最低点位';
ALTER TABLE sector_daily_history MODIFY change_pct DOUBLE COMMENT '涨跌幅(%)';
ALTER TABLE sector_daily_history MODIFY amount DOUBLE COMMENT '成交额(元,腾讯万元×10000)';
ALTER TABLE sector_daily_history MODIFY volume DOUBLE COMMENT '成交量(手)';

-- sector_performance: 板块实时表现
ALTER TABLE sector_performance MODIFY record_date VARCHAR(10) COMMENT '记录日期(YYYYMMDD)';
ALTER TABLE sector_performance MODIFY record_time VARCHAR(10) COMMENT '记录时间(HHMMSS)';
ALTER TABLE sector_performance MODIFY sector_name VARCHAR(64) COMMENT '板块名称';
ALTER TABLE sector_performance MODIFY change_pct DOUBLE COMMENT '涨跌幅(%)';
ALTER TABLE sector_performance MODIFY turn_over DOUBLE COMMENT '换手率(%)';
ALTER TABLE sector_performance MODIFY amount DOUBLE COMMENT '成交额(元,腾讯万元×10000)';
ALTER TABLE sector_performance MODIFY net_inflow DOUBLE COMMENT '净流入(元)';
ALTER TABLE sector_performance MODIFY rise_count INT COMMENT '上涨家数';
ALTER TABLE sector_performance MODIFY fall_count INT COMMENT '下跌家数';
ALTER TABLE sector_performance MODIFY rank_type VARCHAR(16) COMMENT '排名类型: top_gain/top_loss';

-- stock_quotes: 个股实时行情
ALTER TABLE stock_quotes MODIFY stock_code VARCHAR(10) COMMENT '股票代码';
ALTER TABLE stock_quotes MODIFY name VARCHAR(32) COMMENT '股票名称';
ALTER TABLE stock_quotes MODIFY current_price DOUBLE COMMENT '当前价';
ALTER TABLE stock_quotes MODIFY change_pct DOUBLE COMMENT '涨跌幅(%)';
ALTER TABLE stock_quotes MODIFY open DOUBLE COMMENT '开盘价';
ALTER TABLE stock_quotes MODIFY high DOUBLE COMMENT '最高价';
ALTER TABLE stock_quotes MODIFY low DOUBLE COMMENT '最低价';
ALTER TABLE stock_quotes MODIFY pre_close DOUBLE COMMENT '昨收价';
ALTER TABLE stock_quotes MODIFY volume DOUBLE COMMENT '成交量(股,腾讯手×100)';
ALTER TABLE stock_quotes MODIFY amount DOUBLE COMMENT '成交额(元,腾讯万元×10000)';
ALTER TABLE stock_quotes MODIFY timestamp VARCHAR(20) COMMENT '数据时间戳';
