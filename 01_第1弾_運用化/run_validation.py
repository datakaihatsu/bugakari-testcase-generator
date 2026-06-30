"""
第1弾 4カテゴリ精度検証ハーネス

99差分ベース合格TC = 「正しい改定後TCの正解(ゴールド)」とみなし、01_第1弾が
改定をどれだけ検出できるかを、ノイズを排した4カテゴリで採点する。

  1) 条件の増減          : 入力軸(質問名)の出現/消失
  2) 計上行の正しさ      : 計上される代価表行(=非空のS列)の増減。数値そのものは
                          比較しない(人介在後は脆い)。数値は「手計算一致」の観点(手動確認)へ。
  3) 選択肢の適切さ確認の増減  (副次)
  4) 規格名計上の増減          (副次)

主指標 = 条件・計上行 (構造。JSON実装差分=差分B/代価表追加削除 と対応)。
副次   = 観点列(選択肢の適切さ確認/規格名計上)。叩き台(新規工種モード)とゴールド
         (旧99ロジック)で生成系が異なるため増減件数のみ記録する。
リネーム(表示名変更)は文字変更カテゴリ。再選択不可・自動固定の条件はテスト不要として除外。

対象: 工種別/ のうち 改定前+改定後JSON(2本) かつ 合格CSV がある工種。

各工種の流れ:
  ① 改定前JSON -> 新規工種モード -> 叩き台TC (一時)
  ② 叩き台 vs 合格TC(ゴールド) を4カテゴリで抽出 = 正解シグネチャ
  ③ 差分B(step2フィルタ済みJSON差分 + 計算表/代価表S変更) を算出
  ④ 主指標(条件・期待)を質問名/S変数キーで照合 -> 一致/未説明
  ⑤ 各工種が4パターンのどれに該当するか分類

出力 (01_第1弾_運用化/運用案件/_精度検証/):
  4カテゴリ精度検証_集計.csv   工種 × パターン × 採点
  4カテゴリ精度検証_詳細.csv   工種 × カテゴリ × 項目 × 差分B説明有無

使い方:
  python3 01_第1弾_運用化/run_validation.py            # 対象工種すべて
  python3 01_第1弾_運用化/run_validation.py 00 21 24   # 接頭辞で限定
"""
import sys
import os
import glob
import re
import traceback

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
for _p in ('engine', 'step1_diff', 'step2_proposals', 'step3_csv'):
    sys.path.insert(0, os.path.join(BASE, _p))
sys.path.insert(0, BASE)

from bugakari_json import BugakariJSON              # noqa: E402
from generate_proposals_new import run as run_plan_new   # noqa: E402
from generate_csv import run as run_csv             # noqa: E402
import run_phase3 as p3                             # noqa: E402

KOSHU = os.path.join(ROOT, '工種別')
OUT_DIR = os.path.join(BASE, '運用案件', '_精度検証')


def _datekey(path):
    ds = re.findall(r'(\d{8})', os.path.basename(path))
    return ds[-1] if ds else ''


def eligible(prefixes=None):
    out = []
    for d in sorted(os.listdir(KOSHU)):
        if prefixes and not any(d.startswith(p) for p in prefixes):
            continue
        folder = os.path.join(KOSHU, d)
        idir, odir = os.path.join(folder, 'input'), os.path.join(folder, 'output')
        if not os.path.isdir(idir):
            continue
        js = [p for p in sorted(glob.glob(os.path.join(idir, '*.json')), key=_datekey)
              if '参考' not in os.path.basename(p)]
        gold = sorted(glob.glob(os.path.join(odir, '*合格*.csv')))
        if len(js) >= 2 and gold:
            out.append((d, js[0], js[-1], gold[0]))   # 旧, 新, ゴールド
    return out


def _items(body, col):
    """観点列の項目集合 (行をまたいで集約、・/改行で分割、空/- を除外)。"""
    out = set()
    for r in body:
        cell = (r.get(col, '') or '').strip()
        if cell in ('', '-'):
            continue
        for part in re.split(r'[\n・]', cell):
            t = part.strip(' 　・\t')
            if t and t != '-':
                out.add(t)
    return out


def _plan_kinds(plan_csv):
    """新規工種モード計画CSVから {質問名/列ラベル: 種別(vary/auto/fix)} を作る。"""
    import csv as _csv
    out = {}
    rows = None
    for enc in ('cp932', 'utf-8-sig'):
        try:
            rows = list(_csv.reader(open(plan_csv, encoding=enc, newline='')))
            break
        except (UnicodeDecodeError, FileNotFoundError):
            continue
    if not rows:
        return out
    for r in rows[1:]:
        if len(r) >= 5:
            kind = (r[2] or '').strip()
            for key in ((r[1] or '').strip(), (r[4] or '').strip()):
                if key:
                    out.setdefault(key, set()).add(kind)
    return out


def _canon(n, rename_map):
    """質問名正規化: 「(固定)」サフィックス除去 + 旧名->新名(リネーム)。"""
    n = re.sub(r'\(固定\)$', '', (n or '')).strip()
    return (rename_map or {}).get(n, n)


def extract_signature(base_path, gold_path, rename_map=None):
    """叩き台 → ゴールド の4カテゴリ差分(=改定の正解シグネチャ)。
       rename_map(旧名->新名)で質問名を正規化し、表示名変更(リネーム)を
       条件の増減ではなく『文字変更』として扱う。"""
    rename_map = rename_map or {}
    b_head, b_body = p3._read_tc(base_path)
    g_head, g_body = p3._read_tc(gold_path)
    raw_b = set(p3._axis_cols(b_head))
    raw_g = set(p3._axis_cols(g_head))

    def canon(n):
        # 「(固定)」等のエンジン付与サフィックスを除去してから旧名->新名正規化。
        #   叩き台(新規型)とゴールド(差分型)で固定/vary判定が違い、同一質問が
        #   "X" と "X(固定)" に割れて偽の条件増減になるのを防ぐ。
        n = re.sub(r'\(固定\)$', '', n).strip()
        return rename_map.get(n, n)
    b_axis = {canon(x) for x in raw_b}
    g_axis = {canon(x) for x in raw_g}
    # この工種で実際に効いたリネーム(叩き台に旧名・ゴールドに新名がある)
    renamed = {new for old, new in rename_map.items()
               if old in raw_b and new in raw_g}

    # 期待カテゴリは「計上行(=計上される代価表行=非空のS列)」の有無で見る。
    #   数値そのものは比較しない(人介在後は脆い)。数値の正しさは手計算一致の観点(手動)へ。
    def keijo(head, body):
        return {sc.replace('期待:', '') for sc in p3._s_cols(head)
                if any((r.get(sc, '') or '').strip() not in ('', '-') for r in body)}
    kb, kg = keijo(b_head, b_body), keijo(g_head, g_body)

    teki_b, teki_g = _items(b_body, '選択肢の適切さ確認'), _items(g_body, '選択肢の適切さ確認')
    kik_b, kik_g = _items(b_body, '規格名計上'), _items(g_body, '規格名計上')
    return {
        'cond_added': g_axis - b_axis,
        'cond_removed': b_axis - g_axis,
        'g_exercised': g_axis,   # ゴールドTCが行使する質問(canon済)
        'renamed': renamed,
        'keijo_added': kg - kb, 'keijo_removed': kb - kg,   # 計上行の増減
        'exp_changed': (kg - kb) | (kb - kg),               # 主指標用(計上行変化)
        'teki_added': teki_g - teki_b, 'teki_removed': teki_b - teki_g,
        'kik_added': kik_g - kik_b, 'kik_removed': kik_b - kik_g,
    }


def score_koshu(name, old_json, new_json, gold_csv, work):
    os.makedirs(work, exist_ok=True)
    plan = os.path.join(work, 'step2.0_テスト計画.csv')
    base_tc = os.path.join(work, 'step3.0_叩き台.csv')
    run_plan_new(old_json, plan)
    run_csv(plan, old_json, base_tc)

    rawrows = p3.DiffExtractor(p3.BugakariJSON(old_json),
                               p3.BugakariJSON(new_json)).extract_all()
    # 表示名変更(リネーム) 旧名->新名 マップ (質問のみ・フロートークンは除外)
    rename_map = {}
    for c, k, _i, nm, ov, nv, note in rawrows:
        if c in ('質問', '質問設定') and '表示名変更' in (note or ''):
            o, n = (ov or '').strip(), (nv or '').strip()
            if o and n and o != n and not p3._looks_like_var(o) and not p3._looks_like_var(n):
                rename_map[o] = n
    sig = extract_signature(base_tc, gold_csv, rename_map)
    # (A) ノイズ除去: 生JSONに計算表/代価表の値変更が無い工種の「期待変更」は
    #     叩き台(新規型)とゴールド(差分型)のコンボ差由来の偽陽性なので落とす(#23型)。
    has_value_change = any(
        c in ('計算表', '代価表') and any(t in (note or '') for t in ('式', '値', '固定'))
        for c, k, _i, nm, ov, nv, note in rawrows)
    if not has_value_change:
        sig['exp_changed'] = set()
    b_q, b_s = p3.diff_b(old_json, new_json, work)
    bq, bs = set(b_q), set(b_s)

    # 新JSONを新規工種モードで分類し、auto(自動確定=再選択不可)条件はテスト不要として
    #   主指標から除外する(ユーザ定義: 再選択可能=必要 / 再選択不可・自動固定=不要)。
    new_plan = os.path.join(work, 'step2.0_新JSON計画.csv')
    try:
        run_plan_new(new_json, new_plan)
        kinds = _plan_kinds(new_plan)
    except Exception:
        kinds = {}

    def _is_autofixed(nm):
        ks = set()
        for key in (nm, nm.replace('(固定)', ''), f'{nm}(固定)'):
            ks |= kinds.get(key, set())
        # vary/fix(再選択可)が1つでもあれば自動固定ではない=テスト必要
        return bool(ks) and 'vary' not in ks and 'fix' not in ks

    # 差分Bが検出した「変わった質問」= step2 vary軸(b_q) ∪ 生diffの選択肢変更質問。
    #   これを人作成TC(ゴールド)が列として行使=カバーしているかで採点
    #   (条件・選択肢・規格を質問単位で統合。観点テキストは比較しない=モードノイズ回避)。
    #   b_q(step2 vary) は選択肢追加/削除/規格計上driven の質問を既にフィルタ済みで含む
    #   ため、生diffの選択肢(ノイズ多)は使わず b_q のカバレッジで測る。
    changed_all = {_canon(q, rename_map) for q in bq if q}
    cond_skip = {q for q in changed_all if _is_autofixed(q)}  # 自動固定=テスト不要
    cond = changed_all - cond_skip                            # 採点対象=変更質問
    exercised = sig.get('g_exercised', set())
    cond_match = cond & exercised                             # TCが行使=カバー
    cond_miss = cond - exercised                              # 改定なのにTC未カバー=乖離
    # 計上行(期待)の照合は構造ベース: 代価表行の追加/削除が生JSON差分にあれば、
    #   計上行の増減はそれで裏付けられる(S列名とJSON変数名は揃わないため名前照合しない)。
    daika_add = any(c == '代価表' and k == '追加' for c, k, *_ in rawrows)
    daika_del = any(c == '代価表' and k == '削除' for c, k, *_ in rawrows)
    k_add, k_rm = sig.get('keijo_added', set()), sig.get('keijo_removed', set())
    exp = k_add | k_rm
    exp_match = set()
    if daika_add:
        exp_match |= k_add
    if daika_del:
        exp_match |= k_rm
    exp_miss = exp - exp_match

    renamed = sig.get('renamed', set())
    pattern = ''.join([
        '条' if cond else '-',
        '期' if sig['exp_changed'] else '-',
        '選' if (sig['teki_added'] or sig['teki_removed']) else '-',
        '規' if (sig['kik_added'] or sig['kik_removed']) else '-',
        '文' if renamed else '-',
    ])
    main_total = len(cond) + len(exp)
    main_match = len(cond_match) + len(exp_match)
    rate = f'{100*main_match/main_total:.0f}%' if main_total else '-'

    summary = [name, pattern,
               len(cond), len(cond_match), len(cond_miss),
               len(exp), len(exp_match), len(exp_miss),
               len(cond_skip),
               len(renamed),
               len(sig['teki_added']), len(sig['teki_removed']),
               len(sig['kik_added']), len(sig['kik_removed']),
               f'{main_match}/{main_total}' if main_total else '-', rate]

    detail = []
    for q in sorted(cond):
        detail.append([name, '変更質問(差分B)', q,
                       '○(TCがカバー)' if q in exercised else '×(TC未カバー=乖離)'])
    for q in sorted(cond_skip):
        detail.append([name, '変更質問(自動固定)', q, '仕様内(再選択不可・テスト不要)'])
    for q in sorted(sig.get('renamed', set())):
        detail.append([name, '文字変更(リネーム)', q, '○(差分Bの表示名変更で説明)'])
    for s in sorted(exp):
        v = ('○(代価表追加で裏付け)' if (s in exp_match) else '×(代価表構造変更なし)')
        detail.append([name, '計上行', s, v])
    return summary, detail


SUM_HEADER = ['工種', 'パターン(条期選規文)',
              '変更質問(差分B)', 'TCカバー', 'TC未カバー(乖離)',
              '計上行 変化', '計上行 裏付', '計上行 未裏付',
              '変更質問:自動固定(除外)',
              '文字変更(リネーム)',
              '選択肢適切さ:増', '選択肢適切さ:減', '規格計上:増', '規格計上:減',
              '主指標一致', '主指標一致率']
DET_HEADER = ['工種', 'カテゴリ', '項目', '差分B説明']


def _append(path, header, rows):
    import csv as _csv
    import io as _io
    exists = os.path.exists(path) and os.path.getsize(path) > 0
    buf = _io.StringIO()
    w = _csv.writer(buf, lineterminator='\r\n')
    if not exists:
        w.writerow(header)
    for r in rows:
        w.writerow(r)
    with open(path, 'a', encoding='cp932', newline='') as f:
        f.write(buf.getvalue())


def main(prefixes, reset=False):
    os.makedirs(OUT_DIR, exist_ok=True)
    sum_path = os.path.join(OUT_DIR, '4カテゴリ精度検証_集計.csv')
    det_path = os.path.join(OUT_DIR, '4カテゴリ精度検証_詳細.csv')
    if reset:
        for p in (sum_path, det_path):
            try:
                open(p, 'w', encoding='cp932').close()   # truncate (remove不可なmountに対応)
            except OSError:
                pass
    targets = eligible(prefixes)
    print(f'対象工種: {len(targets)}件')
    for name, oldj, newj, gold in targets:
        work = os.path.join(OUT_DIR, '_work', name)
        try:
            s, d = score_koshu(name, oldj, newj, gold, work)
            _append(sum_path, SUM_HEADER, [s])
            _append(det_path, DET_HEADER, d)
            print(f'  [OK] {s[1]}  一致{s[14]}({s[15]})  {name}')
        except Exception as e:
            _append(sum_path, SUM_HEADER,
                    [[name, 'ERROR', '', '', '', '', '', '', '', '', '', '', '', '', '',
                      e.__class__.__name__]])
            print(f'  [ERR] {name}: {e.__class__.__name__}: {e}')
    print(f'\n集計: {sum_path}\n詳細: {det_path}')


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if a != '--reset']
    main(args or None, reset='--reset' in sys.argv)
