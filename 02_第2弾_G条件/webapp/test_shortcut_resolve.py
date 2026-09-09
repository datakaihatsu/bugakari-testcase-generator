# -*- coding: utf-8 -*-
"""test_shortcut_resolve.py ― 確定設計B（ShortCut 質問の選択肢を計画段で解決）

実行: python3 02_第2弾_G条件/webapp/test_shortcut_resolve.py

背景（2026-09-08 定例 2件目 / 公園照明 160-822）:
  ShortCut 質問（SitsumonItem.ShortCutSitsumonNo 持ち）は Sitsumon019 を自分では持たず、
  選択肢の定義は canonical 側にある。計画段(step2)がこれを解決せず「選択肢0件」と
  数えると、本来 vary の質問が fix(選択肢1件)に落ち、強制行IDも空になる。その結果
    - 単価計上方法が切り替わらない → 「①単価DBより選択→単価は不要」の注が導けない
    - 表示は①・計算は②という食い違ったテストケースが出る
  ここでは索引の ShortCut 解決・既定行の ShortCut 解決・表示行の強制を固定する。
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_BASE = os.path.dirname(_HERE)
for _p in ('engine', 'step2_proposals', 'step3_csv'):
    sys.path.insert(0, os.path.join(_BASE, _p))
sys.path.insert(0, _BASE)

from bugakari_json import BugakariJSON, _ShortCutResolvingDict  # noqa: E402


class _FakeBJ:
    """最小の疑似 BugakariJSON。Sit20 が Sit10 の ShortCut 子。"""

    def __init__(self):
        self.sitsumon_by_no = {
            10: {'SitsumonNo': 10, 'SitsumonKind': 19},
            20: {'SitsumonNo': 20, 'SitsumonKind': 19, 'ShortCutSitsumonNo': 10},
            30: {'SitsumonNo': 30, 'SitsumonKind': 19, 'ShortCutSitsumonNo': 20},
            40: {'SitsumonNo': 40, 'SitsumonKind': 19, 'ShortCutSitsumonNo': 40},
        }
        self.sitsumon019_by_no = _ShortCutResolvingDict({10: {'SitsumonNo': 10}}, self)


class TestShortCutIndex(unittest.TestCase):
    """B-R1: Sitsumon019 索引が ShortCut 先まで解決する。"""

    def setUp(self):
        self.bj = _FakeBJ()

    def test_own_definition_wins(self):
        self.assertEqual(self.bj.sitsumon019_by_no.get(10)['SitsumonNo'], 10)

    def test_resolves_one_hop(self):
        self.assertEqual(self.bj.sitsumon019_by_no.get(20)['SitsumonNo'], 10)

    def test_resolves_chain(self):
        """30 → 20 → 10 の連鎖もたどる。"""
        self.assertEqual(self.bj.sitsumon019_by_no.get(30)['SitsumonNo'], 10)

    def test_self_reference_does_not_hang(self):
        """自分自身を指す壊れたデータでも無限ループしない。"""
        self.assertIsNone(self.bj.sitsumon019_by_no.get(40))

    def test_unknown_returns_default(self):
        self.assertEqual(self.bj.sitsumon019_by_no.get(999, 'X'), 'X')


def _park_lighting_json():
    """公園照明(商品) 160-822。ローカルの GaiaCloud データが無い環境ではスキップ。"""
    p = (r'C:/ProgramData/CoBeing/GaiaCloud/DB/Bugakari/160/000000'
         r'/160-822.20230401.20240701.json')
    return p if os.path.exists(p) else None


@unittest.skipIf(_park_lighting_json() is None, 'GaiaCloud データが無い環境')
class TestParkLightingPlan(unittest.TestCase):
    """B-R1/R2/R3 の実データ確認（公園照明 160-822）。"""

    @classmethod
    def setUpClass(cls):
        import io
        import csv
        import tempfile
        import contextlib
        from generate_proposals_new import run as run_plan
        from generate_csv import ColumnTCGenerator
        p = _park_lighting_json()
        cls.bj = BugakariJSON(p)
        work = tempfile.mkdtemp()
        cls.plan_csv = os.path.join(work, 'plan.csv')
        with contextlib.redirect_stdout(io.StringIO()):
            run_plan(p, cls.plan_csv)
            cls.gen = ColumnTCGenerator(cls.plan_csv, p)
            cls.rows = cls.gen.generate()
        with open(cls.plan_csv, encoding='cp932', errors='replace') as f:
            cls.plan = list(csv.reader(f))

    def _kind_of(self, sit_no):
        for r in self.plan[1:]:
            if r and r[3] == str(sit_no):
                return r[2]
        return None

    def test_shortcut_question_counts_its_choices(self):
        """Sit29(→Sit12) は選択肢2件として数えられ vary になる（旧: 0件→fix）。"""
        self.assertEqual(len(self.gen._get_axis_rows(29)), 2)
        self.assertEqual(self._kind_of(29), 'vary')

    def test_default_row_follows_shortcut(self):
        """B-R2: 既定行は ShortCut 先の DefaultRowID（旧: 先頭行）。"""
        rows = self.gen._get_axis_rows(29)
        own = self.gen._get_default_row(29, rows)
        canonical = self.gen._get_default_row(12, self.gen._get_axis_rows(12))
        self.assertIsNotNone(own)
        self.assertEqual(own['row_id'], canonical['row_id'])

    def test_no_row_shows_db_choice_with_a_price(self):
        """表示と計算の一致: 「単価DBより選択」の行で単価列が値を持たない。"""
        hdr = self.rows[0]
        i_way = hdr.index('照明器具1台当たりの単価計上方法')
        i_price = hdr.index('照明器具1台当たりの単価')
        bad = [r for r in self.rows[1:]
               if r[i_way].endswith('単価DBより選択') and r[i_price] not in ('', '-')]
        self.assertEqual(bad, [], f'表示と計算が食い違う行が残っている: {bad[:2]}')


if __name__ == '__main__':
    unittest.main(verbosity=2)
