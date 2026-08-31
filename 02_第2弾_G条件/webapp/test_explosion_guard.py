# -*- coding: utf-8 -*-
"""
組合せ爆発ガードのテスト (2026-08-31 不具合: 65-546-6435-22 消波ブロック製作)

3段構えの対策を検証する:
  1. 軸統合   : 実体同一(同名+同選択肢)の軸を連動化 → 直積の桁を減らす
  2. 直積上限 : MAX_FULL_COMBOS 超過で「基準+1軸ずつ変更」方式に縮退
  3. 最終防壁 : 縮退後も超過なら CombinationExplosionError /
               時間上限超過で flow_walker.GenerationTimeout
"""

import io
import os
import sys
import time
import contextlib
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _p in (_HERE, _PARENT, os.path.join(_PARENT, 'step3_csv'),
           os.path.join(_PARENT, 'engine')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from generate_csv import ColumnTCGenerator, CombinationExplosionError  # noqa: E402
import flow_walker  # noqa: E402


def _rows(labels, sit_no):
    return [{'row_id': i + 1, 'display': lb, 'var_settings': {},
             'sit_no': sit_no, 'disp_col': 1, 'value_col': None}
            for i, lb in enumerate(labels)]


def _ax(sit_no, name):
    return {'SitsumonNo': str(sit_no), '軸名': name, '軸ID': f'A{sit_no}',
            '変更理由': '新規工種:全選択肢網羅'}


class _Stub:
    """_build_combos は self.MAX_FULL_COMBOS しか参照しない。"""
    MAX_FULL_COMBOS = ColumnTCGenerator.MAX_FULL_COMBOS
    _build_combos = ColumnTCGenerator._build_combos


def _build(vary_row_lists, cap=None):
    stub = _Stub()
    if cap is not None:
        stub.MAX_FULL_COMBOS = cap
    with contextlib.redirect_stdout(io.StringIO()):
        return stub._build_combos(vary_row_lists)


class TestAxisBundling(unittest.TestCase):
    def test_identical_axes_move_together(self):
        # 同名+同選択肢の軸2本 → 連動1グループ: 2x2=4 ではなく 2 combo
        vrl = [(_ax(1, '単位選択'), _rows(['t', 'm'], 1)),
               (_ax(2, '単位選択'), _rows(['t', 'm'], 2))]
        combos = _build(vrl)
        self.assertEqual(len(combos), 2)
        for cb in combos:
            # メンバーはリーダーと同じ行番号(=同じ表示)を選ぶ
            self.assertEqual(cb[0]['display'], cb[1]['display'])
            # 各軸には自分自身の row オブジェクト(sit_no)が入る
            self.assertEqual(cb[0]['sit_no'], 1)
            self.assertEqual(cb[1]['sit_no'], 2)

    def test_different_axes_not_bundled(self):
        # 名前が同じでも選択肢が違えば独立 (2x3=6)
        vrl = [(_ax(1, '区分'), _rows(['A', 'B'], 1)),
               (_ax(2, '区分'), _rows(['A', 'B', 'C'], 2))]
        self.assertEqual(len(_build(vrl)), 6)

    def test_empty(self):
        self.assertEqual(_build([]), [tuple()])


class TestProductCap(unittest.TestCase):
    def test_full_product_under_cap(self):
        vrl = [(_ax(i, f'軸{i}'), _rows(['A', 'B'], i)) for i in range(3)]
        self.assertEqual(len(_build(vrl)), 8)  # 2^3 は上限内 → 全組合せ

    def test_one_hot_fallback_over_cap(self):
        # 2^12=4096 > cap 100 → 基準1 + 各軸1変化(12) = 13件
        vrl = [(_ax(i, f'軸{i}'), _rows(['A', 'B'], i)) for i in range(12)]
        combos = _build(vrl, cap=100)
        self.assertEqual(len(combos), 13)
        # 基準combo は全軸先頭行
        self.assertTrue(all(r['display'] == 'A' for r in combos[0]))
        # 全選択肢が最低1回は登場する (到達網羅=G条件表の内容は不変)
        for i in range(12):
            self.assertTrue(any(cb[i]['display'] == 'B' for cb in combos))

    def test_bundling_then_cap_like_6435_36(self):
        # 実不具合の縮図: 同一軸x9 + 独立軸x12 → 統合で 2^13、なお超過 → 縮退
        vrl = [(_ax(i, '単位選択'), _rows(['t', 'm'], i)) for i in range(9)]
        vrl += [(_ax(100 + i, f'軸{i}'), _rows(['A', 'B'], 100 + i))
                for i in range(12)]
        combos = _build(vrl, cap=100)
        self.assertEqual(len(combos), 1 + 13)  # グループ13(統合1+独立12)が各1変化

    def test_explosion_error_when_one_hot_still_over(self):
        vrl = [(_ax(i, f'軸{i}'), _rows(['A', 'B'], i)) for i in range(30)]
        with self.assertRaises(CombinationExplosionError):
            _build(vrl, cap=10)  # 2^30 → 縮退31件 > 10


class TestTimeBudget(unittest.TestCase):
    def tearDown(self):
        flow_walker.set_time_budget(None)

    def test_timeout_raises_and_clears(self):
        flow_walker.set_time_budget(0.01)
        time.sleep(0.05)
        with self.assertRaises(flow_walker.GenerationTimeout):
            flow_walker.check_time_budget()
        flow_walker.set_time_budget(None)
        flow_walker.check_time_budget()  # 解除後は例外なし

    def test_no_budget_is_noop(self):
        flow_walker.check_time_budget()


if __name__ == '__main__':
    unittest.main()
