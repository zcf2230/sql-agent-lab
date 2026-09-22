"""Print paired significance tests and the noise floor.

Thin CLI over `sqlagent.stats`, which owns the maths, the run pairings and the
reason each pairing is pinned. The numbers quoted in README and drawn in
docs/figures come from the same code, so they cannot drift apart.

Run:  python scripts/significance.py
"""

from __future__ import annotations

import sys

from sqlagent.stats import all_comparisons, noise_floor


def main() -> int:
    print(f"{'comparison':<40}{'n':>5}{'base':>8}{'variant':>9}{'Δ':>8}"
          f"{'95% CI':>18}{'fix':>5}{'brk':>5}{'p':>8}  verdict")
    print("-" * 116)
    for c in all_comparisons():
        ci = f"{c['ci_low']:.1%}-{c['ci_high']:.1%}"
        print(f"{c['label']:<40}{c['n']:>5}{c['base_rate']:>7.1%}{c['variant_rate']:>9.1%}"
              f"{c['delta_pp']:>+6.1f}pp{ci:>18}"
              f"{c['fixed']:>5}{c['broken']:>5}{c['p_value']:>8.4f}  "
              f"{'显著' if c['significant'] else '未达显著'}")

    nf = noise_floor()
    print("\n噪声底：同配置基线两两差异（这就是可报告增益的下限）")
    for d, x, y in sorted(nf["pairs"], reverse=True):
        print(f"  {d:>2} 题 ({d / nf['n'] * 100:>4.1f}pp)  {x} vs {y}")
    print(f"  最大两两差异 {nf['worst_tasks']} 题 = {nf['worst_pp']:.1f}pp"
          f"  ← {nf['worst_pair'][0]} vs {nf['worst_pair'][1]}")
    print("  文档若引用具体阈值，以本输出为准；不要抄写。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
