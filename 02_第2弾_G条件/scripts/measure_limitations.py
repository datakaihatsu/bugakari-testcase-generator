# -*- coding: utf-8 -*-
"""制限事項ガイド(xlsx)に載せる実測値を、回帰40工種の改定後JSONから採取する。

出力: scripts/_limitations_measured.json （gen_limitations_xlsx.py が読む）

なぜ計測するのか:
  制限は版で変わる（v1.3.0 で静的破砕工が 13→20列 になったように）。
  資料の数値を手書きにすると必ず古びるので、版ごとに再計測して差し替える。

使い方: python scripts/measure_limitations.py
"""
import os
import sys
import io
import json
import collections
import contextlib

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # 02_第2弾_G条件
ROOT = os.path.dirname(BASE)
sys.path.insert(0, BASE)
for _p in ('engine', 'step2_proposals', 'step3_csv'):
    sys.path.insert(0, os.path.join(BASE, _p))

import yaml                                              # noqa: E402
from bugakari_json import BugakariJSON                   # noqa: E402
import gjoken_reach as GR                                # noqa: E402
import generate_proposals_new as PN                      # noqa: E402
from generate_proposals import TestPlanGenerator         # noqa: E402

# BugakariDefine.cs:200 SitsumonKindEnum（表示名は DisplayText 属性の値）
KIND_NAME = {
    0: 'なし', 8: '単価選択', 9: '補正係数', 11: '子代価', 14: '運転費',
    17: '変数直接入力', 19: '万能表', 20: '汎用補正', 102: '代価表切替',
    105: '計算表→代価表', 107: '規格名計上', 113: '代価表単価→計算表',
    115: 'ランク供用係数', 119: 'フロー切替', 120: '機労材反映',
    201: '文字列反映', 202: '労務補正を指定', 203: 'メッセージ',
    204: 'コメント', 205: '終点',
}
# Sitsumon000 を継承する型＝ユーザーに質問ダイアログとして表示される（Sitsumon000.cs:7-11）
VISIBLE_KINDS = (8, 9, 11, 14, 17, 19, 20)


def classify(bj, json_path):
    """analyze_reach の is_col と同一判定を、質問ごとの理由付きで返す。

    戻り値: ({SitsumonNo: (判定コード, SitsumonKind)}, 既定歩行で到達した質問No集合)
    """
    walker = GR._Walker(bj)
    auto = TestPlanGenerator(None, json_path)

    def is_k19(sn):
        return (bj.sitsumon_by_no.get(sn) or {}).get('SitsumonKind') == 19

    order1 = GR._discover(bj, walker,
                          lambda sn: is_k19(sn) and not GR.is_system_branch(bj, sn))[0]
    gen = PN.NewKotsuPlanGenerator(json_path)
    gen._selectable_rows = lambda sn: GR.selectable_rows(bj, sn)
    gen._discover_reachable = lambda: order1
    with contextlib.redirect_stdout(io.StringIO()):
        plan = gen.generate()
    kind_of = {int(r[3]): r[2] for r in plan}
    reason_of = {int(r[3]): r[5] for r in plan}
    base_reached = set(walker.walk({}).order)

    def promoted(sn):
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
        return kind_of.get(sn) in ('vary', 'fix') or promoted(sn)

    def can_vary(sn):
        return (is_k19(sn) and not GR.is_system_branch(bj, sn)
                and (kind_of.get(sn) == 'vary' or promoted(sn)))

    order, _scope, walks, _pos, _obs = GR._discover(bj, walker, can_vary)

    def opens_on_forced_route(sn):
        for sel, vis in walks:
            if sn not in vis:
                continue
            try:
                if auto._opens_on_forced_route(sn, dict(sel)):
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    reached = set(order)
    out = {}
    for s in bj.data.get('SitsumonItem', []):
        sn, kind = s.get('SitsumonNo'), s.get('SitsumonKind')
        if kind not in (17, 19):
            continue
        if sn not in reached:
            out[sn] = ('E1_到達しない', kind)
            continue
        if kind == 17:
            try:
                ok = bool(auto._is_ui_visible_axis(s))
            except Exception:  # noqa: BLE001
                ok = False
            out[sn] = ('列' if ok else 'E2_数量入力だがUI非可視', kind)
            continue
        if GR.is_system_branch(bj, sn):
            out[sn] = ('E3_システム分岐', kind)
            continue
        if not user_pickable(sn):
            out[sn] = ('E4_人が選べない(自動確定)', kind)
            continue
        try:
            if auto._all_rows_autoselect(s):
                out[sn] = ('E5_全行が自動選択条件', kind)
            elif bool(auto._is_ui_visible_axis(s)) or opens_on_forced_route(sn):
                out[sn] = ('列', kind)
            else:
                out[sn] = ('E6_UI非可視(定数/凍結)', kind)
        except Exception:  # noqa: BLE001
            out[sn] = ('E7_判定例外', kind)
    return out, base_reached


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    tg = yaml.safe_load(open(os.path.join(BASE, 'regression/targets.yaml'),
                             encoding='utf-8'))
    kind_total = collections.Counter()
    kind_reached = collections.Counter()
    kind_col = collections.Counter()
    kind_koshu = collections.Counter()
    reason = collections.Counter()          # (kind, 判定) -> 件数
    hidden_hit = []                         # 可視なのに列にならない型に到達した工種
    n_ok, errors = 0, []

    for t in tg['targets']:
        kid = t['id']
        jp = os.path.join(ROOT, '工種別', kid, 'input', t['new'])
        if not os.path.exists(jp):
            errors.append('%s: JSONなし' % kid)
            continue
        try:
            bj = BugakariJSON(jp)
            with contextlib.redirect_stdout(io.StringIO()):
                cls, base = classify(bj, jp)
        except Exception as e:                              # noqa: BLE001
            errors.append('%s: %s' % (kid, type(e).__name__))
            continue
        n_ok += 1
        here, here_reached = set(), set()
        for s in bj.data.get('SitsumonItem', []):
            k, sn = s.get('SitsumonKind'), s.get('SitsumonNo')
            kind_total[k] += 1
            here.add(k)
            if sn in base:
                kind_reached[k] += 1
                here_reached.add(k)
        for k in here:
            kind_koshu[k] += 1
        for sn, (why, k) in cls.items():
            reason[(k, why)] += 1
            if why == '列':
                kind_col[k] += 1
        miss = sorted(k for k in here_reached if k in (8, 11, 14))
        if miss:
            hidden_hit.append([kid, '/'.join(KIND_NAME[k] for k in miss)])
        print('  %s' % kid)

    data = {
        'koshu_count': n_ok,
        'errors': errors,
        'kinds': [{
            'kind': k,
            'name': KIND_NAME.get(k, '(enum外:%s)' % k),
            'visible': k in VISIBLE_KINDS,
            'total': kind_total.get(k, 0),
            'reached': kind_reached.get(k, 0),
            'col': kind_col.get(k, 0),
            'koshu': kind_koshu.get(k, 0),
        } for k in sorted(set(list(KIND_NAME) + list(kind_total)))],
        'reasons': [{'kind': k, 'name': KIND_NAME.get(k, str(k)),
                     'why': w, 'count': c}
                    for (k, w), c in sorted(reason.items(),
                                            key=lambda x: (x[0][0], x[0][1]))],
        'hidden_hit': hidden_hit,
    }
    out = os.path.join(BASE, 'scripts', '_limitations_measured.json')
    with io.open(out, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print('\n計測完了: %d工種 / 保存: %s' % (n_ok, out))
    if errors:
        print('エラー: %s' % errors)


if __name__ == '__main__':
    main()
