"""Paired significance tests over recorded runs.

Why this exists: the README and the resume quote a McNemar exact p-value and Wilson
confidence intervals, and until now those numbers existed only in prose. A reviewer
who re-derives them has to guess which of the five near-identical baseline files in
`results/` to pair - one reviewer picked `abl-baseline` instead of `abl2-baseline`
and got p=0.092, which contradicts the document. So the file names below are
pinned, with the reason, and the script refuses to invent a pairing.

Run:  python scripts/significance.py
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"

# Pinned on purpose. Every entry is a run of the *same* 192 tasks under one config.
# `abl-baseline` (89.58%) is deliberately not used: it predates the few-shot wiring, so
# pairing it with `abl2-3shot` would compare two code versions and call the result an
# ablation.
COMPARISONS = [
    ("3-shot vs baseline (deepseek)", "abl2-baseline", "abl2-3shot"),
    ("self-repair OFF vs baseline (deepseek)", "abl2-baseline", "abl2-norepair"),
    ("3-shot vs baseline (qwen-flash)", "qwen-baseline", "qwen-3shot"),
]

# Same-configuration repeats. The spread here is the noise floor; any claimed gain has
# to clear it. Reported as a range, not a single number, because a single number from
# six pairs is a false precision.
BASELINES = ["baseline-v2", "baseline-v3", "abl-baseline", "abl2-baseline"]


def load(tag: str) -> dict[str, bool]:
    path = RESULTS / f"{tag}.jsonl"
    if not path.exists():
        raise SystemExit(f"missing {path.name}: run the eval before quoting its numbers")
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    out = {r["id"]: bool(r["correct"]) for r in rows[1:] if "id" in r}
    if not out:
        raise SystemExit(f"{tag}.jsonl has no per-task rows")
    return out


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - m) / d, (c + m) / d)


def mcnemar_exact(a: dict[str, bool], b: dict[str, bool]) -> tuple[int, int, float]:
    """Two-sided exact McNemar. Returns (b: a wrong & b right, c: a right & b wrong, p)."""
    common = set(a) & set(b)
    b_fix = sum(1 for i in common if b[i] and not a[i])
    c_break = sum(1 for i in common if a[i] and not b[i])
    n = b_fix + c_break
    if n == 0:
        return 0, 0, 1.0
    k = min(b_fix, c_break)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2**n
    return b_fix, c_break, min(1.0, 2 * tail)


def main() -> int:
    print(f"{'comparison':<38}{'n':>5}{'95% CI':>18}   {'fix':>4}{'break':>6}   p        verdict")
    print("-" * 100)
    for label, x, y in COMPARISONS:
        A, B = load(x), load(y)
        ka, kb = len(A), len(B)
        wa, wb = sum(A.values()), sum(B.values())
        lo, hi = wilson(wb, kb)
        fix, brk, p = mcnemar_exact(A, B)
        verdict = "显著" if p < 0.05 else "未达显著"
        if ka != kb:
            print(f"{label:<38}!! 题数不一致 {ka} vs {kb} - 不可配对，先查数据集是否变过")
            continue
        print(f"{label:<38}{kb:>5}{f'{wb/kb:.1%} [{lo:.1%},{hi:.1%}]':>18}   {fix:>4}{brk:>6}   "
              f"{p:<7.4f} {verdict}")

    print("\n噪声底：同配置基线两两差异")
    sets = {t: load(t) for t in BASELINES}
    diffs = []
    for i, x in enumerate(BASELINES):
        for y in BASELINES[i + 1:]:
            if len(sets[x]) != len(sets[y]):
                continue
            d = sum(1 for a in sets[x] if sets[x][a] != sets[y].get(a))
            diffs.append((d, x, y))
    for d, x, y in sorted(diffs, reverse=True):
        print(f"  {d:>2} 题 ({d/192*100:>4.1f}pp)  {x} vs {y}")
    worst = max(d for d, _, _ in diffs) if diffs else 0
    print(f"  最大两两差异 {worst} 题 = {worst/192*100:.1f}pp  -> 可报告增益阈值应取此量级之上")
    print("  注：阈值不写死在文档里。文档若引用了某个具体 pp 数，以本脚本输出为准。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
