#!/usr/bin/env python3
"""
股票智能查询Agent脚本
功能：回答老大关于股票的各类问题
触发：老大说"分析XXX"或"查一下XXX"时调用
"""
import sys
import os
import json
import warnings
from datetime import datetime, date, timedelta

warnings.filterwarnings('ignore')

# 路径配置
WORKSPACE = "/home/YDL/.openclaw/workspace"
STOCK_POOL_DIR = "/home/YDL/.openclaw/agent_stock_work/stock_pool"
CACHE_DIR = "/home/YDL/.openclaw/agent_stock_work/cache_temp"

# ============ 数据获取 ============

def get_tushare():
    import tushare as ts
    return ts.pro_api('063dde2a5efdda7c004459717c2ca8b93bb63ce24fdbb9abad3e8a3e')

def get_stock_basic_info(pro, code):
    """获取股票基本信息"""
    try:
        ts_code = code + '.SH' if code.startswith(('6', '5', '7', '9')) else code + '.SZ'
        df = pro.stock_basic(ts_code=ts_code, fields='name,industry,list_date,market')
        if df is not None and len(df) > 0:
            return df.iloc[0].to_dict()
    except:
        pass
    return {}

def get_recent_kline(pro, code, days=20):
    """获取近N日K线"""
    try:
        ts_code = code + '.SH' if code.startswith(('6', '5', '7', '9')) else code + '.SZ'
        # 修复：使用正确的30天回溯计算
        end = date.today().strftime('%Y%m%d')
        start = (date.today() - timedelta(days=30)).strftime('%Y%m%d')
        df = pro.daily(ts_code=ts_code, start_date=start, end_date=end)
        if df is not None and len(df) > 0:
            df = df.sort_values('trade_date')
            # 修复：排除今天的不完整数据，只用完整交易日计算量比
            # 如果最后一条是今天，跳过它
            if df.iloc[-1]['trade_date'] == date.today().strftime('%Y%m%d'):
                df = df.iloc[:-1]
            return df if len(df) >= 5 else None
        return None
    except:
        return None

def get_fund_flow(pro, code):
    """获取资金流数据（近5日）"""
    try:
        import akshare as ak
        result = ak.stock_individual_fund_flow(stock=code, market="sh" if code.startswith('6') else "sz")
        return result.tail(5) if result is not None and len(result) > 0 else None
    except:
        return None

def get_zt_pool(pro, trade_date):
    """获取涨停股池"""
    try:
        df = pro.stock_zt_pool_em(date=trade_date.strftime('%Y-%m-%d'))
        return df if df is not None and len(df) > 0 else None
    except:
        return None

# ============ 技术分析 ============

def calc_technical(df):
    """计算技术指标"""
    if df is None or len(df) < 5:
        return {}
    
    import numpy as np
    
    closes = df['close'].values
    vols = df['vol'].values if 'vol' in df.columns else df['volume'].values if 'volume' in df.columns else None
    
    # 均线
    ma5 = np.mean(closes[-5:]) if len(closes) >= 5 else None
    ma10 = np.mean(closes[-10:]) if len(closes) >= 10 else None
    ma20 = np.mean(closes[-20:]) if len(closes) >= 20 else None
    
    # 最新价
    latest_close = closes[-1]
    latest_pct = float(df.iloc[-1]['pct_chg']) if 'pct_chg' in df.columns else 0
    
    # 均线方向（修复：MA5≈MA10时判断为横盘待变）
    ma_direction = ""
    if ma5 and ma10 and ma20:
        # 计算MA5和MA10的差值比例
        ma_diff_pct = abs(ma5 - ma10) / ma10 * 100 if ma10 != 0 else 0
        
        if ma5 > ma10 > ma20:
            if ma_diff_pct < 0.5:
                ma_direction = "横盘待变 ⭐"
            else:
                ma_direction = "多头排列 ⭐"
        elif ma5 < ma10 < ma20:
            if ma_diff_pct < 0.5:
                ma_direction = "横盘待变 ⭐"
            else:
                ma_direction = "空头排列"
        else:
            ma_direction = "混乱排列"
    
    # 量价分析（只使用最后5个完整交易日）
    vol_analysis = ""
    if vols is not None and len(vols) >= 5:
        # 修复：使用最后5个完整交易日计算量比，而不是全部历史数据
        avg_vol = np.mean(vols[-5:])
        latest_vol = vols[-1]
        vol_ratio = latest_vol / avg_vol if avg_vol > 0 else 0
        if vol_ratio > 1.5:
            vol_analysis = f"放量({vol_ratio:.1f}x)"
        elif vol_ratio < 0.7:
            vol_analysis = f"缩量({vol_ratio:.1f}x)"
        else:
            vol_analysis = f"正常({vol_ratio:.1f}x)"
    
    # 近期高低点
    high_20 = np.max(closes[-20:]) if len(closes) >= 20 else np.max(closes)
    low_20 = np.min(closes[-20:]) if len(closes) >= 20 else np.min(closes)
    
    return {
        'latest_close': latest_close,
        'latest_pct': latest_pct,
        'ma5': round(ma5, 2) if ma5 else None,
        'ma10': round(ma10, 2) if ma10 else None,
        'ma20': round(ma20, 2) if ma20 else None,
        'ma_direction': ma_direction,
        'vol_analysis': vol_analysis,
        'high_20': round(high_20, 2),
        'low_20': round(low_20, 2),
    }

# ============ 持仓检查 ============

def check_position(code):
    """检查是否在持仓或候选池中"""
    result = {'in_positions': False, 'in_watchlist': False, 'info': None}
    
    # 检查持仓（从台账读取）
    positions = {
        '000783': {'name': '长江证券', 'cost': 7.338, 'qty': 600, 'stop': 6.80, 'take': 8.10},
        '002230': {'name': '科大讯飞', 'cost': 48.00, 'qty': 100, 'stop': 44.60, 'take': 52.80},
        '601088': {'name': '中国神华', 'cost': 46.73, 'qty': 100, 'stop': 43.00, 'take': 52.00},
        '600900': {'name': '长江电力', 'cost': 26.895, 'qty': 200, 'stop': 25.00, 'take': 30.00},
    }
    
    if code in positions:
        p = positions[code]
        pnl = (float(p['latest_close'] if 'latest_close' in p else 0) - p['cost']) * p['qty']
        result['in_positions'] = True
        result['info'] = f"🟢 持仓: {p['name']} {p['qty']}股 成本{p['cost']} 止损{p['stop']} 目标{p['take']}"
        return result
    
    # 检查候选池
    try:
        pool_file = f"{STOCK_POOL_DIR}/优质股票池.csv"
        if os.path.exists(pool_file):
            import pandas as pd
            df = pd.read_csv(pool_file)
            df['代码'] = df['代码'].astype(str).str.zfill(6)
            matched = df[df['代码'] == code.zfill(6)]
            if len(matched) > 0:
                result['in_watchlist'] = True
                row = matched.iloc[0]
                result['info'] = f"📋 候选池: {row['名称']} 现价{row['现价']} 昨日{row['涨跌幅']}"
    except:
        pass
    
    return result

# ============ 主查询函数 ============

def query_stock(code):
    """综合查询股票并返回分析结果"""
    print(f"\n{'='*50}")
    print(f"🔍 查询股票: {code}")
    print(f"{'='*50}")
    
    try:
        pro = get_tushare()
        
        # 1. 基本信息
        basic = get_stock_basic_info(pro, code)
        name = basic.get('name', code)
        industry = basic.get('industry', '未知')
        
        print(f"📌 {name}（{code}）| 行业: {industry}")
        
        # 2. K线数据
        df = get_recent_kline(pro, code, days=20)
        
        # 3. 技术分析
        tech = calc_technical(df) if df is not None else {}
        
        print(f"\n📊 技术面:")
        print(f"  最新价: {tech.get('latest_close', 'N/A')} ({tech.get('latest_pct', 0):+.2f}%)")
        print(f"  MA5: {tech.get('ma5', 'N/A')} | MA10: {tech.get('ma10', 'N/A')} | MA20: {tech.get('ma20', 'N/A')}")
        print(f"  均线状态: {tech.get('ma_direction', 'N/A')}")
        print(f"  量价: {tech.get('vol_analysis', 'N/A')}")
        print(f"  20日区间: {tech.get('low_20', 'N/A')} ~ {tech.get('high_20', 'N/A')}")
        
        # 4. 持仓检查
        pos = check_position(code)
        print(f"\n{'📋 持仓状态:'}")
        if pos['in_positions'] or pos['in_watchlist']:
            print(f"  {pos['info']}")
        else:
            print(f"  不在持仓/候选池中")
        
        # 5. 构建输出
        result = {
            'code': code,
            'name': name,
            'industry': industry,
            'tech': tech,
            'position': pos,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M')
        }
        
        return result
        
    except Exception as e:
        print(f"❌ 查询失败: {e}")
        return {'code': code, 'error': str(e)}

def format_for_feishu(result):
    """格式化为飞书消息"""
    if 'error' in result:
        return f"❌ 查询失败: {result['error']}"
    
    code = result['code']
    name = result['name']
    industry = result['industry']
    tech = result.get('tech', {})
    pos = result.get('position', {})
    
    msg = f"""📊 **{name}（{code}）** 分析
行业: {industry}
时间: {result['timestamp']}

*📈 技术面*
最新价: {tech.get('latest_close', 'N/A')} ({tech.get('latest_pct', 0):+.2f}%)
MA5: {tech.get('ma5', 'N/A')} | MA10: {tech.get('ma10', 'N/A')} | MA20: {tech.get('ma20', 'N/A')}
均线: {tech.get('ma_direction', 'N/A')}
量价: {tech.get('vol_analysis', 'N/A')}
20日区间: {tech.get('low_20', 'N/A')} ~ {tech.get('high_20', 'N/A')}

*📋 持仓状态*
{pos.get('info', '不在持仓/候选池中')}"""
    
    return msg

# ============ 主程序 ============

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python3 stock_query_agent.py <股票代码>")
        print("示例: python3 stock_query_agent.py 002475")
        sys.exit(1)
    
    code = sys.argv[1].strip()
    result = query_stock(code)
    msg = format_for_feishu(result)
    print("\n" + msg)