# ③(G条件→改定後TC)の精度確認ハーネス(使い捨て)
# 回帰targets(diffモード)ごとに: 20=gen_gjoken(旧JSON) / 30=gen_gjoken(新JSON=人作成の土台)
# → gen_tc_from_gjoken 実行 → 合格TCと構造突合(軸列・行数・テスト区分・入力値。期待:は対象外=制限事項)
# 使い方: PYTHONIOENCODING=utf-8 python3 _精度確認③_使い捨て.py [開始idx] [終了idx]
import sys
import os
import io
import csv
import re
import tempfile
import contextlib

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
sys.path.insert(0, BASE)
import gen_gjoken            # noqa: E402
import gen_tc_from_gjoken    # noqa: E402

RESULT = os.path.join(BASE, '運用案件', '_精度確認', '③精度確認_合格TC構造突合.csv')


def read_csv_any(p):
    for enc in ('utf-8-sig', 'cp932'):
        try:
            return list(csv.reader(io.StringIO(open(p, encoding=enc).read())))
        except UnicodeDecodeError:
            continue
    raise RuntimeError(p)


def targets():
    import yaml  # 無ければ手動パースに切替
    y = yaml.safe_load(open(os.path.join(BASE, 'regression', 'targets.yaml'),
                            encoding='utf-8'))
    return [t for t in y['targets'] if t.get('mode') == 'diff']


def targets_manual():
    txt = open(os.path.join(BASE, 'regression', 'targets.yaml'), encoding='utf-8').read()
    out = []
    cur = None
    for line in txt.splitlines():
        m = re.match(r'\s*- id:\s*(\S+)', line)
        if m:
            cur = {'id': m.group(1)}
            out.append(cur)
            continue
        if cur is None:
            continue
        for k in ('mode', 'old', 'new'):
            m = re.match(rf'\s+{k}:\s*(\S+)', line)
            if m:
                cur[k] = m.group(1)
    return [t for t in out if t.get('mode') == 'diff']


def struct(rows):
    """(軸列名リスト, [区分+軸値 の行リスト])。
    期待:/代価表行と数量/観点/規格名計上 は構造比較の対象外(期待値=制限事項)。"""
    h = rows[0]
    end = len(h)
    stop = {gen_tc_from_gjoken.DAIKA_COL, '選択肢の適切さ確認', '規格名計上'}
    for i, x in enumerate(h):
        if str(x).startswith('期待:') or str(x) in stop:
            end = i
            break
    data = [[r[1]] + [c.strip() for c in r[2:end]]
            for r in rows[1:] if r and str(r[0]).startswith('TC')]
    return h[2:end], data


def compare(kid, gold_rows, ours_rows, notes):
    ha, da = struct(gold_rows)
    hb, db = struct(ours_rows)
    issues = []
    order_only = False
    if set(ha) != set(hb):
        only_g = [x for x in ha if x not in hb]
        only_o = [x for x in hb if x not in ha]
        issues.append(f'軸差: 合格のみ{only_g} 生成のみ{only_o}')
    elif ha != hb:
        order_only = True
    if len(da) != len(db):
        issues.append(f'行数: 合格{len(da)} 生成{len(db)}')
    if set(ha) == set(hb) and len(da) == len(db):
        # 名前ベースで行比較(列順差は不問)
        bad = []
        for i, (x, y) in enumerate(zip(da, db)):
            dx = dict(zip(['区分'] + ha, x))
            dy = dict(zip(['区分'] + hb, y))
            if dx != dy:
                bad.append(i + 1)
        if bad:
            issues.append(f'行不一致: TC{bad}')
    if issues:
        status = '差分あり'
    else:
        status = '一致(列順差)' if order_only else '一致'
    return [kid, status, f'{len(ha)}/{len(hb)}', f'{len(da)}/{len(db)}',
            ' / '.join(issues), notes]


def main():
    try:
        ts = targets()
    except Exception:
        ts = targets_manual()
    lo = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    hi = int(sys.argv[2]) if len(sys.argv) > 2 else len(ts)
    ts = ts[lo:hi]
    results = []
    for t in ts:
        kid = t['id']
        kdir = os.path.join(ROOT, '工種別', kid)
        old = os.path.join(kdir, 'input', t['old'])
        new = os.path.join(kdir, 'input', t['new'])
        gold = os.path.join(kdir, 'output', 'step3.0_テストケース【合格】.csv')
        if not (os.path.exists(old) and os.path.exists(new) and os.path.exists(gold)):
            results.append([kid, 'スキップ', '', '', '入力/合格TC欠落', ''])
            print(kid, 'スキップ(ファイル欠落)')
            continue
        try:
            work = tempfile.mkdtemp()
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                c20 = gen_gjoken.build_g(old, os.path.join(work, '20_叩き台G条件'))
                c30 = gen_gjoken.build_g(new, os.path.join(work, '30_人作成G条件'))
                out_csv = gen_tc_from_gjoken.run(c20, c30, old, os.path.join(work, 'out'))
            log = buf.getvalue()
            notes = ''
            m = re.findall(r'新規列: (\S+)', log)
            if m:
                notes += f'新規列{m} '
            if '追加=' in log:
                adds = re.findall(r"追加=\[([^\]]*)\]", log)
                adds = [a for a in adds if a.strip()]
                if adds:
                    notes += f'選択肢追加あり '
            row = compare(kid, read_csv_any(gold), read_csv_any(out_csv), notes.strip())
            results.append(row)
            print(row[0], row[1], row[4][:120])
        except Exception as e:
            results.append([kid, 'エラー', '', '', f'{type(e).__name__}: {e}', ''])
            print(kid, 'エラー', str(e)[:120])
    # 追記保存
    os.makedirs(os.path.dirname(RESULT), exist_ok=True)
    exists = os.path.exists(RESULT)
    old_rows = read_csv_any(RESULT)[1:] if exists else []
    hdr = ['工種', '結果', '軸数(合格/生成)', '行数(合格/生成)', '差分内容', 'メモ']
    keep = [r for r in old_rows if r and r[0] not in {x[0] for x in results}]
    from bugakari_json import BugakariJSON
    BugakariJSON.write_csv([hdr] + keep + results, RESULT)
    print('保存:', RESULT)


if __name__ == '__main__':
    sys.path.insert(0, os.path.join(BASE, 'engine'))
    main()
