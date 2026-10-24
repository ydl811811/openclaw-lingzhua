#!/usr/bin/env python3
"""
Tushare Pro Replay API 统一客户端
=================================
镜像站: https://ai-tool.indevs.in
API Key: huanghanchi（咸鱼买的）

设计目标:
1. 单一入口 - 所有调用都过这个客户端
2. 自动适配 data 是 dict / list 的两种返回格式
3. 内置缓存 - 减少重复请求
4. 内置重试 - 网络抖动自动恢复
5. 内置限频 - 避免触发反爬
6. 统一日志 - 便于排查问题

使用方法:
    from tushare_client import TushareClient
    
    cli = TushareClient()
    
    # 调用任意接口
    df = cli.daily(ts_code='000001.SZ', start_date='20260101', end_date='20260110')
    
    # 调用打板池
    df = cli.limit_list_d(trade_date='20260115')
    
    # 调用北向资金
    df = cli.moneyflow_hsgt(trade_date='20260115')
"""

import os
import json
import time
import logging
import hashlib
from pathlib import Path
from datetime import datetime, timedelta
from urllib.parse import urlencode
from typing import Any, Dict, List, Optional, Union
from functools import wraps

import requests
import pandas as pd

# ============== 配置 ==============
BASE_URL = "https://ai-tool.indevs.in/tushare/pro"
# Key 池（老大 2026-08-17 给的两个 key，旧的 huanghanchi 已失效）
# 优先使用环境变量 TUSHARE_RELAY_KEY，否则从下面按顺序轮询。
#
# 2026-10-24 修正：池顺序原为 [b64c6ee7, e58c1ffe]，但实测
#   b64c6ee7 → HTTP 403 permission_denied（该 key 权限已过期）
#   e58c1ffe → code=0 正常返回日线
# 客户端默认取 pool[0]，导致 daily 等接口全线 403，且失败被当成
# "无数据"缓存 1 小时（24 个 daily 缓存有 22 个是空的）。
# 现把可用 key 提到首位，并加 403 自动轮换，见 _request。
API_KEY_POOL = [
    "e58c1ffea3ad1fc14801b4516c3c5a7314fba16160c990e0ab0151df",  # Key 1（2026-10-24 实测可用）
    "b64c6ee7023944c8e6502ce1e95b6c356d70027c14e74a7778720ce0",  # Key 2（2026-10-24 实测 403，保留备用）
]
DEFAULT_API_KEY = os.environ.get("TUSHARE_RELAY_KEY") or API_KEY_POOL[0]
CACHE_DIR = Path("/home/YDL/.openclaw/workspace/cache_temp/tushare_replay")
CACHE_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = Path("/home/YDL/.openclaw/workspace/logs/tushare_client.log")

# 请求限频：每秒最多 3 次
RATE_LIMIT_INTERVAL = 0.4  # 秒
_last_call_time = 0.0


# ============== 日志 ==============
def _setup_logger():
    logger = logging.getLogger("tushare_client")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    return logger


log = _setup_logger()


# ============== 缓存装饰器 ==============
def _cache_key(api_name: str, params: Dict, fields: Optional[str]) -> str:
    payload = json.dumps({"api": api_name, "params": params, "fields": fields}, sort_keys=True)
    return hashlib.md5(payload.encode()).hexdigest()


def cache(ttl_seconds: int = 3600):
    """缓存装饰器，ttl_seconds 默认 1 小时"""
    def decorator(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            cache_ttl = kwargs.pop("__cache_ttl", ttl_seconds)
            cache_key = _cache_key(func.__name__, kwargs, None)
            cache_path = CACHE_DIR / f"{func.__name__}_{cache_key}.json"

            # 命中缓存
            if cache_path.exists():
                mtime = cache_path.stat().st_mtime
                if time.time() - mtime < cache_ttl:
                    try:
                        with open(cache_path, "r", encoding="utf-8") as f:
                            cached = json.load(f)
                        return pd.DataFrame(cached)
                    except Exception:
                        pass  # 缓存损坏就走实际请求

            # 实际调用
            result = func(self, *args, **kwargs)

            # 写缓存
            # 2026-10-24 修正：失败时 result 是空 DataFrame（_request 兜底返回
            # code=-1 + items=[]），原先照样写缓存，等于把"接口失败"伪装成
            # "该标的无数据"缓存 1 小时，排障时极具误导性。
            # 现在：空结果一律不落盘，并清理可能已存在的空缓存。
            try:
                records = result.to_dict(orient="records")
                if not records:
                    if cache_path.exists():
                        cache_path.unlink()
                    log.warning(f"{func.__name__} 返回空结果，不写缓存（避免失败伪装成无数据）")
                else:
                    with open(cache_path, "w", encoding="utf-8") as f:
                        json.dump(records, f, ensure_ascii=False, default=str)
            except Exception as e:
                log.warning(f"缓存写入失败: {e}")

            return result
        return wrapper
    return decorator


# ============== 主客户端 ==============
class TushareClient:
    """Tushare Pro Replay 统一客户端"""

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, timeout: int = 20):
        self.api_key = api_key or DEFAULT_API_KEY
        self.base_url = base_url or BASE_URL
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"X-API-Key": self.api_key})
        log.info(f"TushareClient 初始化 key={self.api_key[:10]}...{self.api_key[-4:]}")

    # ---------- 底层调用 ----------
    def _rate_limit(self):
        """全局限频"""
        global _last_call_time
        elapsed = time.time() - _last_call_time
        if elapsed < RATE_LIMIT_INTERVAL:
            time.sleep(RATE_LIMIT_INTERVAL - elapsed)
        _last_call_time = time.time()

    def _rotate_key(self, api_name: str, reason: str) -> bool:
        """当前 key 不可用时，轮换到 key 池里的下一个。返回是否成功切换。"""
        current = self.api_key
        for key in API_KEY_POOL:
            if key != current:
                log.warning(f"{api_name} 当前 key 不可用({reason[:60]})，轮换到 {key[:10]}...{key[-4:]}")
                self.api_key = key
                self.session.headers.update({"X-API-Key": key})
                return True
        log.error(f"{api_name} key 池全部不可用")
        return False

    def _request(self, api_name: str, params: Optional[Dict] = None, fields: Optional[str] = None,
                 max_retries: int = 3) -> Dict:
        """底层 HTTP 调用（带重试 + 限频 + IP 限额识别）"""
        self._rate_limit()

        # 序列化参数（list → 逗号分隔）
        clean_params = {}
        if params:
            for k, v in params.items():
                if isinstance(v, list):
                    clean_params[k] = ",".join(str(x) for x in v)
                elif v is not None and v != "":
                    clean_params[k] = str(v)

        if fields:
            clean_params["fields"] = fields

        url = f"{self.base_url}/{api_name}"
        if clean_params:
            url += "?" + urlencode(clean_params)

        last_err = None
        for attempt in range(1, max_retries + 1):
            try:
                start = time.time()
                resp = self.session.get(url, timeout=self.timeout)
                elapsed_ms = int((time.time() - start) * 1000)

                if resp.status_code != 200:
                    # 2026-10-24 修正：403 通常是"该 key 权限过期"，
                    # 换池里下一个 key 重试，而不是把同一个坏 key 重试 3 次。
                    if resp.status_code == 403 and self._rotate_key(api_name, resp.text[:120]):
                        attempt -= 1  # 换 key 不算重试次数
                        continue
                    raise Exception(f"HTTP {resp.status_code}: {resp.text[:200]}")

                data = resp.json()

                # ok 字段显式 false（咸鱼接口风格）
                if data.get("ok") is False:
                    err = data.get("error", "")
                    msg = data.get("message", "")
                    lease = data.get("lease_seconds", 0)
                    # IP 限额 → 等 lease 秒再重试
                    if "active_ip_limit" in err:
                        log.warning(f"{api_name} IP 限额，等 {lease}s 后重试 (attempt {attempt}/{max_retries})")
                        time.sleep(lease + 1)
                        continue
                    # invalid_api_key → 立即终止（key 失效，无重试意义）
                    if "invalid_api_key" in err:
                        log.error(f"{api_name} API Key 无效: {msg}")
                        return {"api": api_name, "code": -1, "msg": msg, "items": []}
                    # permission_denied → 该 key 权限过期，换下一个 key
                    if "permission_denied" in err and self._rotate_key(api_name, msg[:120]):
                        attempt -= 1
                        continue
                    raise Exception(f"业务错误 err={err} msg={msg}")

                # code 非 0 视为业务错误
                code = data.get("code", 0)
                if code != 0 and code is not None:
                    msg = data.get("msg", "")
                    # 503 freshness 不达标 - 不算错，跳过重试
                    if code == 503 or "freshness" in str(msg).lower():
                        log.warning(f"{api_name} freshness 不达标: {msg}")
                        return {"api": api_name, "code": code, "msg": msg, "items": []}
                    raise Exception(f"业务错误 code={code} msg={msg}")

                # 适配 data 是 dict / list
                payload = data.get("data")
                if isinstance(payload, dict):
                    items = payload.get("items") or []
                    fields_out = payload.get("fields")
                elif isinstance(payload, list):
                    items = payload
                    fields_out = None
                else:
                    items = []
                    fields_out = None

                log.info(f"{api_name} ✅ {elapsed_ms}ms rows={len(items)}")
                return {
                    "api": api_name,
                    "code": code,
                    "msg": data.get("msg"),
                    "request_id": data.get("request_id"),
                    "fields": fields_out,
                    "items": items,
                    "elapsed_ms": elapsed_ms,
                }

            except Exception as e:
                last_err = e
                wait = 2 ** (attempt - 1)  # 1s, 2s, 4s
                log.warning(f"{api_name} 第 {attempt} 次失败: {e}，{wait}s 后重试")
                time.sleep(wait)

        log.error(f"{api_name} 重试 {max_retries} 次仍失败: {last_err}")
        return {"api": api_name, "code": -1, "msg": str(last_err), "items": []}

    def _to_df(self, result: Dict) -> pd.DataFrame:
        """结果转 DataFrame"""
        items = result.get("items", [])
        fields = result.get("fields")
        if not items:
            return pd.DataFrame()

        df = pd.DataFrame(items)

        # 字段命名（fields 是 list 时用，否则用 items 里的 key）
        if fields and len(fields) == len(df.columns):
            df.columns = fields

        return df

    # ---------- 股票基础信息 ----------
    @cache(ttl_seconds=86400)  # 1 天
    def stock_basic(self, **kwargs) -> pd.DataFrame:
        """股票基础信息"""
        result = self._request("stock_basic", kwargs)
        return self._to_df(result)

    @cache(ttl_seconds=86400)
    def trade_cal(self, **kwargs) -> pd.DataFrame:
        """交易日历"""
        result = self._request("trade_cal", kwargs)
        return self._to_df(result)

    @cache(ttl_seconds=86400)
    def namechange(self, **kwargs) -> pd.DataFrame:
        """股票曾用名"""
        result = self._request("namechange", kwargs)
        return self._to_df(result)

    # ---------- 行情数据 ----------
    @cache(ttl_seconds=3600)
    def daily(self, **kwargs) -> pd.DataFrame:
        """A 股日线行情"""
        result = self._request("daily", kwargs)
        return self._to_df(result)

    @cache(ttl_seconds=86400)
    def adj_factor(self, **kwargs) -> pd.DataFrame:
        """复权因子"""
        result = self._request("adj_factor", kwargs)
        return self._to_df(result)

    @cache(ttl_seconds=86400)
    def suspend(self, **kwargs) -> pd.DataFrame:
        """停复牌"""
        result = self._request("suspend", kwargs)
        return self._to_df(result)

    # ---------- 资金流向 ----------
    @cache(ttl_seconds=3600)
    def moneyflow(self, **kwargs) -> pd.DataFrame:
        """个股资金流"""
        result = self._request("moneyflow", kwargs)
        return self._to_df(result)

    @cache(ttl_seconds=3600)
    def moneyflow_hsgt(self, **kwargs) -> pd.DataFrame:
        """北向/南向资金"""
        result = self._request("moneyflow_hsgt", kwargs)
        return self._to_df(result)

    @cache(ttl_seconds=3600)
    def hsgt_top10(self, **kwargs) -> pd.DataFrame:
        """北向资金 Top10"""
        result = self._request("hsgt_top10", kwargs)
        return self._to_df(result)

    # ---------- 融资融券 ----------
    @cache(ttl_seconds=3600)
    def margin(self, **kwargs) -> pd.DataFrame:
        """融资融券汇总"""
        result = self._request("margin", kwargs)
        return self._to_df(result)

    @cache(ttl_seconds=3600)
    def margin_detail(self, **kwargs) -> pd.DataFrame:
        """融资融券明细"""
        result = self._request("margin_detail", kwargs)
        return self._to_df(result)

    # ---------- 打板/涨停 ----------
    @cache(ttl_seconds=3600)
    def limit_list_d(self, **kwargs) -> pd.DataFrame:
        """涨停板池（每日）"""
        result = self._request("limit_list_d", kwargs)
        return self._to_df(result)

    @cache(ttl_seconds=3600)
    def limit_list(self, **kwargs) -> pd.DataFrame:
        """涨停板池（实时）"""
        result = self._request("limit_list", kwargs)
        return self._to_df(result)

    @cache(ttl_seconds=3600)
    def limit_step(self, **kwargs) -> pd.DataFrame:
        """连板天梯"""
        result = self._request("limit_step", kwargs)
        return self._to_df(result)

    @cache(ttl_seconds=3600)
    def top_list(self, **kwargs) -> pd.DataFrame:
        """龙虎榜"""
        result = self._request("top_list", kwargs)
        return self._to_df(result)

    @cache(ttl_seconds=3600)
    def top_inst(self, **kwargs) -> pd.DataFrame:
        """龙虎榜机构"""
        result = self._request("top_inst", kwargs)
        return self._to_df(result)

    @cache(ttl_seconds=3600)
    def kpl_list(self, **kwargs) -> pd.DataFrame:
        """开盘啦榜单"""
        result = self._request("kpl_list", kwargs)
        return self._to_df(result)

    @cache(ttl_seconds=3600)
    def hm_list(self, **kwargs) -> pd.DataFrame:
        """高手清单"""
        result = self._request("hm_list", kwargs)
        return self._to_df(result)

    # ---------- 同花顺板块 ----------
    @cache(ttl_seconds=86400)
    def ths_index(self, **kwargs) -> pd.DataFrame:
        """同花顺板块列表"""
        result = self._request("ths_index", kwargs)
        return self._to_df(result)

    @cache(ttl_seconds=86400)
    def ths_member(self, **kwargs) -> pd.DataFrame:
        """同花顺板块成分"""
        result = self._request("ths_member", kwargs)
        return self._to_df(result)

    # ---------- 指数 ----------
    @cache(ttl_seconds=3600)
    def index_basic(self, **kwargs) -> pd.DataFrame:
        """指数基础信息"""
        result = self._request("index_basic", kwargs)
        return self._to_df(result)

    @cache(ttl_seconds=3600)
    def index_daily(self, **kwargs) -> pd.DataFrame:
        """指数日线"""
        result = self._request("index_daily", kwargs)
        return self._to_df(result)

    # ---------- 财务三大表 ----------
    @cache(ttl_seconds=86400)
    def income(self, **kwargs) -> pd.DataFrame:
        """利润表"""
        result = self._request("income", kwargs)
        return self._to_df(result)

    @cache(ttl_seconds=86400)
    def balancesheet(self, **kwargs) -> pd.DataFrame:
        """资产负债表"""
        result = self._request("balancesheet", kwargs)
        return self._to_df(result)

    @cache(ttl_seconds=86400)
    def cashflow(self, **kwargs) -> pd.DataFrame:
        """现金流量表"""
        result = self._request("cashflow", kwargs)
        return self._to_df(result)

    @cache(ttl_seconds=86400)
    def fina_indicator(self, **kwargs) -> pd.DataFrame:
        """财务指标"""
        result = self._request("fina_indicator", kwargs)
        return self._to_df(result)

    # ---------- 期货 / 期权 / ETF ----------
    @cache(ttl_seconds=3600)
    def fut_basic(self, **kwargs) -> pd.DataFrame:
        """期货基础信息"""
        result = self._request("fut_basic", kwargs)
        return self._to_df(result)

    @cache(ttl_seconds=3600)
    def fund_basic(self, **kwargs) -> pd.DataFrame:
        """基金基础信息"""
        result = self._request("fund_basic", kwargs)
        return self._to_df(result)

    @cache(ttl_seconds=3600)
    def fund_daily(self, **kwargs) -> pd.DataFrame:
        """基金日线"""
        result = self._request("fund_daily", kwargs)
        return self._to_df(result)


# ============== 工具函数 ==============
def get_client() -> TushareClient:
    """全局单例"""
    global _client_instance
    if "_client_instance" not in globals():
        globals()["_client_instance"] = TushareClient()
    return globals()["_client_instance"]


def clear_cache(api_name: Optional[str] = None):
    """清理缓存"""
    if api_name:
        for f in CACHE_DIR.glob(f"{api_name}_*.json"):
            f.unlink()
        log.info(f"清理缓存: {api_name} ({len(list(CACHE_DIR.glob(f'{api_name}_*.json')))} 个文件)")
    else:
        count = 0
        for f in CACHE_DIR.glob("*.json"):
            f.unlink()
            count += 1
        log.info(f"清理全部缓存: {count} 个文件")


# ============== 单元测试 ==============
if __name__ == "__main__":
    cli = TushareClient()

    print("=" * 60)
    print(f"Tushare Pro Replay 客户端测试  {datetime.now().isoformat()}")
    print("=" * 60)

    today = datetime.now().strftime("%Y%m%d")

    # 1. 北向资金
    print("\n>>> 1. moneyflow_hsgt - 北向资金")
    df = cli.moneyflow_hsgt(trade_date=today)
    print(df.head())
    if not df.empty:
        print(f"   北向净流入: {df.iloc[0].get('north_money', 'N/A')} 万")

    # 2. 涨停池
    print("\n>>> 2. limit_list_d - 涨停池")
    df = cli.limit_list_d(trade_date=today)
    print(f"   涨停数量: {len(df)}")
    if not df.empty:
        print(df.head(3))

    # 3. 龙虎榜
    print("\n>>> 3. top_list - 龙虎榜")
    df = cli.top_list(trade_date=today)
    print(f"   龙虎榜数量: {len(df)}")

    # 4. 同花顺板块
    print("\n>>> 4. ths_index - 同花顺板块")
    df = cli.ths_index(exchange="A", type="N")
    print(f"   板块数量: {len(df)}")
    if not df.empty:
        print(df.head(3))

    # 5. 个股行情
    print("\n>>> 5. daily - 个股日线")
    df = cli.daily(ts_code="000001.SZ", start_date="20260720", end_date=today)
    print(f"   行情行数: {len(df)}")
    if not df.empty:
        print(df.head())

    # 6. 个股资金流
    print("\n>>> 6. moneyflow - 个股资金流")
    df = cli.moneyflow(ts_code="600519.SH", start_date="20260720", end_date=today)
    print(f"   资金流行数: {len(df)}")

    print("\n✅ 测试完成，缓存目录:", CACHE_DIR)