# -*- coding: utf-8 -*-
"""test_display_and_range.py ― 確定設計C（選択肢の表示列を①③で一本化 / 数量入力の範囲）

実行: python3 02_第2弾_G条件/webapp/test_display_and_range.py

背景（2026-09-08 定例 4件目 / 現地滞在のための旅費 65-109-64-12 = 64-1538）:
  ①(_g_options) は「VarNameなし優先」、③(_get_axis_rows) は「VarName持ち優先」と
  逆のスコアで表示列を選んでいたため、同じ質問の選択肢が①では
  「30km以上 60km未満」、③では「0.1」（基準日額日数の値）になっていた。
  また数量入力の定義(Sitsumon017)を ShortCut 先まで探しておらず `(値なし)` が出ていた。
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_BASE = os.path.dirname(_HERE)
for _p in ('engine', 'step2_proposals', 'step3_csv'):
    sys.path.insert(0, os.path.join(_BASE, _p))
sys.path.insert(0, _BASE)

from bugakari_json import choice_display_map, numeric_input_spec, fmt_num  # noqa: E402


def _sit019(cols, rows, cells):
    """SitCols/SitRows/セル辞書から Sitsumon019 相当の dict を作る。"""
    return {
        'SitCols': cols,
        'SitRows': rows,
        'SitTabCells': [{'RowID': r, 'ColID': c, 'Value': v}
                        for (r, c), v in cells.items()],
    }


class TestChoiceDisplayMap(unittest.TestCase):
    """C-R2/R2': 表示列の選び方と、単一列で足りないときの連結。"""

    def test_prefers_label_over_variable_column(self):
        """4件目①: ラベル列(IsFixed)と変数列が同点でも、ラベル列を選ぶ。"""
        cols = [{'ColID': 3, 'IsFixed': True},
                {'ColID': 4, 'VarName': 'S1'}]
        rows = [{'RowID': 3, 'IsFixed': True}, {'RowID': 4}, {'RowID': 5}]
        cells = {(3, 3): '片道距離区分', (3, 4): '基準日額日数(日)',
                 (4, 3): '30km以上 60km未満', (4, 4): '0.1',
                 (5, 3): '60km以上 100km未満', (5, 4): '0.2'}
        disp, _c, extra = choice_display_map(_sit019(cols, rows, cells))
        self.assertEqual(disp[4], '30km以上 60km未満')
        self.assertEqual(extra, [])

    def test_prefers_column_filled_for_every_row(self):
        """C-R2 第1キー: 一部の行しか値が無い列は選ばない（'Row4' 退行の防止）。"""
        cols = [{'ColID': 1}, {'ColID': 2}]
        rows = [{'RowID': 4}, {'RowID': 5}]
        cells = {(4, 1): '', (5, 1): 'のみ', (4, 2): '甲', (5, 2): '乙'}
        disp, _c, _e = choice_display_map(_sit019(cols, rows, cells))
        self.assertEqual([disp[4], disp[5]], ['甲', '乙'])

    def test_concatenates_until_unique(self):
        """C-R2': 単一列で行が区別できないとき、表の列順で ' / ' 連結して一意化。

        40 小型不整地運搬車運搬 の「不整地運搬車規格選択」（規格列・積算方法列とも
        単独では重複があり、旧実装では5行が3択に畳まれていた）。
        """
        cols = [{'ColID': 2}, {'ColID': 3}]
        rows = [{'RowID': r} for r in (4, 5, 6, 7, 8)]
        cells = {
            (4, 2): 'ホイール式0.7t級', (4, 3): '選択した規格の賃貸料金で積算',
            (5, 2): 'ホイール式0.7t級', (5, 3): '上位規格(1.0t級)の賃貸料金で代替積算 (参考)',
            (6, 2): 'クローラ式0.5t級', (6, 3): '選択した規格の賃貸料金で積算',
            (7, 2): 'クローラ式0.5t級', (7, 3): '上位規格(0.6t級)の賃貸料金で代替積算 (参考)',
            (8, 2): 'クローラ式2.0t級', (8, 3): '(クローラ式2.0t級)',
        }
        disp, _c, extra = choice_display_map(_sit019(cols, rows, cells))
        self.assertEqual(disp[4], 'ホイール式0.7t級 / 選択した規格の賃貸料金で積算')
        self.assertEqual(len(set(disp.values())), 5, '5行すべてが区別できること')
        self.assertEqual(extra, [2])

    def test_concatenation_tolerates_blank_cells(self):
        """空欄のある列も連結対象にする（02 供用損料の「補正率」列が空欄を含む）。"""
        cols = [{'ColID': 2}, {'ColID': 3}]
        rows = [{'RowID': 4}, {'RowID': 5}, {'RowID': 6}]
        cells = {(4, 2): '損料を適用', (4, 3): '',
                 (5, 2): '補正を行う', (5, 3): '+10% (北海道以外)',
                 (6, 2): '補正を行う', (6, 3): '+15% (北海道)'}
        disp, _c, _e = choice_display_map(_sit019(cols, rows, cells))
        self.assertEqual(disp[4], '損料を適用')       # 空欄は飛ばす
        self.assertEqual(len(set(disp.values())), 3)

    def test_falls_back_when_no_values(self):
        cols = [{'ColID': 1}]
        rows = [{'RowID': 4}]
        disp, col, _e = choice_display_map(_sit019(cols, rows, {}))
        self.assertEqual(disp[4], 'Row4')
        self.assertIsNone(col)


class _BJ:
    def __init__(self, items, s017):
        self.sitsumon_by_no = {i['SitsumonNo']: i for i in items}
        self.data = {'Sitsumon017': s017}


class TestNumericInputSpec(unittest.TestCase):
    """C-R5/R7: 数量入力の定義を ShortCut 先まで探し、範囲を組み立てる。

    範囲の意味は Sirius 確定（内部仕様書 追補3）:
      Min/Max は非null decimal（JSON にキーが無ければ 0）
      MinKigou 1='<' 2='<=' 0=制限なし（3=Equal は制限なし扱い）
    """

    def _bj(self, **kw):
        e = {'SitsumonNo': 3, 'VarName': 'GY', 'TaniMesho': '人', 'DefaultValue': '0'}
        e.update(kw)
        return _BJ([{'SitsumonNo': 3, 'SitsumonKind': 17},
                    {'SitsumonNo': 22, 'SitsumonKind': 17, 'ShortCutSitsumonNo': 3}], [e])

    def test_resolves_via_shortcut(self):
        """4件目②-a: ShortCut 子(Sit22)でも canonical(Sit3)の定義を引く。"""
        spec = numeric_input_spec(self._bj(MinKigou=1), 22)
        self.assertIsNotNone(spec)
        self.assertEqual(spec['unit'], '人')

    def test_min_only_greater(self):
        """MinKigou=1 かつ Min 未指定 → 0 < 値（64-1538 業務員数）。"""
        self.assertEqual(numeric_input_spec(self._bj(MinKigou=1), 3)['range'], '0 < 値')

    def test_min_and_max(self):
        spec = numeric_input_spec(self._bj(MinKigou=1, MaxKigou=2, Max=100.0), 3)
        self.assertEqual(spec['range'], '0 < 値 <= 100')

    def test_max_only(self):
        spec = numeric_input_spec(self._bj(MaxKigou=2, Max=12.5), 3)
        self.assertEqual(spec['range'], '値 <= 12.5')

    def test_no_kigou_means_no_range(self):
        self.assertEqual(numeric_input_spec(self._bj(), 3)['range'], '')

    def test_equal_kigou_is_unbounded(self):
        """Kigou=3(Equal) は IsInRange の default 側＝制限なし。"""
        self.assertEqual(numeric_input_spec(self._bj(MinKigou=3), 3)['range'], '')

    def test_returns_none_for_non_numeric_question(self):
        bj = _BJ([{'SitsumonNo': 5, 'SitsumonKind': 19}], [])
        self.assertIsNone(numeric_input_spec(bj, 5))

    def test_fmt_num_trims_zeros(self):
        self.assertEqual([fmt_num(0), fmt_num(100.0), fmt_num(12.50)], ['0', '100', '12.5'])


def _travel_json():
    p = (r'C:/ProgramData/CoBeing/GaiaCloud/DB/Bugakari/64/001000'
         r'/64-1538.20230401.20231001.json')
    return p if os.path.exists(p) else None


@unittest.skipIf(_travel_json() is None, 'GaiaCloud データが無い環境')
class TestTravelKoshu(unittest.TestCase):
    """4件目の報告工種（現地滞在のための旅費 64-1538）で受け入れ条件を確認。"""

    @classmethod
    def setUpClass(cls):
        import io
        import csv
        import tempfile
        import contextlib
        from generate_proposals_new import run as run_plan
        from generate_csv import run as run_tc
        p = _travel_json()
        work = tempfile.mkdtemp()
        plan = os.path.join(work, 'plan.csv')
        out = os.path.join(work, 'tc.csv')
        with contextlib.redirect_stdout(io.StringIO()):
            run_plan(p, plan)
            run_tc(plan, p, out)
        with open(out, encoding='cp932', errors='replace') as f:
            cls.rows = list(csv.reader(f))

    def _col(self, name):
        i = self.rows[0].index(name)
        return [r[i] for r in self.rows[1:]]

    def test_distance_shows_label_not_number(self):
        self.assertIn('30km以上 60km未満', self._col('片道距離区分'))
        self.assertNotIn('0.1', self._col('片道距離区分'))

    def test_headcount_is_arbitrary_not_no_value(self):
        vals = self._col('1往復当りの業務員数')
        self.assertNotIn('(値なし)', vals)
        self.assertIn('任意', vals)

    def test_range_appears_in_check_column(self):
        checks = '\n'.join(self._col('選択肢の適切さ確認'))
        self.assertIn('1往復当りの業務員数', checks)
        self.assertIn('0 < 値', checks)
        self.assertIn('警告が出ること', checks)


if __name__ == '__main__':
    unittest.main(verbosity=2)
