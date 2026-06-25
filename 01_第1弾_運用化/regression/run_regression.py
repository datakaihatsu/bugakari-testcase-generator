# -*- coding: utf-8 -*-
"""
回帰テストランナー(司令塔)

レジストリ(targets.yaml)に登録された全工種でパイプラインを実行し、
ベースライン(pre_J2 等)と出力を比較。差分は CSV 構造を理解した要約で報告する。

使い方:
    python3 01_第1弾_運用化/regression/run_regression.py                  # 全工種 実行+比較
    python3 01_第1弾_運用化/regression/run_regression.py --only 02,06     # id 前方一致で絞り込み
    python3 01_第1弾_運用化/regression/run_regression.py --no-run         # 再実行せず比較のみ
    python3 01_第1弾_運用化/regression/run_regression.py --update-baseline 02_605773_大型ブレーカ
    python3 01_第1弾_運用化/regression/run_regression.py --update-baseline all

終了コード: 0=全一致 / 1=差分あり / 2=実行エラーあり

運用ルール:
  - スクリプト(01_第1弾_運用化/engine, step1_5, step2_proposals, step3_csv, pipeline.py)を
    改修したら、コミット前に必ず実行する。
  - 差分が「意図した変更のみ」であることをユーザが承認した後にのみ
    --update-baseline でベースラインを更新する。
"""

import sys
import os
import csv
import io
import time
import shutil
import argparse
import subprocess
from pathlib import Path

try:
    import yaml
except ImportError:
    print('PyYAML が必要です。以下を実行してください:')
    print('  Windows : pip install pyyaml')
    print('  Linux   : pip install pyyaml --break-system-packages')
    sys.exit(2)

ROOT = Path(__file__).resolve().parents[2]
REG_PATH = Path(__file__).resolve().parent / 'targets.yaml'

MAX_CELL_DIFFS_PER_FILE = 20   # 1ファイルあたりのセル差分表示上限
MAX_ROW_LIST = 10              # 追加/削除行の表示上限


# =====================================================================
# CSV 読み込み (cp932 / utf-8-sig 両対応)
# =====================================================================

def gokaku_csv(out_dir):
    """工種 output 内の【合格】CSV(最終テストケースの人承認版)を返す。無ければ None。
    回帰の step3 はこの合格CSVと照合する(=人の承認を唯一の正とする)。"""
    import glob as _g
    exact = out_dir / 'step3.0_テストケース【合格】.csv'
    if exact.exists():
        return exact
    cands = sorted(_g.glob(str(out_dir / '*【合格】.csv')))
    return Path(cands[0]) if cands else None


def read_csv_rows(path):
    data = Path(path).read_bytes()
    for enc in ('cp932', 'utf-8-sig'):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = data.decode('utf-8', errors='replace')
    return list(csv.reader(io.StringIO(text)))


# =====================================================================
# CSV 構造差分の要約
# =====================================================================

def summarize_csv_diff(base_path, cur_path):
    """ベースラインと現出力の差分を人間向けの行リストで返す"""
    lines = []
    base = read_csv_rows(base_path)
    cur = read_csv_rows(cur_path)
    if not base or not cur:
        lines.append(f'  - ファイルが空 (baseline {len(base)}行 / 現 {len(cur)}行)')
        return lines

    bh, ch = base[0], cur[0]

    # 列構成の変化
    removed_cols = [c for c in bh if c not in ch]
    added_cols = [c for c in ch if c not in bh]
    if removed_cols:
        lines.append(f'  - 列削除: {removed_cols} ({len(bh)}列→{len(ch)}列)')
    if added_cols:
        lines.append(f'  - 列追加: {added_cols} ({len(bh)}列→{len(ch)}列)')

    def row_label(row, idx):
        head = (row[0] if row else '').strip()
        return f'{head or "(無題)"} (行{idx + 1})'

    if removed_cols or added_cols:
        # 列構成が違う場合は共通列のみでセル比較 (行は順序対応)
        common = [c for c in bh if c in ch]
        bidx = {c: bh.index(c) for c in common}
        cidx = {c: ch.index(c) for c in common}
        n = min(len(base), len(cur)) - 1
        cnt = 0
        for i in range(1, n + 1):
            for c in common:
                bv = base[i][bidx[c]] if bidx[c] < len(base[i]) else ''
                cv = cur[i][cidx[c]] if cidx[c] < len(cur[i]) else ''
                if bv != cv:
                    cnt += 1
                    if cnt <= MAX_CELL_DIFFS_PER_FILE:
                        lines.append(f'  - {row_label(cur[i], i)} 列「{c}」: '
                                     f'{bv!r} → {cv!r}')
        if cnt > MAX_CELL_DIFFS_PER_FILE:
            lines.append(f'  - …ほか {cnt - MAX_CELL_DIFFS_PER_FILE} 件のセル差分')
    elif len(base) == len(cur):
        # 同一形状: セル単位比較
        cnt = 0
        for i in range(1, len(base)):
            brow, crow = base[i], cur[i]
            w = max(len(brow), len(crow))
            for j in range(w):
                bv = brow[j] if j < len(brow) else ''
                cv = crow[j] if j < len(crow) else ''
                if bv != cv:
                    col = bh[j] if j < len(bh) else f'列{j + 1}'
                    cnt += 1
                    if cnt <= MAX_CELL_DIFFS_PER_FILE:
                        lines.append(f'  - {row_label(crow, i)} 列「{col}」: '
                                     f'{bv!r} → {cv!r}')
        if cnt > MAX_CELL_DIFFS_PER_FILE:
            lines.append(f'  - …ほか {cnt - MAX_CELL_DIFFS_PER_FILE} 件のセル差分')
        if cnt == 0:
            lines.append('  - (バイト差はあるがセル内容は同一: 改行/エンコーディング差の可能性)')
    else:
        # 行数が違う: 先頭列キーで追加/削除を推定
        bkeys = [r[0] for r in base[1:] if r]
        ckeys = [r[0] for r in cur[1:] if r]
        lines.append(f'  - 行数変化: {len(base) - 1}行 → {len(cur) - 1}行')
        removed = [k for k in bkeys if k not in ckeys][:MAX_ROW_LIST]
        added = [k for k in ckeys if k not in bkeys][:MAX_ROW_LIST]
        if removed:
            lines.append(f'  - 削除行(先頭列): {removed}')
        if added:
            lines.append(f'  - 追加行(先頭列): {added}')
        if not removed and not added:
            lines.append('  - (同一キーで行の増減/重複あり。手動確認推奨)')
    return lines


# =====================================================================
# パイプライン実行
# =====================================================================

def run_target(t, log):
    """対象工種のパイプラインを実行。(成功?, 経過秒) を返す"""
    tid = t['id']
    d = ROOT / '工種別' / tid
    out = d / 'output'
    t0 = time.time()
    try:
        if t['mode'] == 'diff':
            cmds = [[sys.executable, str(ROOT / '01_第1弾_運用化/pipeline.py'),
                     str(d / 'input' / t['old']), str(d / 'input' / t['new']), str(out)]]
        else:  # new
            step3_cmd = [sys.executable, str(ROOT / '01_第1弾_運用化/step3_csv/generate_csv.py'),
                         str(out / 'step2.0_テスト計画.csv'), str(d / 'input' / t['new']),
                         str(out / 'step3.0_テストケース.csv')]
            # 参考JSON (input/参考/) があれば文字比較の比較元として渡す (run_koshu と同挙動)
            import re as _re

            def _datekey(path):
                m = _re.findall(r'(\d{8})', os.path.basename(str(path)))
                return m[-1] if m else ''
            refs = sorted((d / 'input' / '参考').glob('*.json'), key=_datekey)
            if refs:
                step3_cmd += ['--ref', str(refs[-1])]
            cmds = [
                [sys.executable, str(ROOT / '01_第1弾_運用化/step2_proposals/generate_proposals_new.py'),
                 str(d / 'input' / t['new']), str(out / 'step2.0_テスト計画.csv')],
                step3_cmd,
            ]
        for cmd in cmds:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding='utf-8', errors='replace', cwd=str(ROOT))
            if r.returncode != 0:
                log.append(f'[ERROR] {tid}: パイプライン失敗 (rc={r.returncode})')
                tail = (r.stderr or r.stdout or '').strip().splitlines()[-5:]
                for ln in tail:
                    log.append(f'    {ln}')
                return False, time.time() - t0
        return True, time.time() - t0
    except Exception as ex:
        log.append(f'[ERROR] {tid}: 実行例外 {ex}')
        return False, time.time() - t0


# =====================================================================
# メイン
# =====================================================================

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', help='対象 id の前方一致カンマ区切り (例: 02,06)')
    ap.add_argument('--no-run', action='store_true', help='パイプライン再実行せず比較のみ')
    ap.add_argument('--update-baseline', metavar='ID|all',
                    help='現出力をベースラインとして上書き保存(承認後のみ使用)')
    args = ap.parse_args()

    cfg = yaml.safe_load(REG_PATH.read_text(encoding='utf-8'))
    targets = cfg['targets']
    if args.only:
        keys = [k.strip() for k in args.only.split(',')]
        targets = [t for t in targets if any(t['id'].startswith(k) for k in keys)]
        if not targets:
            print(f'--only に一致する対象がありません: {args.only}')
            return 2

    base_root = ROOT / cfg['baseline_root'] / cfg['baseline_name']

    def steps_of(t):
        if 'steps' in t:
            return t['steps']
        return cfg['default_steps_diff'] if t['mode'] == 'diff' else cfg['default_steps_new']

    # --- ベースライン更新モード ---
    if args.update_baseline:
        sel = targets if args.update_baseline == 'all' else \
            [t for t in targets if t['id'] == args.update_baseline]
        if not sel:
            print(f'id が見つかりません: {args.update_baseline}')
            return 2
        for t in sel:
            src = ROOT / '工種別' / t['id'] / 'output'
            dst = base_root / t['id']
            dst.mkdir(parents=True, exist_ok=True)
            for f in steps_of(t):
                if (src / f).exists():
                    shutil.copy2(src / f, dst / f)
                    print(f'[UPDATED] {t["id"]}/{f}')
                else:
                    print(f'[SKIP]    {t["id"]}/{f} (出力なし)')
        print('ベースライン更新完了。git管理外のため必要ならバックアップを取ってください。')
        return 0

    # --- 実行+比較 ---
    log = []
    timings = []
    total0 = time.time()
    if not args.no_run:
        for t in targets:
            ok, sec = run_target(t, log)
            timings.append((t['id'], sec, ok))

    results = []   # (id, status, detail_lines)
    err = any(not ok for _, _, ok in timings)
    for t in targets:
        tid = t['id']
        out = ROOT / '工種別' / tid / 'output'
        bdir = base_root / tid
        diffs = []
        missing = []
        for f in steps_of(t):
            cp = out / f
            if f == 'step3.0_テストケース.csv':
                # 最終テストケースは【合格】CSV(人の承認)と照合する
                bp = gokaku_csv(out)
                if bp is None:
                    missing.append(f'{f} (合格CSVなし)')
                    continue
            else:
                bp = bdir / f
            if not bp.exists() or not cp.exists():
                missing.append(f'{f} ({"baseline/合格" if not bp.exists() else "出力"}なし)')
                continue
            if bp.read_bytes() == cp.read_bytes():
                continue
            diffs.append((f, summarize_csv_diff(bp, cp)))
        if missing:
            results.append((tid, 'MISS', [f'  - {m}' for m in missing]))
        elif diffs:
            detail = []
            for f, lines in diffs:
                detail.append(f'  {f}:')
                detail.extend(['  ' + ln for ln in lines])
            results.append((tid, 'DIFF', detail))
        else:
            results.append((tid, 'OK', []))

    total_sec = time.time() - total0

    # --- レポート生成 ---
    ok_ids = [r[0] for r in results if r[1] == 'OK']
    ng = [r for r in results if r[1] != 'OK']
    rep = []
    rep.append(f'# 回帰テスト結果 {time.strftime("%Y-%m-%d %H:%M")}')
    rep.append('')
    rep.append(f'- 対象: {len(results)} 工種 / step3=【合格】CSV照合 / 中間ステップ基準: {cfg["baseline_name"]}')
    rep.append(f'- 実行時間: {total_sec:.1f} 秒'
               + (' (比較のみ)' if args.no_run else ''))
    rep.append('')
    if ok_ids:
        rep.append(f'[OK] 完全一致: {len(ok_ids)} 工種')
        rep.append('  ' + ', '.join(i.split("_")[0] for i in ok_ids))
    for tid, status, detail in ng:
        rep.append('')
        rep.append(f'[{status}] {tid}')
        rep.extend(detail)
    if log:
        rep.append('')
        rep.append('## 実行ログ(エラー)')
        rep.extend(log)
    if timings:
        rep.append('')
        rep.append('## 実行時間内訳')
        for tid, sec, ok in timings:
            rep.append(f'- {tid}: {sec:.1f}s' + ('' if ok else ' [ERROR]'))
    rep.append('')
    verdict = ('全工種 完全一致' if not ng and not err else
               '差分/エラーあり (意図した変更か確認し、承認後に --update-baseline)')
    rep.append(f'**結論: {verdict}**')

    text = '\n'.join(rep)
    print(text)
    report_path = ROOT / cfg['report_path']
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(text + '\n', encoding='utf-8')

    if err:
        return 2
    return 1 if ng else 0


if __name__ == '__main__':
    sys.exit(main())
