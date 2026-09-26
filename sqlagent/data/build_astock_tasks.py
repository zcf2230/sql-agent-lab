"""Generate `data/tasks_finance.jsonl`: the investment-research (投研取数) task set.

Same generator discipline as `build_tasks.py`, with one addition the finance
data forces: an **absence dimension**. A-share report periods are cumulative and
disclosed on a fixed calendar, so "2026 年三季报的营收" is not a hard question —
it is a question whose correct behaviour is to *check and refuse*, and the most
dangerous real-world failure is a model that quietly answers it with the latest
available quarter instead. Those tasks carry `expect_absent: true` and are
graded by `sqlagent/eval/absence.py`, not by `scoring.py`.

Rules inherited from the main generator (each enforced in code, not in prose):
  1. every gold SQL executes against the real database
  2. value pools are read out of the database, never typed by hand
  3. an answerable gold returning zero rows is dropped as `vacuous`
  4. a top-k that ties at the LIMIT boundary is dropped as `ambiguous_topk`

Finance-specific rules:
  5. report periods are cumulative; year-over-year questions always compare the
     *same period type* across years. No 环比 questions exist for that reason,
     and the reason is written here rather than discovered by a model.
  6. every question states its unit and formula in the text (亿元, 小数比值).
     A gold that depends on an unstated convention is an unfair question.
  7. absence gold SQL must itself return zero rows — it is the *proof* that the
     asked-for data is not in the snapshot, checked at build time.

Question style is Chinese on purpose: A-share research is done in Chinese, and
the terminology (归母净利润, 扣非, 净现比) is part of what is being tested.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = ROOT / "data" / "astock.db"
OUT = ROOT / "data" / "tasks_finance.jsonl"
DROPPED = ROOT / "data" / "tasks_finance_dropped.jsonl"

if not DB_PATH.exists():
    raise SystemExit("no database - run: python scripts/fin_fetch.py first")

_CONN = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
_CONN.row_factory = sqlite3.Row

PERIOD_LABELS = {  # 报告期 -> question label; cumulative statements, same-type YoY only
    "03-31": "一季报", "06-30": "半年报", "09-30": "三季报", "12-31": "年报",
}
PERIODS = [f"{y}-{md}" for y in (2023, 2024, 2025) for md in PERIOD_LABELS] + ["2026-03-31", "2026-06-30"]

OUTSIDE_COMPANIES = ["分众传媒", "顺丰控股", "海康威视", "中国中免"]  # deliberately not in the universe

tasks: list[dict] = []
dropped: list[dict] = []


def add(category: str, difficulty: str, question: str, gold_sql: str, *,
        expect_absent: bool = False, require_order: bool = False, gold_tables: list[str] | None = None):
    try:
        rows = _CONN.execute(gold_sql).fetchall()
    except sqlite3.Error as exc:
        dropped.append({"category": category, "question": question, "reason": "gold_broken", "detail": str(exc)})
        return
    if expect_absent:
        if rows:
            dropped.append({"category": category, "question": question,
                            "reason": "absence_violated", "detail": f"{len(rows)} rows"})
        else:
            tasks.append({"category": category, "difficulty": difficulty, "question": question,
                          "gold_sql": gold_sql, "expect_absent": True, "require_order": False,
                          "gold_tables": gold_tables or []})
        return
    if not rows:
        dropped.append({"category": category, "question": question, "reason": "vacuous"})
        return
    # a single-value gold that is NULL is not an answer: goodwill is NULL for half
    # the universe, and "多少亿元？" with a NULL gold is an unfair question
    if len(rows[0]) == 1 and rows[0][0] is None:
        dropped.append({"category": category, "question": question, "reason": "null_gold"})
        return
    # a single-value gold that is exactly 0 is degenerate for the same reason
    # COUNT(*)=0 was on the main set: it scores a guess, not a query. The v1
    # pilot missed this because the guard checked the row count, and COUNT
    # always returns one row - its zero sat inside the value.
    if len(rows[0]) == 1 and rows[0][0] == 0:
        dropped.append({"category": category, "question": question, "reason": "vacuous_zero"})
        return
    tasks.append({"category": category, "difficulty": difficulty, "question": question,
                  "gold_sql": gold_sql, "expect_absent": False, "require_order": require_order,
                  "gold_tables": gold_tables or []})


def company(code_of: str) -> str:
    return f"(SELECT code FROM companies WHERE short_name='{code_of}')"


def period_of(report_date: str) -> str:
    y, md = report_date[:4], report_date[5:]
    return f"{y}年{PERIOD_LABELS[md]}"


# ---------------------------------------------------------------- metric_lookup
FIELDS = {
    "parent_net_profit": ("归母净利润", "/100000000.0"),
    "total_revenue": ("营业总收入", "/100000000.0"),
    "deducted_parent_np": ("扣除非经常性损益后的归母净利润", "/100000000.0"),
    "op_cashflow": ("经营活动产生的现金流量净额", "/100000000.0"),
    "goodwill": ("商誉", "/100000000.0"),
    "total_parent_equity": ("归属于母公司股东的净资产", "/100000000.0"),
}
_names = [r["short_name"] for r in _CONN.execute("SELECT short_name FROM companies ORDER BY code")]
_n_fields = len(FIELDS)
_lookup_pairs = [(_names[i % len(_names)], PERIODS[(i * 7) % len(PERIODS)]) for i in range(14)]
for i, (nm, rd) in enumerate(_lookup_pairs):
    field, (label, div) = list(FIELDS.items())[i % _n_fields]
    table = ("income_statements" if field in ("parent_net_profit", "total_revenue", "deducted_parent_np")
             else "cashflow_statements" if field == "op_cashflow" else "balance_sheets")
    add("metric_lookup", "easy",
        f"{nm}{period_of(rd)}的{label}是多少亿元？",
        f"SELECT {field}{div} AS answer FROM {table} WHERE code={company(nm)} AND report_date='{rd}'")

# ---------------------------------------------------------------------- ratio
RATIOS = [
    ("销售毛利率（（营业总收入-营业成本）/营业总收入，小数）",
     "(i.total_revenue - i.operating_cost) / i.total_revenue"),
    ("归母净利率（归母净利润/营业总收入，小数）", "i.parent_net_profit / i.total_revenue"),
    ("期末ROE（归母净利润/期末归母净资产，小数）", "i.parent_net_profit / b.total_parent_equity"),
]
_ratio_pairs = [(_names[(i * 5) % len(_names)], PERIODS[(i * 3 + 1) % len(PERIODS)]) for i in range(9)]
for i, (nm, rd) in enumerate(_ratio_pairs):
    label, expr = RATIOS[i % len(RATIOS)]
    add("ratio", "medium", f"{nm}{period_of(rd)}的{label}是多少？",
        f"SELECT {expr} AS answer FROM income_statements i "
        f"JOIN balance_sheets b ON b.code=i.code AND b.report_date=i.report_date "
        f"WHERE i.code={company(nm)} AND i.report_date='{rd}'")

# --------------------------------------------------------------------- growth
for i, (nm, md) in enumerate([(  _names[(i * 11) % len(_names)], list(PERIOD_LABELS)[i % 4]) for i in range(8)]):
    cur, prev = f"2025-{md}", f"2024-{md}"
    label = "营业总收入" if i % 2 == 0 else "归母净利润"
    col = "total_revenue" if i % 2 == 0 else "parent_net_profit"
    add("growth", "medium",
        f"{nm}的{label}2025年{PERIOD_LABELS[md]}较2024年同期（同一报告期口径）的增长率是多少（小数）？",
        f"SELECT (c.{col} - p.{col}) / p.{col} AS answer FROM income_statements c "
        f"JOIN income_statements p ON p.code=c.code AND p.report_date='{prev}' "
        f"WHERE c.code={company(nm)} AND c.report_date='{cur}'")

# ------------------------------------------------------------ cross_statement
CROSS = [
    ("净现比（经营活动现金流量净额/归母净利润，小数）",
     "cf.op_cashflow / i.parent_net_profit"),
    ("商誉压力比（商誉/归母净资产，小数）", "b.goodwill / b.total_parent_equity"),
    ("应收压力比（应收账款/营业总收入，小数）", "b.accounts_receivable / i.total_revenue"),
]
_cross_pairs = [(_names[(i * 13 + 3) % len(_names)], PERIODS[(i * 5 + 2) % len(PERIODS)]) for i in range(8)]
for i, (nm, rd) in enumerate(_cross_pairs):
    label, expr = CROSS[i % len(CROSS)]
    add("cross_statement", "hard", f"{nm}{period_of(rd)}的{label}是多少？",
        f"SELECT {expr} AS answer FROM income_statements i "
        f"JOIN cashflow_statements cf ON cf.code=i.code AND cf.report_date=i.report_date "
        f"JOIN balance_sheets b ON b.code=i.code AND b.report_date=i.report_date "
        f"WHERE i.code={company(nm)} AND i.report_date='{rd}'")

# ------------------------------------------------------------------ screening
SCREEN_DATES = ["2025-12-31", "2025-06-30", "2024-12-31"]
# (v1 pilot bug, kept as a comment so it is not re-introduced: these were tuples
# of (label, date) unpacked as `for rd, label`, which put the Chinese label into
# the SQL and the raw date into the question - every COUNT gold silently
# evaluated to 0 because report_date='2025年年报' matches nothing. Labels now come
# from period_of() only, and a value-0 gold is dropped below.)
# thresholds target a small hit list (2..8): read the live ratio distribution,
# walk down from the top until the cut leaves a handful of names - a screening
# question that returns half the universe is a list, not a screen
def _goodwill_thresholds(rd: str) -> list[float]:
    ratios = sorted(
        (r[0] for r in _CONN.execute(
            "SELECT goodwill/total_parent_equity FROM balance_sheets "
            "WHERE report_date=? AND total_parent_equity>0 AND goodwill IS NOT NULL", (rd,))
         if r[0] is not None), reverse=True)
    out, seen = [], set()
    for k in range(1, min(12, len(ratios))):
        thr = round(ratios[k], 4)
        hits = sum(1 for v in ratios if v > thr)
        if 2 <= hits <= 8 and thr not in seen:
            seen.add(thr)
            out.append(thr)
        if len(out) == 2:
            break
    return out

for rd in SCREEN_DATES:
    for thr in _goodwill_thresholds(rd):
        add("screening", "hard",
            f"{period_of(rd)}商誉占归母净资产比例超过{thr}的公司有哪些？（返回公司简称）",
            f"SELECT c.short_name AS answer FROM balance_sheets b "
            f"JOIN companies c ON c.code=b.code WHERE b.report_date='{rd}' "
            f"AND b.total_parent_equity>0 AND b.goodwill/b.total_parent_equity > {thr}")
for rd in SCREEN_DATES:
    add("screening", "medium",
        f"{period_of(rd)}经营活动现金流量净额为负的公司数量是多少？",
        f"SELECT COUNT(*) AS answer FROM cashflow_statements WHERE report_date='{rd}' AND op_cashflow<0")
    add("screening", "medium",
        f"{period_of(rd)}归母净利润为负的公司数量是多少？",
        f"SELECT COUNT(*) AS answer FROM income_statements WHERE report_date='{rd}' AND parent_net_profit<0")

# ----------------------------------------------------------------------- topk
TOPK = [
    ("归母净利润最高", "parent_net_profit", "income_statements", "DESC"),
    ("营业总收入最高", "total_revenue", "income_statements", "DESC"),
    ("经营活动现金流量净额最高", "op_cashflow", "cashflow_statements", "DESC"),
    ("商誉最高", "goodwill", "balance_sheets", "DESC"),
]
_topk_pairs = [(_names[(i * 3 + 2) % len(_names)], PERIODS[(i * 9 + 3) % len(PERIODS)]) for i in range(6)]
for i, (nm, rd) in enumerate(_topk_pairs):
    label, col, table, _ = TOPK[i % len(TOPK)]
    # within-company top-k over the four same-type periods of one year. The v1
    # pilot phrased these as "…的{label}最高的值" - leftover template wording that
    # reads as "the highest value" for a single company-period lookup, and the
    # model was right to answer with the full period history instead.
    year = rd[:4]
    same_type = tuple(f"{year}-{m}" for m in PERIOD_LABELS)
    top2 = _CONN.execute(
        f"SELECT {col} FROM {table} WHERE code={company(nm)} "
        f"AND report_date IN {same_type!r} AND {col} IS NOT NULL "
        f"ORDER BY {col} DESC LIMIT 2").fetchall()
    if len(top2) == 2 and top2[0][0] == top2[1][0]:
        dropped.append({"category": "topk", "question": f"{nm} {year} {label}",
                        "reason": "ambiguous_topk", "detail": "tied at LIMIT 1"})
        continue
    add("topk", "medium",
        f"{nm}在{year}年的四个报告期中，{label}的报告期是哪一个？（返回该报告期日期 YYYY-MM-DD）",
        f"SELECT report_date AS answer FROM {table} WHERE code={company(nm)} "
        f"AND report_date IN {same_type!r} AND {col} IS NOT NULL "
        f"ORDER BY {col} DESC LIMIT 1")
for rd in ("2025-12-31", "2026-06-30"):
    add("topk", "medium",
        f"{period_of(rd)}归母净利润最高的公司简称是什么？",
        f"SELECT c.short_name AS answer FROM income_statements i JOIN companies c ON c.code=i.code "
        f"WHERE i.report_date='{rd}' ORDER BY i.parent_net_profit DESC LIMIT 1")

# -------------------------------------------------------------------- market
add("market", "easy", "诺德股份2026年9月24日的收盘价是多少元？",
    "SELECT q.close AS answer FROM daily_quotes q JOIN companies c ON c.code=q.code "
    "WHERE c.short_name='诺德股份' AND q.trade_date='2026-09-24'")
add("market", "medium", "按最新总市值从高到低排名，前3家公司的简称依次是什么？",
    "SELECT c.short_name AS answer FROM market_snapshot s JOIN companies c ON c.code=s.code "
    "ORDER BY s.total_mv_yuan DESC LIMIT 3", require_order=True)
add("market", "medium", "最新总市值最大的公司简称是什么？",
    "SELECT c.short_name AS answer FROM market_snapshot s JOIN companies c ON c.code=s.code "
    "ORDER BY s.total_mv_yuan DESC LIMIT 1")
add("market", "medium", "最新市盈率TTM最低的5家公司简称，按市盈率从低到高排列，依次是什么？",
    "SELECT c.short_name AS answer FROM market_snapshot s JOIN companies c ON c.code=s.code "
    "WHERE s.pe_ttm>0 ORDER BY s.pe_ttm LIMIT 5", require_order=True)
add("market", "medium", "2026年9月24日当日涨跌幅最高的公司简称是什么？",
    "SELECT c.short_name AS answer FROM daily_quotes q JOIN companies c ON c.code=q.code "
    "WHERE q.trade_date='2026-09-24' ORDER BY q.pct_chg DESC LIMIT 1")
add("market", "hard", "诺德股份2025年全年涨跌幅（2025-12-31收盘价/2025-01-02收盘价-1，小数）是多少？",
    "SELECT (SELECT close FROM daily_quotes q JOIN companies c ON c.code=q.code "
    "WHERE c.short_name='诺德股份' AND q.trade_date='2025-12-31') / "
    "(SELECT close FROM daily_quotes q JOIN companies c ON c.code=q.code "
    "WHERE c.short_name='诺德股份' AND q.trade_date='2025-01-02') - 1.0 AS answer")
add("market", "medium", "宁德时代2025年成交量最高的交易日的收盘价是多少元？",
    "SELECT close AS answer FROM daily_quotes q JOIN companies c ON c.code=q.code "
    "WHERE c.short_name='宁德时代' ORDER BY volume_hand DESC LIMIT 1")
add("market", "medium", "2025年全年区间涨跌幅（2025-12-31收盘/2025-01-02收盘-1，小数）最高的公司简称是什么？",
    "SELECT c.short_name AS answer FROM companies c JOIN daily_quotes qs "
    "ON qs.code=c.code AND qs.trade_date='2025-12-31' JOIN daily_quotes qe "
    "ON qe.code=c.code AND qe.trade_date='2025-01-02' "
    "ORDER BY qs.close/qe.close-1.0 DESC LIMIT 1")
# one per-company volume day question, pool-driven
_vol_pool = _CONN.execute(
    "SELECT c.short_name, COUNT(*) n FROM daily_quotes q JOIN companies c ON c.code=q.code "
    "GROUP BY 1 HAVING n>300 ORDER BY 1 LIMIT 6").fetchall()
_nm = _vol_pool[2]["short_name"]
add("market", "medium", f"{_nm}2025年下半年成交量最高的交易日是哪一天？（返回YYYY-MM-DD）",
    f"SELECT q.trade_date AS answer FROM daily_quotes q JOIN companies c ON c.code=q.code "
    f"WHERE c.short_name='{_nm}' AND q.trade_date>='2025-07-01' AND q.trade_date<='2025-12-31' "
    f"ORDER BY q.volume_hand DESC LIMIT 1")

# --------------------------------------------------------------- aggregation
add("aggregation", "medium", "2025年年报商誉总额（所有公司合计）是多少亿元？",
    "SELECT SUM(goodwill)/100000000.0 AS answer FROM balance_sheets WHERE report_date='2025-12-31'")
add("aggregation", "medium", "28家公司中，最新总市值超过3000亿元的有几家？",
    "SELECT COUNT(*) AS answer FROM market_snapshot WHERE total_mv_yuan>300000000000.0")
add("aggregation", "medium", "按行业（sector）统计最新总市值合计，总市值最高的行业是什么？",
    "SELECT c.sector AS answer FROM market_snapshot s JOIN companies c ON c.code=s.code "
    "GROUP BY c.sector ORDER BY SUM(s.total_mv_yuan) DESC LIMIT 1")
add("aggregation", "hard", "2025年年报净现比（经营现金流净额/归母净利润）大于1且归母净利润为正的公司数量是多少？",
    "SELECT COUNT(*) AS answer FROM income_statements i JOIN cashflow_statements cf "
    "ON cf.code=i.code AND cf.report_date=i.report_date "
    "WHERE i.report_date='2025-12-31' AND i.parent_net_profit>0 AND cf.op_cashflow/i.parent_net_profit>1")
add("aggregation", "medium", "2026年半年报营业总收入最高的行业（按行业内公司营收合计）是什么？",
    "SELECT c.sector AS answer FROM income_statements i JOIN companies c ON c.code=i.code "
    "WHERE i.report_date='2026-06-30' GROUP BY c.sector ORDER BY SUM(i.total_revenue) DESC LIMIT 1")
add("aggregation", "hard", "2025年年报商誉占归母净资产比例最高的公司简称是什么？",
    "SELECT c.short_name AS answer FROM balance_sheets b JOIN companies c ON c.code=b.code "
    "WHERE b.report_date='2025-12-31' AND b.total_parent_equity>0 AND b.goodwill IS NOT NULL "
    "ORDER BY b.goodwill/b.total_parent_equity DESC LIMIT 1")
add("aggregation", "medium", "所有公司2026年半年报经营活动现金流量净额合计是多少亿元？",
    "SELECT SUM(op_cashflow)/100000000.0 AS answer FROM cashflow_statements WHERE report_date='2026-06-30'")
add("aggregation", "medium", "2025年年报基本每股收益最高的公司简称是什么？",
    "SELECT c.short_name AS answer FROM income_statements i JOIN companies c ON c.code=i.code "
    "WHERE i.report_date='2025-12-31' AND i.basic_eps IS NOT NULL ORDER BY i.basic_eps DESC LIMIT 1")

# ------------------------------------------------------------------- absence
# A: 未披露报告期 —— snapshot 停在 2026-06-30，三季报/年报还不存在
ABSENCE_PERIODS = ["2026-09-30", "2026-12-31"]
for i, nm in enumerate([_names[(i * 4) % len(_names)] for i in range(5)]):
    rd = ABSENCE_PERIODS[i % 2]
    field = "parent_net_profit" if i % 2 == 0 else "total_revenue"
    label = FIELDS[field][0]
    add("absence_period", "hard",
        f"{nm}{period_of(rd)}的{label}是多少亿元？",
        f"SELECT 1 AS proof_of_absence FROM income_statements WHERE code={company(nm)} AND report_date='{rd}'",
        expect_absent=True)
# B: 范围外期间 —— 库从 2023 年开始
for nm in [_names[(i * 9 + 1) % len(_names)] for i in range(3)]:
    add("absence_period", "hard",
        f"{nm}2022年年报的归母净利润是多少亿元？",
        f"SELECT 1 AS proof_of_absence FROM income_statements WHERE code={company(nm)} AND report_date='2022-12-31'",
        expect_absent=True)
# C: 范围外公司 —— 不在 universe 里
for nm in OUTSIDE_COMPANIES[:4]:
    add("absence_company", "hard",
        f"{nm}2025年年报的归母净利润是多少亿元？",
        f"SELECT 1 AS proof_of_absence FROM income_statements WHERE code={company(nm)} AND report_date='2025-12-31'",
        expect_absent=True)
# D: 日期不在行情表 —— 周末、休市、起始日前。题面不带任何提示：缺失要被查出来，
# 不是被读出来（题面写"库中无该日"就等于把 expect_absent 标在了脑门上）
for nm, day in [("贵州茅台", "2026-09-26"), ("宁德时代", "2026-10-01"), ("美的集团", "2024-12-31")]:
    add("absence_date", "hard",
        f"{nm}{day[:4]}年{int(day[5:7])}月{int(day[8:10])}日的收盘价是多少元？",
        f"SELECT 1 AS proof_of_absence FROM daily_quotes q JOIN companies c ON c.code=q.code "
        f"WHERE c.short_name='{nm}' AND q.trade_date='{day}'",
        expect_absent=True)

# ---------------------------------------------------------------- write out
_cat_counters: dict[str, int] = {}
ordered_tasks: list[dict] = []
for t in tasks:
    _cat_counters[t["category"]] = _cat_counters.get(t["category"], 0) + 1
    t["id"] = f"{t['category']}-{_cat_counters[t['category']]:03d}"
    t["gold_row_count"] = len(_CONN.execute(t["gold_sql"]).fetchall())
    t["db"] = "astock.db"
    ordered_tasks.append({k: t[k] for k in ("id", "question", "gold_sql", "category", "difficulty",
                                            "gold_tables", "gold_row_count", "expect_absent",
                                            "require_order", "db")})
tasks = ordered_tasks

with OUT.open("w", encoding="utf-8", newline="\n") as fh:
    for t in tasks:
        fh.write(json.dumps(t, ensure_ascii=False) + "\n")
with DROPPED.open("w", encoding="utf-8", newline="\n") as fh:
    for d in dropped:
        fh.write(json.dumps(d, ensure_ascii=False) + "\n")

n_absent = sum(1 for t in tasks if t["expect_absent"])
print(f"wrote {len(tasks)} tasks ({n_absent} expect_absent) to {OUT.relative_to(ROOT)}")
print(f"dropped {len(dropped)}: " + ", ".join(sorted({d['reason'] for d in dropped})))
