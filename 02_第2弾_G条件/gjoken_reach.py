# -*- coding: utf-8 -*-
"""確定設計F: G条件の列と(注)を歩掛JSONの到達走査から導く。

【なぜ別に持つのか】
  G条件表（この歩掛にどんな条件があり、何を選ぶと何が閉じるか＝網羅）と
  テストケース（改定を確認するのに何を試せば十分か＝間引き）は答える問いが違う。
  従来は後者の結果（step3 のテストケース行列の '-' パターン）を前者に流用していたため、
    - 相関を因果と取り違えた偽の注が出る（3件目(a)）
    - 真の注が出ない（2件目）
    - 深い枝の条件が列にすらならない（3件目(b)。静的破砕工で7列欠落）
  が起きていた。本モジュールは G条件専用の走査を行い、TC生成(step2/step3)は変更しない。

【アルゴリズム】
  F-R1 累積探索: 既定で歩く → 見つけた質問を「見つけたときの選択の組を保ったまま」
       1行ずつ変えて歩く → 新しい質問が出たら同様に掘る。
       従来の「1つだけ変えて他は既定に戻す」では、2つ以上同時に変えないと届かない
       質問に永遠に到達しなかった。
  F-R12 注は局所走査: 各列 X が到達している**各局面**で X だけを各行に変え、
       (a) すべての局面で Y が閉じる かつ (b) 同じ局面で X を別の行にすると Y が開く
       （＝ X が真の原因）を満たすものだけ注にする。全組合せは使わない
       （静的破砕工の全組合せは448万通り。局所走査なら数百歩行で済む）。

使い方（gen_gjoken から呼ばれる）:
    from gjoken_reach import analyze_reach
    res = analyze_reach(bj, json_path)   # {'cols': [...], 'notes': [...], 'walks': n}
"""
import os
import sys
import io
import contextlib

BASE = os.path.dirname(os.path.abspath(__file__))
for _p in ('engine', 'step2_proposals', 'step3_csv'):
    _full = os.path.join(BASE, _p)
    if _full not in sys.path:
        sys.path.insert(0, _full)

from flow_walker import FlowWalker                      # noqa: E402
from bugakari_json import choice_display_map            # noqa: E402
import generate_proposals_new as PN                     # noqa: E402
from generate_proposals import TestPlanGenerator        # noqa: E402


# F-R6: 探索歩行数の上限。実測最大は 4,487 歩行（回帰40工種）。
MAX_WALKS = 50000


class ReachExplosionError(RuntimeError):
    """探索が上限を超えた（本ツールの守備範囲外の大規模歩掛）。"""


class _Visited(set):
    """到達質問の集合＋到達順＋行の決まり方(row_sources)。"""

    def __init__(self, lst):
        super().__init__(lst)
        self.order = list(dict.fromkeys(lst))
        self.src = {}


class _Walker:
    """FlowWalker のラッパ。同じ選択の組は一度しか歩かない。"""

    def __init__(self, bj):
        self.bj = bj
        self.n = 0
        self._cache = {}

    def walk(self, sel):
        key = tuple(sorted(sel.items()))
        if key in self._cache:
            return self._cache[key]
        if self.n >= MAX_WALKS:
            raise ReachExplosionError(
                'この歩掛は条件の組合せが多すぎるため、本ツールでは生成できません'
                '（探索が上限 %s 回を超えました）。通常サイズの歩掛を対象とした'
                'ツールのため、この歩掛は対象外です。'
                '必要な場合は工種キーを開発担当へお知らせください。' % format(MAX_WALKS, ','))
        self.n += 1
        res = FlowWalker(self.bj, vary_selections=dict(sel)).walk()
        v = _Visited(list(res.get('visited_sitsumons', [])))
        v.src = dict(res.get('row_sources', {}))
        self._cache[key] = v
        return v


def _s019(bj, sn):
    return bj.sitsumon019_by_no.get(sn)


def selectable_rows(bj, sn):
    """選択可能行。ShortCut は索引側で解決済み（確定設計B）。"""
    s = _s019(bj, sn)
    if not s:
        return []
    return [r['RowID'] for r in s.get('SitRows', [])
            if r.get('Visible', True) and not r.get('IsFixed', False)]


def is_system_branch(bj, sn):
    """F-R3 システム分岐か。

    行の自動選択条件の駆動変数が **先頭 `~`**（~SYSV/~NJI/~4WH* 等）または
    **c~4wh / a~4wh** の質問は、Gaia が内部で決める分岐であり人が選ぶ条件ではない。
    ※ `K~`/`L~` 等の計設定変数は人が選ぶ条件なので対象外にしてはいけない
      （「~ を含む」で切ると 37 工事区分(K~KK)・公園照明 賃料長期割引(L~LK) まで消える）。
    """
    s = _s019(bj, sn)
    if not s:
        return False
    for r in s.get('SitTabRows', []):
        v = (r.get('AutoSelectJoken') or {}).get('VarName') or ''
        if v.startswith('~') or v in ('c~4wh', 'a~4wh'):
            return True
    return False


def choice_label(bj, sn, row_id):
    s = _s019(bj, sn)
    if not s:
        return str(row_id)
    disp, _c, _e = choice_display_map(s)
    return disp.get(row_id, str(row_id))


def _discover(bj, walker, can_vary):
    """F-R1 累積探索。戻り: (order, scope_of, walks, walkpos, obs_src)"""
    order, scope_of, seen = [], {}, set()
    walks, walkpos, obs_src = [], {}, {}

    def absorb(vis, sel):
        added = []
        for i, sn in enumerate(vis.order):
            if sn not in sel:
                obs_src.setdefault(sn, set()).add(vis.src.get(sn))
            if sn not in seen:
                seen.add(sn)
                order.append(sn)
                scope_of[sn] = dict(sel)
                walkpos[sn] = (i, len(order))
                added.append(sn)
        return added

    base = walker.walk({})
    walks.append(({}, base))
    absorb(base, {})
    queue = list(order)
    while queue:
        sn = queue.pop(0)
        if not can_vary(sn):
            continue
        rows = selectable_rows(bj, sn)
        if len(rows) < 2:
            continue
        for rid in rows:
            sel = dict(scope_of[sn])
            sel[sn] = rid
            v = walker.walk(sel)
            walks.append((sel, v))
            queue += absorb(v, sel)
    return order, scope_of, walks, walkpos, obs_src


def analyze_reach(bj, json_path):
    """G条件の列と注を導く。

    戻り値: {
      'cols':  [{'name', 'sits'(set), 'kind'}, ...]  … フロー順
      'notes': [(x列index, x選択肢row_id, [y列index, ...]), ...]
      'walks': 歩行回数
    }
    """
    walker = _Walker(bj)
    auto = TestPlanGenerator(None, json_path)

    def is_k19(sn):
        return (bj.sitsumon_by_no.get(sn) or {}).get('SitsumonKind') == 19

    # --- パス1: システム分岐だけ除いて掘り、step2 の軸分類を得る ---
    order1, _s1, _w1, _p1, obs1 = _discover(
        bj, walker, lambda sn: is_k19(sn) and not is_system_branch(bj, sn))
    gen = PN.NewKotsuPlanGenerator(json_path)
    gen._selectable_rows = lambda sn: selectable_rows(bj, sn)
    gen._discover_reachable = lambda: order1
    with contextlib.redirect_stdout(io.StringIO()):
        plan = gen.generate()
    kind_of = {int(r[3]): r[2] for r in plan}
    reason_of = {int(r[3]): r[5] for r in plan}
    base_reached = set(walker.walk({}).order)

    def promoted(sn):
        """再選択可能な auto（現行 _promote_reselectable_for_coverage の一般化）。"""
        if kind_of.get(sn) != 'auto':
            return False
        if 'デフォルト実行' not in (reason_of.get(sn) or ''):
            return False
        if sn not in base_reached:
            return False
        try:
            return not auto._all_rows_autoselect(bj.sitsumon_by_no.get(sn) or {})
        except Exception:  # noqa: BLE001
            return False

    def user_pickable(sn):
        """F-R2 人が選べる質問か（現行 step2 の分類にそのまま揃える＝案X）。"""
        return kind_of.get(sn) in ('vary', 'fix') or promoted(sn)

    def can_vary(sn):
        return (is_k19(sn) and not is_system_branch(bj, sn)
                and (kind_of.get(sn) == 'vary' or promoted(sn)))

    # --- パス2: 規則F-R2/F-R3 で掘り直す ---
    order, scope_of, walks, walkpos, obs = _discover(bj, walker, can_vary)

    def opens_on_forced_route(sn):
        """F-R5: 静的に非可視でも「上流選択で駆動変数が範囲外→開く」なら列にする
        （現行 generate_proposals._opens_on_forced_route の移植。#40 条件選択）。"""
        for sel, vis in walks:
            if sn not in vis:
                continue
            try:
                if auto._opens_on_forced_route(sn, dict(sel)):
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def is_col(sn):
        """F-R7 列にするか。ゲートは製品(step2/step3)と同じ。"""
        sit = bj.sitsumon_by_no.get(sn) or {}
        kind = sit.get('SitsumonKind')
        if kind == 17:
            try:
                return bool(auto._is_ui_visible_axis(sit))
            except Exception:  # noqa: BLE001
                return False
        if kind != 19 or is_system_branch(bj, sn) or not user_pickable(sn):
            return False
        try:
            if auto._all_rows_autoselect(sit):
                return False
            return bool(auto._is_ui_visible_axis(sit)) or opens_on_forced_route(sn)
        except Exception:  # noqa: BLE001
            return False

    # --- F-R8 同名質問を1列に統合 / F-R9 列順は発見した歩行内の位置 ---
    pos = {sn: walkpos.get(sn, (10 ** 6, i)) for i, sn in enumerate(order)}
    cols = {}
    for sn in order:
        sit = bj.sitsumon_by_no.get(sn) or {}
        if sit.get('SitsumonKind') not in (17, 19):
            continue
        name = sit.get('Mesho', '')
        c = cols.setdefault(name, {'name': name, 'sits': set(), 'first': pos[sn],
                                   'kind': sit.get('SitsumonKind'), 'shown': False})
        c['sits'].add(sn)
        c['first'] = min(c['first'], pos[sn])
        if is_col(sn):
            c['shown'] = True
    col_list = sorted([c for c in cols.values() if c['shown']], key=lambda c: c['first'])

    # --- F-R12 注（局所走査＋真因フィルタ）---
    notes = []
    for xi, xc in enumerate(col_list):
        if xc['kind'] != 19:
            continue
        xs = [s for s in xc['sits'] if is_col(s) and len(selectable_rows(bj, s)) >= 2]
        if not xs:
            continue
        rows = selectable_rows(bj, xs[0])
        scopes = [(sel, vis) for sel, vis in walks if vis & set(xs)]
        per = []
        for sel, vis in scopes:
            hit = next(s for s in xs if s in vis)
            per.append({rid: walker.walk({**sel, hit: rid}) for rid in rows})
        if not per:
            continue
        for rid in rows:
            for yi in range(xi + 1, len(col_list)):   # F-R13 対象は後続列のみ
                ymem = col_list[yi]['sits']
                if not all(not (d[rid] & ymem) for d in per):
                    continue          # (a) すべての局面で閉じる、を満たさない
                if any((d[r2] & ymem) for d in per for r2 in rows if r2 != rid):
                    notes.append((xi, rid, yi))   # (b) X だけ変えると開く＝真因

    out_cols = [{'name': c['name'], 'sits': sorted(c['sits']), 'kind': c['kind']}
                for c in col_list]
    return {'cols': out_cols, 'notes': notes, 'walks': walker.n}
