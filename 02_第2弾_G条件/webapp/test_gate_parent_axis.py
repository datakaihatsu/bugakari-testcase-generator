# -*- coding: utf-8 -*-
"""test_gate_parent_axis.py ― ③(G条件→TC) の (注)ゲートと親条件軸の単体テスト

2026-09-11 受入テストNG（静的破砕工 32-16123）への対応:
  (P1) (注)ゲートは列補完(reconcile_columns_with_g30)の後に当てる。
       前に当てると、後付けされる列を源泉とする注が読み飛ばされていた。
  (P1') reconcile 内の列位置表を、列を足すたびに更新する。
  (P2) 新規列を開閉する親条件が step2 計画に無ければ vary 軸として追加する
       (add_missing_gate_axes)。promote_gate_axes は計画にある軸の昇格しかできない。

実行: python3 02_第2弾_G条件/webapp/test_gate_parent_axis.py
"""
import os
import sys
import csv
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_BASE = os.path.dirname(_HERE)
sys.path.insert(0, _BASE)
for _p in ('engine', 'step2_proposals', 'step3_csv'):
    sys.path.insert(0, os.path.join(_BASE, _p))

import gen_tc_from_gjoken as G3M   # noqa: E402


def _g(cols, notes):
    """最小の G条件 dict。cols=[(name, opts, numeric)], notes=[(src_g, src_choice, targets)]"""
    return {
        'cols': [{'name': n, 'opts': list(o), 'opts_raw': list(o), 'numeric': num}
                 for n, o, num in cols],
        'notes': [{'src_g': s, 'src_name': cols[s][0], 'src_choice': c, 'targets': list(t)}
                  for s, c, t in notes],
    }


# 静的破砕工を縮めた形: 施工区分 → 無筋・鉄筋区分 → (無筋側の子 / 鉄筋側の子)
G30 = _g(
    cols=[('施工区分', ['転石の破砕', 'コンクリートの破砕'], False),          # 0
          ('無筋・鉄筋区分', ['無筋コンクリート', '鉄筋コンクリート'], False),   # 1
          ('無筋の子', ['A1', 'A2'], False),                                # 2
          ('鉄筋の子', ['B1', 'B2'], False)],                               # 3
    notes=[(0, '転石の破砕', [1, 2, 3]),
           (1, '無筋コンクリート', [3]),
           (1, '鉄筋コンクリート', [2])],
)

LEAD = ['テストID', 'テスト区分']


class TestReconcileThenGate(unittest.TestCase):
    """(P1)(P1') 源泉列が後付けされる注でも、ゲートが効く。"""

    def _rows_missing_parent(self):
        # TC生成本体が「無筋・鉄筋区分」を作れなかった状態を再現:
        # 施工区分・無筋の子・鉄筋の子 だけがあり、鉄筋の子に値が入っている。
        return [LEAD + ['施工区分', '無筋の子', '鉄筋の子'],
                ['TC-001', '差分', 'コンクリートの破砕', 'A1', 'B1'],
                ['TC-002', '差分', 'コンクリートの破砕', 'A2', 'B2'],
                ['TC-003', '差分', '転石の破砕', '-', '-']]

    def test_後付けされた親を源泉とする注が効く(self):
        rows = self._rows_missing_parent()
        rows = G3M.reconcile_columns_with_g30(rows, G30)
        rows = G3M.apply_g30_gating(rows, G30)          # 補完の後に当てる(run() と同順)
        h = rows[0]
        ip, ik = h.index('無筋・鉄筋区分'), h.index('鉄筋の子')
        for r in rows[1:3]:
            self.assertEqual(r[ip], '無筋コンクリート')  # 後付け＝先頭選択肢
            self.assertEqual(r[ik], '-', '無筋の行に鉄筋の子が残っている: %s' % r)
        self.assertEqual(rows[3][ip], '-')               # 転石の破砕 → 親も不要

    def test_前に当てる旧順序では効かない_退行検知用(self):
        """旧順序(ゲート→補完)だと鉄筋の子が残る。run() の順序が戻ったら気づけるように。"""
        rows = self._rows_missing_parent()
        rows = G3M.apply_g30_gating(rows, G30)
        rows = G3M.reconcile_columns_with_g30(rows, G30)
        ik = rows[0].index('鉄筋の子')
        self.assertEqual(rows[1][ik], 'B1')

    def test_同じループで足した列を源泉とするゲートも効く(self):
        """(P1') 親と子が両方欠けているとき、親を足した直後に子のゲート判定に使える。"""
        rows = [LEAD + ['施工区分'],
                ['TC-001', '差分', 'コンクリートの破砕'],
                ['TC-002', '差分', '転石の破砕']]
        rows = G3M.reconcile_columns_with_g30(rows, G30)
        h = rows[0]
        ip, ia, ik = h.index('無筋・鉄筋区分'), h.index('無筋の子'), h.index('鉄筋の子')
        # TC-001: 親=無筋(先頭) → 鉄筋の子は補完時点で '-' 、無筋の子は開く
        self.assertEqual(rows[1][ip], '無筋コンクリート')
        self.assertEqual(rows[1][ia], 'A1')
        self.assertEqual(rows[1][ik], '-')
        # TC-002: 施工区分=転石 → 親・子すべて '-'
        self.assertEqual([rows[2][ip], rows[2][ia], rows[2][ik]], ['-', '-', '-'])

    def test_run_の呼び順_ゲートは補完の後(self):
        """run() 内で apply_g30_gating が reconcile_columns_with_g30 より後にあること。"""
        import inspect
        src = inspect.getsource(G3M.run)
        self.assertLess(src.index('reconcile_columns_with_g30(out'),
                        src.index('apply_g30_gating(out'))


class TestAddMissingGateAxes(unittest.TestCase):
    """(P2) 新規列を開閉する親条件が計画に無ければ vary 軸として追加する。"""

    HEADER = ['軸ID', '軸名', '種別', 'SitsumonNo', '列ラベル', '変更理由', '備考', '強制行ID']

    def _plan(self, rows):
        d = tempfile.mkdtemp()
        p = os.path.join(d, 'step2.0_テスト計画.csv')
        with open(p, 'w', encoding='cp932', newline='') as f:
            w = csv.writer(f)
            w.writerow(self.HEADER)
            w.writerows(rows)
        return p

    def _read(self, p):
        with open(p, encoding='cp932', newline='') as f:
            return list(csv.DictReader(f))

    def setUp(self):
        # 20: 鉄筋の子は無い(改定で新規)。30: G30。col_map 20→30 は同名対応。
        self.g20 = _g(cols=[('施工区分', ['転石の破砕', 'コンクリートの破砕'], False),
                            ('無筋・鉄筋区分', ['無筋コンクリート', '鉄筋コンクリート'], False),
                            ('無筋の子', ['A1', 'A2'], False)], notes=[])
        self.diffs = {'col_map': {0: 0, 1: 1, 2: 2}, 'new_cols': [3]}
        self.g_list = [{'name': '施工区分', 'sits': [5]},
                       {'name': '無筋・鉄筋区分', 'sits': [6]},
                       {'name': '無筋の子', 'sits': [32]}]

    def test_計画に無い親が_vary軸として追加される(self):
        p = self._plan([['AX-001', '施工区分', 'vary', '5', '施工区分', '選択肢テキスト変更', 'SitsumonKind=19', '']])
        added = G3M.add_missing_gate_axes(p, self.g20, G30, self.diffs, self.g_list, {1})
        self.assertEqual(added, ['無筋・鉄筋区分'])
        plan = self._read(p)
        ax = [r for r in plan if r['軸名'] == '無筋・鉄筋区分']
        self.assertEqual(len(ax), 1)
        self.assertEqual(ax[0]['種別'], 'vary')
        self.assertEqual(ax[0]['SitsumonNo'], '6')
        self.assertEqual(ax[0]['軸ID'], 'AX-002')
        self.assertIn('親条件追加', ax[0]['変更理由'])

    def test_計画に既にある親は追加しない(self):
        p = self._plan([['AX-001', '無筋・鉄筋区分', 'fix', '6', '無筋・鉄筋区分', '', 'SitsumonKind=19', '']])
        added = G3M.add_missing_gate_axes(p, self.g20, G30, self.diffs, self.g_list, {1})
        self.assertEqual(added, [])
        self.assertEqual(len(self._read(p)), 1)

    def test_固定表記の列ラベルでも既存と判定する(self):
        p = self._plan([['AX-001', '無筋・鉄筋区分', 'fix', '6', '無筋・鉄筋区分(固定)', '', '', '']])
        self.assertEqual(G3M.add_missing_gate_axes(p, self.g20, G30, self.diffs, self.g_list, {1}), [])

    def test_B7で除外された親は追加しない(self):
        """eligible_src に無い源泉(全選択肢が対象列を閉じるゲート)は足さない。"""
        p = self._plan([['AX-001', '施工区分', 'vary', '5', '施工区分', '', '', '']])
        self.assertEqual(G3M.add_missing_gate_axes(p, self.g20, G30, self.diffs, self.g_list, set()), [])

    def test_親自身が新規列なら追加しない(self):
        """改定前JSONに質問が無い親は軸にできない(col_map に無い)。"""
        diffs = {'col_map': {0: 0, 2: 2}, 'new_cols': [1, 3]}
        p = self._plan([['AX-001', '施工区分', 'vary', '5', '施工区分', '', '', '']])
        self.assertEqual(G3M.add_missing_gate_axes(p, self.g20, G30, diffs, self.g_list, {1}), [])

    def test_改名された親は20側の名前で質問Noを引く(self):
        """30 で親の条件名が変わっていても、20 の名前で改定前JSONの質問を引く。"""
        g30 = _g(cols=[('施工区分', ['転石の破砕', 'コンクリートの破砕'], False),
                       ('無筋・鉄筋の区分', ['無筋コンクリート', '鉄筋コンクリート'], False),
                       ('無筋の子', ['A1', 'A2'], False),
                       ('鉄筋の子', ['B1', 'B2'], False)],
                 notes=[(1, '無筋コンクリート', [3])])
        p = self._plan([['AX-001', '施工区分', 'vary', '5', '施工区分', '', '', '']])
        added = G3M.add_missing_gate_axes(p, self.g20, g30, self.diffs, self.g_list, {1})
        self.assertEqual(added, ['無筋・鉄筋区分'])
        ax = [r for r in self._read(p) if r['軸名'] == '無筋・鉄筋区分'][0]
        self.assertEqual(ax['SitsumonNo'], '6')
        self.assertIn('無筋・鉄筋の区分', ax['変更理由'])

    def test_同名質問が複数なら全部追加する(self):
        g_list = [{'name': '施工区分', 'sits': [5]}, {'name': '無筋・鉄筋区分', 'sits': [6, 60]}]
        p = self._plan([['AX-001', '施工区分', 'vary', '5', '施工区分', '', '', '']])
        added = G3M.add_missing_gate_axes(p, self.g20, G30, self.diffs, g_list, {1})
        self.assertEqual(added, ['無筋・鉄筋区分'])
        nos = sorted(r['SitsumonNo'] for r in self._read(p) if r['軸名'] == '無筋・鉄筋区分')
        self.assertEqual(nos, ['6', '60'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
