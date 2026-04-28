"""
股票技术分析共享模块
所有脚本统一调用此模块，确保逻辑一致
"""
import numpy as np
from datetime import date, timedelta
import tushare as ts

# Tushare Token
TUSHARE_TOKEN = '063dde2a5efdda7c004459717c2ca8b93bb63ce24fdbb9abad3e8a3e'

def get_pro():
    """获取tushare接口"""
    return ts.pro_api(TUSHARE_TOKEN)

def get_kline(ts_code, days=20):
    """
    获取日线数据（只返回完整的交易日，不含今天）
    返回: DataFrame 或 None
    """
    try:
        end = (date.today() - timedelta(days=1)).strftime('%Y%m%d')  # 只到昨天
        start = (date.today() - timedelta(days=30)).strftime('%Y%m%d')  # 30天前
        df = get_pro().daily(ts_code=ts_code, start_date=start, end_date=end)
        if df is not None and len(df) > 0:
            df = df.sort_values('trade_date')
            # 排除今天的不完整数据（如果有）
            if df.iloc[-1]['trade_date'] == date.today().strftime('%Y%m%d'):
                df = df.iloc[:-1]
            return df if len(df) >= 5 else None
        return None
    except:
        return None

def calc_technical(df):
    """
    计算技术指标（基于完整的日线数据）
    返回: dict
    """
    if df is None or len(df) < 5:
        return {}
    
    closes = df['close'].values
    vols = df['vol'].values if 'vol' in df.columns else None
    
    latest_close = closes[-1]
    latest_pct = float(df.iloc[-1]['pct_chg']) if 'pct_chg' in df.columns else 0
    
    # 均线
    ma5 = np.mean(closes[-5:]) if len(closes) >= 5 else None
    ma10 = np.mean(closes[-10:]) if len(closes) >= 10 else None
    ma20 = np.mean(closes[-20:]) if len(closes) >= 20 else None
    
    # 均线状态（修复：MA5≈MA10时判断为横盘待变）
    ma_direction = ""
    if ma5 and ma10 and ma20:
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
    vol_ratio = 0
    if vols is not None and len(vols) >= 5:
        avg_vol = np.mean(vols[-5:])  # 只用最后5天
        latest_vol = vols[-1]
        vol_ratio = latest_vol / avg_vol if avg_vol > 0 else 0
        if vol_ratio > 1.5:
            vol_analysis = f"放量({vol_ratio:.1f}x)"
        elif vol_ratio < 0.7:
            vol_analysis = f"缩量({vol_ratio:.1f}x)"
        else:
            vol_analysis = f"正常({vol_ratio:.1f}x)"
    
    # 20日高低
    high_20 = np.max(closes[-20:]) if len(closes) >= 20 else np.max(closes)
    low_20 = np.min(closes[-20:]) if len(closes) >= 20 else np.min(closes)
    
    return {
        'latest_close': round(latest_close, 2),
        'latest_pct': round(latest_pct, 2),
        'ma5': round(ma5, 2) if ma5 else None,
        'ma10': round(ma10, 2) if ma10 else None,
        'ma20': round(ma20, 2) if ma20 else None,
        'ma_direction': ma_direction,
        'vol_analysis': vol_analysis,
        'vol_ratio': round(vol_ratio, 1),
        'high_20': round(high_20, 2),
        'low_20': round(low_20, 2),
    }

def analyze_stock(code, market='sh'):
    """
    综合分析单只股票
    code: 股票代码（如 '000783'）
    market: 'sh' 或 'sz'
    返回: dict
    """
    ts_code = code + '.SH' if market == 'sh' else code + '.SZ'
    
    result = {'code': code, 'market': market}
    
    # 基本信息
    try:
        pro = get_pro()
        basic = pro.stock_basic(ts_code=ts_code, fields='name,industry')
        if basic is not None and len(basic) > 0:
            result['name'] = basic.iloc[0].get('name', code)
            result['industry'] = basic.iloc[0].get('industry', '未知')
    except:
        result['name'] = code
        result['industry'] = '未知'
    
    # 技术面
    df = get_kline(ts_code, days=20)
    tech = calc_technical(df)
    result.update(tech)
    
    # 20日区间位置
    if 'latest_close' in tech and 'high_20' in tech and 'low_20' in tech:
        if tech['high_20'] != tech['low_20']:
            pos = (tech['latest_close'] - tech['low_20']) / (tech['high_20'] - tech['low_20']) * 100
            result['pos_in_range'] = round(pos, 0)
    
    return result

def format_analysis(result):
    """格式化分析结果为可读字符串"""
    if 'error' in result:
        return f"❌ 分析失败: {result['error']}"
    
    name = result.get('name', result['code'])
    code = result['code']
    price = result.get('latest_close', 'N/A')
    pct = result.get('latest_pct', 0)
    
    msg = f"""📊 **{name}（{code}）** 分析
行业: {result.get('industry', '未知')}

*📈 技术面*
最新价: {price} ({pct:+.2f}%)
MA5: {result.get('ma5', 'N/A')} | MA10: {result.get('ma10', 'N/A')} | MA20: {result.get('ma20', 'N/A')}
均线: {result.get('ma_direction', 'N/A')}
量价: {result.get('vol_analysis', 'N/A')}
20日区间: {result.get('low_20', 'N/A')} ~ {result.get('high_20', 'N/A')}"""
    
    return msg
