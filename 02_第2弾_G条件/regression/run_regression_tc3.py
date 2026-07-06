#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""③(G条件→改定後TC)回帰テストランナー

targets.yaml の diff 工種ごとに 20/30 G条件を生成→③でTCを生成し、
ベースライン(進捗報告/regression_baseline/tc3/)とバイト比較する。
gen_gjoken.py(G条件生成) と gen_tc_from_gjoken.py(③本体) のエンドツーエンド回帰。

使い方:
    python3 02_第2弾_G条件/regression/run_regression_tc3.py                 # 全件
    python3 02_第2弾_G条件/regression/run_regression_tc3.py --only 06,27    # id前方一致
    python3 02_第2弾_G条件/regression/run_regression_tc3.py --update-baseline <id>|all
    python3 02_第2弾_G条件/regression/run_regression_tc3.py --range 0 12    # index範囲(時間制限環境用)

終了コード: 0=全一致 / 1=DIFFあり / 2=エラーあり

運用ルール(2026-07-06 ③精度確認完了時に正式化):
  - gen_gjoken.py / gen_tc_from_gjoken.py を改修したら、コミット前に必ず実行する。
  - DIFF は「意図した変更のみ」をユーザが承認した後にのみ --update-baseline で更新。
  - 21側溝は1工種で30秒以上かかる(全体約2〜3分)。
"""

import sys
import os
import io
import time
import shutil
import argparse
import contextlib
import tempfile
from pathlib import Path

try:
    import yaml
except ImportError:
    print('PyYAML が必要です (pip install pyyaml --break-system-packages)')
    sys.exit(2)

HERE = Path(__file__).resolve().parent
BASE = HERE.parent            # 02_第2弾_G条件
ROOT = BASE.parent
sys.path.insert(0, str(BASE))
import gen_gjoken             # noqa: E402
import gen_tc_from_gjoken     # noqa: E402
from run_regression import summarize_csv_diff  # noqa: E402  (CSV差分要約を共用)

BASELINE = ROOT / '進捗報告' / 'regression_baseline' / 'tc3'
REPORT = ROOT / '進捗報告' / 'regression_tc3_report_最新.md'
FILES = ('20_G条件.csv', '30_G条件.csv', 'step3.0_テストケース.csv')


def targets_diff():
    cfg = yaml.safe_load((HERE / 'targets.yaml').read_text(encoding='utf-8'))
    return [t for t in cfg['targets'] if t.get('mode') == 'diff']


def generate(t):
    """1工種分を生成し {ファイル名: 実パス} を返す。stdoutは抑制。"""
    kdir = ROOT / '工種別' / t['id']
    old = kdir / 'input' / t['old']
    new = kdir / 'input' / t['new']
    if not old.exists() or not new.exists():
        raise FileNotFoundError(f"input欠落: {old.name if not old.exists() else new.name}")
    work = Path(tempfile.mkdtemp(prefix='tc3reg_'))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        c20 = gen_gjoken.build_g(str(old), str(work / '20'))
        c30 = gen_gjoken.build_g(str(new), str(work / '30'))
        out = gen_tc_from_gjoken.run(c20, c30, str(old), str(work / 'out'))
    return {'20_G条件.csv': Path(c20), '30_G条件.csv': Path(c30),
            'step3.0_テストケース.csv': Path(out)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', help='id 前方一致カンマ区切り (例: 06,27)')
    ap.add_argument('--range', nargs=2, type=int, metavar=('LO', 'HI'),
                    help='対象indexの範囲 [LO, HI) (時間制限環境でのチャンク実行用)')
    ap.add_argument('--update-baseline', metavar='ID|all',
                    help='現出力をベースラインとして保存(ユーザ承認後のみ使用)')
    args = ap.parse_args()

    ts = targets_diff()
    if args.only:
        keys = [k.strip() for k in args.only.split(',')]
        ts = [t for t in ts if any(t['id'].startswith(k) for k in keys)]
    if args.range:
        ts = ts[args.range[0]:args.range[1]]
    if not ts:
        print('対象がありません')
        return 2

    # --- ベースライン更新 ---
    if args.update_baseline:
        sel = ts if args.update_baseline == 'all' else \
            [t for t in ts if t['id'] == args.update_baseline]
        if not sel:
            print(f'id が見つかりません: {args.update_baseline}')
            return 2
        for t in sel:
            try:
                got = generate(t)
            except Exception as ex:
                print(f'[ERROR] {t["id"]}: {ex}')
                continue
            dst = BASELINE / t['id']
            dst.mkdir(parents=True, exist_ok=True)
            for name, src in got.items():
                shutil.copy2(src, dst / name)
            print(f'[UPDATED] {t["id"]}')
        print('ベースライン更新完了(git管理外)。')
        return 0

    # --- 実行+比較 ---
    results = []   # (id, status, detail)
    t_all = time.time()
    for t in ts:
        t0 = time.time()
        try:
            got = generate(t)
        except Exception as ex:
            results.append((t['id'], 'ERROR', [f'  - {type(ex).__name__}: {ex}'], 0))
            continue
        bdir = BASELINE / t['id']
        detail = []
        status = 'OK'
        for name in FILES:
            bp, cp = bdir / name, got[name]
            if not bp.exists():
                status = 'MISS'
                detail.append(f'  - baselineなし: {name} (--update-baseline {t["id"]} で取得)')
                continue
            if bp.read_bytes() == cp.read_bytes():
                continue
            status = 'DIFF' if status != 'MISS' else status
            detail.append(f'  {name}:')
            detail.extend(['  ' + ln for ln in summarize_csv_diff(bp, cp)])
        results.append((t['id'], status, detail, time.time() - t0))

    # --- レポート ---
    ok = [r for r in results if r[1] == 'OK']
    ng = [r for r in results if r[1] != 'OK']
    rep = [f'# ③回帰テスト結果 {time.strftime("%Y-%m-%d %H:%M")}', '',
           f'- 対象: {len(results)} 工種 (diff) / 比較=20・30 G条件 + ③TC のバイト一致',
           f'- 実行時間: {time.time() - t_all:.1f} 秒', '']
    if ok:
        rep.append(f'[OK] 完全一致: {len(ok)} 工種')
        rep.append('  ' + ', '.join(r[0].split('_')[0] for r in ok))
    for tid, status, detail, _ in ng:
        rep += ['', f'[{status}] {tid}'] + detail
    rep.append('')
    verdict = ('全工種 完全一致' if not ng else
               'DIFF/エラーあり (意図した変更か確認し、承認後に --update-baseline)')
    rep.append(f'**結論: {verdict}**')
    text = '\n'.join(rep)
    print(text)
    if not (args.only or args.range):   # 全件実行時のみレポート保存
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(text + '\n', encoding='utf-8')
    return 1 if ng else 0


if __name__ == '__main__':
    sys.exit(main())
