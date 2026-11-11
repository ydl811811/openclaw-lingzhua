#!/usr/bin/env python3
"""
自选股买入提醒脚本
- 监控未建仓股票，进入买入区间时提醒
- 30秒间隔检查
- 只发买入提醒，不发止盈提醒（因为还没买）
- 增加缩量条件：价格到位 + 缩量（今日量<昨日量）才提醒
- 2016-05-19: 修复重复发送问题，使用文件锁保证单实例
"""
import os
import sys
import fcntl
import time
import json
import re
import urllib.request
from datetime import datetime
import subprocess

PID_FILE = "/tmp/watchlist_monitor.pid"
LOCK_FILE = "/tmp/watchlist_monitor.lock"
VOLUME_FILE = "/tmp/watchlist_volume.json"

# 模拟盘条件单账本：pending_orders 里有显式 buy_low/buy_high/gates，
# 是「模拟盘真正可执行」的口径，与 WATCHLIST 的技术扫描区不同，二者并存。
# 2026-10-28：监控改用模拟盘口径判定，避免推送「已进技术区但模拟盘不能买」的噪声信号。
SIM_ORDERS_FILE = "/home/YDL/.openclaw/workspace/a_stock_plan/simulated/positions.json"

TENCENT_MULTIDAY_URL = "https://web.ifzq.gtimg.cn/appstock/app/day/query?code={code}&date={date}"
# 同时点量比缓存（TTL 秒），避免每 30 秒都拉多日分时
_SAME_TIME_VOL_CACHE = {}
_SAME_TIME_VOL_TTL = 300

# 买入提醒去重窗口（秒）。2026-11-09 由 300 改为 1800：
# 原 5 分钟窗口导致每 5 分钟推一批、每批 4-5 条，实测 13:00-13:26 推了 4 批。
ALERT_DEDUP_SEC = 1800

# ============ 单实例锁定 ============
def acquire_lock():
    """使用文件锁确保只有一个实例运行"""
    try:
        lock_fd = open(LOCK_FILE, 'w')
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_fd.write(str(os.getpid()))
        lock_fd.flush()
        return lock_fd
    except (IOError, OSError) as e:
        if 'LOCK_NB' not in str(e) and hasattr(e, 'errno') and e.errno == 11:
            print(f"已有实例运行，无法获取锁 (PID_FILE: {PID_FILE})")
        else:
            print(f"无法获取锁: {e}")
        sys.exit(1)

def release_lock(lock_fd):
    """释放文件锁"""
    try:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        lock_fd.close()
        if os.path.exists(LOCK_FILE):
            os.unlink(LOCK_FILE)
    except:
        pass

# ============ 成交量数据管理 ============
def get_volume_from_sina(codes):
    """通过新浪财经接口获取今日成交量数据（更稳定）"""
    import requests
    import re
    try:
        prefixed = []
        for code in codes:
            if code.startswith(('5', '6', '7', '9')):
                prefixed.append(f"sh{code}")
            else:
                prefixed.append(f"sz{code}")
        
        url = f'http://hq.sinajs.cn/list={",".join(prefixed)}'
        headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'http://finance.sina.com.cn/'}
        resp = requests.get(url, headers=headers, timeout=10)
        resp.encoding = 'gbk'
        
        volumes = {}
        for line in resp.text.split('\n'):
            m = re.search(r'hq_str_(\w+)="(.+?)"', line)
            if not m:
                continue
            code_raw = m.group(1).replace('sh', '').replace('sz', '')
            content = m.group(2)
            parts = content.split(',')
            if len(parts) >= 9:
                try:
                    # 新浪数据格式: parts[8] = 成交量(股)
                    volume_shares = int(parts[8])
                    volumes[code_raw] = volume_shares
                except:
                    pass
        return volumes
    except Exception as e:
        print(f"[SinaVol] 获取成交量失败: {e}")
        return {}

def get_volume_from_adata(codes):
    """通过adata API获取今日成交量数据（备用）"""
    try:
        cmd = f'/usr/bin/python3.12 /home/yu/.hermes/skills/adata-stock-data/scripts/fetch_data.py realtime {" ".join(codes)}'
        result = subprocess.run(
            ['ssh', '-i', '/home/YDL/.ssh/id_ed25519', '-o', 'StrictHostKeyChecking=no',
             'yu@192.168.31.141', cmd],
            capture_output=True, text=True, timeout=30
        )
        volumes = {}
        for line in result.stdout.strip().split('\n'):
            line = line.strip()  # 去除前后空格
            if line.startswith('stock_code') or not line:
                continue
            parts = line.split()
            if len(parts) >= 6:
                code = parts[0]
                try:
                    volume = int(parts[5])  # volume is at index 5
                    volumes[code] = volume
                except:
                    pass
        return volumes
    except Exception as e:
        print(f"[AdataVol] 获取成交量失败: {e}")
        return {}

def get_5day_avg_volume(codes):
    """通过新浪财经接口获取前5个交易日均量（用于缩量判断，更稳定）"""
    import requests
    import json
    avg_volumes = {}
    try:
        for code in codes:
            try:
                # 新浪财经K线接口
                symbol = f'sz{code}' if not code.startswith(('5', '6', '7', '9')) else f'sh{code}'
                url = f'http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=5'
                resp = requests.get(url, timeout=10)
                data = json.loads(resp.text)
                
                if data and len(data) > 0:
                    # 取最新的5日均量数据（ma_volume5字段）
                    latest = data[-1]
                    ma_vol = float(latest.get('ma_volume5', 0))
                    if ma_vol > 0:
                        avg_volumes[code] = ma_vol
            except Exception as e:
                print(f"[SinaKline] 获取{code}均量失败: {e}")
                continue
    except Exception as e:
        print(f"[SinaKline] 获取均量失败: {e}")
    return avg_volumes

def load_previous_volume():
    """加载昨日成交量数据（兼容旧版本）"""
    if os.path.exists(VOLUME_FILE):
        try:
            with open(VOLUME_FILE, 'r') as f:
                data = json.load(f)
                return data.get('volumes', {})
        except:
            return {}
    return {}

def save_current_volume(volumes):
    """保存今日成交量数据（供明日使用）"""
    try:
        with open(VOLUME_FILE, 'w') as f:
            json.dump({'volumes': volumes, 'date': datetime.now().strftime('%Y-%m-%d')}, f)
    except Exception as e:
        print(f"保存成交量失败: {e}")

def is_volume_contracted(code, today_volume, avg_volume):
    """检查是否缩量：今日成交量 < 5日均量 × 0.8

    today_volume / avg_volume 同时支持标量和 {code: volume} 字典，
    避免调用方传标量时抛 TypeError 导致整个检查循环中断（2026-10-21 修复）。
    """
    if isinstance(avg_volume, dict):
        avg_volume = avg_volume.get(code, 0)
    if isinstance(today_volume, dict):
        today_volume = today_volume.get(code, 0)

    if not avg_volume or avg_volume == 0:
        # 没有均量数据，跳过缩量检查（假设不缩量）
        return True
    if today_volume == 0:
        return False
    # 缩量条件：今日量 < 5日均量 × 0.8（给予20%弹性空间）
    return today_volume < avg_volume * 0.8


# ============ 模拟盘条件单口径（2026-10-28 新增）============

def load_sim_orders():
    """读取模拟盘 pending_orders（带显式 buy_low/buy_high/gates 的条目）

    只读，不写。positions.json 里 condition 是文本，条件是结构化的 gates，
    这里以 gates 为准，缺失 gates 的条目退回 condition 文本不可判定处理。
    """
    try:
        with open(SIM_ORDERS_FILE, 'r', encoding='utf-8') as f:
            d = json.load(f)
    except Exception as e:
        print(f"[模拟盘条件单] 读取失败: {e}")
        return {}
    out = {}
    for o in d.get('pending_orders', []):
        if o.get('status') != 'active_pending':
            continue
        if 'buy_low' not in o or 'buy_high' not in o or not o.get('gates'):
            continue
        out[o['code']] = o
    return out


def load_held_codes():
    """读取模拟盘当前持仓代码集合

    2026-11-09 修复：监控脚本只读 pending_orders，没有排除 positions，
    导致已持仓标的（如 300623 捷捷微电）仍被反复推送「买入提醒」。
    """
    try:
        with open(SIM_ORDERS_FILE, 'r', encoding='utf-8') as f:
            d = json.load(f)
    except Exception as e:
        print(f"[持仓] 读取失败: {e}")
        return set()
    out = set()
    for p in d.get('positions', []) or d.get('holdings', []) or []:
        c = str(p.get('code', '')).strip()
        if c:
            out.add(c)
    return out


# ============ 趋势过滤（2026-11-09 新增）============
# 背景：旧逻辑只有「价格进区间 + 缩量」两个条件，在下跌通道里「回踩+缩量」
# 是最常见的形态，导致 9470 次提醒、0 次成交。必须加趋势门槛拦掉接飞刀信号。
_TREND_CACHE = {}
_TREND_TTL = 900  # 15分钟缓存，避免每30秒都拉K线


def get_trend_info(code):
    """取 MA20 及其斜率，判断是否处于上升趋势

    返回 dict:
      ma20        当前MA20（None=取数失败）
      ma20_slope  MA20的10日变化率（%）
      above_ma20  现价是否站上MA20
      valid       数据是否有效（不足20根K线时为False）
    """
    import requests
    key = code
    now = time.time()
    cached = _TREND_CACHE.get(key)
    if cached and now - cached[0] < _TREND_TTL:
        return cached[1]

    result = {'ma20': None, 'ma20_slope': None, 'above_ma20': False, 'valid': False}
    try:
        symbol = f'sh{code}' if code.startswith(('5', '6', '7', '9')) else f'sz{code}'
        url = (f'http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/'
               f'CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=5')
        resp = requests.get(url, timeout=15)
        data = resp.json()
        closes = [float(x['close']) for x in data]
        if len(closes) >= 30:
            ma20 = sum(closes[-20:]) / 20
            ma20_prev = sum(closes[-30:-10]) / 20
            result['ma20'] = round(ma20, 3)
            result['ma20_slope'] = round((ma20 / ma20_prev - 1) * 100, 2) if ma20_prev > 0 else None
            result['valid'] = True
    except Exception as e:
        print(f"[趋势] 获取{code}失败: {e}")

    _TREND_CACHE[key] = (now, result)
    return result


def check_trend_gate(code, price, label=''):
    """趋势门槛：拒绝接飞刀

    判定规则（2026-11-09）：
      1. 必须站上 MA20 —— 未站上一律拒绝（下行通道中“回踩+缩量”是最常见形态）
      2. 且满足其一：
         a. MA20 斜率 >= 0（均线本身走平/上行，趋势已确认）
         b. 现价高于 MA20 达 5% 以上（均线仍微跌但股价强势收复，属反转初期）

    返回 (成立与否, 说明文字)。取数失败时返回 None（无法判定）。
    """
    t = get_trend_info(code)
    if not t['valid'] or t['ma20'] is None:
        return None, f"{label}趋势维度无法判定（K线数据不足）"
    ma20 = t['ma20']
    slope = t['ma20_slope']
    above = price >= ma20
    dist = (price / ma20 - 1) * 100
    if not above:
        return False, (f"MA20={ma20:.2f}(斜率{slope:+.1f}%) "
                       f"现价{price:.2f}未站上MA20（偏离{dist:+.1f}%）→ 下跌通道，拒绝接飞刀")
    if slope is not None and slope >= 0:
        return True, f"MA20={ma20:.2f} 斜率{slope:+.1f}% 现价{price:.2f}站上MA20（{dist:+.1f}%）→ 趋势确认"
    if dist >= 5.0:
        return True, (f"MA20={ma20:.2f} 斜率{slope:+.1f}% 现价{price:.2f}站上MA20（{dist:+.1f}%）"
                       f"→ 强势收复（超MA20 5%以上）")
    return False, (f"MA20={ma20:.2f} 斜率{slope:+.1f}% 现价{price:.2f}站上MA20但仅{dist:+.1f}% "
                   f"→ 均线仍下行，未确认反转")


def load_latest_zones():
    """从最新一天的候选股评估 JSON 读取买入区间，覆盖硬编码 WATCHLIST

    2026-11-09 修复：WATCHLIST 里的区间是 2026-11-05 收盘重算后写死的，
    到 11-09 已过期 4 天。鼎胜新材当时跌穿下沿（区间 14.75，现价 13.98），
    区间完全失效却还在参与判定 —— 属于用过期数据做决策。
    这里改为每天重新评估后读取最新 JSON，区间随数据滚动。
    """
    base = "/home/YDL/.openclaw/workspace/a_stock_plan/daily"
    try:
        days = sorted([d for d in os.listdir(base) if re.match(r'^\d{4}-\d{2}-\d{2}$', d)], reverse=True)
    except Exception as e:
        print(f"[区间刷新] 读取日目录失败: {e}")
        return {}, None

    for day in days:
        ddir = os.path.join(base, day)
        try:
            files = sorted([f for f in os.listdir(ddir) if f.startswith('候选股评估') and f.endswith('.json')],
                           reverse=True)
        except Exception:
            continue
        for fn in files:
            path = os.path.join(ddir, fn)
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except Exception:
                continue
            zones = {}
            for r in data:
                c = str(r.get('code', ''))
                if not c:
                    continue
                zones[c] = {
                    'buy_low': r.get('buy_low'),
                    'buy_high': r.get('buy_high'),
                    'stop': r.get('stop'),
                    'target_low': r.get('target_low'),
                    'target_high': r.get('target_high'),
                    'note': f"距MA20 {r.get('distance_ma20', 0):+.1f}%",
                    'ma20': r.get('ma20'),
                }
            if zones:
                print(f"[区间刷新] 使用 {day}/{fn} 的 {len(zones)} 条区间")
                return zones, f"{day}/{fn}"
    return {}, None


def get_day_low(code):
    """取当日最低价（腾讯分时序列），失败返回 None"""
    try:
        prefixed = f'sh{code}' if code.startswith(('5', '6', '7', '9')) else f'sz{code}'
        url = f'https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={prefixed}&_var=min_data'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        raw = urllib.request.urlopen(req, timeout=15).read().decode('gbk', errors='ignore')
        j = json.loads(raw[raw.index('{'):])
        node = j['data'][prefixed]
        rl = node['data']['data'] if isinstance(node.get('data'), dict) else node['data']
        lows = [float(b.split()[1]) for b in rl if len(b.split()) >= 2]
        return min(lows) if lows else None
    except Exception as e:
        print(f"[当日最低] {code} 获取失败: {e}")
        return None


def get_same_time_volume_ratio(code):
    """同时点量比 = 今日截至当前分钟的累计量 / 前 N 日同一分钟的累计量均值

    为什么不用「今日总量 / 5日均量」：日内成交呈 U 型分布，开盘早段量占比高，
    用全天量对比全天均量会在早盘严重低估缩量程度。必须同时点对比。
    阈值 0.8：<0.8 视为缩量成立。

    返回 (ratio, detail)；数据不足时返回 (None, detail)。
    """
    now = time.time()
    cached = _SAME_TIME_VOL_CACHE.get(code)
    if cached and now - cached['ts'] < _SAME_TIME_VOL_TTL:
        return cached['ratio'], cached['detail']

    import urllib.request
    detail = ""
    ratio = None
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        prefixed = f'sh{code}' if code.startswith(('5', '6', '7', '9')) else f'sz{code}'
        url = TENCENT_MULTIDAY_URL.format(code=prefixed, date=today)
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        raw = urllib.request.urlopen(req, timeout=20).read().decode('utf-8', errors='ignore')
        j = json.loads(raw[raw.index('{'):])
        node = j['data'][prefixed]['data']
        days = {}
        for day in node:
            cum = 0
            series = {}
            for b in day['data']:
                p = b.split()
                if len(p) >= 3:
                    cum += int(p[2])
                    series[p[0]] = cum
            days[day['date']] = series
        if not days:
            return None, "多日分时无数据"

        today_key = max(days.keys())
        today_series = days[today_key]
        if not today_series:
            return None, "今日分时为空"
        target = max(today_series.keys())

        prev = [days[d][target] for d in sorted(days.keys())
                if d != today_key and target in days[d]]
        tv = today_series[target]
        if not prev or not tv:
            detail = f"样本不足（历史同时点命中 {len(prev)} 个）"
            return None, detail
        avg = sum(prev) / len(prev)
        ratio = tv / avg
        detail = (f"同时点 {target[:2]}:{target[2:]} 分｜今日累计 {tv:,} 手 vs "
                  f"前{len(prev)}日同时点均量 {avg:,.0f} 手｜量比 {ratio:.3f}")
    except Exception as e:
        detail = f"同时点量比获取失败: {e}"

    _SAME_TIME_VOL_CACHE[code] = {'ts': now, 'ratio': ratio, 'detail': detail}
    return ratio, detail


def get_index_drop_pct(index_codes):
    """取指数当日涨跌幅（负值代表下跌），返回 {code: pct}"""
    if not index_codes:
        return {}
    try:
        prefixed = []
        for c in index_codes:
            # 已带前缀的直接用，避免重复拼接（如 szsz399006）
            if c.startswith(('sh', 'sz')):
                prefixed.append(c)
            else:
                prefixed.append(f'sh{c}' if c.startswith(('5', '6', '7', '9')) else f'sz{c}')
        url = f'http://hq.sinajs.cn/list={",".join(prefixed)}'
        import requests
        resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0',
                                          'Referer': 'http://finance.sina.com.cn/'}, timeout=15)
        resp.encoding = 'gbk'
        import re as _re
        out = {}
        for line in resp.text.split('\n'):
            m = _re.search(r'hq_str_(\w+)="(.+?)"', line)
            if not m:
                continue
            code = m.group(1).replace('sh', '').replace('sz', '')
            parts = m.group(2).split(',')
            if len(parts) < 4:
                continue
            try:
                price = float(parts[3])
                prev = float(parts[2])
                if prev > 0:
                    out[code] = round((price - prev) / prev * 100, 3)
            except Exception:
                pass
        return out
    except Exception as e:
        print(f"[指数跌幅] 获取失败: {e}")
        return {}


def evaluate_gate(gate, code, price, day_low):
    """判定单个 gate。返回 (成立与否, 说明文字)

    成立= True / False / None(无法判定)
    """
    gtype = gate.get('type')
    if gtype == 'price':
        lo, hi = gate.get('low'), gate.get('high')
        ok = (lo <= price <= hi) or (day_low is not None and day_low <= hi)
        return ok, f"价格 {price}（当日最低 {day_low}）{'已' if ok else '未'}进入 {lo}-{hi}"
    if gtype == 'volume_contract':
        thr = gate.get('threshold', 0.8)
        ratio, detail = get_same_time_volume_ratio(code)
        if ratio is None:
            return None, f"缩量维度无法判定（{detail}）"
        return ratio < thr, f"缩量：{detail}｜阈值 {thr} → {'成立' if ratio < thr else '不成立'}"
    if gtype == 'market_stable':
        max_drop = gate.get('max_drop_pct', 1.5)
        drops = get_index_drop_pct(gate.get('index_codes', []))
        if not drops:
            return None, "大盘企稳维度无法判定（指数行情缺失）"
        # 要求：任一指数当日跌幅不超过 max_drop%（即最差的那个也不能跌超）
        worst = min(drops.values())
        ok = worst > -max_drop
        detail = "、".join(f"{c} {v:+.2f}%" for c, v in drops.items())
        return ok, f"大盘企稳：{detail}｜门槛 跌幅<{max_drop}% → {'成立' if ok else '不成立'}"
    if gtype == 'index_drop':
        max_drop = gate.get('max_drop_pct', 2.0)
        drops = get_index_drop_pct(gate.get('index_codes', []))
        if not drops:
            return None, "指数跌幅维度无法判定（指数行情缺失）"
        worst = min(drops.values())
        ok = worst > -max_drop
        detail = "、".join(f"{c} {v:+.2f}%" for c, v in drops.items())
        return ok, f"指数门槛：{detail}｜要求跌幅<{max_drop}% → {'成立' if ok else '不成立'}"
    if gtype == 'no_earnings_bomb':
        # 财报维度由风控/复盘批次核验，监控脚本不做财务请求（避免高频拉财报接口）
        return None, "无业绩雷维度由财报批次核验，监控脚本不重复拉取"
    if gtype == 'trend_gate':
        # 2026-11-09 新增：条件单可显式声明趋势门槛，避免「价格进区间+缩量」
        # 在下跌通道里被当作买点（接飞刀）。规则与 check_trend_gate 一致。
        ok, desc = check_trend_gate(code, price)
        return ok, f"趋势门槛：{desc}"
    return None, f"未知门槛类型 {gtype}"



# ============ 配置 ============
FEISHU_BOT_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/fbfd7f01-878c-4ece-80e6-5e7324ab3692"
FEISHU_SECRET = "9vXyEvLigZ70Ynw1YeUtI"

# 自选股池（未建仓，等待买入提醒）
# === 2026-11-05 收盘后重新评估更新 ===
# 候选股买入区间（来自候选股重新评估脚本 2026-11-05 15:30）
# 数据源：a_stock_plan/daily/2026-11-05/候选股评估_20261105_1530.json
WATCHLIST = {
    '002475': {'name': '立讯精密', 'buy_low': 46.39, 'buy_high': 48.32, 'stop': 44.07, 'target_low': 50.73, 'target_high': 52.25, 'note': '距MA20 +7.4%'},
    '300552': {'name': '万集科技', 'buy_low': 18.25, 'buy_high': 20.95, 'stop': 17.34, 'target_low': 21.99, 'target_high': 22.95, 'note': '距MA20 -3.7%'},
    '600552': {'name': '凯盛科技', 'buy_low': 11.71, 'buy_high': 13.62, 'stop': 11.12, 'target_low': 14.3, 'target_high': 15.42, 'note': '距MA20 -2.1%'},
    '300613': {'name': '富瀚微', 'buy_low': 67.27, 'buy_high': 70.7, 'stop': 63.91, 'target_low': 74.24, 'target_high': 76.68, 'note': '距MA20 +4.7%'},
    '603876': {'name': '鼎胜新材', 'buy_low': 14.75, 'buy_high': 15.5, 'stop': 14.01, 'target_low': 16.27, 'target_high': 18.13, 'note': '距MA20 +0.4%'},
    '300623': {'name': '捷捷微电', 'buy_low': 24.76, 'buy_high': 29.04, 'stop': 23.52, 'target_low': 30.49, 'target_high': 31.96, 'note': '距MA20 -3.1%'},
    '002407': {'name': '多氟多', 'buy_low': 21.47, 'buy_high': 25.43, 'stop': 20.39, 'target_low': 26.7, 'target_high': 30.0, 'note': '距MA20 -2.8%'},
    '603379': {'name': '三美股份', 'buy_low': 47.02, 'buy_high': 48.43, 'stop': 44.67, 'target_low': 50.85, 'target_high': 53.02, 'note': '距MA20 +14.4%'},
    # 2026-11-09 新增：光伏ETF（华泰柏瑞中证光伏产业）
    # 区间锚定 MA20 与30日区间，不随现价漂移；趋势门槛由条件单 gates 显式声明
    '515790': {'name': '光伏ETF华', 'buy_low': 0.7095, 'buy_high': 0.7395, 'stop': 0.674, 'target_low': 0.78, 'target_high': 0.825, 'note': 'MA20=0.718 斜率-5.3% 待确认反转'},
}

# ============ 已移除个股持仓监控 ============
# 长江电力、鼎胜新材的止损/止盈监控已集成到 auto_stock_alert.py
# 本脚本现在专注于ETF接回监控

# ============ 函数 ============

def get_realtime(codes):
    """获取实时数据（新浪接口）"""
    import requests
    import re
    prefixed = []
    for code in codes:
        if code.startswith(('5', '6', '7', '9')):
            prefixed.append(f"sh{code}")
        else:
            prefixed.append(f"sz{code}")
    
    url = f'http://hq.sinajs.cn/list={",".join(prefixed)}'
    headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'http://finance.sina.com.cn/'}
    
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.encoding = 'gbk'
    except:
        return {}
    
    results = {}
    for line in resp.text.split('\n'):
        m = re.search(r'hq_str_(\w+)="(.+?)"', line)
        if not m:
            continue
        code_raw = m.group(1).replace('sh', '').replace('sz', '')
        content = m.group(2)
        parts = content.split(',')
        if len(parts) < 10:
            continue
        try:
            price = float(parts[3]) if parts[3] != '0' else 0
            prev_close = float(parts[2]) if parts[2] else 0
            if prev_close > 0:
                pct = (price - prev_close) / prev_close * 100
            else:
                pct = 0
            results[code_raw] = {'price': price, 'pct': pct}
        except:
            pass
    return results

def send_alert(msg):
    """发送飞书提醒（带去重）"""
    import hashlib
    import hmac
    import base64
    import urllib.request
    import json
    
    # 去重检查：同一个消息30分钟内不重复发送
# 2026-11-09：原为 5 分钟，导致每 5 分钟推一批、每批 4-5 条，实际刷屏。
# 拉长到 30 分钟，与风控检查节奏对齐。
    now = time.time()
    msg_hash = hashlib.md5(msg.encode()).hexdigest()
    cache_key = f'/tmp/watchlist_alert_cache_{msg_hash}'
    
    try:
        if os.path.exists(cache_key):
            last_time = float(open(cache_key).read().strip())
            if now - last_time < ALERT_DEDUP_SEC:  # 默认30分钟内不重复
                return
        open(cache_key, 'w').write(str(now))
        # 清理过期缓存（24小时前）
        for f in os.listdir('/tmp'):
            if f.startswith('watchlist_alert_cache_') and (now - os.path.getmtime(f'/tmp/{f}') > 86400):
                try:
                    os.unlink(f'/tmp/{f}')
                except:
                    pass
    except:
        pass
    
    try:
        timestamp = str(int(now))
        string_to_sign = timestamp + '\n' + FEISHU_SECRET
        sign = base64.b64encode(hmac.new(string_to_sign.encode(), digestmod=hashlib.sha256).digest()).decode()

        payload = {
            "msg_type": "text",
            "content": {"text": msg},
            "timestamp": timestamp,
            "sign": sign
        }
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            FEISHU_BOT_URL,
            data=data,
            headers={'Content-Type': 'application/json'}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode('utf-8')
        # 2026-10-23 修复：不校验响应体时，飞书返回 code=19021（签名失败）也会被当成发送成功，
        # 买入提醒会静默丢失。现在必须 code==0 才算成功。
        try:
            parsed = json.loads(body)
        except Exception:
            print(f"❌ 发送失败(响应无法解析): {body[:200]}")
            return
        if parsed.get('code') != 0:
            print(f"❌ 发送失败(飞书返回错误): code={parsed.get('code')} msg={parsed.get('msg')}")
            return
        print(f"✅ 提醒已投递: {body[:120]}")
    except Exception as e:
        print(f"发送失败: {e}")

def is_trading_hours():
    """检查是否在交易时间内"""
    from datetime import time as dt_time
    now = datetime.now()
    
    if now.weekday() >= 5:
        return False
    
    current_time = now.time()
    morning_start = dt_time(9, 15)
    morning_end = dt_time(11, 30)
    afternoon_start = dt_time(13, 0)
    afternoon_end = dt_time(15, 0)
    
    in_morning = morning_start <= current_time <= morning_end
    in_afternoon = afternoon_start <= current_time <= afternoon_end
    
    return in_morning or in_afternoon

def check_watchlist():
    """检查候选股是否进入买入区间，触发缩量确认提醒

    2026-10-28：采用模拟盘口径（pending_orders.gates）判定。
    - 模拟盘有条件单的标的：按 gates 全部门槛判定，全部成立才提醒
    - 无模拟盘条件单的标的：沿用技术扫描区 + 缩量确认（保持原有行为）
    """
    codes = list(WATCHLIST.keys())
    quotes = get_realtime(codes)
    sim_orders = load_sim_orders()
    # 2026-11-09 修复：排除已持仓标的，避免「持仓中还在推买入提醒」
    held = load_held_codes()
    if held:
        print(f"[持仓排除] 本轮跳过已持仓: {', '.join(sorted(held))}")

    # 2026-11-09 修复：用最新评估 JSON 覆盖硬编码区间（区间随数据滚动，避免过期）
    zones, zone_src = load_latest_zones()
    if zones:
        for c, z in zones.items():
            if c in WATCHLIST and z.get('buy_low') and z.get('buy_high'):
                WATCHLIST[c].update({
                    'buy_low': z['buy_low'], 'buy_high': z['buy_high'],
                    'stop': z.get('stop', WATCHLIST[c].get('stop')),
                    'target_low': z.get('target_low', WATCHLIST[c].get('target_low')),
                    'target_high': z.get('target_high', WATCHLIST[c].get('target_high')),
                    'note': z.get('note', ''),
                })
        print(f"[区间刷新] 已用 {zone_src} 覆盖 {len(zones)} 条买入区间")
    else:
        print("[区间刷新] 未找到评估 JSON，沿用硬编码区间（可能已过期）")

    # 获取成交量数据（仅用于无模拟盘条件单的标的）
    try:
        today_vol = get_volume_from_sina(codes)
        avg_vol = get_5day_avg_volume(codes)
    except Exception as e:
        print(f"[成交量] 获取失败: {e}")
        today_vol, avg_vol = {}, {}

    alerts = []
    for code, info in WATCHLIST.items():
        if code not in quotes:
            continue
        price = quotes[code]['price']
        name = info['name']

        # 2026-11-09 修复：已持仓标的不再推买入提醒
        if code in held:
            print(f"[已持仓跳过] {name}({code}) 现价{price}")
            continue

        # ============ 路径 A：模拟盘有条件单 → 按 gates 判定 ============
        # 条件单以自身 gates 为唯一裁决口径（含 trend_gate），不再叠加全局趋势门槛，
        # 否则条件单在趋势未反转时会永远等不到成交。技能 entry-band-construction 亦要求
        # 「让趋势门槛决定 dips 是否算入场」，即由条件单显式声明，而非脚本全局拦截。
        if code in sim_orders:
            order = sim_orders[code]
            day_low = info.get('day_low')
            # 模拟盘口径的当日最低价（新浪实时接口未提供，用分时补齐）
            if day_low is None:
                day_low = get_day_low(code)
            results = []
            all_ok = True
            for gate in order['gates']:
                ok, desc = evaluate_gate(gate, code, price, day_low)
                results.append((gate.get('desc', gate.get('type')), ok, desc))
                if ok is not True:
                    all_ok = False
            if not all_ok:
                def _label(ok):
                    if ok is True:
                        return "成立"
                    if ok is False:
                        return "不成立"
                    return "无法判定"
                pending_desc = "；".join(
                    f"{n}→{_label(ok)}" for n, ok, _ in results)
                print(f"[模拟盘条件未满足] {name}({code}) 现价{price}｜{pending_desc}")
                continue
            detail_lines = "\n".join(f"  ✅ {n}：{d}" for n, _, d in results)
            alert = (f"🎯模拟盘买入条件全数成立\n"
                     f"{name}({code}) 现价{price:.2f}元\n"
                     f"模拟盘买入区间: {order['buy_low']}-{order['buy_high']}\n\n"
                     f"{detail_lines}\n"
                     f"备注: 取消线{order.get('cancel_below', '未设')} "
                     f"止损{order.get('stop_loss', info.get('stop'))} "
                     f"目标{order.get('take_profit_1', info.get('target_low'))}-"
                     f"{order.get('take_profit_2', info.get('target_high'))}")
            alerts.append((code, alert))
            print(f"\n{'='*50}\n{alert}\n{'='*50}")
            continue

        # ============ 路径 B：无模拟盘条件单 → 原技术扫描逻辑 ============
        buy_low = info['buy_low']
        buy_high = info['buy_high']

        if not (buy_low <= price <= buy_high):
            continue

        # 2026-11-09：路径B 同样需要趋势门槛（路径A 由条件单自带 gates 裁决）
        # 下跌通道中「回踩+缩量」是最常见形态，无趋势过滤会持续误报接飞刀信号
        trend_ok, trend_desc = check_trend_gate(code, price, name)
        if trend_ok is False:
            print(f"[趋势不通过] {name}({code}) 现价{price}｜{trend_desc}")
            continue

        stop = info['stop']
        target_low = info['target_low']
        target_high = info['target_high']
        note = info.get('note', '')

        today_v = today_vol.get(code, 0)
        avg_v = avg_vol.get(code, 0)

        if today_v == 0:
            print(f"[数据缺失] {name} 价格到位但无法获取今日成交量，暂不提醒")
            continue

        contracted = is_volume_contracted(code, today_v, avg_v)
        vol_str = f"{today_v/10000:.1f}万" if today_v > 0 else "未知"
        avg_str = f"{avg_v/10000:.1f}万" if avg_v > 0 else "未知"

        if not contracted:
            print(f"[条件未满足] {name} 价格到位但未缩量 成交量: {vol_str} (5日均量{avg_str}) - 未缩量")
            continue

        alert = (f"🎯买入提醒(缩量确认)\n"
                 f"{name}({code}) 现价{price:.2f}元\n"
                 f"已进入买入区间: {buy_low}-{buy_high}\n\n"
                 f"成交量: {vol_str} (5日均量{avg_str})\n"
                 f"备注: 止损{stop} 目标{target_low}-{target_high} {note}")
        alerts.append((code, alert))
        print(f"\n{'='*50}\n{alert}\n{'='*50}")

    return alerts

def main():
    print("="*60)
    print("📋 候选股买入信号监控脚本启动")
    print("="*60)
    print(f"监控标的: {len(WATCHLIST)} 只")
    for code, info in WATCHLIST.items():
        print(f"  {code} {info['name']} 买入区间:{info['buy_low']}-{info['buy_high']}")
    print("="*60)
    print("规则: 价格进入买入区间 + 缩量确认才推送")
    print()
    
    last_alert_time = {}
    
    while True:
        try:
            now = datetime.now()
            
            # 非交易时间跳过
            if not is_trading_hours():
                time.sleep(60)
                continue
            
            alerts = check_watchlist()
            
            for code, alert in alerts:
                if code in last_alert_time:
                    if (now - last_alert_time[code]).seconds < ALERT_DEDUP_SEC:
                        continue
                
                print(f"\n{'='*50}")
                print(alert)
                print(f"{'='*50}")
                send_alert(alert)
                last_alert_time[code] = now
            
            # 没有触发时：完全静默，不打印任何内容
            
        except Exception as e:
            print(f"错误: {e}")
        
        time.sleep(30)

if __name__ == "__main__":
    lock_fd = acquire_lock()
    
    try:
        main()
    except KeyboardInterrupt:
        print("\n停止监控")
    finally:
        release_lock(lock_fd)
        if os.path.exists(PID_FILE):
            os.unlink(PID_FILE)