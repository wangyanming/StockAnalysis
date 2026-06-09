#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
回测引擎 v1 — 全历史回溯，按 v5.5 评分规则每日模拟选股

设计原则：
  不修改 scorer.py 原有代码。通过 patch datetime.now 来模拟历史日期。
  每天独立建候选池 → 评分 → 选前5×2组 → 查次日涨跌幅 → 入库。

数据源：
  stock_daily      — 日K线（筹码、趋势、次日涨跌幅）
  daily_limit_up   — 涨停明细（资金接力、板块热度）
  index_quotes     — 大盘指数（大盘安全垫）

输出表：
  backtest_results — 每日每只推荐票的详细记录

用法：
  # 全量回测
  python3 -c "from core.analyzer.backtest_engine import run_all; run_all()"

  # 指定日期段
  python3 -c "from core.analyzer.backtest_engine import run_all; run_all('20260301','20260601')"

单位约定：
  金额: 元
  涨幅: %
  成交量: 股（新浪/同花顺原始数据 ×100）
"""

import sys, os, json, logging, time
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from collections import defaultdict

_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _project_root)

from utils.dao import get_db
from utils.logger import setup_logger

logger = setup_logger("backtest_engine")

# ============================================================
# 1. DB 表结构（自动建表）
# ============================================================

BACKTEST_DDL = """
CREATE TABLE IF NOT EXISTS backtest_results (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  trade_date    VARCHAR(10)  NOT NULL COMMENT '选股日期(YYYYMMDD)',
  code          VARCHAR(10)  NOT NULL COMMENT '股票代码',
  name          VARCHAR(32)  NOT NULL COMMENT '股票名称',
  source        VARCHAR(16)  NOT NULL COMMENT '来源: 涨停热点/区间潜伏',
  group_rank    INT          NOT NULL COMMENT '组内排名(1-5)',
  total_score   DECIMAL(5,1) NOT NULL COMMENT '综合评分',
  score_chip    INT          DEFAULT 0 COMMENT '筹码结构分',
  score_money   INT          DEFAULT 0 COMMENT '资金接力分',
  score_sector  INT          DEFAULT 0 COMMENT '板块环境分',
  score_trend   INT          DEFAULT 0 COMMENT '趋势位置分',
  score_market  INT          DEFAULT 0 COMMENT '大盘安全分',
  score_pos     INT          DEFAULT 0 COMMENT '位置评估分',
  risk_flags    VARCHAR(128) DEFAULT '' COMMENT '风险标记',
  is_pick       TINYINT(1)   DEFAULT 0 COMMENT '是否精选(is_pick=1)',
  next_open     DECIMAL(10,2) DEFAULT NULL COMMENT 'T+1开盘价(买入价)',
  next_close    DECIMAL(10,2) DEFAULT NULL COMMENT 'T+1收盘价',
  next_change   DECIMAL(6,2)  DEFAULT NULL COMMENT 'T+1涨跌幅(收盘相比前收, %)',
  exit_open     DECIMAL(10,2) DEFAULT NULL COMMENT 'T+2开盘价(卖出价)',
  trade_return   DECIMAL(6,2)  DEFAULT NULL COMMENT '实际交易收益: (T+2开盘-T+1开盘)/T+1开盘(%)',
  sh_change     DECIMAL(6,2)  DEFAULT NULL COMMENT '当日上证涨跌幅(%)',
  run_batch     VARCHAR(20)  DEFAULT '' COMMENT '回测批次标识',
  created_at    DATETIME     DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_date_code (trade_date, code, source),
  KEY idx_date (trade_date),
  KEY idx_batch (run_batch),
  KEY idx_exit (trade_date, code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='回测结果明细表';
"""

# ============================================================
# 2. 核心回测逻辑
# ============================================================

class BacktestEngine:
    """回测引擎，对每个交易日执行完整的选股→评分→验证流程"""

    MA_FILTER = None  # 'multi_bull': 只选MA5>MA10>MA20>MA30的票

    def __init__(self, batch_id: str = None):
        self.batch_id = batch_id or datetime.now().strftime('%Y%m%d_%H%M%S')
        self.db = get_db()
        self._ensure_table()
        # 写操作后自动 commit
        self._auto_commit = True
        self._ensure_table()
        self.stats = {
            'total_days': 0,
            'total_picks': 0,
            'limit_up_group': {'cnt': 0, 'win': 0, 'sum_return': 0.0, 'max_return': -999, 'min_return': 999},
            'latent_group':  {'cnt': 0, 'win': 0, 'sum_return': 0.0, 'max_return': -999, 'min_return': 999},
            'score_buckets': defaultdict(lambda: {'cnt': 0, 'win': 0, 'sum_return': 0.0}),
            'daily_returns': [],
        }

    def _ensure_table(self):
        """确保 backtest_results 表存在"""
        if not self.db.table_exists('backtest_results'):
            self.db._conn.cursor().execute(BACKTEST_DDL)
            self.db._conn.commit()
            logger.info('backtest_results 表已创建')

    # --------------------------------------------------------
    # 日期工具
    # --------------------------------------------------------

    def _get_trade_dates(self) -> List[str]:
        """
        获取交易日（今年第一个交易日~昨天）
        """
        rows = self.db.fetchall("""
            SELECT DISTINCT trade_date FROM stock_daily
            WHERE trade_date >= '20260105' AND trade_date <= DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 1 DAY), '%%Y%%m%%d')
            ORDER BY trade_date
        """)
        dates = [r['trade_date'] for r in rows]
        logger.info(f'交易日: {len(dates)} 天 ({dates[0]} ~ {dates[-1]})')
        return dates


    def _get_next_trade_date(self, date: str) -> Optional[str]:
        """获取指定日期的下一个交易日"""
        row = self.db.fetchone(
            'SELECT trade_date FROM stock_daily WHERE trade_date > %s ORDER BY trade_date LIMIT 1',
            (date,))
        return row['trade_date'] if row else None

    # --------------------------------------------------------
    # 历史日期模拟：patch scorer.py 的 datetime.now
    # --------------------------------------------------------

    def _patch_now(self, target_date: str):
        """
        将 datetime.now 临时指向 target_date，使 scorer.py 内的所有 datetime.now()
        都返回该日期。回测完成后再恢复。
        """
        fake_dt = datetime.strptime(target_date, '%Y%m%d')
        self._original_now = datetime.now

        class FakeDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                if tz:
                    return fake_dt.replace(tzinfo=tz)
                return fake_dt

        import core.analyzer.scorer as scorer
        scorer.datetime = FakeDatetime

    def _restore_now(self):
        """恢复 datetime.now"""
        import core.analyzer.scorer as scorer
        import datetime as real_dt
        scorer.datetime = real_dt

    # --------------------------------------------------------
    # 回测一天的选股
    # --------------------------------------------------------

    def _backtest_one_day(self, trade_date: str) -> List[Dict]:
        '''
        对单个历史交易日执行选股流程 — 使用 build_candidate_pool() 方案

        ① 从 stock_daily 获取涨停列表（替代 daily_limit_up 补录脏数据）
        ② 按 build_candidate_pool() 逻辑构建候选池：
           路径A: 涨停热点（1-2板，换手>=1%，排除688/300/301/ST）
           路径B: 强势非涨停（涨幅2.5~9%，价格5~200，成交额>2000万，涨幅TOP30）
        ③ 逐一评分（monkey-patch scorer 的 datetime.now）
        ④ 涨停回踩组 TOP5 + 区间潜伏组 TOP5
        ⑤ 查次日涨跌幅，写入 backtest_results
        '''
        from core.analyzer.scorer import score_candidate

        results = []

        import sys as _sys
        _sys.stderr.write(f'  [{trade_date}] ...')
        _sys.stderr.flush()

        # ① 预加载当日 stock_daily 行情到 scorer 模块内联缓存
        daily_rows = self.db.fetchall(
            'SELECT code, name, change_pct, close, high, low, open, volume, amount, turnover_rate '
            'FROM stock_daily WHERE trade_date=%s',
            (trade_date,))
        daily_map = {}
        _fake_quotes = {}
        for r in daily_rows:
            code = r['code']
            daily_map[code] = r
            close = r['close'] or 0
            chg = r['change_pct'] or 0
            _fake_quotes[code] = {
                'price': close,
                'prev_close': close / (1 + chg / 100) if chg != 0 else (r['open'] or close),
                'open': r['open'],
                'high': r['high'],
                'low': r['low'],
                'volume': int((r['volume'] or 0) * 100),
                'amount': r['amount'] or 0,
            }
        import core.analyzer.scorer as _scorer
        _orig_get_quote = _scorer._get_today_quote_from_db
        def _cached_quote(code):
            q = _fake_quotes.get(code)
            if q:
                return q
            return _orig_get_quote(code)
        _scorer._get_today_quote_from_db = _cached_quote

        # ② 从 stock_daily 获取涨停列表
        zt_rows = self.db.fetchall(
            'SELECT code, name, change_pct, turnover_rate, close, amount FROM stock_daily '
            'WHERE trade_date=%s AND change_pct>=9.5 ORDER BY change_pct DESC',
            (trade_date,))
        zt_list = []
        for r in zt_rows or []:
            code = str(r['code'])
            name = str(r['name'])
            if not code or not name:
                continue
            # 推连板数
            _prev_rows = self.db.fetchall(
                'SELECT trade_date, change_pct FROM stock_daily WHERE code=%s AND trade_date<=%s ORDER BY trade_date DESC LIMIT 10',
                (code, trade_date))
            boards = 0
            for _pr in _prev_rows or []:
                if _pr['change_pct'] and _pr['change_pct'] >= 9.5:
                    boards += 1
                else:
                    break
            zt_list.append({
                'code': code, 'name': name,
                'board_times': boards,
                'turnover_rate': float(r['turnover_rate'] or 0),
                'industry': '',
            })

        # ③ 按 build_candidate_pool() 逻辑构建候选池
        candidates = []
        seen = set()

        # 路径A: 涨停热点（1-2板）
        for s in zt_list:
            code, name = s['code'], s['name']
            boards, turnover = s['board_times'], s['turnover_rate']
            if code.startswith('688') or code.startswith('300') or code.startswith('301'):
                continue
            if 'ST' in name or '*ST' in name or '退' in name:
                continue
            if boards > 2:
                continue
            if 0 < turnover < 1:
                continue
            candidates.append({
                'code': code, 'name': name, 'source': '涨停热点',
                'board_times': boards, 'turnover': turnover, 'industry': '',
            })
            seen.add(code)

        # 路径B: 强势非涨停（涨幅2.5~9%，价格5~200，成交额>2000万，涨幅TOP30）
        try:
            strong = self.db.fetchall(
                'SELECT code, name, change_pct, close, amount FROM stock_daily '
                'WHERE trade_date=%s '
                '  AND change_pct BETWEEN 2.5 AND 9.0 '
                '  AND close BETWEEN 5 AND 200 '
                '  AND amount > 20000000 '
                '  AND code NOT LIKE "688%%" '
                '  AND code NOT LIKE "300%%" '
                '  AND code NOT LIKE "301%%" '
                '  AND name NOT LIKE "%%ST%%" '
                '  AND name NOT LIKE "%%退%%" '
                'ORDER BY change_pct DESC LIMIT 30',
                (trade_date,))
            for r in strong or []:
                code = str(r['code'])
                name = str(r['name'])
                if code in seen:
                    continue
                if 'ST' in name or '*ST' in name or '退' in name:
                    continue
                seen.add(code)
                candidates.append({
                    'code': code, 'name': name, 'source': '强势涨幅',
                    'board_times': 0, 'turnover': 0, 'industry': '',
                })
        except Exception as e:
            logger.warning(f'[{trade_date}] 强势候选失败: {e}')

        if not candidates:
            logger.debug(f'[{trade_date}] 候选池为空')
            _scorer._get_today_quote_from_db = _orig_get_quote
            return []

        # ④ 逐一评分
        scored = []
        for cand in candidates:
            code, name = cand['code'], cand['name']
            source = cand['source']

            # 补全名称
            if not name or name == '':
                name = daily_map.get(code, {}).get('name', '')

            self._patch_now(trade_date)
            try:
                report = score_candidate(code, name)
            except Exception as e:
                logger.warning(f'评分失败 {code} {name}: {e}')
                self._restore_now()
                continue
            finally:
                self._restore_now()

            if report['total_score'] <= 0:
                continue

            scored.append({
                'code': code,
                'name': name,
                'source': source,
                'total_score': report['total_score'],
                'chip': report['breakdown'].get('筹码结构', {}).get('score', 0),
                'money': report['breakdown'].get('资金接力', {}).get('score', 0),
                'sector': report['breakdown'].get('板块环境', {}).get('score', 0),
                'trend': report['breakdown'].get('趋势位置', {}).get('score', 0),
                'market': report['breakdown'].get('大盘安全', {}).get('score', 0),
                'pos': report['breakdown'].get('位置评估', {}).get('score', 0),
                'risks': ','.join(report.get('risks', [])),
                'board_times': cand.get('board_times', 0),
            })

        if not scored:
            _scorer._get_today_quote_from_db = _orig_get_quote
            return []

        # ⑤a 趋势硬否决过滤（可选）
        if self.MA_FILTER == 'trend_hard_reject':
            filtered = []
            rejected_reasons = []
            for r in scored:
                trend_score = r.get('trend', 0) or 0
                # 趋势硬开关触发条件：趋势分<=0（scorer中已做硬判）
                # 具体硬开关逻辑在 score_trend_position 的阶段一：
                #   - MA20下方+MA20下行
                #   - 短线破位(MA5/MA10下方)
                #   - 近3日累计跌>7%
                #   - 从20日高点回撤>8%
                #   - 近5日4天收阴
                if trend_score <= 0:
                    rejected_reasons.append(f'{r["code"]} {r["name"]} trend={trend_score}')
                else:
                    filtered.append(r)
            if rejected_reasons:
                logger.info(f'  趋势硬否决: 剔除{len(rejected_reasons)}只: {rejected_reasons[:3]}...')
            scored = filtered
            if not scored:
                logger.info(f'  趋势硬否决后无候选股，跳过{trade_date}')
                _scorer._get_today_quote_from_db = _orig_get_quote
                return []

        # ⑤ 补查信息（简化版，不需要 daily_limit_up）
        for r in scored:
            code = r['code']
            _row = self.db.fetchone(
                'SELECT total_market_cap FROM stock_daily WHERE code=%s AND trade_date=%s LIMIT 1',
                (code, trade_date))
            mcap = _row['total_market_cap'] if _row else 0
            r['total_market_cap'] = mcap if mcap and mcap > 0 else 100000000000

            r['industry'] = ''
            r['board_times'] = r.get('board_times', 1)

            _klines_60 = self.db.fetchall(
                'SELECT trade_date, close, low, high, change_pct, volume, amount FROM stock_daily '
                'WHERE code=%s AND trade_date>=%s AND trade_date<=%s ORDER BY trade_date',
                (code,
                 (datetime.strptime(trade_date, '%Y%m%d') - timedelta(days=90)).strftime('%Y%m%d'),
                 trade_date))
            if _klines_60:
                _low = min((k['low'] for k in _klines_60 if k['low']), default=0)
                _high = max((k['high'] for k in _klines_60 if k['high']), default=0)
                _close_now = _klines_60[-1]['close']
                r['_60d_low'] = _low
                r['_60d_high'] = _high
                r['_60d_position'] = ((_close_now - _low) / (_high - _low) * 100) if (_high - _low) > 0 else 50
                five_ago = (datetime.strptime(trade_date, '%Y%m%d') - timedelta(days=7)).strftime('%Y%m%d')
                _last5 = [k for k in _klines_60 if k['trade_date'] >= five_ago][-5:]
                r['_5d_up_days'] = sum(1 for k in _last5 if (k.get('change_pct') or 0) > 0)
                if len(_last5) >= 2:
                    r['_5d_chg'] = (_last5[-1]['close'] - _last5[0]['close']) / _last5[0]['close'] * 100 if _last5[0]['close'] else 0
                else:
                    r['_5d_chg'] = 0
                _vol_last5 = [k['amount'] for k in _last5 if k['amount']]
                _vol_prev10 = [k['amount'] for k in _klines_60 if k['amount']][-15:-5] if len(_klines_60) >= 15 else []
                r['_5d_avg_amount'] = sum(_vol_last5) / len(_vol_last5) if _vol_last5 else 0
                r['_10d_prev_avg_amount'] = sum(_vol_prev10) / len(_vol_prev10) if _vol_prev10 else 0
                _closes_last5 = [k['close'] for k in _klines_60 if k['close']][-5:]
                r['_ma5'] = sum(_closes_last5) / len(_closes_last5) if _closes_last5 else 0
            else:
                r['_60d_position'] = 50; r['_5d_up_days'] = 0; r['_5d_chg'] = 0
                r['_5d_avg_amount'] = 0; r['_10d_prev_avg_amount'] = 0; r['_ma5'] = 0

            _chg_row = self.db.fetchone(
                'SELECT change_pct, close FROM stock_daily WHERE code=%s AND trade_date=%s LIMIT 1',
                (code, trade_date))
            r['change_pct'] = _chg_row['change_pct'] if _chg_row else 0
            r['today_close'] = _chg_row['close'] if _chg_row else 0

        # ⑥ 分组出榜（与 build_candidate_pool() 原始逻辑一致）
        # 涨停热点组：路径A的涨停票直接按评分取TOP5
        # 区间潜伏组：路径B的强势涨幅票，经条件过滤后取TOP5
        limit_up_top5 = []
        latent_top5 = []

        # 涨停热点组：按评分降序取TOP5
        up_scored = [r for r in scored if r.get('source') == '涨停热点']
        up_scored.sort(key=lambda x: x.get('total_score', 0), reverse=True)
        limit_up_top5 = up_scored[:5]

        # 区间潜伏组：路径B的强势涨幅票过滤后取TOP5
        non_up_group = []
        for r in scored:
            if r.get('source') != '强势涨幅':
                continue
            trend_score = r.get('trend', 0) or 0
            if trend_score < 5:
                continue
            pos_score = r.get('pos', 0) or 0
            if pos_score < 3:
                continue
            mcap = r.get('total_market_cap', 0) or 0
            if mcap > 0 and mcap < 5000000000:
                continue
            code = r['code']
            if code.startswith('688') or code.startswith('300') or code.startswith('301'):
                continue
            pos_60 = r.get('_60d_position', 100)
            if pos_60 > 50:
                continue
            high_60 = r.get('_60d_high', 0)
            low_60 = r.get('_60d_low', 0)
            if high_60 > 0 and (high_60 - low_60) / high_60 < 0.20:
                continue
            up_days = r.get('_5d_up_days', 0)
            if up_days < 3:
                continue
            chg_5 = r.get('_5d_chg', 0)
            if chg_5 < 2 or chg_5 > 15:
                continue
            avg_5 = r.get('_5d_avg_amount', 0) or 0
            avg_10 = r.get('_10d_prev_avg_amount', 0) or 0
            if avg_10 > 0 and avg_5 < avg_10 * 0.8:
                continue
            if avg_10 > 0 and avg_5 > avg_10 * 2.5:
                continue
            ma5 = r.get('_ma5', 0) or 0
            close_now = r.get('today_close', 0) or 0
            if ma5 > 0 and close_now < ma5:
                continue
            non_up_group.append(r)

        non_up_group.sort(key=lambda x: (x.get('total_score', 0), x.get('_60d_position', 50)), reverse=True)
        latent_top5 = non_up_group[:5]

        # ⑦ 查次日行情
        next_date = self._get_next_trade_date(trade_date)

        sh_change = 0
        sh_row = self.db.fetchone(
            'SELECT change_pct FROM index_quotes WHERE index_code=%s AND record_date=%s',
            ('szzs', datetime.strptime(trade_date, '%Y%m%d').strftime('%Y-%m-%d')))
        if sh_row:
            sh_change = float(sh_row['change_pct'])

        # ⑧ 查T+1和T+2行情
        next2_date = self._get_next_trade_date(next_date) if next_date else None  # T+2

        next_data = {}
        next2_data = {}
        if next_date:
            next_rows = self.db.fetchall(
                'SELECT code, open, close, change_pct FROM stock_daily WHERE trade_date=%s',
                (next_date,))
            for r in next_rows or []:
                next_data[r['code']] = {
                    'open': r['open'],
                    'close': r['close'],
                    'change': r['change_pct'],
                }
        if next2_date:
            next2_rows = self.db.fetchall(
                'SELECT code, open FROM stock_daily WHERE trade_date=%s',
                (next2_date,))
            for r in next2_rows or []:
                next2_data[r['code']] = r['open']

        # ⑨ 写入结果
        all_top5 = []
        for rank, item in enumerate(limit_up_top5 + latent_top5, 1):
            code = item['code']
            nd = next_data.get(code, {})           # T+1行情
            exit_open_ = next2_data.get(code)       # T+2开盘价
            is_pick = 1 if rank <= 10 else 0
            buy_open = nd.get('open')                # T+1开盘(买入价)
            sell_open = exit_open_                   # T+2开盘(卖出价)
            trade_return = None
            if buy_open and sell_open and float(buy_open) > 0:
                trade_return = round((float(sell_open) - float(buy_open)) / float(buy_open) * 100, 2)

            entry = {
                'trade_date': trade_date,
                'code': code,
                'name': item['name'],
                'source': item['source'],
                'group_rank': rank if rank <= 5 else rank - 5,
                'total_score': item['total_score'],
                'score_chip': item.get('chip', 0),
                'score_money': item.get('money', 0),
                'score_sector': item.get('sector', 0),
                'score_trend': item.get('trend', 0),
                'score_market': item.get('market', 0),
                'score_pos': item.get('pos', 0),
                'risk_flags': item.get('risks', ''),
                'is_pick': is_pick,
                'next_open': buy_open,
                'next_close': nd.get('close'),
                'next_change': nd.get('change'),
                'exit_open': sell_open,
                'trade_return': trade_return,
                'sh_change': sh_change,
                'run_batch': self.batch_id,
            }
            all_top5.append(entry)

            try:
                self.db.execute('''
                    REPLACE INTO backtest_results
                    (trade_date, code, name, source, group_rank, total_score,
                     score_chip, score_money, score_sector, score_trend, score_market, score_pos,
                     risk_flags, is_pick, next_open, next_close, next_change, exit_open, trade_return, sh_change, run_batch)
                    VALUES (%s,%s,%s,%s,%s,%s, %s,%s,%s,%s,%s,%s, %s,%s,%s,%s,%s,%s,%s,%s,%s)
                ''', (
                    entry['trade_date'], entry['code'], entry['name'], entry['source'],
                    entry['group_rank'], entry['total_score'],
                    entry['score_chip'], entry['score_money'], entry['score_sector'],
                    entry['score_trend'], entry['score_market'], entry['score_pos'],
                    entry['risk_flags'], entry['is_pick'],
                    entry['next_open'], entry['next_close'], entry['next_change'],
                    entry['exit_open'], entry['trade_return'],
                    entry['sh_change'], entry['run_batch'],
                ))
            except Exception as e:
                logger.warning(f'写入失败 {code} {trade_date}: {e}')
                continue

        _scorer._get_today_quote_from_db = _orig_get_quote

        if self._auto_commit:
            self.db._conn.commit()

        for _e in all_top5:
            self._update_stats(_e)

        return all_top5

    def _patch_info(self, trade_date: str, scored: list, today_up: list):
        """补查候选股补充信息 与 daily_pick_v2.py 第7步一致"""
        three_ago = (datetime.strptime(trade_date, '%Y%m%d') - timedelta(days=90)).strftime('%Y%m%d')
        five_ago = (datetime.strptime(trade_date, '%Y%m%d') - timedelta(days=7)).strftime('%Y%m%d')
        _today_str = trade_date

        for r in scored:
            code = r['code']
            _row = self.db.fetchone(
                'SELECT total_market_cap FROM stock_daily WHERE code=%s AND trade_date=%s LIMIT 1',
                (code, _today_str))
            mcap_raw = _row['total_market_cap'] if _row else 0
            r['total_market_cap'] = mcap_raw if mcap_raw and mcap_raw > 0 else 100000000000

            _ind = self.db.fetchone(
                'SELECT DISTINCT industry FROM daily_limit_up WHERE code=%s AND trade_date=%s AND industry IS NOT NULL AND industry!="" LIMIT 1',
                (code, _today_str))
            r['industry'] = _ind['industry'] if _ind else ''

            _zt = self.db.fetchone(
                'SELECT board_times, bomb_times, price, turnover_rate FROM daily_limit_up WHERE code=%s AND trade_date=%s LIMIT 1',
                (code, _today_str))
            r['board_times'] = _zt['board_times'] if _zt else 1
            r['bomb_times'] = _zt['bomb_times'] if _zt else 0

            _zt5 = self.db.fetchone(
                'SELECT trade_date, price FROM daily_limit_up WHERE code=%s AND trade_date>=%s AND trade_date<=%s AND board_times=1 AND (status IS NULL OR status!="\u8dcc\u505c") ORDER BY trade_date DESC LIMIT 1',
                (code, five_ago, _today_str))
            r['recent_zt_date'] = _zt5['trade_date'] if _zt5 else ''
            r['recent_zt_price'] = _zt5['price'] if _zt5 else 0.0

            _klines_60 = self.db.fetchall(
                'SELECT trade_date, close, low, high, change_pct, volume, amount FROM stock_daily WHERE code=%s AND trade_date>=%s AND trade_date<=%s ORDER BY trade_date',
                (code, three_ago, _today_str))
            if _klines_60:
                _low = min((k['low'] for k in _klines_60 if k['low']), default=0)
                _high = max((k['high'] for k in _klines_60 if k['high']), default=0)
                _close_now = _klines_60[-1]['close']
                r['_60d_low'] = _low
                r['_60d_high'] = _high
                r['_60d_position'] = ((_close_now - _low) / (_high - _low) * 100) if (_high - _low) > 0 else 50
                _last5 = [k for k in _klines_60 if k['trade_date'] >= five_ago][-5:]
                r['_5d_up_days'] = sum(1 for k in _last5 if (k.get('change_pct') or 0) > 0)
                if len(_last5) >= 2:
                    r['_5d_chg'] = (_last5[-1]['close'] - _last5[0]['close']) / _last5[0]['close'] * 100 if _last5[0]['close'] else 0
                else:
                    r['_5d_chg'] = 0
                _vol_last5 = [k['amount'] for k in _last5 if k['amount']]
                _vol_prev10 = [k['amount'] for k in _klines_60 if k['amount']][-15:-5] if len(_klines_60) >= 15 else []
                r['_5d_avg_amount'] = sum(_vol_last5) / len(_vol_last5) if _vol_last5 else 0
                r['_10d_prev_avg_amount'] = sum(_vol_prev10) / len(_vol_prev10) if _vol_prev10 else 0
                _closes_last5 = [k['close'] for k in _klines_60 if k['close']][-5:]
                r['_ma5'] = sum(_closes_last5) / len(_closes_last5) if _closes_last5 else 0
            else:
                r['_60d_position'] = 50
                r['_5d_up_days'] = 0
                r['_5d_chg'] = 0
                r['_5d_avg_amount'] = 0
                r['_10d_prev_avg_amount'] = 0
                r['_ma5'] = 0

            _chg_row = self.db.fetchone(
                'SELECT change_pct, close FROM stock_daily WHERE code=%s AND trade_date=%s LIMIT 1',
                (code, _today_str))
            r['change_pct'] = _chg_row['change_pct'] if _chg_row else 0
            r['today_close'] = _chg_row['close'] if _chg_row else 0

    def _group_and_filter(self, trade_date: str, scored: list, today_up: list) -> dict:
        """分组过滤与精选出榜 与 daily_pick_v2.py 第8步完全一致"""
        _today_str = trade_date

        # ---- 涨停回踩组 ----
        # 条件：近5日有首板涨停(不是今天) -> 今日未涨停 -> 缩量回踩不破起涨点
        up_group = []
        for r in scored:
            code = r['code']
            change = r.get('change_pct', 0) or 0
            # 跳过今日涨停
            if change >= 9.5:
                continue
            # 近5日有首板涨停
            zt_date = r.get('recent_zt_date', '')
            zt_price = r.get('recent_zt_price', 0.0)
            if not zt_date or zt_price <= 0:
                continue
            # 涨停日不能是今天
            if zt_date >= _today_str:
                continue
            # 今日收盘不低于涨停价97%
            today_close_ = r.get('today_close', 0)
            if today_close_ < zt_price * 0.97:
                continue
            # 相对涨停价在+-5%区间
            zt_change_pct = (today_close_ - zt_price) / zt_price * 100
            if zt_change_pct < -5 or zt_change_pct > 5:
                continue
            # 换手率不爆量
            _ztv = self.db.fetchone(
                'SELECT turnover_rate FROM daily_limit_up WHERE code=%s AND trade_date=%s LIMIT 1',
                (code, zt_date))
            _tdv = self.db.fetchone(
                'SELECT turnover_rate FROM stock_daily WHERE code=%s AND trade_date=%s LIMIT 1',
                (code, _today_str))
            zt_turn = float(_ztv['turnover_rate']) if _ztv and _ztv.get('turnover_rate') is not None else 0
            td_turn = float(_tdv['turnover_rate']) if _tdv and _tdv.get('turnover_rate') is not None else 0
            if zt_turn > 0 and td_turn > zt_turn * 2.0:
                continue
            # 市值>=50亿(跳过0值)
            mcap = r.get('total_market_cap', 0) or 0
            if mcap > 0 and mcap < 5000000000:
                continue
            # 排除688/300/301/8/4/ST/退
            if code.startswith('688') or code.startswith('300') or code.startswith('301') or code.startswith('8') or code.startswith('4'):
                continue
            if 'ST' in r.get('name', '') or '\u9000' in r.get('name', ''):
                continue
            recoil_pct = (today_close_ - zt_price) / zt_price * 100
            r['zt_date'] = zt_date
            r['zt_price'] = zt_price
            r['today_close'] = today_close_
            r['recoil_pct'] = recoil_pct
            r['group'] = '\u6da8\u505c\u56de\u8e29'
            up_group.append(r)

        up_group.sort(key=lambda x: (x.get('total_score', 0), -abs(x.get('recoil_pct', 0))), reverse=True)
        up_top5 = up_group[:5]

        # ---- 区间潜伏组 ----
        non_up_group = []
        for r in scored:
            if r.get('source') == '\u6da8\u505c\u70ed\u70b9':
                continue
            trend_score = (r.get('breakdown', {}).get('\u8d8b\u52bf\u4f4d\u7f6e', {}).get('score', 0) or 0)
            if trend_score < 5:
                continue
            pos_score = (r.get('breakdown', {}).get('\u4f4d\u7f6e\u8bc4\u4f30', {}).get('score', 0) or 0)
            if pos_score < 3:
                continue
            mcap = r.get('total_market_cap', 0) or 0
            if mcap > 0 and mcap < 5000000000:
                continue
            code = r['code']
            if code.startswith('688') or code.startswith('300') or code.startswith('301') or code.startswith('8') or code.startswith('4'):
                continue
            if 'ST' in r.get('name', '') or '\u9000' in r.get('name', ''):
                continue
            pos_60 = r.get('_60d_position', 100)
            if pos_60 > 50:
                continue
            high_60 = r.get('_60d_high', 0)
            low_60 = r.get('_60d_low', 0)
            if high_60 > 0 and (high_60 - low_60) / high_60 < 0.20:
                continue
            up_days = r.get('_5d_up_days', 0)
            if up_days < 3:
                continue
            chg_5 = r.get('_5d_chg', 0)
            if chg_5 < 2 or chg_5 > 15:
                continue
            avg_5 = r.get('_5d_avg_amount', 0)
            avg_10 = r.get('_10d_prev_avg_amount', 0)
            if avg_10 > 0 and avg_5 < avg_10 * 0.8:
                continue
            if avg_10 > 0 and avg_5 > avg_10 * 2.5:
                continue
            ma5 = r.get('_ma5', 0)
            close_now = r.get('today_close', 0)
            if ma5 > 0 and close_now < ma5:
                continue
            r['group'] = '\u533a\u95f4\u6f5c\u4f0f'
            non_up_group.append(r)

        non_up_group.sort(key=lambda x: (x.get('total_score', 0), x.get('_60d_position', 50)), reverse=True)
        non_up_top5 = non_up_group[:5]
        return {'up_top5': up_top5, 'non_up_top5': non_up_top5}
    def _update_stats(self, entry: Dict):
        """更新统计变量 — 使用 trade_return (T+1开盘→T+2开盘)"""
        group = entry['source']
        change = entry.get('trade_return', entry.get('next_change'))
        score = entry['total_score']

        if change is not None:
            if group == '涨停热点':
                s = self.stats['limit_up_group']
            else:
                s = self.stats['latent_group']
            s['cnt'] += 1
            if change > 0:
                s['win'] += 1
            s['sum_return'] += change
            s['max_return'] = max(s['max_return'], change)
            s['min_return'] = min(s['min_return'], change)

            # 分桶
            bucket = int(score // 10) * 10
            bucket_key = f'{bucket}-{bucket+9}'
            b = self.stats['score_buckets'][bucket_key]
            b['cnt'] += 1
            if change > 0:
                b['win'] += 1
            b['sum_return'] += change

        self.stats['total_picks'] += 1

    # --------------------------------------------------------
    # 报告生成
    # --------------------------------------------------------

    def _print_report(self):
        """打印回测统计报告"""
        print(f'\n{"="*60}')
        print(f'  回测报告 — 批次: {self.batch_id}')
        print(f'{"="*60}')
        print(f'  总交易日: {self.stats["total_days"]}')
        print(f'  总推荐票数: {self.stats["total_picks"]}')
        print()

        for group_name, s in [('涨停接力组', 'limit_up_group'), ('区间潜伏组', 'latent_group')]:
            g = self.stats[s]
            if g['cnt'] == 0:
                continue
            win_rate = g['win'] / g['cnt'] * 100
            avg_ret = g['sum_return'] / g['cnt']
            print(f'  📊 {group_name}:')
            print(f'     推荐次数: {g["cnt"]}')
            print(f'     胜率: {win_rate:.1f}% ({g["win"]}/{g["cnt"]})')
            print(f'     平均收益: {avg_ret:+.2f}%')
            print(f'     最大收益: {g["max_return"]:+.2f}%')
            print(f'     最大亏损: {g["min_return"]:+.2f}%')
            print(f'     总收益: {g["sum_return"]:+.2f}%')
            print()

        # 分桶
        if self.stats['score_buckets']:
            print(f'  📊 评分分段表现:')
            for bucket in sorted(self.stats['score_buckets'].keys()):
                b = self.stats['score_buckets'][bucket]
                if b['cnt'] == 0:
                    continue
                win_rate = b['win'] / b['cnt'] * 100
                avg_ret = b['sum_return'] / b['cnt']
                print(f'     {bucket:>5}分: {b["cnt"]:>4}次 | 胜率{win_rate:5.1f}% | 平均{avg_ret:+.2f}%')

        print()
        # 从 DB 拉完整统计（使用 trade_return / T+1开盘→T+2开盘）
        try:
            for group_name, src in [('涨停接力组', '涨停热点'), ('区间潜伏组', '区间潜伏')]:
                stats = self.db.fetchone('''
                    SELECT COUNT(*) as cnt,
                           AVG(trade_return) as avg_ret,
                           SUM(CASE WHEN trade_return > 0 THEN 1 ELSE 0 END)/COUNT(*) as wr,
                           MAX(trade_return) as max_ret,
                           MIN(trade_return) as min_ret,
                           SUM(CASE WHEN trade_return > 5 THEN 1 ELSE 0 END)/COUNT(*) as big_win_rate,
                           SUM(CASE WHEN trade_return < -3 THEN 1 ELSE 0 END)/COUNT(*) as big_loss_rate
                    FROM backtest_results
                    WHERE run_batch=%s AND source=%s AND trade_return IS NOT NULL
                ''', (self.batch_id, src))
                if stats and stats['cnt'] > 0:
                    print(f'  📊 {group_name}:')
                    print(f'     推荐次数: {stats["cnt"]}')
                    print(f'     胜率: {float(stats["wr"]):.1%}')
                    print(f'     平均收益: {float(stats["avg_ret"]):+.2f}%')
                    print(f'     最大收益: {float(stats["max_ret"]):+.2f}%')
                    print(f'     最大亏损: {float(stats["min_ret"]):+.2f}%')
                    print(f'     大胜率(>5%): {float(stats["big_win_rate"]):.1%}')
                    print(f'     大亏率(<-3%): {float(stats["big_loss_rate"]):.1%}')
                    print()

            pick_stats = self.db.fetchone('''
                SELECT COUNT(*) as cnt,
                       AVG(trade_return) as avg_ret,
                       SUM(CASE WHEN trade_return > 0 THEN 1 ELSE 0 END)/COUNT(*) as wr
                FROM backtest_results
                WHERE is_pick=1 AND trade_return IS NOT NULL AND run_batch=%s
            ''', (self.batch_id,))
            if pick_stats and pick_stats['cnt'] > 0:
                print(f'  📊 精选(TOP10): {pick_stats["cnt"]}次 | '
                      f'胜率{float(pick_stats["wr"]):.1%} | 平均{float(pick_stats["avg_ret"]):+.2f}%')
        except Exception as e:
            pass

        print(f'{"="*60}\n')


# ============================================================
# 3. 对外接口
# ============================================================

def run_all(start_date: str = None, end_date: str = None,
            batch_id: str = None, sample_days: int = None):
    """
    全量回测入口

    参数:
      start_date: 开始日期 YYYYMMDD，默认自动推断
      end_date:   结束日期 YYYYMMDD，默认昨天
      batch_id:   批次标识
      sample_days: 如果设置，只跑最近N天（快速验证模式）
    """
    engine = BacktestEngine(batch_id)
    all_dates = engine._get_trade_dates()

    if sample_days and sample_days > 0:
        all_dates = all_dates[-sample_days:]
        logger.info(f'快速模式: 只跑最近 {sample_days} 天')

    if start_date:
        all_dates = [d for d in all_dates if d >= start_date]
    if end_date:
        all_dates = [d for d in all_dates if d <= end_date]

    # 去掉最后一天（没有次日数据）
    if len(all_dates) > 0:
        all_dates = all_dates[:-1]

    logger.info(f'开始回测: {len(all_dates)} 个交易日')

    engine.stats['total_days'] = len(all_dates)
    run_start = time.time()

    for i, trade_date in enumerate(all_dates):
        if i > 0 and i % 20 == 0:
            elapsed = time.time() - run_start
            rate = i / elapsed if elapsed > 0 else 0
            remaining = (len(all_dates) - i) / rate if rate > 0 else 0
            logger.info(f'  进度: {i}/{len(all_dates)} ({i/len(all_dates)*100:.0f}%) '
                        f'耗时{elapsed:.0f}s 预计剩余{remaining:.0f}s')

        results = engine._backtest_one_day(trade_date)
        if results:
            logger.debug(f'  {trade_date}: {len(results)} 只候选')

    elapsed = time.time() - run_start
    logger.info(f'回测完成! 耗时 {elapsed:.0f}s')

    engine._print_report()
    return engine


def print_ranking_report(batch_id: str = None):
    """
    打印基于已入库结果的统计报告（不重新跑回测）
    如果 batch_id 为空，取最新批次
    """
    db = get_db()

    if not batch_id:
        row = db.fetchone('SELECT run_batch FROM backtest_results ORDER BY created_at DESC LIMIT 1')
        if not row:
            print('没有回测数据')
            return
        batch_id = row['run_batch']

    print(f'\n{"="*60}')
    print(f'  回测报告 — 批次: {batch_id}')
    print(f'{"="*60}')

    # 基本统计
    base = db.fetchone('''
        SELECT COUNT(DISTINCT trade_date) as days, COUNT(*) as picks
        FROM backtest_results WHERE run_batch=%s
    ''', (batch_id,))
    print(f'  交易日: {base["days"]} | 总推荐: {base["picks"]}')

    for group_name, src in [('涨停接力组', '涨停热点'), ('区间潜伏组', '区间潜伏')]:
        stats = db.fetchone('''
            SELECT COUNT(*) as cnt,
                   AVG(next_change) as avg_ret,
                   SUM(CASE WHEN next_change > 0 THEN 1 ELSE 0 END)/COUNT(*) as wr,
                   MAX(next_change) as max_ret,
                   MIN(next_change) as min_ret
            FROM backtest_results
            WHERE run_batch=%s AND source=%s AND next_change IS NOT NULL
        ''', (batch_id, src))
        if stats and stats['cnt'] > 0:
            print(f'\n  📊 {group_name}:')
            print(f'     次数: {stats["cnt"]}')
            print(f'     胜率: {float(stats["wr"]):.1%}')
            print(f'     平均: {float(stats["avg_ret"]):+.2f}%')
            print(f'     最大: {float(stats["max_ret"]):+.2f}% / 最小: {float(stats["min_ret"]):+.2f}%')

    # 精选统计
    pick = db.fetchone('''
        SELECT COUNT(*) as cnt, AVG(next_change) as avg_ret,
               SUM(CASE WHEN next_change > 0 THEN 1 ELSE 0 END)/COUNT(*) as wr
        FROM backtest_results WHERE run_batch=%s AND is_pick=1 AND next_change IS NOT NULL
    ''', (batch_id,))
    if pick and pick['cnt'] > 0:
        print(f'\n  📊 精选(is_pick=1):')
        print(f'     次数: {pick["cnt"]} | 胜率: {float(pick["wr"]):.1%} | 平均: {float(pick["avg_ret"]):+.2f}%')

    # 评分分段
    buckets = db.fetchall('''
        SELECT FLOOR(total_score/10)*10 as bucket, COUNT(*) as cnt, AVG(next_change) as avg_ret,
               SUM(CASE WHEN next_change > 0 THEN 1 ELSE 0 END)/COUNT(*) as wr
        FROM backtest_results WHERE run_batch=%s AND next_change IS NOT NULL
        GROUP BY bucket ORDER BY bucket
    ''', (batch_id,))
    if buckets:
        print(f'\n  📊 评分分段:')
        for b in buckets:
            if b['cnt'] == 0:
                continue
            lo = int(b['bucket'])
            hi = lo + 9
            print(f'     {lo:>3}-{hi:>3}分: {b["cnt"]:>4}次 | 胜率{float(b["wr"]):5.1%} | 平均{float(b["avg_ret"]):+.2f}%')

    print(f'\n{"="*60}\n')


# ============================================================
# 4. 直接运行
# ============================================================

if __name__ == '__main__':
    import sys as _sys
    args = _sys.argv[1:]

    if '--report' in args:
        bid = args[args.index('--report') + 1] if '--report' in args and len(args) > args.index('--report') + 1 else None
        print_ranking_report(bid)
    else:
        start = args[0] if len(args) > 0 else None
        end   = args[1] if len(args) > 1 else None
        sample = int(args[2]) if len(args) > 2 else None
        run_all(start, end, sample_days=sample)
