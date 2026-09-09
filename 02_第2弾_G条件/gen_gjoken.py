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

from bugakari_json import (BugakariJSON, choice_display_map,  # noqa: E402
                           numeric_input_spec)
from generate_proposals_new import run as run_plan_new   # noqa: E402
from generate_proposals import TestPlanGenerator         # noqa: E402
from generate_csv import ColumnTCGenerator, CombinationExplosionError  # noqa: E402
from gjoken_reach import analyze_reach, ReachExplosionError  # noqa: E402,F401


def _mk(oi):
    """選択肢マーカー。①..⑳(0-19)、20以降は (21) 形式(cp932安全・番号欠落防止)。"""
    return chr(9312 + oi) if oi < 20 else f'({oi + 1})'


def _strip_code(v):
    """表示文字列の先頭 Gaia入力条件コード(【A=1】等)を除去。内部の統合/注記判定は
    コード非依存で行う(TC側=generate_csv がコードを併記表示するようになったため、
    ここで剥がして従来の識別ロジックを保つ。2026-07-08)。"""
    return re.sub(r'^【[^】]*】[\s　]+', '', str(v or '')).strip()


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
    """G条件の選択肢を [(row_id, 内部キー, 表示文字列), ...] で返す。

    表示列の選定は ③(generate_csv._get_axis_rows) と同じ共通関数
    `choice_display_map` を使う（確定設計C C-R1）。従来は①③で別々のスコアを
    使っており、同じ質問の選択肢が「30km以上 60km未満」と「0.1」に食い違っていた。
    """
    s019 = _sit019_for(bj, sit_no)
    if not s019:
        return []
    disp_map, _dc, _ec = choice_display_map(s019)
    sel = [r['RowID'] for r in s019.get('SitRows', [])
           if r.get('Visible', True) and not r.get('IsFixed', False)]
    out = []
    for r in sel:
        raw = str(disp_map.get(r, '') or '').strip()
        # raw = 表示用(元のまま。Gaia入力条件コード【A=1】等を残す)
        # v   = 内部識別子(コード除去。畳み込み/突合キーは従来どおり)
        v = re.sub(r'^【[^】]*】[\s　]+', '', raw).strip()
        out.append((r, v or f'Row{r}', raw or f'Row{r}'))
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
        vals = [_strip_code(r[ci] if ci < len(r) else '') for r in data]
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
            for rid, lbl, rawlbl in _g_options(bj, sit):
                if lbl in seen_lbl:
                    continue
                seen_lbl.add(lbl)
                opts.append((sit, rid, lbl, rawlbl))
        if not opts:
            for v in merged:
                if v not in ('', '-') and v not in seen_lbl:
                    seen_lbl.add(v)
                    opts.append((None, None, v, v))
        opt_labels = [lbl for _, _, lbl, _ in opts]
        opt_raws = [rl for _, _, _, rl in opts]  # 表示用(コード併記)。内部はopt_labels
        # 数量を直接入力する質問(Kind17)は「任意」ではなく「(実数入力)」＋「(単位)」で表示。
        #   単位は Sitsumon017.TaniMesho。選択肢番号(①②)は付けない。
        numeric = any((bj.sitsumon_by_no.get(s, {}) or {}).get('SitsumonKind') == 17
                      for s in sits)
        if numeric:
            # 確定設計C C-R5/R7/R8: 単位と入力範囲は共通関数から取る。
            #   ShortCut 子は Sitsumon017 を持たないため、自分の番号だけで探すと
            #   単位も範囲も取れない(4件目②)。範囲があれば「(範囲: 0 < 値)」を1セル追加。
            unit, rng = '', ''
            for s in sits:
                spec = numeric_input_spec(bj, s)
                if not spec:
                    continue
                unit = unit or spec.get('unit') or ''
                rng = rng or spec.get('range') or ''
                if unit and rng:
                    break
            opt_labels = ['(実数入力)'] + ([f'({unit})'] if unit else [])
            if rng:
                opt_labels.append(f'(範囲: {rng})')
            opt_raws = list(opt_labels)  # 数量入力はコード併記なし
        label2mk = {lbl: _mk(i) for i, lbl in enumerate(opt_labels)}
        # TC表示値 -> 説明ラベル(row_id経由・Sitごと)。注のラベル/番号を表と一致。
        disp2label = {}
        for sit in sits:
            rid2label = {rid: lbl for s, rid, lbl, _ in opts if s == sit}
            try:
                tc_rows = gen._get_axis_rows(sit)
            except Exception:
                tc_rows = []
            for r in tc_rows:
                d = _strip_code(r.get('display', ''))
                rid = r.get('row_id')
                if d and rid in rid2label and d not in disp2label:
                    disp2label[d] = rid2label[rid]
        # 規格名計上: この条件(グループ内いずれかの質問)が規格名/規格を代価表へ計上するか。
        #   判定は TC生成側 ColumnTCGenerator._has_kikaku_keijo を再利用(KikakuKeijoGaia9 /
        #   ShortCut継承 / SitTabCols.KikakuKeijoNaiyo)。合格TCの規格名計上列と整合。
        kikaku = (any(gen._has_kikaku_keijo(int(s)) for s in sits)
                  or any(_writes_vars(int(s)) & echo_kikaku_vars for s in sits))
        g_list.append({'name': name, 'sits': sits, 'vals': merged,
                       'opt_labels': opt_labels, 'opt_raws': opt_raws,
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
                # 確定設計A A-R8: 条件名を書かない短い形。対象は「、」区切りの番号のみ。
                ylabel = '、'.join(f'G{yi+1}条件' for yi, _yn in gated)
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
        # 確定設計A A-R8: 選択肢も印だけの短い形。
        #   実数入力(印が無い)が起点の注だけ「任意」を残す。
        def _sel_of(g):
            return g['mk'] if g['mk'] else f"「{g['label']}」"
        if len(grp) == 1:
            sel = f"{_sel_of(e)}を選択した場合は"
        else:
            grp.sort(key=lambda g: g['pos'])
            poss = [g['pos'] for g in grp]
            contiguous = all(b - a == 1 for a, b in zip(poss, poss[1:])) and -1 not in poss
            if contiguous and len(grp) >= 3:
                sel = f"{_sel_of(grp[0])}～{_sel_of(grp[-1])}のいずれかを選択した場合は"
            else:
                sel = '・'.join(_sel_of(g) for g in grp) + 'のいずれかを選択した場合は'
        notes.append(f"G{e['xi']+1}条件で{sel}、"
                     f"{e['ylabel']}を入力する必要はない。")

    return g_list, notes


def analyze(json_path):
    """G条件の解析結果を返す(CSV出力なし)。

    Returns: (bj, gen, g_list, notes)
      g_list[i] = {'name', 'sits', 'opt_labels', 'opt_raws', 'label2mk',
                   'numeric', 'kikaku'}
    ③(gen_tc_from_gjoken)は 'name' と 'sits' のみを使う（確定設計F F-R15）。

    確定設計F: 列・選択肢・注は **G条件専用の到達走査**(gjoken_reach)から作る。
      従来はテストケース行列の '-' パターンから帰納していたため、偽の注が出たり
      深い枝の列が落ちたりしていた（2件目・3件目）。
      TC生成(step2/step3)は呼ぶが **step3(テストケース生成)は実行しない**
      （02 大型ブレーカが組合せ上限超で①ごと失敗するため。確定設計B B-3(4)）。
      規格名計上の判定 `_has_kikaku_keijo` は JSON しか見ないので生成不要。
    """
    bj = BugakariJSON(json_path)

    # 規格名計上○の判定に ColumnTCGenerator を使う（generate() は呼ばない）
    work = tempfile.mkdtemp()
    plan_csv = os.path.join(work, 'plan.csv')
    run_plan_new(json_path, plan_csv)
    gen = ColumnTCGenerator(plan_csv, json_path)

    reach = analyze_reach(bj, json_path)
    print('  [到達走査] 歩行 %d 回 / 列 %d / 注 %d'
          % (reach['walks'], len(reach['cols']), len(reach['notes'])))

    # --- 規格名計上のエコー計上変数（従来と同じ判定） ---
    _vis = TestPlanGenerator(None, json_path)
    echo_kikaku_vars = set()
    for _s in bj.data.get('SitsumonItem', []):
        _sn = _s.get('SitsumonNo')
        if not gen._has_kikaku_keijo(_sn):
            continue
        if _vis._is_ui_visible_axis(_s):
            continue
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

    g_list = []
    for col in reach['cols']:
        sits = list(col['sits'])
        opts, seen_lbl = [], set()
        for sit in sits:
            for rid, lbl, rawlbl in _g_options(bj, sit):
                if lbl in seen_lbl:
                    continue
                seen_lbl.add(lbl)
                opts.append((sit, rid, lbl, rawlbl))
        opt_labels = [lbl for _, _, lbl, _ in opts]
        opt_raws = [rl for _, _, _, rl in opts]
        rid_of = {}
        for _s, rid, lbl, _r in opts:
            rid_of.setdefault(rid, lbl)
        numeric = any((bj.sitsumon_by_no.get(s, {}) or {}).get('SitsumonKind') == 17
                      for s in sits)
        if numeric:
            unit, rng = '', ''
            for s in sits:
                spec = numeric_input_spec(bj, s)
                if not spec:
                    continue
                unit = unit or spec.get('unit') or ''
                rng = rng or spec.get('range') or ''
                if unit and rng:
                    break
            opt_labels = ['(実数入力)'] + ([f'({unit})'] if unit else [])
            if rng:
                opt_labels.append(f'(範囲: {rng})')
            opt_raws = list(opt_labels)
        label2mk = {lbl: _mk(i) for i, lbl in enumerate(opt_labels)}
        kikaku = (any(gen._has_kikaku_keijo(int(s)) for s in sits)
                  or any(_writes_vars(int(s)) & echo_kikaku_vars for s in sits))
        g_list.append({'name': col['name'], 'sits': sits,
                       'opt_labels': opt_labels, 'opt_raws': opt_raws,
                       'label2mk': label2mk, 'numeric': numeric, 'kikaku': kikaku,
                       '_rid_label': rid_of})

    # --- 注: (x列, x選択肢row_id, y列) を「同一x・同一対象集合」で1行にまとめる ---
    by_choice = {}
    for xi, rid, yi in reach['notes']:
        by_choice.setdefault((xi, rid), []).append(yi)
    grouped = {}
    for (xi, rid), ys in by_choice.items():
        grouped.setdefault((xi, tuple(sorted(ys))), []).append(rid)

    notes = []
    for (xi, ys), rids in sorted(grouped.items(),
                                 key=lambda kv: (kv[0][0], min(kv[0][1]))):
        g = g_list[xi]
        # 選択肢row_id → 表示ラベル → 番号（表の並びと一致させる）
        poss = []
        for rid in rids:
            lbl = g['_rid_label'].get(rid)
            poss.append(g['opt_labels'].index(lbl)
                        if lbl in g['opt_labels'] else -1)
        poss = sorted(p for p in poss if p >= 0)
        # 確定設計A A-R8: 条件名を書かない短い形
        if g['numeric'] or not poss:
            sel = '「任意」を選択した場合は'
        elif len(poss) == 1:
            sel = f'{_mk(poss[0])}を選択した場合は'
        else:
            contiguous = all(b - a == 1 for a, b in zip(poss, poss[1:]))
            if contiguous and len(poss) >= 3:
                sel = f'{_mk(poss[0])}～{_mk(poss[-1])}のいずれかを選択した場合は'
            else:
                sel = '・'.join(_mk(p) for p in poss) + 'のいずれかを選択した場合は'
        tgt = '、'.join(f'G{yi + 1}条件' for yi in ys)
        notes.append(f'G{xi + 1}条件で{sel}、{tgt}を入力する必要はない。')

    for g in g_list:
        g.pop('_rid_label', None)
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
                cell = g.get('opt_raws', g['opt_labels'])[oi]  # 表示はコード併記(元のまま)
                row.append(cell if g.get('numeric') else f'{_mk(oi)}{cell}')
            else:
                row.append('')
        out.append(row)
    out.append([])
    out.append(['(注)'])
    for i, nn in enumerate(notes, 1):
        out.append(['', f'{i}. {nn}'])
    # (外部設計メモ): 人が自由記述するための欄。テストケース生成には一切影響しない
    #   (③ read_gjoken はこの見出しで読み取りを打ち切る。2026-07-09 運用者フィードバック)。
    out.append([])
    out.append(['(外部設計メモ)'])
    out.append(['', '※自由記述欄です。ここへの記入はテストケース生成に影響しません。'])

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
