"""
dev 环境回放 7/1~7/31 标准选股，落库到 dev 对照表 dev_replay_jul
含 dev 重算的 total + 各维度分，供与生产 daily_picks 对比。
"""
import os, sys, time, json, warnings
warnings.filterwarnings('ignore')
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.getcwd())

from core.analyzer.daily_pick_v2_backtest import pick_stocks_v2
from utils.dao import get_db

TRADE_DATES = ['20260701','20260702','20260703','20260706','20260707','20260708',
    '20260709','20260710','20260713','20260714','20260715','20260716','20260717',
    '20260720','20260721','20260722','20260723','20260724','20260727','20260728',
    '20260729','20260730','20260731']

def extract_dims(r):
    bd = r.get('breakdown', {})
    return {
        'chip': bd.get('筹码结构', {}).get('score'),
        'money': bd.get('资金接力', {}).get('score'),
        'sector': bd.get('板块环境', {}).get('score'),
        'trend': bd.get('趋势位置', {}).get('score'),
        'market': bd.get('大盘安全', {}).get('score'),
        'pos': bd.get('位置评估', {}).get('score'),
    }

def run():
    db = get_db()
    db.execute('DELETE FROM dev_replay_jul')  # 清空可重跑
    total_ins = 0
    for td in TRADE_DATES:
        t0 = time.time()
        try:
            res = pick_stocks_v2(trade_date=td)
            scored = res.get('scored', [])
            # 生产对应日期的落库分
            prods = db.fetchall(
                "SELECT code,total_score,score_chip,score_money,score_sector,score_trend,score_market,score_pos "
                "FROM daily_picks WHERE trade_date=%s", (td,))
            prod_map = {}
            for p in prods:
                k = str(p['code'])
                if k.endswith('.0'): k = k[:-2]
                k = k.zfill(6)
                prod_map[k] = p
            for r in scored:
                code = str(r.get('code')).zfill(6)
                d = extract_dims(r)
                p = prod_map.get(code, {})
                db.insert_or_ignore('dev_replay_jul', {
                    'trade_date': td, 'code': code, 'name': r.get('name'),
                    'dev_total': r.get('total_score'),
                    'dev_chip': d['chip'], 'dev_money': d['money'], 'dev_sector': d['sector'],
                    'dev_trend': d['trend'], 'dev_market': d['market'], 'dev_pos': d['pos'],
                    'prod_total': p.get('total_score'),
                    'prod_chip': p.get('score_chip'), 'prod_money': p.get('score_money'),
                    'prod_sector': p.get('score_sector'), 'prod_trend': p.get('score_trend'),
                    'prod_market': p.get('score_market'), 'prod_pos': p.get('score_pos'),
                    'source': r.get('source'),
                })
                total_ins += 1
            print(f'  {td}: 评分{len(scored)}只 (对照表累计{total_ins}) 耗时{time.time()-t0:.0f}s', flush=True)
        except Exception as e:
            import traceback
            print(f'  {td}: 失败 {e}', flush=True)
            traceback.print_exc()
    print(f'\n完成: 共回放落库 {total_ins} 条到 dev_replay_jul')
    db.close()

if __name__ == '__main__':
    run()
