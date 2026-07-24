#!/usr/bin/env python3
"""
Tushare Pro Replay API 测试脚本
镜像站: https://ai-tool.indevs.in
API Key: huanghanchi (咸鱼买的)

测试目标:
1. limit_list_d  - 打板池（涨停板）
2. moneyflow_hsgt - 北向资金
3. ths_member     - 同花顺板块成分
4. daily          - 实时行情（拿一只票做基础验证）

记录：响应时间、数据质量、字段完整度、错误情况
"""

import requests
import time
import json
from datetime import datetime

BASE_URL = "https://ai-tool.indevs.in/tushare/pro"
API_KEY = "huanghanchi"

def call_api(api_name, params=None, fields=None, method="GET", timeout=15):
    """统一调用入口"""
    headers = {"X-API-Key": API_KEY}
    start = time.time()

    if method == "GET":
        url = f"{BASE_URL}/{api_name}"
        if params:
            from urllib.parse import urlencode
            url += "?" + urlencode({k: (",".join(v) if isinstance(v, list) else v) for k, v in params.items()})
        if fields:
            from urllib.parse import urlencode
            sep = "&" if params else "?"
            url += sep + urlencode({"fields": fields})
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
        except Exception as e:
            return {"error": f"网络异常: {e}", "elapsed_ms": int((time.time()-start)*1000)}

    elif method == "POST":
        url = BASE_URL
        payload = {"api_name": api_name, "params": params or {}, "fields": fields}
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        except Exception as e:
            return {"error": f"网络异常: {e}", "elapsed_ms": int((time.time()-start)*1000)}

    elapsed = int((time.time() - start) * 1000)

    if resp.status_code != 200:
        return {
            "api": api_name,
            "http_status": resp.status_code,
            "elapsed_ms": elapsed,
            "error": f"HTTP {resp.status_code}",
            "text": resp.text[:500]
        }

    try:
        data = resp.json()
    except Exception as e:
        return {"api": api_name, "elapsed_ms": elapsed, "error": f"JSON 解析失败: {e}", "text": resp.text[:500]}

    payload = data.get("data") if isinstance(data.get("data"), dict) else None
    fields = (payload or {}).get("fields") if payload else None
    items = (payload or {}).get("items") if payload else (data.get("data") if isinstance(data.get("data"), list) else [])

    return {
        "api": api_name,
        "http_status": resp.status_code,
        "elapsed_ms": elapsed,
        "code": data.get("code"),
        "msg": data.get("msg"),
        "request_id": data.get("request_id"),
        "fields": fields,
        "items_count": len(items or []),
        "sample_items": (items or [])[:3]
    }


def main():
    print("=" * 70)
    print(f"Tushare Pro Replay 测试  时间: {datetime.now().isoformat()}")
    print(f"镜像站: {BASE_URL}")
    print("=" * 70)

    tests = [
        # (name, api, params, fields, method)
        ("测试1: daily - 平安银行基础行情", "daily",
         {"ts_code": "000001.SZ", "start_date": "20260720", "end_date": "20260724"},
         "ts_code,trade_date,open,high,low,close,vol,amount",
         "GET"),

        ("测试2: limit_list_d - 涨停板池", "limit_list_d",
         {"trade_date": "20260724"},
         "ts_code,name,industry,close,pct_chg,amount,limit_amount,fd_amount,first_time,last_time",
         "GET"),

        ("测试3: moneyflow_hsgt - 北向资金", "moneyflow_hsgt",
         {"trade_date": "20260724"},
         "trade_date,hgt,sgt,north_money,south_money",
         "GET"),

        ("测试4: ths_index - 同花顺板块列表", "ths_index",
         {"exchange": "A", "type": "N"},
         "ts_code,name,count,exchange,list_date,type",
         "GET"),

        ("测试5: ths_member - 同花顺板块成分(人工智能)", "ths_member",
         {"ts_code": "885573.TI"},
         "ts_code,con_code,name,weight,in_date,is_new",
         "GET"),

        ("测试6: moneyflow - 个股资金流(贵州茅台)", "moneyflow",
         {"ts_code": "600519.SH", "start_date": "20260720", "end_date": "20260724"},
         "ts_code,trade_date,buy_sm_vol,buy_md_vol,buy_lg_vol,sell_sm_vol,sell_md_vol,sell_lg_vol",
         "GET"),

        ("测试7: top_list - 龙虎榜(今日)", "top_list",
         {"trade_date": "20260724"},
         "ts_code,name,close,pct_chinr,turnover_rate,amount,buy_amount,sell_amount",
         "GET"),

        ("测试8: margin - 融资融券汇总", "margin",
         {"trade_date": "20260724"},
         "trade_date,exchange,rzye,rzmre,rzche,rqye,rqyl,rqmcl",
         "GET"),
    ]

    results = []
    for name, api, params, fields, method in tests:
        print(f"\n>>> {name}")
        result = call_api(api, params, fields, method)
        result["test_name"] = name
        results.append(result)

        # 打印
        if "error" in result:
            print(f"   ❌ 错误: {result['error']}")
            if "text" in result:
                print(f"   原文: {result['text'][:300]}")
        else:
            print(f"   ✅ HTTP {result['http_status']} | {result['elapsed_ms']}ms")
            print(f"   code={result['code']} msg={result['msg']}")
            print(f"   fields={result['fields']}")
            print(f"   items_count={result['items_count']}")
            if result.get('sample_items'):
                print(f"   sample={result['sample_items'][0]}")

    # 汇总
    print("\n" + "=" * 70)
    print("📊 测试汇总")
    print("=" * 70)
    for r in results:
        if "error" in r:
            print(f"❌ {r['test_name'][:40]} 错误: {r['error']}")
        else:
            print(f"✅ {r['test_name'][:40]:40s} {r['elapsed_ms']:>5}ms  rows={r['items_count']:>5}  code={r['code']}")

    # 写日志
    log_path = "/home/YDL/.openclaw/workspace/logs/tushare_replay_test.log"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"\n=== {datetime.now().isoformat()} ===\n")
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    print(f"\n📝 详细日志: {log_path}")


if __name__ == "__main__":
    main()