"""Build `data/astock.db`: an investment-research snapshot of 28 A-share companies.

v2 module, additive by design. The main benchmark (`data/learning_platform.db`)
stays untouched; this database is a *committed artifact* so CI and offline
reviewers never need network access — exactly the trade `scripts/bird_judge.py`
made for BIRD.

Data source: Eastmoney public JSON endpoints for the three statements and the
market snapshot, Tencent's fqkline endpoint for daily bars, fetched over
`httpx` (already a dependency — no new packages enter pyproject for a one-time
build).  An earlier plan also wanted CSDC pledge ratios (`RPT_CSDC_LIST`); that
report is now empty for every filter we tried, so the pledge table was dropped
rather than faked. Eastmoney's kline host in turn started dropping our
connections outright mid-build — hence Tencent for bars. Both decays are
recorded here so the next reader does not rediscover them.

Tables (all money in yuan, as the API returns it; questions convert to 亿元):
  companies          28 rows, code / names / exchange / sector
  balance_sheets     14 report dates x 28 companies (goodwill, equity, ...)
  income_statements  same periods (revenue, parent_net_profit, ...)
  cashflow_statements same periods (op/invest/financing cashflow, capex)
  daily_quotes       2025-01 .. fetch day, forward-adjusted bars (close-derived
                     pct_chg; no amount/turnover — Tencent does not serve them)
  market_snapshot    one row per company as of the fetch moment

Reproducibility contract: re-running this script AFTER a later reporting season
changes the database; tasks built from the old snapshot then break their "gold
executes non-empty" guarantee. Therefore the task builder pins the digest of the
database file it was built against, and `tests/test_astock_tasks.py` refuses a
mismatch. Rebuild tasks together with the database or not at all.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "astock.db"

HEADERS = {"User-Agent": "Mozilla/5.0 (fin_fetch.py; sql-agent-lab v2 build)"}
SLEEP_S = 0.35  # be polite to a free endpoint we do not pay for

# (code, secid_prefix, short_name). Financial firms excluded on purpose: banks and
# brokers use a different statement layout (companyType), which would break one
# uniform schema. Short names are pinned here rather than scraped: the database is
# the artifact, and a deterministic build beats a third network dependency.
UNIVERSE: list[tuple[str, str, str]] = [
    ("600110", "1.", "诺德股份"), ("600519", "1.", "贵州茅台"), ("600900", "1.", "长江电力"),
    ("600276", "1.", "恒瑞医药"), ("688981", "1.", "中芯国际"), ("600887", "1.", "伊利股份"),
    ("601012", "1.", "隆基绿能"), ("600690", "1.", "海尔智家"), ("603288", "1.", "海天味业"),
    ("600031", "1.", "三一重工"), ("688111", "1.", "金山办公"), ("600584", "1.", "长电科技"),
    ("601899", "1.", "紫金矿业"), ("601127", "1.", "赛力斯"),
    ("301236", "0.", "软通动力"), ("000516", "0.", "国际医学"), ("002812", "0.", "恩捷股份"),
    ("001211", "0.", "双枪科技"), ("000858", "0.", "五粮液"), ("300750", "0.", "宁德时代"),
    ("002594", "0.", "比亚迪"), ("000333", "0.", "美的集团"), ("002230", "0.", "科大讯飞"),
    ("000651", "0.", "格力电器"), ("002714", "0.", "牧原股份"), ("300760", "0.", "迈瑞医疗"),
    ("002475", "0.", "立讯精密"), ("000725", "0.", "京东方A"),
]

SECTORS = {
    "600110": "有色金属", "600519": "食品饮料", "600900": "公用事业", "600276": "医药生物",
    "688981": "电子", "600887": "食品饮料", "601012": "电力设备", "600690": "家用电器",
    "603288": "食品饮料", "600031": "机械设备", "688111": "计算机", "600584": "电子",
    "601899": "有色金属", "601127": "汽车", "301236": "计算机", "000516": "医药生物",
    "002812": "电力设备", "001211": "轻工制造", "000858": "食品饮料", "300750": "电力设备",
    "002594": "汽车", "000333": "家用电器", "002230": "计算机", "000651": "家用电器",
    "002714": "农林牧渔", "300760": "医药生物", "002475": "电子", "000725": "电子",
}

STATEMENT_PERIODS = [  # 14 report dates, 2023A .. 2026H1, fetched in chunks of 5
    "2023-03-31", "2023-06-30", "2023-09-30", "2023-12-31",
    "2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31",
    "2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31",
    "2026-03-31", "2026-06-30",
]
# Deliberately NOT in the database (the abstention families depend on these
# absences being real, not simulated): 2026-09-30 (Q3 not yet disclosed at
# snapshot time), 2022-12-31 and earlier (database starts at 2023).

QUOTE_BEG, QUOTE_END = "20250101", "20260926"

BALANCE_COLS = {
    "MONETARYFUNDS": "monetary_funds", "ACCOUNTS_RECE": "accounts_receivable",
    "INVENTORY": "inventory", "TOTAL_CURRENT_ASSETS": "total_current_assets",
    "FIXED_ASSET": "fixed_assets", "GOODWILL": "goodwill",
    "TOTAL_ASSETS": "total_assets", "ACCOUNTS_PAYABLE": "accounts_payable",
    "TOTAL_CURRENT_LIAB": "total_current_liabilities",
    "TOTAL_LIABILITIES": "total_liabilities", "TOTAL_PARENT_EQUITY": "total_parent_equity",
}
INCOME_COLS = {
    "TOTAL_OPERATE_INCOME": "total_revenue", "OPERATE_COST": "operating_cost",
    "OPERATE_PROFIT": "operating_profit", "TOTAL_PROFIT": "total_profit",
    "NETPROFIT": "net_profit", "PARENT_NETPROFIT": "parent_net_profit",
    "DEDUCT_PARENT_NETPROFIT": "deducted_parent_np", "BASIC_EPS": "basic_eps",
}
CASHFLOW_COLS = {
    "NETCASH_OPERATE": "op_cashflow", "NETCASH_INVEST": "invest_cashflow",
    "NETCASH_FINANCE": "financing_cashflow",
    "CONSTRUCT_LONG_ASSET": "capex_payment", "END_CCE": "ending_cash",
}


def _get(url: str, params: dict) -> dict:
    """One fresh connection per request.

    A shared client's keep-alive pool got poisoned mid-build: eastmoney silently
    closes pooled sockets after a burst, and every retry over the dead socket
    failed with `RemoteProtocolError` while a fresh connection worked. New
    client per request costs a TLS handshake - irrelevant at ~300 requests.
    """
    for attempt in range(3):
        try:
            with httpx.Client(headers=HEADERS, timeout=20) as c:
                r = c.get(url, params=params)
            if r.status_code == 200:
                return r.json()
        except httpx.HTTPError:
            pass
        time.sleep(1.5 * (attempt + 1))
    raise SystemExit(f"fetch failed after retries: {url} {params.get('code', '')}")


def company_type(secid: tuple[str, str]) -> str:
    """A minority of companies answer companyType=4 with an empty table; probe down."""
    for ct in ("4", "3", "2", "1"):
        code, prefix = secid
        url = "https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/zcfzbAjaxNew"
        params = {"companyType": ct, "reportDateType": "0", "reportType": "1",
                  "dates": "2025-12-31", "code": f"{'SH' if prefix == '1.' else 'SZ'}{code}"}
        data = _get(url, params)
        if data.get("data"):
            return ct
        time.sleep(SLEEP_S)
    return "4"


def fetch_all_statements(secid: tuple[str, str], ct: str) -> dict[str, dict]:
    code, prefix = secid
    out: dict[str, dict] = {}
    for kind, cols in (("zcfzb", BALANCE_COLS), ("lrb", INCOME_COLS), ("xjllb", CASHFLOW_COLS)):
        url = f"https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/{kind}AjaxNew"
        for i in range(0, len(STATEMENT_PERIODS), 5):
            params = {"companyType": ct, "reportDateType": "0", "reportType": "1",
                      "dates": ",".join(STATEMENT_PERIODS[i:i + 5]),
                      "code": f"{'SH' if prefix == '1.' else 'SZ'}{code}"}
            for row in _get(url, params).get("data") or []:
                rd = str(row.get("REPORT_DATE", ""))[:10]
                if not rd:
                    continue
                slot = out.setdefault(rd, {})
                for src, dst in cols.items():
                    slot[dst] = row.get(src)
            time.sleep(SLEEP_S)
    return out


def fetch_quotes(secid: str) -> list[dict]:
    """Daily bars from Tencent's fqkline endpoint (forward-adjusted).

    Eastmoney's kline host (push2his) started dropping our connections mid-build -
    an unannotated, IP-level refusal with no error body. Tencent serves the same
    bars with fewer columns, so `daily_quotes` carries no amount/turnover fields:
    `pct_chg` is derived here from consecutive closes (first row NULL), and that
    derivation is part of this file, not of any question's gold SQL.
    """
    code, prefix = secid[:2]
    symbol = f"{'sh' if prefix == '1.' else 'sz'}{code}"
    data = _get("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get", {
        "param": f"{symbol},day,{QUOTE_BEG[:4]}-01-01,{QUOTE_END[:4]}-12-31,800,qfq",
    })["data"][symbol]
    raw = data.get("qfqday") or data.get("day") or []
    out = []
    prev_close: float | None = None
    for p in raw:
        close = float(p[2])
        out.append({"trade_date": p[0], "open": float(p[1]), "close": close,
                    "high": float(p[3]), "low": float(p[4]), "volume_hand": int(float(p[5])),
                    "pct_chg": round((close - prev_close) / prev_close * 100, 4) if prev_close else None})
        prev_close = close
    return out


def fetch_snapshot(secid: str) -> dict | None:
    code, prefix = secid
    try:
        data = _get("https://push2.eastmoney.com/api/qt/stock/get", {
            "secid": f"{prefix}{code}",
            "invt": "2", "fltt": "2",
            "fields": "f43,f57,f58,f116,f117,f162,f167,f86",
        })["data"] or {}
    except SystemExit:
        return None  # snapshot is optional; questions degrade gracefully without it
    if not data:
        return None
    return {"close": data.get("f43"), "total_mv_yuan": data.get("f116"),
            "circ_mv_yuan": data.get("f117"), "pe_ttm": data.get("f162"),
            "pb": data.get("f167"), "trade_date": time.strftime("%Y-%m-%d", time.localtime(data.get("f86", 0)))}


def build_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.executescript("""
    DROP TABLE IF EXISTS companies; DROP TABLE IF EXISTS balance_sheets;
    DROP TABLE IF EXISTS income_statements; DROP TABLE IF EXISTS cashflow_statements;
    DROP TABLE IF EXISTS daily_quotes; DROP TABLE IF EXISTS market_snapshot;
    CREATE TABLE companies (
        code TEXT PRIMARY KEY, short_name TEXT NOT NULL, exchange TEXT NOT NULL, sector TEXT NOT NULL);
    CREATE TABLE balance_sheets (
        code TEXT NOT NULL REFERENCES companies(code), report_date TEXT NOT NULL,
        monetary_funds REAL, accounts_receivable REAL, inventory REAL,
        total_current_assets REAL, fixed_assets REAL, goodwill REAL,
        total_assets REAL, accounts_payable REAL, total_current_liabilities REAL,
        total_liabilities REAL, total_parent_equity REAL,
        PRIMARY KEY (code, report_date));
    CREATE TABLE income_statements (
        code TEXT NOT NULL REFERENCES companies(code), report_date TEXT NOT NULL,
        total_revenue REAL, operating_cost REAL, operating_profit REAL,
        total_profit REAL, net_profit REAL, parent_net_profit REAL,
        deducted_parent_np REAL, basic_eps REAL,
        PRIMARY KEY (code, report_date));
    CREATE TABLE cashflow_statements (
        code TEXT NOT NULL REFERENCES companies(code), report_date TEXT NOT NULL,
        op_cashflow REAL, invest_cashflow REAL, financing_cashflow REAL,
        capex_payment REAL, ending_cash REAL,
        PRIMARY KEY (code, report_date));
    CREATE TABLE daily_quotes (
        code TEXT NOT NULL REFERENCES companies(code), trade_date TEXT NOT NULL,
        open REAL, close REAL, high REAL, low REAL, volume_hand INTEGER,
        pct_chg REAL,
        PRIMARY KEY (code, trade_date));
    CREATE TABLE market_snapshot (
        code TEXT PRIMARY KEY REFERENCES companies(code), trade_date TEXT,
        close REAL, total_mv_yuan REAL, circ_mv_yuan REAL, pe_ttm REAL, pb REAL);
    CREATE INDEX idx_quotes_date ON daily_quotes(trade_date);
    """)

    for code, prefix, name in UNIVERSE:
        secid = (code, prefix)
        ct = company_type(secid)
        stmt = fetch_all_statements(secid, ct)
        quotes = fetch_quotes(secid)
        snap = fetch_snapshot(secid)
        cur.execute("INSERT INTO companies VALUES (?,?,?,?)",
                    (code, name, "SH" if prefix == "1." else "SZ", SECTORS.get(code, "")))
        for rd, vals in sorted(stmt.items()):
            if any(k in vals for k in BALANCE_COLS.values()):
                cur.execute(f"INSERT OR REPLACE INTO balance_sheets VALUES ({','.join('?'*13)})",
                            [code, rd] + [vals.get(c) for c in BALANCE_COLS.values()])
            if any(k in vals for k in INCOME_COLS.values()):
                cur.execute(f"INSERT OR REPLACE INTO income_statements VALUES ({','.join('?'*10)})",
                            [code, rd] + [vals.get(c) for c in INCOME_COLS.values()])
            if any(k in vals for k in CASHFLOW_COLS.values()):
                cur.execute(f"INSERT OR REPLACE INTO cashflow_statements VALUES ({','.join('?'*7)})",
                            [code, rd] + [vals.get(c) for c in CASHFLOW_COLS.values()])
        for q in quotes:
            cur.execute(f"INSERT OR REPLACE INTO daily_quotes VALUES ({','.join('?'*8)})",
                        [code] + [q[c] for c in ("trade_date", "open", "close", "high", "low",
                                                 "volume_hand", "pct_chg")])
        if snap:
            cur.execute("INSERT OR REPLACE INTO market_snapshot VALUES (?,?,?,?,?,?,?)",
                        [code] + [snap[c] for c in ("trade_date", "close", "total_mv_yuan",
                                                    "circ_mv_yuan", "pe_ttm", "pb")])
            n_periods = sum(1 for v in stmt.values() if "total_revenue" in v)
            print(f"  {code} {name}: {n_periods} periods, {len(quotes)} bars, "
                  f"snapshot={'yes' if snap else 'no'}", flush=True)

    conn.commit()
    n = {t: cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
         for t in ("companies", "balance_sheets", "income_statements", "cashflow_statements",
                   "daily_quotes", "market_snapshot")}
    print("row counts:", json.dumps(n))
    conn.close()


if __name__ == "__main__":
    print(f"building {DB_PATH}")
    build_db()
    print("done")
