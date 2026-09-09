# -*- coding: utf-8 -*-
"""test_gjoken_reach.py ― 確定設計F（G条件の列と(注)を到達走査から導く）

実行: python3 02_第2弾_G条件/webapp/test_gjoken_reach.py

背景（2026-09-08 定例 2件目・3件目）:
  従来はテストケース行列（テスト用に間引かれた集合）の '-' パターンから (注) と
  列を帰納していたため、
    - 相関を因果と取り違えた偽の注（例 公園照明「G9→G11」の真因は G4）
    - 真の注が出ない（G2→G3・G9→G10）
    - 2つ以上同時に選ばないと届かない条件が列にすらならない（静的破砕工で7列欠落）
  が起きていた。G条件専用の到達走査に切り替えたことを固定する。
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_BASE = os.path.dirname(_HERE)
for _p in ('engine', 'step2_proposals', 'step3_csv'):
    sys.path.insert(0, os.path.join(_BASE, _p))
sys.path.insert(0, _BASE)

import gjoken_reach as R      # noqa: E402
import gen_gjoken as G        # noqa: E402
from bugakari_json import BugakariJSON  # noqa: E402


class _SysBJ:
    def __init__(self, rows):
        self.sitsumon_by_no = {1: {'SitsumonNo': 1, 'SitsumonKind': 19}}
        self.sitsumon019_by_no = {1: {'SitTabRows': rows}}


class TestSystemBranchRule(unittest.TestCase):
    """F-R3: システム分岐の除外は「先頭 ~」と c~4wh/a~4wh のみ。

    「~ を含む」で切ると、37 工事区分(K~KK)・公園照明 賃料長期割引(L~LK) のような
    **人が選ぶ条件**まで消える。計設定変数(K~/L~/Lv~)は除外してはいけない。
    """

    def _is_sys(self, var):
        return R.is_system_branch(_SysBJ([{'AutoSelectJoken': {'VarName': var}}]), 1)

    def test_reserved_prefix_is_system(self):
        for v in ('~SYSV', '~NJI', '~4WHR'):
            self.assertTrue(self._is_sys(v), v)

    def test_week_holiday_group_vars_are_system(self):
        for v in ('c~4wh', 'a~4wh'):
            self.assertTrue(self._is_sys(v), v)

    def test_keisettei_vars_are_not_system(self):
        """K~/L~ は人が選ぶ条件。除外すると 37・公園照明の列が消える。"""
        for v in ('K~KK', 'K~4wh', 'L~LK', 'L~CK', 'Lv~TS'):
            self.assertFalse(self._is_sys(v), v)

    def test_plain_vars_are_not_system(self):
        for v in ('FG1', 'J2', 'SEKO', ''):
            self.assertFalse(self._is_sys(v), v)

    def test_no_019_is_not_system(self):
        bj = _SysBJ([])
        bj.sitsumon019_by_no = {}
        self.assertFalse(R.is_system_branch(bj, 1))


def _json_if_exists(p):
    return p if os.path.exists(p) else None


_PARK = _json_if_exists(r'C:/ProgramData/CoBeing/GaiaCloud/DB/Bugakari/160/000000'
                        r'/160-822.20230401.20240701.json')
_HASAI = _json_if_exists(r'C:/ProgramData/CoBeing/GaiaCloud/DB/Bugakari/32/016000'
                         r'/32-16123.20240401.20240801.json')


@unittest.skipIf(_PARK is None, 'GaiaCloud データが無い環境')
class TestParkLighting(unittest.TestCase):
    """2件目の受け入れ条件（公園照明 160-822・商品）。"""

    @classmethod
    def setUpClass(cls):
        import io
        import contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            cls.bj, cls.gen, cls.gl, cls.notes = G.analyze(_PARK)
        cls.names = [g['name'] for g in cls.gl]

    def _gi(self, name):
        return self.names.index(name) + 1

    def test_missing_notes_now_appear(self):
        """要求2件が①方向（単価DBより選択 → 単価は不要）で出る。"""
        way = self._gi('照明器具1台当たりの単価計上方法')
        price = self._gi('照明器具1台当たりの単価')
        self.assertIn(f'G{way}条件で①を選択した場合は、G{price}条件を入力する必要はない。',
                      self.notes)
        way2 = self._gi('灯柱1本当たりの単価計上方法')
        price2 = self._gi('灯柱1本当たりの単価')
        self.assertIn(f'G{way2}条件で①を選択した場合は、G{price2}条件を入力する必要はない。',
                      self.notes)

    def test_false_note_is_gone(self):
        """偽の注（灯柱の計上方法 → アーム規格区分。真因は ポール規格区分）が消える。"""
        way2 = self._gi('灯柱1本当たりの単価計上方法')
        arm = self._gi('アーム規格区分')
        for n in self.notes:
            if n.startswith(f'G{way2}条件で'):
                self.assertNotIn(f'G{arm}条件', n, f'真因でない注が残っている: {n}')

    def test_note_format_is_short(self):
        """確定設計A A-R8: 条件名を書かない短い形。"""
        import re
        for n in self.notes:
            self.assertRegex(n, r'^G\d+条件で.+を選択した場合は、G\d+条件'
                                r'(、G\d+条件)*を入力する必要はない。$')

    def test_columns_kept(self):
        self.assertEqual(len(self.gl), 11)


@unittest.skipIf(_HASAI is None, 'GaiaCloud データが無い環境')
class TestSeitekiHasai(unittest.TestCase):
    """3件目の受け入れ条件（静的破砕工 32-16123・商品）。"""

    @classmethod
    def setUpClass(cls):
        import io
        import contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            cls.bj, cls.gen, cls.gl, cls.notes = G.analyze(_HASAI)
        cls.names = [g['name'] for g in cls.gl]

    def test_deep_branch_columns_appear(self):
        """2〜3個同時に選ばないと届かない条件が列になる（旧: 13列で7列欠落）。"""
        for n in ('2次破砕の方法(鉄筋コンクリート)',
                  '岩石の破砕[ベンチカット工法](クローラドリル使用)',
                  '岩石の破砕[トレンチ工法](クローラドリル使用)',
                  '鉄筋コンクリートの破砕(ハンドハンマー)2次ハンドブレーカー',
                  '鉄筋コンクリートの破砕(ハンドハンマー)2次大型機械',
                  '鉄筋コンクリートの破砕(クローラドリル)2次大型機械'):
            self.assertIn(n, self.names)

    def test_shortcut_gated_column_appears(self):
        """ShortCut 質問(単位選択)を切り替えて初めて届く列（確定設計B と併せて）。"""
        self.assertIn('1箱当りの内容量(質量)', self.names)

    def test_column_count(self):
        self.assertEqual(len(self.gl), 20)


@unittest.skipIf(_PARK is None, 'GaiaCloud データが無い環境')
class TestWalkBudget(unittest.TestCase):
    """F-R6: 探索は線形。上限を超えたら「生成できません」を返す。"""

    def test_walks_are_bounded(self):
        bj = BugakariJSON(_PARK)
        res = R.analyze_reach(bj, _PARK)
        self.assertLess(res['walks'], 1000)

    def test_raises_when_over_budget(self):
        bj = BugakariJSON(_PARK)
        orig = R.MAX_WALKS
        R.MAX_WALKS = 3
        try:
            with self.assertRaises(R.ReachExplosionError):
                R.analyze_reach(bj, _PARK)
        finally:
            R.MAX_WALKS = orig


if __name__ == '__main__':
    unittest.main(verbosity=2)
