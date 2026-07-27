"""
TimeMachine - 选股引擎历史日期模拟器

在回测时将选股引擎中所有 datetime.now() 替换为指定交易日，
使选股逻辑按历史日期查询对应数据。

工作原理：
  1. 使用 unittest.mock.patch 替换各模块中的 datetime 引用
  2. datetime.now() 返回模拟的目标日期
  3. datetime.strftime/strptime/combine 保持原始行为

支持的模块（按 pick_stocks_v2() 调用链）：
  - core.analyzer.daily_pick_v2    (主选股引擎)
  - core.analyzer.scorer           (评分引擎)
  - core.fetcher.limit_up_analysis (涨停分析)
"""
from datetime import datetime
from unittest.mock import patch


class TimeMachine:
    """时间模拟器：patch 选股引擎中的 datetime 模块
    
    用法:
        with TimeMachine('20260513'):
            results = pick_stocks_v2()
    """
    
    # 需要 patch 的模块列表（按 pick_stocks_v2() 调用链）
    PATCH_MODULES = [
        'core.analyzer.daily_pick_v2',
        'core.analyzer.scorer',
        'core.fetcher.limit_up_analysis',
    ]
    
    def __init__(self, target_date: str):
        self.target = datetime.strptime(target_date, '%Y%m%d')
        self._patches = []
    
    def __enter__(self):
        for mod_name in self.PATCH_MODULES:
            try:
                p = patch(f'{mod_name}.datetime')
                mock_dt = p.start()
                mock_dt.now.return_value = self.target
                mock_dt.strftime = datetime.strftime
                mock_dt.strptime = datetime.strptime
                mock_dt.combine = datetime.combine
                from datetime import timedelta
                mock_dt.timedelta = timedelta
                self._patches.append(p)
            except Exception as e:
                print(f"  [TimeMachine] Skip {mod_name}: {e}")
        return self
    
    def __exit__(self, *args):
        for p in self._patches:
            try:
                p.stop()
            except Exception:
                pass


def pick_stocks_v2_for_date(trade_date: str):
    """
    在指定历史日期上运行选股引擎
    
    Args:
        trade_date: YYYYMMDD 格式，如 '20260513'
    
    Returns:
        pick_stocks_v2() 的完整返回结果
    """
    with TimeMachine(trade_date):
        from core.analyzer.daily_pick_v2 import pick_stocks_v2
        result = pick_stocks_v2()
    return result


# 如果直接运行，执行快速自检
if __name__ == '__main__':
    with TimeMachine('20260513'):
        now = datetime.now()
        assert now.strftime('%Y%m%d') == '20260513', f"TimeMachine failed: {now}"
    print("TimeMachine self-test passed")
