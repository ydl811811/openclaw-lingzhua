#!/usr/bin/env python3
"""
模拟盘自动执行器
================
- 读取 a_stock_plan/simulated/positions.json（唯一账本）
- 读取 a_stock_plan/simulated/directives.json（灵爪写下的执行指令）
- 按指令执行模拟买卖，同步更新账本 + 交易日志 + 飞书通知
- 由 systemd timer 每日 09:30 / 13:30 触发，也可手动跑

指令格式（directives.json）：
{
  "directives": [
    {
      "id": "20261028_002049_force_exit",
      "code": "002049",
      "action": "sell_all",              # sell_all / buy
      "reason": "业绩雷无条件清仓",
      "shares": 200,                     # sell_all 可省略
      "max_price": null,                 # 卖出限价保护（null=市价）
      "min_price": null,                 # 买入限价保护
      "valid_until": "2026-10-28T15:00:00+08:00",
      "status": "pending"
    }
  ]
}
"""
import os
import sys
import json
import subprocess
import hashlib
import hmac
import base64
import urllib.request
import time
from datetime import datetime
from pathlib import Path

WORKSPACE = "/home/YDL/.openclaw/workspace"
SIM_DIR = Path(f"{WORKSPACE}/a_stock_plan/simulated")
POSITIONS = Path(os.environ.get("SIM_POSITIONS", SIM_DIR / "positions.json"))
DIRECTIVES = Path(os.environ.get("SIM_DIRECTIVES", SIM_DIR / "directives.json"))
TRADE_LOG_DIR = Path(os.environ.get("SIM_LOG_DIR", SIM_DIR))
PER_NAME_CAP = 10000.0
FEISHU_BOT_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/fbfd7f01-878c-4ece-80e6-5e7324ab3692"
FEISHU_SECRET = "9vXyEvLigZ70Ynw1YeUtI"
SSH = [
    "ssh", "-i", "/home/YDL/.ssh/id_ed25519",
    "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
    "yu@192.168.31.141",
]
QUOTE_SCRIPT = "/home/yu/.hermes/scripts/stock_quote.py"
FEE_RATE = 0.00025      # 佣金 万2.5
MIN_FEE = 5.0
STAMP_TAX = 0.0005      # 卖出印花税 万5


def log(msg):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    try:
        with open(f"{WORKSPACE}/logs/sim_executor.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def market_prefix(code):
    return f"sh{code}" if code.startswith(("5", "6", "7", "9")) else f"sz{code}"


def fetch_quotes(codes):
    """拉实时行情，返回 {code: {price, pct}}"""
    if not codes:
        return {}
    args = SSH + [f"/usr/bin/python3.12 {QUOTE_SCRIPT} realtime " + " ".join(market_prefix(c) for c in codes)]
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=25).stdout
    except Exception as e:
        log(f"行情拉取失败: {e}")
        return {}
    quotes = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[1].isdigit():
            try:
                quotes[parts[1]] = {"price": float(parts[2]), "pct": float(parts[3].replace("%", ""))}
            except ValueError:
                continue
    return quotes


def send_feishu(title, lines):
    """发飞书消息（需签名，code==0 才算成功）。SIM_DRY_RUN=1 时只落盘不真发。"""
    text = f"**{title}**\n\n" + "\n".join(lines)
    if os.environ.get("SIM_DRY_RUN") == "1":
        log(f"[DRY_RUN] 跳过飞书投递，内容:\n{text}")
        try:
            with open(f"{WORKSPACE}/logs/sim_executor_dryrun.log", "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().isoformat()}] {text}\n")
        except Exception:
            pass
        return True
    try:
        ts = str(int(time.time()))
        string_to_sign = ts + "\n" + FEISHU_SECRET
        sign = base64.b64encode(
            hmac.new(string_to_sign.encode(), digestmod=hashlib.sha256).digest()
        ).decode()
        payload = {
            "msg_type": "text",
            "content": {"text": text},
            "timestamp": ts,
            "sign": sign,
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            FEISHU_BOT_URL, data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            res = json.loads(r.read().decode())
        ok = res.get("code") == 0 or res.get("StatusCode") == 0
        log(f"飞书投递 {'✅' if ok else '❌'} {res}")
        if not ok:
            with open(f"{WORKSPACE}/logs/sim_executor_notify_fail.log", "a", encoding="utf-8") as f:
                f.write(f"[{ts}] {res}\n{text}\n")
        return ok
    except Exception as e:
        log(f"飞书投递失败: {e}")
        try:
            with open(f"{WORKSPACE}/logs/sim_executor_notify_fail.log", "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().isoformat()}] {e}\n{text}\n")
        except Exception:
            pass
        return False


def calc_fees(amount, side):
    """手续费：佣金双边万2.5最低5元；卖出加印花税万5"""
    commission = max(amount * FEE_RATE, MIN_FEE)
    tax = amount * STAMP_TAX if side == "sell" else 0.0
    return round(commission + tax, 2)


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def load_positions(path=None, default=None):
    return load_json(path or POSITIONS, default)


def save_positions(d):
    Path(POSITIONS).write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def write_trade_log(entry_md, date_str):
    p = TRADE_LOG_DIR / f"trade_log_{date_str}.md"
    if p.exists():
        with p.open("a", encoding="utf-8") as f:
            f.write(entry_md)
    else:
        p.write_text(f"# 🎯 模拟盘交易日志 — {date_str}\n" + entry_md, encoding="utf-8")


def reconcile():
    """对账：市值、现金、总额、整手、单只上限"""
    d = load_positions(POSITIONS, None)
    if not d:
        return ["账本读取失败"]
    errs = []
    cap = d["capital"]
    fees = d.get("fees", 0.0)
    realized = d.get("realized_pnl", 0.0)
    tc = tv = 0.0
    for p in d["positions"]:
        if abs(p["shares"] * p["close"] - p["market_value"]) > 1:
            errs.append(f"{p['code']} 市值不符")
        if p["shares"] % 100 != 0:
            errs.append(f"{p['code']} 非整手 {p['shares']}")
        spent = p["shares"] * p["cost"]
        if spent > PER_NAME_CAP:
            errs.append(f"{p['code']} 成本 ¥{spent:,.0f} 超单只上限 ¥{PER_NAME_CAP:,.0f}")
        tc += p["shares"] * p["cost"]
        tv += p["market_value"]
    cash = d["cash"]
    expected = cap - tc - fees + realized
    if abs(cash - expected) > 1:
        errs.append(f"现金不符：记录 {cash:.2f} vs 计算 {expected:.2f}")
    if abs(d["total_asset"] - (cash + tv)) > 1:
        errs.append("总资产不符")
    return errs


def execute():
    d = load_positions(POSITIONS, None)
    if not d:
        log("账本不存在，退出")
        return

    dirs = load_json(DIRECTIVES, {"directives": []})
    # 过滤 pending：not_before未到 + valid_until过期
    raw_pending = [x for x in dirs.get("directives", []) if x.get("status") == "pending"]
    pending = []
    now = datetime.now().replace(tzinfo=None)
    for x in raw_pending:
        nb = x.get("not_before")
        if nb:
            try:
                nb_dt = datetime.fromisoformat(nb)
                if nb_dt.tzinfo:
                    nb_dt = nb_dt.replace(tzinfo=None)
                if nb_dt > now:
                    continue
            except Exception:
                pass
        vu = x.get("valid_until")
        if vu:
            try:
                vu_dt = datetime.fromisoformat(vu)
                if vu_dt.tzinfo:
                    vu_dt = vu_dt.replace(tzinfo=None)
                if vu_dt < now:
                    log(f"指令 {x['id']} 已过期({vu})，标记 expired")
                    x["status"] = "expired"
                    continue
            except Exception:
                pass
        pending.append(x)
    if not pending:
        log("无待执行指令（仍执行自动止损/止盈检查）")

    # 需要行情的标的：显式指令涉及的 + 所有有止损/止盈的持仓
    codes_needed = set()
    for x in pending:
        if x["action"] == "sell_all":
            for p in d["positions"]:
                if p["code"] == x["code"]:
                    codes_needed.add(x["code"])
        elif x["action"] == "buy":
            codes_needed.add(x["code"])

    # 自动止损/止盈：持仓现价触及 stop_loss 即自动清仓（无需显式指令）
    for p in d["positions"]:
        if p.get("stop_loss") or p.get("take_profit_1") or p.get("take_profit_2"):
            codes_needed.add(p["code"])

    # 大盘企稳门槛需要指数行情
    for x in pending:
        if x["action"] == "buy" and x.get("max_index_drop"):
            codes_needed.update(x.get("index_codes", ["sh000001", "sz399006", "sh000688"]))

    quotes = fetch_quotes(sorted(codes_needed))
    if pending or os.environ.get("SIM_VERBOSE") == "1":
        log(f"行情快照: {quotes}")

    date_str = datetime.now().strftime("%Y-%m-%d")
    md = f"\n---\n\n## 🤖 自动执行 {datetime.now().strftime('%H:%M:%S')}\n\n"
    notify = []

    # 自动止损检查（风控优先于显式指令）
    for p in list(d["positions"]):
        sl = p.get("stop_loss")
        if not sl:
            continue
        px = quotes.get(p["code"], {}).get("price")
        if px is None:
            continue
        if px <= sl:
            fee = calc_fees(p["shares"] * px, "sell")
            gross = (px - p["cost"]) * p["shares"]
            log(f"🔴 自动止损触发: {p['code']} {p['name']} 现价 {px} <= 止损 {sl}")
            d["positions"] = [q for q in d["positions"] if q["code"] != p["code"]]
            d.setdefault("realized_pnl", 0.0)
            d["realized_pnl"] = round(d["realized_pnl"] + gross, 2)
            d["fees"] = round(d.get("fees", 0.0) + fee, 2)
            d["cash"] = round(d["cash"] + p["shares"] * px - fee, 2)
            d.setdefault("auto_stop_log", []).append({
                "code": p["code"], "name": p["name"], "price": px, "stop_loss": sl,
                "shares": p["shares"], "gross_pnl": round(gross, 2), "fee": fee,
                "at": datetime.now().isoformat()
            })
            tv0 = sum(q["shares"] * q["close"] for q in d["positions"])
            d["total_asset"] = round(d["cash"] + tv0, 2)
            d["total_return_pct"] = round((d["total_asset"] / d["capital"] - 1) * 100, 2)
            md += (f"### 🔴 自动止损 {p['code']} {p['name']}\n"
                   f"- 现价 ¥{px:.2f} <= 止损 ¥{sl}\n- 数量 {p['shares']}股\n"
                   f"- 金额 ¥{p['shares']*px:,.2f}\n- 手续费 ¥{fee:.2f}\n"
                   f"- 盈亏 ¥{gross-fee:+,.2f} ({(px/p['cost']-1)*100:+.2f}%)\n\n")
            notify.append(f"🔴 自动止损 {p['code']} {p['name']} {p['shares']}股 @¥{px:.2f}｜盈亏 ¥{gross-fee:+,.0f}（止损位¥{sl}）")

    for x in pending:
        # 未到执行时间（not_before）则跳过
        nb = x.get("not_before")
        if nb:
            try:
                if datetime.fromisoformat(nb) > datetime.now():
                    continue
            except Exception:
                pass
        # 过期指令不执行
        vu = x.get("valid_until")
        if vu:
            try:
                if datetime.fromisoformat(vu) < datetime.now():
                    log(f"指令 {x['id']} 已过期({vu})，标记 expired")
                    x["status"] = "expired"
                    continue
            except Exception:
                pass
        code = x["code"]
        act = x["action"]
        pos = next((p for p in d["positions"] if p["code"] == code), None)
        px = quotes.get(code, {}).get("price")

        if act == "sell_all":
            if not pos:
                log(f"跳过 {code}: 无持仓")
                x["status"] = "skipped"
                continue
            if px is None:
                log(f"跳过 {code}: 无行情")
                continue
            maxp = x.get("max_price")
            if maxp and px > maxp:
                log(f"跳过 {code}: 现价 {px} > 限价 {maxp}")
                continue
            shares = pos["shares"]
            amount = shares * px
            fee = calc_fees(amount, "sell")
            pnl_gross = amount - shares * pos["cost"]
            pnl_net = pnl_gross - fee
            cash_in = amount - fee
            d["cash"] = round(d["cash"] + cash_in, 2)
            d["positions"] = [p for p in d["positions"] if p["code"] != code]
            d["fees"] = round(d.get("fees", 0.0) + fee, 2)
            d["realized_pnl"] = round(d.get("realized_pnl", 0.0) + pnl_gross, 2)
            tv = sum(p["shares"] * p["close"] for p in d["positions"])
            d["total_asset"] = round(d["cash"] + tv, 2)
            d["total_return_pct"] = round((d["total_asset"] / d["capital"] - 1) * 100, 2)
            d["as_of"] = datetime.now().isoformat()

            x["status"] = "executed"
            x["executed_at"] = datetime.now().isoformat()
            x["executed_price"] = px

            md += (f"### 卖出 {code} {pos['name']}（自动执行 · {x['reason']}）\n"
                   f"- 数量: {shares}股\n- 成交价: ¥{px:.2f}\n- 金额: ¥{amount:,.2f}\n"
                   f"- 手续费: ¥{fee:.2f}\n- 盈亏: ¥{pnl_net:+,.2f} ({(px/pos['cost']-1)*100:+.2f}%)\n\n")
            notify.append(f"🔻 卖出 {code} {pos['name']} {shares}股 @¥{px:.2f}｜盈亏 ¥{pnl_net:+,.0f}（{(px/pos['cost']-1)*100:+.1f}%）")

        elif act == "buy":
            if pos:
                log(f"跳过 {code}: 已有持仓")
                x["status"] = "skipped"
                continue
            if px is None:
                log(f"跳过 {code}: 无行情")
                continue
            budget = x.get("budget", 5000)
            maxp = x.get("max_price")
            minp = x.get("min_price")
            if maxp and px > maxp:
                log(f"跳过 {code}: 现价 {px} > 上限 {maxp}")
                continue
            if minp and px < minp:
                log(f"跳过 {code}: 现价 {px} < 下限 {minp}")
                continue
            shares = int(budget / px / 100) * 100
            if shares <= 0:
                log(f"跳过 {code}: 预算不足一手")
                continue
            amount = shares * px
            fee = calc_fees(amount, "buy")
            if amount + fee > d["cash"]:
                shares = int((d["cash"] - fee) / px / 100) * 100
                if shares <= 0:
                    log(f"跳过 {code}: 现金不足")
                    continue
                amount = shares * px
                fee = calc_fees(amount, "buy")
            d["cash"] = round(d["cash"] - amount - fee, 2)
            d["fees"] = round(d.get("fees", 0.0) + fee, 2)
            d.setdefault("realized_pnl", 0.0)
            d["positions"].append({
                "code": code, "name": x.get("name", code), "rail": x.get("rail", "长线"),
                "shares": shares, "cost": round(px, 3), "close": round(px, 3),
                "pnl_pct": 0.0, "market_value": round(amount, 2),
                "stop_loss": x.get("stop_loss"), "take_profit_1": x.get("take_profit_1"),
                "take_profit_2": x.get("take_profit_2"), "note": x.get("reason", ""),
            })
            tv = sum(p["shares"] * p["close"] for p in d["positions"])
            d["total_asset"] = round(d["cash"] + tv, 2)
            d["total_return_pct"] = round((d["total_asset"] / d["capital"] - 1) * 100, 2)
            d["as_of"] = datetime.now().isoformat()

            x["status"] = "executed"
            x["executed_at"] = datetime.now().isoformat()
            x["executed_price"] = px

            md += (f"### 买入 {code} {x.get('name','')}（自动执行 · {x['reason']}）\n"
                   f"- 数量: {shares}股\n- 成交价: ¥{px:.2f}\n- 金额: ¥{amount:,.2f}\n"
                   f"- 手续费: ¥{fee:.2f}\n- 止损: ¥{x.get('stop_loss')}\n- 目标: ¥{x.get('take_profit_1')}\n\n")
            notify.append(f"🔼 买入 {code} {x.get('name','')} {shares}股 @¥{px:.2f}｜金额 ¥{amount:,.0f}")

    save_positions(d)
    Path(DIRECTIVES).write_text(json.dumps(dirs, ensure_ascii=False, indent=2), encoding="utf-8")
    if md.strip() != f"\n---\n\n## 🤖 自动执行 {datetime.now().strftime('%H:%M:%S')}\n\n":
        write_trade_log(md, date_str)

    errs = reconcile()
    if errs:
        log(f"⚠️ 对账异常: {errs}")

    if notify:
        notify.append(f"💰 总资产 ¥{d['total_asset']:,.2f}｜收益率 {d['total_return_pct']:+.2f}%｜现金 ¥{d['cash']:,.2f}")
        send_feishu("🤖 模拟盘自动执行", notify)
    log(f"执行完成，交易通知 {len(notify)} 条")
    return len(notify)


def watch_loop(interval=15):
    """常驻模式：每 interval 秒扫描一次持仓，止损/止盈秒级响应"""
    import fcntl
    lock_path = "/tmp/sim_trade_executor.lock"
    lock_fd = open(lock_path, "w")
    try:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError):
        log("已有执行器实例在运行，watch 退出")
        return
    lock_fd.write(str(os.getpid()))
    lock_fd.flush()
    log(f"常驻监控启动 PID {os.getpid()}，扫描间隔 {interval}s")
    while True:
        try:
            execute()
        except Exception as e:
            log(f"watch 轮询异常: {e}")
        time.sleep(interval)


if __name__ == "__main__":
    # 安全门：非systemd环境必须显式设 SIM_LIVE=1 才能真写
    if os.environ.get("SIM_LIVE") != "1":
        print("⚠️ 无 SIM_LIVE=1，以只读模式运行（不写账本、不飞书）")
        sys.exit(0)
    if "--watch" in sys.argv:
        watch_loop()
    else:
        execute()
