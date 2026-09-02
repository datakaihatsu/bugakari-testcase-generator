# -*- coding: utf-8 -*-
"""
組合せ爆発ガードのテスト

経緯:
  2026-08-31 (v1.1.2) 65-546-6435-22 消波ブロック製作で直積 2^20 超 → 「生成中」のまま固まる。
    対策 = 軸統合 + 直積上限超で「基準+1軸ずつ変更」へ縮退 + 時間上限。
  2026-09-02 (v1.2.1) 縮退は廃止。縮退サンプルでは G条件表の(注)「Gx=v なら Gy 不要」を
    1 サンプルから帰納してしまい、必要な条件を「不要」と誤記する(43 捨石本均し で実証)。
    方針(ユーザ決定): 普通サイズの歩掛が正しく出ることを優先し、上限超は
    CombinationExplosionError で「この歩掛は生成できない」と返す。上限は実測に基づき 3,000。

検証:
  1. 軸統合   : 実体同一(同名+同選択肢)の軸を連動化 → 直積の桁を減らす
  2. 直積上限 : MAX_FULL_COMBOS 以内は全組合せ / 超過は CombinationExplosionError(縮退しない)
  3. 時間上限 : flow_walker.GenerationTimeout
  4. 網羅性補完(gen2)が上限超のときは補完を破棄して元の結果を使う(実データ 43 で確認)
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
            self.assertEqual(cb[0]['display'], cb[1]['display'])
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
    def test_cap_value_is_3000(self):
        # 実測に基づく値(43 捨石本均し=1,200 を通し、消波ブロック=4,096 超を止める)
        self.assertEqual(ColumnTCGenerator.MAX_FULL_COMBOS, 3000)

    def test_full_product_under_cap(self):
        vrl = [(_ax(i, f'軸{i}'), _rows(['A', 'B'], i)) for i in range(3)]
        self.assertEqual(len(_build(vrl)), 8)  # 2^3 は上限内 → 全組合せ

    def test_exactly_at_cap_is_full_product(self):
        vrl = [(_ax(i, f'軸{i}'), _rows(['A', 'B'], i)) for i in range(3)]
        self.assertEqual(len(_build(vrl, cap=8)), 8)  # 上限ちょうどは許容

    def test_over_cap_raises_not_reduces(self):
        # 2^12=4096 > cap 100 → 縮退せず「生成できない」で返す
        vrl = [(_ax(i, f'軸{i}'), _rows(['A', 'B'], i)) for i in range(12)]
        with self.assertRaises(CombinationExplosionError) as cm:
            _build(vrl, cap=100)
        msg = str(cm.exception)
        self.assertIn('生成できません', msg)
        self.assertIn('4,096', msg)   # 件数を利用者に見せる
        self.assertIn('100', msg)

    def test_bundling_then_cap_like_6435_36(self):
        # 実不具合の縮図: 同一軸x9 + 独立軸x12 → 統合で 2^13=8192、なお超過 → エラー
        vrl = [(_ax(i, '単位選択'), _rows(['t', 'm'], i)) for i in range(9)]
        vrl += [(_ax(100 + i, f'軸{i}'), _rows(['A', 'B'], 100 + i))
                for i in range(12)]
        with self.assertRaises(CombinationExplosionError):
            _build(vrl, cap=100)
        # 統合で上限内に収まれば全組合せで出る (2^13=8192 <= 10000)
        self.assertEqual(len(_build(vrl, cap=10000)), 8192)

    def test_1200_like_43_passes_default_cap(self):
        # 43 捨石本均し 相当(直積1,200)は既定上限で全組合せになる
        vrl = [(_ax(i, f'軸{i}'), _rows(['A', 'B'], i)) for i in range(4)]        # 16
        vrl += [(_ax(10 + i, f'軸x{i}'), _rows(['A', 'B', 'C'], 10 + i)) for i in range(2)]  # x9=144
        vrl += [(_ax(20, '軸y'), _rows(['A', 'B', 'C', 'D', 'E'], 20))]           # x5=720
        vrl += [(_ax(21, '軸z'), _rows(['A', 'B'], 21))]                          # x2=1440 > 1200 でも上限内
        self.assertEqual(len(_build(vrl)), 1440)


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


_JSON43 = os.path.join(_PARENT, '..', '工種別',
                       '43_610827_捨石本均し･荒均し(水中)､捨石本均し･荒均し(陸上) 【潜',
                       'input', '32-5251.20260401.20260401.json')


@unittest.skipUnless(os.path.exists(_JSON43), '43 実データ未配置')
class TestGen2OverCapFallsBack(unittest.TestCase):
    """網羅性補完(gen2)だけが上限超になる境界例: 補完を破棄して元の結果を使う。
    43 は gen=600 / gen2=1,200。上限を 1,000 に落とすと gen2 だけが超える。"""

    def setUp(self):
        self._cap = ColumnTCGenerator.MAX_FULL_COMBOS

    def tearDown(self):
        ColumnTCGenerator.MAX_FULL_COMBOS = self._cap

    def test_gen2_over_cap_uses_gen_result(self):
        import gen_gjoken
        ColumnTCGenerator.MAX_FULL_COMBOS = 1000
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _bj, gen, g_list, notes = gen_gjoken.analyze(_JSON43)
        self.assertIn('昇格を破棄', buf.getvalue())
        self.assertTrue(g_list)
        self.assertTrue(notes)          # 「注なし」にはならない(gen の全組合せから導出)
        self.assertIsNotNone(getattr(gen, '_rows_cache', None))

    def test_default_cap_runs_gen2_full(self):
        import gen_gjoken
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            gen_gjoken.analyze(_JSON43)
        log = buf.getvalue()
        self.assertNotIn('組合せ上限', log)   # 既定上限 3,000 では両インスタンス全組合せ
        self.assertNotIn('昇格を破棄', log)


if __name__ == '__main__':
    unittest.main()
