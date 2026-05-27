# 📊 股票分析系统

基于实时数据的 A 股分析工具。

## 🚀 快速启动

### 1. 安装依赖

```bash
cd ~/workspace/StockAnalysis
pip install -r requirements.txt
```

### 2. 交互式终端模式

```bash
python3 main.py
```

菜单功能：
```
1. 📈 市场概况  — 主要指数 + 热门个股实时行情
2. 🔍 个股查询  — 个股详情与涨跌幅
3. 🏢 指数分析  — 主要指数行情
4. 🏭 行业板块  — 行业排名
5. 🚨 预警检查  — 异常波动预警
6. 📄 生成报告  — HTML 可视化报告
```

### 3. 🌐 Web 模式 **(推荐)**

```bash
python3 web_server.py
```

浏览器打开 `http://localhost:8899`

## 📁 项目结构

```
StockAnalysis/
├── main.py               # 交互式终端
├── web_server.py         # HTTP Web 服务 (端口 8899)
├── stock_analysis_api.py # 数据获取 (新浪财经实时API)
├── data_parser.py        # 技术指标解析 (RSI/MACD/KDJ)
├── strategy.py           # 选股策略 (价值/动量)
├── alert_system.py       # 预警系统
├── data_store.py         # SQLite 数据持久化
├── visualization.py      # 可视化与HTML报告
├── dashboard.py          # Streamlit 仪表盘 (可选)
└── stock_data.db         # 本地数据库 (自动生成)
```

## 🔌 Web API 文档

| 端点 | 说明 |
|---|---|
| `/` | 仪表盘页面 |
| `/api/market-overview` | 市场概况 (JSON) |
| `/api/index?code=szzs` | 指数详情+K线 (支持: szzs/szcz/hs300/cyb/kc50) |
| `/api/stock?secid=1.600519` | 个股详情 |
| `/api/sectors` | 行业板块 |
| `/api/history?kind=index&code=szzs` | 历史行情 |
| `/api/snapshot` | 最新市场快照 |

## 💡 数据来源

- **实时行情**: 新浪财经 API (毫秒级响应)
- **K线数据**: AKShare (指数日线)
- **行业板块**: 东方财富 (可选)
