"""
第2弾 G条件生成器 (TC生成ロジックベース)

既存JSON -> 施工単価入力基準表様式(G条件) を逆生成する。

【設計方針】
  G条件の「列(条件)・列順・分岐の注」は、合格実績のあるTC生成ロジック(step2+step3)を
  そのまま活用して導出する(step3の「到達しない軸の除去」「自動確定/定数の除外」
  「組合せごとの到達判定(-)」をそのまま正とする)。

  - 列(G1..Gn): step3テストケースの入力軸列(フロー=積算出現順)。
  - 同名重複列の統合(質問No.キー):
      (a) canonical質問No.(ShortCut解決後)が同じ列 = 同一質問の別フロー位置 → 1本化。
      (b) 同名かつ全行で値が同一の列 = sibling補完による冗長重複 → 1本化。
      ※質問No.が別で値も異なる同名列(例 #41「条件選択」=土質/杭径, #40「荷卸し時間」の
        運搬物ルート別テーブル)は別条件として別列で維持する。
  - 選択肢    : JSONから全選択肢を取得(_g_options。説明ラベル列を優先)。重複ラベルは畳む。
  - (注)      : step3の行ごとの"-"パターンから導出。或る条件で或る選択肢を選ぶと
                後続条件が全行"-"(=UI非表示)になる場合のみ「入力不要」を注記。
                注の選択肢ラベル・番号は表の選択肢と一致させる。
  - 対象外    : 単価/数量(基準書由来の知識)。ヘッダのタイトル行も出さない(均等割維持)。

使い方: python3 gen_gjoken.py <JSON> [出力ディレクトリ]
"""
import sys
import os
import re
import tempfile

BASE = os.path.dirname(os.path.abspath(__file__))
for p in ('engine', 'step2_proposals', 'step3_csv'):
    sys.path.insert(0, os.path.join(BASE, p))

from bugakari_json import BugakariJSON                   # noqa: E402
from generate_proposals_new import run as run_plan_new   # noqa: E402
from generate_csv import ColumnTCGenerator               # noqa: E402


def _mk(oi):
    """選択肢マーカー。①..⑳(0-19)、20以降は (21) 形式(cp932安全・番号欠落防止)。"""
    return chr(9312 + oi) if oi < 20 else f'({oi + 1})'


def _header(bj):
    daika = (bj.data.get('Daika') or [{}])[0]
    return (daika.get('DaikaTitle') or daika.get('Mesho') or ''), (daika.get('AtariTani') or '')


def _sit019_for(bj, sit_no):
    s019 = bj.sitsumon019_by_no.get(sit_no)
    if s019 is None:
        for s in bj.data.get('SitsumonItem', []):
            if s.get('SitsumonNo') == sit_no and s.get('ShortCutSitsumonNo'):
                s019 = bj.sitsumon019_by_no.get(s['ShortCutSitsumonNo'])
                break
    return s019


def _canon_no(bj, sit_no):
    """ShortCut を解決した canonical 質問No.(同一質問の別位置を同一視)。"""
    seen = set()
    cur = sit_no
    while cur is not None and cur not in seen:
        seen.add(cur)
        s = bj.sitsumon_by_no.get(cur, {})
        sc = s.get('ShortCutSitsumonNo')
        if not sc or sc == cur:
            break
        cur = sc
    return cur


def _g_options(bj, sit_no):
    """G条件の選択肢を [(row_id, ラベル), ...] で返す。説明テキスト列(非VarName・非数値)優先。"""
    s019 = _sit019_for(bj, sit_no)
    if not s019:
        return []
    cells = {}
    for c in s019.get('SitTabCells', []):
        cells.setdefault((c.get('RowID'), c.get('ColID')), c.get('Value'))
    sel = [r['RowID'] for r in s019.get('SitRows', [])
           if r.get('Visible', True) and not r.get('IsFixed', False)]

    def _isnum(v):
        try:
            float(str(v))
            return True
        except ValueError:
            return False

    best_col, best_score = None, (-1, -1, -1)
    for c in s019.get('SitCols', []):
        if c.get('Visible', True) is False:
            continue
        cid = c.get('ColID')
        vals = [str(cells.get((r, cid), '') or '').strip() for r in sel]
        vals = [v for v in vals if v]
        if not vals:
            continue
        score = (0 if c.get('VarName') else 1,
                 sum(1 for v in vals if not _isnum(v)),
                 len(set(vals)))
        if score > best_score:
            best_score, best_col = score, cid
    if best_col is None:
        return []
    out = []
    for r in sel:
        v = str(cells.get((r, best_col), '') or '').strip()
        v = re.sub(r'^【[^】]*】[\s　]+', '', v).replace('\r\n', ' ').strip()
        out.append((r, v or f'Row{r}'))
    return out


def _compatible(a, b):
    """2列の値ベクトルが「同一(冗長)」とみなせるか。各行で両方が非'-'なら一致必須。"""
    for x, y in zip(a, b):
        xz = x not in ('', '-')
        yz = y not in ('', '-')
        if xz and yz and x != y:
            return False
    return True


def build_g(json_path, out_dir=None, label=None):
    bj = BugakariJSON(json_path)

    # --- TC生成(新規工種モード step2+step3) ---
    work = tempfile.mkdtemp()
    plan_csv = os.path.join(work, 'plan.csv')
    run_plan_new(json_path, plan_csv)
    gen = ColumnTCGenerator(plan_csv, json_path)
    rows = gen.generate()
    axcols = list(getattr(gen, '_axes_columns_out', []))   # 列->質問No対応(順序=CSV軸列順)
    header = rows[0]
    data = [r for r in rows[1:] if r and r[0].startswith('TC')]

    # CSVの軸列は header[2 : 2+len(axcols)]。各列の質問No.と値ベクトルを取得。
    raw = []
    for k, ax in enumerate(axcols):
        ci = 2 + k
        sit = int(ax['SitsumonNo'])
        name = re.sub(r'\(固定\)$', '', (ax.get('列ラベル') or ax.get('軸名') or '')).strip()
        vals = [(r[ci] if ci < len(r) else '').strip() for r in data]
        raw.append({'sit': sit, 'canon': _canon_no(bj, sit), 'name': name, 'vals': vals})

    # --- 同名重複列の統合(質問No.キー) ---
    used = [False] * len(raw)
    groups = []
    for i in range(len(raw)):
        if used[i]:
            continue
        grp = [i]
        used[i] = True
        for j in range(i + 1, len(raw)):
            if used[j]:
                continue
            same_canon = raw[i]['canon'] == raw[j]['canon']
            same_name_ident = (raw[i]['name'] == raw[j]['name']
                               and _compatible(raw[i]['vals'], raw[j]['vals']))
            if same_canon or same_name_ident:
                grp.append(j)
                used[j] = True
        groups.append(grp)

    # 統合列を構築
    g_list = []
    for grp in groups:
        # canonical Sit(sit==canon)を先頭に。値は先頭優先の非'-'。
        idxs = sorted(grp, key=lambda k: 0 if raw[k]['sit'] == raw[k]['canon'] else 1)
        sits = [raw[k]['sit'] for k in idxs]
        name = raw[idxs[0]]['name']
        n = len(data)
        merged = []
        for i in range(n):
            v = '-'
            for k in idxs:
                cell = raw[k]['vals'][i]
                if cell not in ('', '-'):
                    v = cell
                    break
            merged.append(v)
        if all(v in ('', '-') for v in merged):
            continue  # どのルートでも開かない列は出さない
        # 選択肢: グループ内Sitの和集合(ラベル重複は畳む)
        opts = []
        seen_lbl = set()
        for sit in sits:
            for rid, lbl in _g_options(bj, sit):
                if lbl in seen_lbl:
                    continue
                seen_lbl.add(lbl)
                opts.append((sit, rid, lbl))
        if not opts:
            for v in merged:
                if v not in ('', '-') and v not in seen_lbl:
                    seen_lbl.add(v)
                    opts.append((None, None, v))
        opt_labels = [lbl for _, _, lbl in opts]
        # 数量を直接入力する質問(Kind17)は「任意」ではなく「(実数入力)」＋「(単位)」で表示。
        #   単位は Sitsumon017.TaniMesho。選択肢番号(①②)は付けない。
        numeric = any((bj.sitsumon_by_no.get(s, {}) or {}).get('SitsumonKind') == 17
                      for s in sits)
        if numeric:
            unit = ''
            for s in sits:
                for e in bj.data.get('Sitsumon017', []):
                    if e.get('SitsumonNo') == s and (e.get('TaniMesho') or '').strip():
                        unit = (e.get('TaniMesho') or '').strip()
                        break
                if unit:
                    break
            opt_labels = ['(実数入力)'] + ([f'({unit})'] if unit else [])
        label2mk = {lbl: _mk(i) for i, lbl in enumerate(opt_labels)}
        # TC表示値 -> 説明ラベル(row_id経由・Sitごと)。注のラベル/番号を表と一致。
        disp2label = {}
        for sit in sits:
            rid2label = {rid: lbl for s, rid, lbl in opts if s == sit}
            try:
                tc_rows = gen._get_axis_rows(sit)
            except Exception:
                tc_rows = []
            for r in tc_rows:
                d = str(r.get('display', '')).strip()
                rid = r.get('row_id')
                if d and rid in rid2label and d not in disp2label:
                    disp2label[d] = rid2label[rid]
        g_list.append({'name': name, 'vals': merged, 'opt_labels': opt_labels,
                       'label2mk': label2mk, 'disp2label': disp2label, 'numeric': numeric})

    # --- 分岐の注: 統合列の"-"パターンから導出 ---
    notes = []
    for xi, gx in enumerate(g_list):
        xvals = []
        for v in gx['vals']:
            if v not in ('', '-') and v not in xvals:
                xvals.append(v)
        for v in xvals:
            ridx = [i for i, cv in enumerate(gx['vals']) if cv == v]
            gated = []
            for yi in range(xi + 1, len(g_list)):
                gy = g_list[yi]
                all_dash = all(gy['vals'][i] in ('', '-') for i in ridx)
                some_input = any(cv not in ('', '-') for cv in gy['vals'])
                if all_dash and some_input:
                    gated.append((yi, gy['name']))
            if gated:
                v_label = gx['disp2label'].get(v, v)
                mk = gx['label2mk'].get(v_label, '')
                ylabel = '・'.join(f'G{yi+1}条件「{yn}」' for yi, yn in gated)
                notes.append(f'G{xi+1}条件「{gx["name"]}」で{mk}「{v_label}」を選択した場合は、'
                             f'{ylabel} を入力する必要はない。')

    # --- CSV 出力 ---
    name, unit = _header(bj)
    out = []
    out.append(['施工区分/入力条件'] + [f'G{i+1}' for i in range(len(g_list))])
    out.append(['各種(条件名)'] + [g['name'] for g in g_list])
    maxopt = max((len(g['opt_labels']) for g in g_list), default=0)
    for oi in range(maxopt):
        row = ['']
        for g in g_list:
            if oi < len(g['opt_labels']):
                cell = g['opt_labels'][oi]
                row.append(cell if g.get('numeric') else f'{_mk(oi)}{cell}')
            else:
                row.append('')
        out.append(row)
    out.append([])
    out.append(['(注)'])
    for i, nn in enumerate(notes, 1):
        out.append(['', f'{i}. {nn}'])

    if out_dir is None:
        out_dir = os.path.dirname(json_path)
    if label is None:
        base = os.path.basename(out_dir.rstrip(os.sep))
        label = '叩き台' if '叩き台' in base else ('人作成' if '人作成' in base else '')
    suffix = f'_{label}' if label else ''
    fname = f'Gaia入力基準表_{name}({unit}){suffix}.csv'
    os.makedirs(out_dir, exist_ok=True)
    out_csv = os.path.join(out_dir, fname)
    BugakariJSON.write_csv(out, out_csv)
    print(f'G条件生成: {out_csv}  (G数={len(g_list)} / 注={len(notes)})')
    for i, g in enumerate(g_list, 1):
        print(f'  G{i}: {g["name"]}  選択肢={g["opt_labels"]}')
    for nn in notes:
        print('  注:', nn)
    return out_csv


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python3 gen_gjoken.py <JSON> [出力ディレクトリ]')
        sys.exit(1)
    build_g(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
