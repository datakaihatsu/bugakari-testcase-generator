# -*- coding: utf-8 -*-
"""設計F試作 v3: 累積探索(2パス) + 局所走査の注。
 規則1: 予約変数(先頭が '~' = ~SYSV/~NJI/~4WH*、または c~4wh/a~4wh)で駆動される質問はシステム分岐 → 変えない・列にしない。K~/L~ 等の計設定変数は人が選ぶ条件なので対象外
 規則2: 変える/列にするのは「ユーザが選べる質問」= step2分類が vary/fix、
        または強制なしの歩行で auto 以外の経緯で行が決まったもの
"""
import sys, os, time, io, contextlib
BASE = os.environ.get('G2_BASE')
for p in ('', 'engine', 'step2_proposals', 'step3_csv'):
    sys.path.insert(0, os.path.join(BASE, p))
from bugakari_json import BugakariJSON
from flow_walker import FlowWalker
import generate_proposals_new as PN
from generate_proposals import TestPlanGenerator


def s019_of(bj, sn):
    s = bj.sitsumon019_by_no.get(sn); seen = set(); cur = sn
    while s is None:
        sc = (bj.sitsumon_by_no.get(cur, {}) or {}).get('ShortCutSitsumonNo')
        if not sc or sc in seen:
            break
        seen.add(sc); cur = sc; s = bj.sitsumon019_by_no.get(sc)
    return s


def sel_rows(bj, sn):
    s = s019_of(bj, sn)
    if not s:
        return []
    return [r['RowID'] for r in s.get('SitRows', [])
            if r.get('Visible', True) and not r.get('IsFixed', False)]


def is_system(bj, sn):
    """規則1: AutoSelectJoken の駆動変数に '~' を含む(予約変数) → システム分岐"""
    s = s019_of(bj, sn)
    if not s:
        return False
    for r in s.get('SitTabRows', []):
        v = (r.get('AutoSelectJoken') or {}).get('VarName') or ''
        if v.startswith('~') or v in ('c~4wh', 'a~4wh'):
            return True
    return False


def label_of(bj, sn, rid):
    s019 = s019_of(bj, sn)
    if not s019:
        return str(rid)
    cells = {(c['RowID'], c['ColID']): c.get('Value') for c in s019['SitTabCells']}
    for c in s019['SitCols']:
        if c.get('Visible', True) is False:
            continue
        v = cells.get((rid, c['ColID']))
        if v and not str(v).replace('.', '').replace('-', '').isdigit():
            return str(v).replace('\r\n', ' ').strip()
    return str(rid)


class OrderedVisited(set):
    def __init__(self, lst):
        super().__init__(lst); self.order = list(dict.fromkeys(lst)); self.src = {}


class Walker:
    def __init__(self, bj):
        self.bj = bj; self.n = 0; self.cache = {}

    def walk(self, sel):
        key = tuple(sorted(sel.items()))
        if key in self.cache:
            return self.cache[key]
        self.n += 1
        res = FlowWalker(self.bj, vary_selections=dict(sel)).walk()
        v = OrderedVisited(list(res.get('visited_sitsumons', [])))
        v.src = dict(res.get('row_sources', {}))
        self.cache[key] = v
        return v


def discover(bj, W, can_vary):
    order = []; scope_of = {}; seen = set(); walks = []; walkpos = {}; obs_src = {}

    def absorb(vis, sel):
        new = []
        for i, sn in enumerate(vis.order):
            if sn not in sel:
                obs_src.setdefault(sn, set()).add(vis.src.get(sn))
            if sn not in seen:
                seen.add(sn); order.append(sn); scope_of[sn] = dict(sel); new.append(sn)
                walkpos[sn] = (i, len(order))
        return new

    v0 = W.walk({}); walks.append(({}, v0)); absorb(v0, {})
    q = list(order)
    while q:
        sn = q.pop(0)
        if not can_vary(sn):
            continue
        rows = sel_rows(bj, sn)
        if len(rows) < 2:
            continue
        parent = W.walk(scope_of[sn])
        if os.environ.get('PROTO_CTX', '1') == '1' and parent.src.get(sn) == 'auto':
            continue  # この局面では変数が行を決めている → 人は選べない
        for rid in rows:
            sel = dict(scope_of[sn]); sel[sn] = rid
            v = W.walk(sel); walks.append((sel, v)); q += absorb(v, sel)
    return order, scope_of, walks, walkpos, obs_src


def run(json_path, want_text=False):
    t0 = time.time(); bj = BugakariJSON(json_path); W = Walker(bj)
    auto = TestPlanGenerator(None, json_path)
    k19 = lambda sn: bj.sitsumon_by_no.get(sn, {}).get('SitsumonKind') == 19
    # --- パス1: 規則1のみで掘る → step2分類と src 観測を得る ---
    order1, _, _, _, obs1 = discover(bj, W, lambda sn: k19(sn) and not is_system(bj, sn))
    g = PN.NewKotsuPlanGenerator(json_path)
    g._selectable_rows = lambda sn: sel_rows(bj, sn)
    g._discover_reachable = lambda: order1
    with contextlib.redirect_stdout(io.StringIO()):
        plan = g.generate()
    kind_of = {int(r[3]): r[2] for r in plan}
    reason_of = {int(r[3]): r[5] for r in plan}

    def user_pickable(sn):
        k = kind_of.get(sn)
        if k in ('vary', 'fix'):
            return True
        if k == 'auto':
            # レベル変数閉・UI非可視・全行AutoSelect は人が選べない。デフォルト実行(再選択可能)のみ例外
            return 'デフォルト実行' in (reason_of.get(sn) or '') and any(s != 'auto' for s in obs1.get(sn, set()))
        return any(s != 'auto' for s in obs1.get(sn, set()))

    # --- パス2: 規則1+2 で掘る ---
    MODE = os.environ.get('PROTO_MODE', 'src')   # src: 従来 / step2: 分類に揃える
    base_reached = set(W.walk({}).order)
    def promoted(sn):   # 現行 gen_gjoken._promote_reselectable_for_coverage 相当
        r = reason_of.get(sn) or ''
        sit = bj.sitsumon_by_no.get(sn, {})
        try:
            aa = auto._all_rows_autoselect(sit)
        except Exception:
            aa = True
        reach_ok = (sn in base_reached) if os.environ.get('PROTO_PROMOTE','base')=='base' else True
        return kind_of.get(sn) == 'auto' and 'デフォルト実行' in r and reach_ok and not aa
    if MODE == 'step2':
        def user_pickable(sn):
            return kind_of.get(sn) in ('vary', 'fix') or promoted(sn)
        can_vary = lambda sn: k19(sn) and not is_system(bj, sn) and (kind_of.get(sn) == 'vary' or promoted(sn))
    else:
        can_vary = lambda sn: k19(sn) and not is_system(bj, sn) and user_pickable(sn)
    order, scope_of, walks, walkpos, obs = discover(bj, W, can_vary)

    def is_col(sn):
        sit = bj.sitsumon_by_no.get(sn, {}); k = sit.get('SitsumonKind')
        if k == 17:
            try:
                return bool(auto._is_ui_visible_axis(sit))
            except Exception:
                return False
        if k != 19 or is_system(bj, sn) or not user_pickable(sn):
            return False
        try:
            return bool(auto._is_ui_visible_axis(sit)) and not auto._all_rows_autoselect(sit)
        except Exception:
            return False

    pos = {sn: walkpos.get(sn, (10 ** 6, i)) for i, sn in enumerate(order)}
    cols = {}
    for sn in order:
        sit = bj.sitsumon_by_no.get(sn, {})
        if sit.get('SitsumonKind') not in (17, 19):
            continue
        name = sit.get('Mesho', '')
        c = cols.setdefault(name, {'members': set(), 'first': pos[sn],
                                   'kind': sit.get('SitsumonKind'), 'shown': False})
        c['members'].add(sn); c['first'] = min(c['first'], pos[sn])
        if is_col(sn):
            c['shown'] = True
    col_list = sorted([kv for kv in cols.items() if kv[1]['shown']], key=lambda kv: kv[1]['first'])

    notes = []
    for xi, (xname, xc) in enumerate(col_list):
        if xc['kind'] != 19:
            continue
        xs = [s for s in xc['members'] if is_col(s) and len(sel_rows(bj, s)) >= 2]
        if not xs:
            continue
        scopes = [(sel, vis) for sel, vis in walks if vis & set(xs)]
        if not scopes:
            continue
        rows = sel_rows(bj, xs[0])
        per = []
        for sel, vis in scopes:
            hit = next(s for s in xs if s in vis); d = {}
            if os.environ.get('PROTO_CTX', '1') == '1' and hit not in sel and vis.src.get(hit) == 'auto':
                continue  # この局面では自動確定 → 人が選ぶ局面ではない
            for rid in rows:
                s2 = dict(sel); s2[hit] = rid; d[rid] = W.walk(s2)
            per.append(d)
        if not per:
            continue
        for rid in rows:
            for yi in range(xi + 1, len(col_list)):
                ymem = col_list[yi][1]['members']
                if not all(not (d[rid] & ymem) for d in per):
                    continue
                if any((d[r2] & ymem) for d in per for r2 in rows if r2 != rid):
                    notes.append((xi + 1, xname, rid, label_of(bj, xs[0], rid), yi + 1, col_list[yi][0]))
    by = {}
    for xi, xn, rid, lab, yi, yn in notes:
        by.setdefault((xi, rid), set()).add(yi)
    lines = {(xi, frozenset(ys)) for (xi, rid), ys in by.items()}
    res = {'json': os.path.basename(json_path), 'n_cols': len(col_list),
           'n_note_lines': len(lines), 'n_notes': len(notes),
           'walks_total': W.n, 'sec': round(time.time() - t0, 1)}
    if want_text:
        res['cols'] = [n for n, _ in col_list]
        agg = {}
        for xi, xn, rid, lab, yi, yn in notes:
            agg.setdefault((xi, xn, rid, lab), []).append((yi, yn))
        res['notes'] = [f'G{xi}条件「{xn}」で「{lab}」→ ' + '・'.join(f'G{yi}「{yn}」' for yi, yn in ys)
                        for (xi, xn, rid, lab), ys in agg.items()]
    return res


if __name__ == '__main__':
    r = run(sys.argv[1], True)
    for k, v in r.items():
        if k in ('cols', 'notes'):
            print(k)
            for x in v:
                print('   ', x)
        else:
            print(k, '=', v)
