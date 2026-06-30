"""
第1弾 ① 叩き台TC生成ランナー

  10_改定前/ の改定前JSON1本を新規工種モードで処理し、20_叩き台TC/ に
  step2.0_テスト計画.csv / step3.0_テストケース.csv を出力する。
  期待:Sx 列は「計上行マーカー(○/空)＋変数タイトル」に整形する(_markerize)。

【使い方】 python3 01_第1弾_運用化/run_phase1.py <案件ディレクトリ>
"""
import sys
import os
import csv
import glob

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, 'engine'))
sys.path.insert(0, os.path.join(BASE, 'step2_proposals'))
sys.path.insert(0, os.path.join(BASE, 'step3_csv'))

from bugakari_json import BugakariJSON                   # noqa: E402
from generate_proposals_new import run as run_plan_new   # noqa: E402
from generate_csv import run as run_csv                   # noqa: E402

IN_DIR = '10_改定前'
OUT_DIR = '20_叩き台TC'
PLAN_CSV = 'step2.0_テスト計画.csv'
TC_CSV = 'step3.0_テストケース.csv'
MARK = '○'   # 計上マーカー ○


def _markerize(tc_csv, json_path):
    """期待:Sx 列を計上行マーカーに変換する。
       見出し: 期待:S1 -> 期待:S1(変数タイトル)  (KeisanItem.Mesho)
       セル  : 計上(値が非空) -> ○ / 非計上 -> 空欄  (数値は出さない)
       末尾に手計算一致の観点行(# 始まり=TC読取りでスキップ)を1行追加。"""
    bj = BugakariJSON(json_path)
    title = {}
    for k in bj.data.get('KeisanItem', []):
        vn = (k.get('VarName') or '').strip()
        if vn:
            title[vn] = (k.get('Mesho') or '').strip()
    rows = None
    for enc in ('cp932', 'utf-8-sig'):
        try:
            rows = list(csv.reader(open(tc_csv, encoding=enc, newline='')))
            break
        except UnicodeDecodeError:
            continue
    if not rows:
        return
    header = rows[0]
    s_idx = []
    for i, h in enumerate(header):
        if h.startswith('期待:'):
            sv = h[len('期待:'):].strip()
            t = title.get(sv, '')
            header[i] = ('期待:' + sv + '(' + t + ')') if t else ('期待:' + sv)
            s_idx.append(i)
    for r in rows[1:]:
        for i in s_idx:
            if i < len(r):
                v = (r[i] or '').strip()
                r[i] = MARK if v not in ('', '-') else ''
    note = [''] * len(header)
    note[s_idx[0] if s_idx else 0] = '計算結果は手計算と一致すること(手動確認)'
    rows.append(note)   # 期待結果の始まり列に観点を表示(テストID列は空=③でスキップ)
    with open(tc_csv, 'w', encoding='cp932', newline='') as f:
        w = csv.writer(f, lineterminator='\r\n')
        for r in rows:
            w.writerow(r)


def _find_single_json(in_dir):
    js = sorted(glob.glob(os.path.join(in_dir, '*.json')))
    if not js:
        raise SystemExit('[ERROR] ' + in_dir + ' に改定前JSONがありません')
    if len(js) > 1:
        raise SystemExit('[ERROR] ' + in_dir + ' のJSONは1本にしてください (検出 '
                         + str(len(js)) + ' 本)')
    return js[0]


def run_case(case_dir):
    in_dir = os.path.join(case_dir, IN_DIR)
    out_dir = os.path.join(case_dir, OUT_DIR)
    if not os.path.isdir(in_dir):
        raise SystemExit('[ERROR] ' + in_dir + ' が見つかりません')
    os.makedirs(out_dir, exist_ok=True)
    src = _find_single_json(in_dir)
    plan_out = os.path.join(out_dir, PLAN_CSV)
    tc_out = os.path.join(out_dir, TC_CSV)
    print('=' * 56)
    print('① 叩き台TC生成 (新規工種モード)  案件: ' + os.path.basename(case_dir))
    print('=' * 56)
    print('  改定前JSON: ' + os.path.basename(src))
    run_plan_new(src, plan_out)
    run_csv(plan_out, src, tc_out)
    _markerize(tc_out, src)
    print()
    print('  -> ' + plan_out)
    print('  -> ' + tc_out)
    print('  次の手順: 20_叩き台TC/ を 30_人作成TC/ にコピーし、人がTCを加工してください。')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python3 01_第1弾_運用化/run_phase1.py <案件ディレクトリ>')
        sys.exit(1)
    run_case(sys.argv[1])
