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
from generate_proposals import TestPlanGenerator         # noqa: E402
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


def _promote_reselectable_for_coverage(plan_csv, bj, gen, rows):
    """G条件網羅性の補完(2026-07-06): 再選択可能なのに auto の質問
    (例 41 杭体内補強鉄筋計上区分=EK1デフォルト実行) を G条件導出用TCに限り
    vary 昇格し、非既定選択肢で開く質問(例 10本当り杭体内補強鉄筋数量)を列化する。
    条件(慎重に限定・07の副作用2種から学習):
      - 種別=auto のみ (fix昇格は既存G条件の列構成を変えた)
      - **基準(1行目)TCで値を持つ=到達済みの列のみ** (未到達autoを昇格すると
        step3の「どのTCでも到達しないvary除去」で列ごと消える。例 07空練りモルタル)
      - SitsumonKind=19 / 選択可能行>=2 / 全行AutoSelectJokenでない
    戻り値: 昇格件数。昇格があった場合のみ呼び元で再生成する(2パス)。
    ※TC生成パイプライン本体(step2/step3)は不変更。gen_gjoken内のみ。"""
    import csv as _csv
    import io as _io
    # 基準行(最初のTC行)で値を持つ質問No集合
    data = [r for r in rows[1:] if r and r[0].startswith('TC')]
    axcols = list(getattr(gen, '_axes_columns_out', []))
    reached = set()
    if data:
        base = data[0]
        for k, ax in enumerate(axcols):
            ci = 2 + k
            v = (base[ci] if ci < len(base) else '').strip()
            if v not in ('', '-'):
                reached.add(int(ax['SitsumonNo']))
    txt = None
    for enc in ('utf-8-sig', 'cp932'):
        try:
            txt = open(plan_csv, encoding=enc).read()
            break
        except UnicodeDecodeError:
            continue
    prows = list(_csv.reader(_io.StringIO(txt)))
    changed = 0
    for r in prows[1:]:
        if len(r) < 6 or r[2] != 'auto':
            continue
        try:
            sit_no = int(r[3])
        except (ValueError, TypeError):
            continue
        if sit_no not in reached:
            continue
        sit = bj.sitsumon_by_no.get(sit_no)
        if not sit or sit.get('SitsumonKind') != 19:
            continue
        s019 = _sit019_for(bj, sit_no)
        if not s019:
            continue
        sel = [x for x in s019.get('SitRows', [])
               if x.get('Visible', True) and not x.get('IsFixed', False)]
        if len(sel) < 2:
            continue
        rowid2tab = {x.get('RowID'): x for x in s019.get('SitTabRows', [])}
        tabs = [rowid2tab.get(x.get('RowID')) for x in sel]
        if all(t and t.get('AutoSelectJoken') for t in tabs):
            continue  # 全行自動選択=ユーザ選択不可 → 昇格しない
        r[2] = 'vary'
        r[4] = re.sub(r'\(固定\)$', '', r[4]).strip()
        r[5] = 'G条件網羅(到達済み再選択可能autoの全選択肢行使)'
        changed += 1
    if changed:
        BugakariJSON.write_csv(prows, plan_csv)
    return changed


def _derive_glist(bj, gen, rows, json_path):
    """step3出力(rows)からG条件(g_list, notes)を導出する(analyzeの本体)。"""
    # --- 規格名計上のエコー計上変数 ---
    #   列にならない自動確定エコー質問(例: 12 機械区分71=機械質量区分のエコー)が
    #   規格名計上を持つ場合、その計上は「行駆動変数(例 J2)を設定する質問=選択条件」で
    #   決まる。よってエコー質問の行駆動変数を集め、その変数を書き込む列に○を付ける
    #   (合格TCが 機械区分 を規格名計上と観点化するのと整合)。
    _vis = TestPlanGenerator(None, json_path)  # UI可視判定(列になるか)用
    echo_kikaku_vars = set()
    for _s in bj.data.get('SitsumonItem', []):
        _sn = _s.get('SitsumonNo')
        if not gen._has_kikaku_keijo(_sn):
            continue
        if _vis._is_ui_visible_axis(_s):
            continue  # 自身が列になる質問は列側で○が付く
        _s019 = bj.sitsumon019_by_no.get(_sn)
        if not _s019:
            continue
        for _r in _s019.get('SitTabRows', []):
            _v = (_r.get('AutoSelectJoken') or {}).get('VarName')
            if _v:
                echo_kikaku_vars.add(_v)

    def _writes_vars(sit_no):
        s019 = bj.sitsumon019_by_no.get(sit_no)
        if not s019:
            return set()
        return {c.get('VarName') for c in s019.get('SitCols', []) if c.get('VarName')}

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
        # 規格名計上: この条件(グループ内いずれかの質問)が規格名/規格を代価表へ計上するか。
        #   判定は TC生成側 ColumnTCGenerator._has_kikaku_keijo を再利用(KikakuKeijoGaia9 /
        #   ShortCut継承 / SitTabCols.KikakuKeijoNaiyo)。合格TCの規格名計上列と整合。
        kikaku = (any(gen._has_kikaku_keijo(int(s)) for s in sits)
                  or any(_writes_vars(int(s)) & echo_kikaku_vars for s in sits))
        g_list.append({'name': name, 'sits': sits, 'vals': merged,
                       'opt_labels': opt_labels,
                       'label2mk': label2mk, 'disp2label': disp2label, 'numeric': numeric,
                       'kikaku': kikaku})

    # --- 分岐の注: 統合列の"-"パターンから導出 ---
    notes = []
    pend = []
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
                pos = (gx['opt_labels'].index(v_label)
                       if v_label in gx['opt_labels'] else -1)
                pend.append({'xi': xi, 'name': gx['name'], 'mk': mk,
                             'label': v_label, 'pos': pos, 'ylabel': ylabel})

    # 同一ソース列・同一対象列の注は1行に統合(読みやすさ 2026-07-06 #21要望)
    #   連番3件以上: ①「A」～③「C」のいずれか / それ以外: ①「A」・②「B」のいずれか
    used = [False] * len(pend)
    for i, e in enumerate(pend):
        if used[i]:
            continue
        grp = [e]
        used[i] = True
        for j in range(i + 1, len(pend)):
            if used[j]:
                continue
            f = pend[j]
            if f['xi'] == e['xi'] and f['ylabel'] == e['ylabel']:
                grp.append(f)
                used[j] = True
        if len(grp) == 1:
            sel = f"{e['mk']}「{e['label']}」を選択した場合は"
        else:
            grp.sort(key=lambda g: g['pos'])
            poss = [g['pos'] for g in grp]
            contiguous = all(b - a == 1 for a, b in zip(poss, poss[1:])) and -1 not in poss
            if contiguous and len(grp) >= 3:
                a, b = grp[0], grp[-1]
                sel = (f"{a['mk']}「{a['label']}」～{b['mk']}「{b['label']}」の"
                       f"いずれかを選択した場合は")
            else:
                sel = ('・'.join(f"{g['mk']}「{g['label']}」" for g in grp)
                       + 'のいずれかを選択した場合は')
        notes.append(f"G{e['xi']+1}条件「{e['name']}」で{sel}、"
                     f"{e['ylabel']} を入力する必要はない。")

    return g_list, notes


def analyze(json_path):
    """G条件の解析結果を返す(CSV出力なし)。

    Returns: (bj, gen, g_list, notes)
      g_list[i] = {'name', 'sits', 'vals', 'opt_labels', 'label2mk',
                   'disp2label', 'numeric', 'kikaku'}
    ③(G条件→改定後TC生成)等の他スクリプトから列⇔質問No対応を得るための共有API。
    """
    bj = BugakariJSON(json_path)

    # --- TC生成(新規工種モード step2+step3) ---
    work = tempfile.mkdtemp()
    plan_csv = os.path.join(work, 'plan.csv')
    run_plan_new(json_path, plan_csv)
    gen = ColumnTCGenerator(plan_csv, json_path)
    rows = gen.generate()
    g_list, notes = _derive_glist(bj, gen, rows, json_path)

    # --- 網羅性補完(2026-07-06・41対応) ---
    #   到達済み再選択可能autoを昇格して再導出し、「列・選択肢が一切失われない」
    #   場合のみ採用(増える方向のみ許容)。玉突きで劣化する場合(例 07)は破棄。
    plan_backup = open(plan_csv, encoding='cp932', errors='replace').read()
    if _promote_reselectable_for_coverage(plan_csv, bj, gen, rows):
        gen2 = ColumnTCGenerator(plan_csv, json_path)
        rows2 = gen2.generate()
        g2, n2 = _derive_glist(bj, gen2, rows2, json_path)
        name2opts = {}
        for g in g2:
            name2opts.setdefault(g['name'], set()).update(g['opt_labels'])
        degraded = False
        for g in g_list:
            if g['name'] not in name2opts or \
                    not set(g['opt_labels']) <= name2opts[g['name']]:
                degraded = True
                break
        if degraded:
            with open(plan_csv, 'w', encoding='cp932', newline='') as f:
                f.write(plan_backup)
            print('  [網羅性補完] 列/選択肢の喪失を検知 → 昇格を破棄(元の結果を使用)')
        else:
            gen, rows, g_list, notes = gen2, rows2, g2, n2

    gen._rows_cache = rows  # ③(gen_tc_from_gjoken)が基準行の到達系列を知るための添付
    return bj, gen, g_list, notes


def build_g(json_path, out_dir=None, label=None):
    bj, gen, g_list, notes = analyze(json_path)

    # --- CSV 出力 ---
    name, unit = _header(bj)
    out = []
    out.append(['施工区分/入力条件'] + [f'G{i+1}' for i in range(len(g_list))])
    # 規格名計上行は G番号の直下(2行目)に置き、条件名→選択肢の並びを隣接させる。
    out.append(['規格名計上'] + ['○' if g.get('kikaku') else '' for g in g_list])
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
    safe = re.sub(r'[\\/:*?"<>|]', '_', name).strip()
    fname = f'Gaia入力基準表_{safe}({unit}){suffix}.csv'
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
