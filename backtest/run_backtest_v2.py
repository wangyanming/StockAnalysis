
#!/usr/bin/env python3
import sys,os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.dao import get_db
START_DATE = "20260506"
END_DATE = "20260723"
BATCH_ID = "b2_%s_%s" % (START_DATE, END_DATE)
SCORE_LOW = 60
SCORE_MED = 70

def clear(db):
    db.execute("DELETE FROM backtest_picks WHERE batch_id = %s", (BATCH_ID,))

def top_pick(db, td):
    return db.fetchone("SELECT code,name,total_score,source FROM daily_picks WHERE trade_date=%s AND total_score IS NOT NULL AND total_score>0 ORDER BY total_score DESC,code ASC LIMIT 1", (td,))

def next_day(db, code, after, offset=0):
    return db.fetchone("SELECT trade_date,open FROM stock_daily WHERE code=%s AND trade_date>%s ORDER BY trade_date LIMIT 1 OFFSET %s", (code, after, offset))

def fp(v):
    return "N/A" if v is None else f"{float(v):.2f}"

def fmtp(v):
    if v is None: return "N/A"
    return f"{'+' if v>=0 else ''}{float(v)*100:.2f}%"

def main():
    db = get_db()
    print(f"📊 回测区间: {START_DATE} ~ {END_DATE}")
    print("📦 Batch ID: %s" % BATCH_ID)
    clear(db)
    tds = [r['trade_date'] for r in db.fetchall("SELECT DISTINCT trade_date FROM stock_daily WHERE trade_date>=%s AND trade_date<=%s ORDER BY trade_date", (START_DATE, END_DATE))]
    print(f"📅 总交易日数: {len(tds)}")
    results = []
    for td in tds:
        pick = top_pick(db, td)
        if pick is None: continue
        code,name,score,source = pick['code'],pick['name'],pick['total_score'],pick.get('source','')
        ep=bd=bp=sd=sp=pr=sr=None
        tr = db.fetchone("SELECT open FROM stock_daily WHERE code=%s AND trade_date=%s",(code,td))
        if tr: ep=tr['open']
        if score < SCORE_LOW:
            sr = "分数<60不买入"
        else:
            d1 = next_day(db,code,td,0)
            if d1:
                bd,bp = d1['trade_date'],d1['open']
                d2 = next_day(db,code,td,1)
                if d2:
                    sd,sp = d2['trade_date'],d2['open']
                    if bp and bp>0: pr=(sp-bp)/bp
            else:
                sr = "无后续交易日数据"
        r = dict(trade_date=td,code=code,name=name,score=score,source=source,entry_price=ep,buy_date=bd,buy_price=bp,sell_date=sd,sell_price=sp,profit_rate=pr,stop_reason=sr)
        results.append(r)
        db.execute("INSERT INTO backtest_picks (trade_date,code,name,total_score,source,entry_price,buy_date,buy_price,sell_date,sell_price,profit_rate,stop_reason,batch_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", (td,code,name,score,source,ep,bd,bp,sd,sp,pr,sr,BATCH_ID))

    print("\n" + ("=" * 80))
    print("📋 交易明细")
    print(f"{'='*80}")
    for r in results:
        td,code,nm,sc = r['trade_date'],r['code'],r['name'],r['score']
        if r['stop_reason']:
            print(f"{td} {code} {nm} {sc}分 | 不买入({r['stop_reason']})")
        else:
            bs = f"买入{r['buy_date']}@{fp(r['buy_price'])}"
            if r['sell_date']:
                print(f"{td} {code} {nm} {sc}分 | {bs} → 卖出{r['sell_date']}@{fp(r['sell_price'])} = {fmtp(r['profit_rate'])}")
            else:
                print(f"{td} {code} {nm} {sc}分 | {bs} → 卖出待定")

    print("\n\n" + ("=" * 80))
    print("📊 统计汇总")
    print(f"{'='*80}")
    total=len(tds); skip=sum(1 for r in results if r['stop_reason']=="分数<60不买入")
    nodata=sum(1 for r in results if r.get('stop_reason') and r['stop_reason']!="分数<60不买入")
    traded=[r for r in results if r['buy_date']]
    win=[r for r in traded if r['profit_rate'] and r['profit_rate']>0]
    loss=[r for r in traded if r['profit_rate'] is not None and r['profit_rate']<=0]
    pending=[r for r in traded if not r['sell_date']]
    tc=len(traded);wc=len(win);lc=len(loss)
    tp=sum(r['profit_rate'] for r in traded if r['profit_rate'] is not None) if tc else 0.0
    ap=tp/tc if tc else 0.0; wr=wc/tc*100 if tc else 0.0
    mw=max(traded,key=lambda r:r['profit_rate'] or -999) if win else None
    ml=min(traded,key=lambda r:r['profit_rate'] or 999) if loss else None
    print(f"总选股日: {total}")
    print(f"交易次数: {tc}")
    print(f"跳过(分数<60): {skip}")
    if nodata: print(f"跳过(数据不足): {nodata}")
    if pending: print(f"买入未卖出: {len(pending)}")
    print(f"盈利: {wc}次({wr:.0f}%)")
    print(f"亏损: {lc}次({100-wr:.0f}%%)")
    print(f"总收益率: {fmtp(tp)}")
    print(f"平均收益率: {fmtp(ap)}")
    if mw: print(f"最大盈利: {mw['name']}({mw['code']}) {fmtp(mw['profit_rate'])}")
    if ml: print(f"最大亏损: {ml['name']}({ml['code']}) {fmtp(ml['profit_rate'])}")
    print("\n按评分分组:")
    if skip: print(f"  <60(过滤): {skip}次  —")
    gl=[r for r in traded if r['score'] < SCORE_MED]
    gh=[r for r in traded if r['score'] >= SCORE_MED]
    if gl:
        glw=sum(1 for r in gl if r['profit_rate'] and r['profit_rate']>0)
        gla=sum(r['profit_rate'] for r in gl if r['profit_rate'] is not None)/len(gl)
        print(f"  60~70: {len(gl)}次 胜率{glw/len(gl)*100:.0f}% 均收益{fmtp(gla)}")
    if gh:
        ghw=sum(1 for r in gh if r['profit_rate'] and r['profit_rate']>0)
        gha=sum(r['profit_rate'] for r in gh if r['profit_rate'] is not None)/len(gh)
        print(f"  ≥70: {len(gh)}次 胜率{ghw/len(gh)*100:.0f}% 均收益{fmtp(gha)}")
    print("\n连续亏损分析:")
    cl=0
    for r in traded:
        if r['profit_rate'] is not None and r['profit_rate'] <= 0: cl+=1
        else:
            if cl>=2 and r['profit_rate'] is not None:
                print(f"  连续{cl}次亏损后 -> {r['trade_date']} {r['name']} {fmtp(r['profit_rate'])}")
            cl=0
    if cl>=2: print(f"  连续{cl}次亏损 -> 最后持仓")
if __name__=='__main__': main()
