# -*- coding: utf-8 -*-
"""
G条件差分(diff_gjoken)と擬似改定後JSON合成のテスト

2026-09-02 不具合(鉄枠固定ボルト材料費 160-1722): 修正後G条件で条件名を「蓋区分」→「○区分」
に変え選択肢を全入替すると、名前の類似(0.667≥0.5)で「改名」扱い → 旧選択肢を先に全削除 →
新選択肢の複製元が無く 0行 → vary軸が空で IndexError。

方針(ユーザ決定 2026-09-02):
  V1 : 条件名は **完全一致のみ** 同じ条件。名前が変わった列は別条件(削除列+新規列)。
       テストは安全側(全選択肢網羅で多く出る)に振る。不要なTCは人が削れる。
  案1: 擬似JSON合成は「追加 → 削除・文字変更」の順(同名・選択肢全入替でも複製元が残る)。
  案3: 追加できなかった選択肢が残れば GjokenApplyError で利用者向けに止める。
"""

import os
import io
import sys
import contextlib
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _p in (_HERE, _PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import gen_tc_from_gjoken as gj  # noqa: E402


def _g(*cols):
    """cols: (name, opts) → diff_gjoken が見る最小構造"""
    return {'cols': [{'name': n, 'opts': list(o), 'numeric': False} for n, o in cols]}


class TestDiffGjokenColumnMatch(unittest.TestCase):
    def _diff(self, g20, g30):
        with contextlib.redirect_stdout(io.StringIO()):
            return gj.diff_gjoken(g20, g30)

    def test_reported_case_similar_name_full_replacement_is_different(self):
        # 今回の報告そのもの: 「蓋区分」→「○区分」・選択肢全入替 → 別条件
        g20 = _g(('蓋区分', ['標準蓋 T-20', '標準蓋 T-25', 'タイル用', 'レンガ用']))
        g30 = _g(('○区分', ['ロックボルト　L=150', 'ロックボルト　L=105']))
        d = self._diff(g20, g30)
        self.assertEqual(d['col_map'], {})
        self.assertEqual(d['del_cols'], [0])
        self.assertEqual(d['new_cols'], [0])
        self.assertEqual(d['choice_diffs'], {})

    def test_similar_name_partial_overlap_is_also_different(self):
        # V1: 名前が完全一致でなければ、選択肢が一部残っていても別条件(安全側)
        g20 = _g(('蓋区分', ['標準蓋 T-20', '標準蓋 T-25', 'タイル用', 'レンガ用']))
        g30 = _g(('○区分', ['標準蓋 T-20', 'ロックボルト　L=150']))
        d = self._diff(g20, g30)
        self.assertEqual(d['col_map'], {})
        self.assertEqual(d['del_cols'], [0])
        self.assertEqual(d['new_cols'], [0])

    def test_prefix_style_rename_is_different(self):
        # 以前は接頭辞ボーナス(B-7)で改名扱いだった注記追加型(13/27 相当)も別条件になる
        g20 = _g(('歩掛(プレテンションT桁)', ['有', '無']))
        g30 = _g(('歩掛(プレテンションT桁、ポストテンション桁(床版桁除く)', ['有', '無']))
        d = self._diff(g20, g30)
        self.assertEqual(d['col_map'], {})
        self.assertEqual(d['del_cols'], [0])
        self.assertEqual(d['new_cols'], [0])

    def test_same_name_is_same_column_with_option_diffs(self):
        # 名前が完全一致 → 同じ条件。選択肢の差分(0.4)は現行どおり
        g20 = _g(('蓋区分', ['標準蓋 T-20', '標準蓋 T-25', 'タイル用', 'レンガ用']))
        g30 = _g(('蓋区分', ['標準蓋 T-20', '標準蓋 T-25(改)', 'レンガ用']))
        d = self._diff(g20, g30)
        self.assertEqual(d['col_map'], {0: 0})
        cd = d['choice_diffs'][0]
        self.assertEqual(cd['renames'], {'標準蓋 T-25': '標準蓋 T-25(改)'})
        self.assertEqual(cd['dels'], ['タイル用'])
        self.assertEqual(cd['adds'], [])

    def test_same_name_full_replacement_is_same_column(self):
        # A③: 名前が完全一致で選択肢を全入替 → 同じ条件(削除N+追加M)。生成は案1で通る(E2Eで確認)
        g20 = _g(('蓋区分', ['標準蓋 T-20', '標準蓋 T-25']))
        g30 = _g(('蓋区分', ['ロックボルト　L=150', 'ロックボルト　L=105']))
        d = self._diff(g20, g30)
        self.assertEqual(d['col_map'], {0: 0})
        cd = d['choice_diffs'][0]
        self.assertEqual(len(cd['dels']), 2)
        self.assertEqual(len(cd['adds']), 2)

    def test_multiple_columns_exact_only(self):
        g20 = _g(('施工区分', ['水中', '陸上']), ('蓋区分', ['標準蓋 T-20', 'タイル用']))
        g30 = _g(('施工区分', ['水中', '陸上']), ('○区分', ['ロックボルト　L=150']))
        d = self._diff(g20, g30)
        self.assertEqual(d['col_map'], {0: 0})
        self.assertEqual(d['del_cols'], [1])
        self.assertEqual(d['new_cols'], [1])

    def test_column_order_change_with_same_names(self):
        # 列順が入れ替わっても名前で対応付く
        g20 = _g(('A', ['1', '2']), ('B', ['x', 'y']))
        g30 = _g(('B', ['x', 'y']), ('A', ['1', '2']))
        d = self._diff(g20, g30)
        self.assertEqual(d['col_map'], {0: 1, 1: 0})
        self.assertEqual(d['del_cols'], [])
        self.assertEqual(d['new_cols'], [])


_ISSUE = os.path.join(_PARENT, '不具合と要求', '20260902_エラー発生')
_G20 = os.path.join(_ISSUE, '商品_Gaia入力基準表_鉄枠固定ボルト材料費(組)_適用20191001 (1).xlsx')
_G30 = os.path.join(_ISSUE, '修正後_Gaia入力基準表_鉄枠固定ボルト材料費(組)_適用20191001 (1).xlsx')
_JSON = r'C:\ProgramData\CoBeing\GaiaCloud\DB\Bugakari\160\001000\160-1722.20190401.20191001.json'


def _csv30_with_name(name):
    """報告の修正後xlsxをCSV化し、条件名だけ差し替えたものを返す(選択肢は全入替のまま)。"""
    import tempfile
    import io_xlsx
    wd = tempfile.mkdtemp(prefix='gjdiff_')
    csv30 = os.path.join(wd, '30.csv')
    io_xlsx.xlsx_to_csv(_G30, csv30)
    raw = open(csv30, 'rb').read()
    for enc in ('utf-8-sig', 'cp932'):
        try:
            txt = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    txt = txt.replace('○区分', name, 1)
    open(csv30, 'w', encoding='utf-8-sig', newline='').write(txt)
    return csv30


@unittest.skipUnless(os.path.exists(_G30) and os.path.exists(_JSON), '報告xlsx/実JSON 未配置')
class TestIssue20260902EndToEnd(unittest.TestCase):
    def test_reported_input_generates(self):
        """報告された入力そのまま(B③)。以前は IndexError → 削除列+新規列で生成できる。"""
        import service
        r = service.gen_tc(_G20, _G30, _JSON)
        self.assertIsNone(r['error'], r['error'])
        self.assertGreater(r['tc_count'], 0)
        self.assertIn('新規列: ○区分', r['log'])
        self.assertIn('削除条件の列をTCから除去', r['log'])

    def test_same_name_full_replacement_generates(self):
        """A③: 条件名をそのまま残して選択肢を全入替 → 案1(追加→削除)で通る。"""
        import service
        r = service.gen_tc(_G20, _csv30_with_name('蓋区分'), _JSON)
        self.assertIsNone(r['error'], r['error'])
        self.assertGreater(r['tc_count'], 0)
        self.assertNotIn('新規列', r['log'])       # 同じ条件として扱われている
        self.assertIn('選択肢追加', r['log'])      # 新選択肢が軸に載っている

    def test_apply_error_is_user_facing(self):
        """案3: 複製元が見つからない状況を人工的に作り、利用者向けメッセージで止まること。"""
        import service
        orig = gj._edit_sit

        def broken_edit_sit(data, sit_no, dels, renames, adds):
            if adds:
                return []           # 追加が一切反映できなかったことにする
            return orig(data, sit_no, dels, renames, adds)

        gj._edit_sit = broken_edit_sit
        try:
            r = service.gen_tc(_G20, _csv30_with_name('蓋区分'), _JSON)
        finally:
            gj._edit_sit = orig
        self.assertIsNotNone(r['error'])
        self.assertIn('反映できませんでした', r['error'])
        self.assertNotIn('GjokenApplyError', r['error'])   # クラス名は見せない
        self.assertNotIn('IndexError', r['error'])


_K12 = os.path.join(_PARENT, '..', '工種別', '12_619272_重建設機械分解組立', 'input')
_K12_OLD = os.path.join(_K12, '32-6.20250401.20250401.json')
_K12_NEW = os.path.join(_K12, '32-6.20260401.20260401.json')


@unittest.skipUnless(os.path.exists(_K12_OLD) and os.path.exists(_K12_NEW), '12 実データ未配置')
class TestPlanCTextChangeObservation(unittest.TestCase):
    """案C (2026-09-02): 文字修正のみの軸の行には「文字修正後の選択肢を選んだとき、代価行・数量・
    単価が積算基準および設計書どおりであること」を必ず添える。12 重建設機械分解組立は
    表記統一14件の中に 1.4m3→1.5m3 の実体変更が混在する実例。"""

    @classmethod
    def setUpClass(cls):
        import csv
        import tempfile
        import gen_gjoken
        work = tempfile.mkdtemp(prefix='planc_')
        with contextlib.redirect_stdout(io.StringIO()):
            c20 = gen_gjoken.build_g(_K12_OLD, os.path.join(work, '20'))
            c30 = gen_gjoken.build_g(_K12_NEW, os.path.join(work, '30'))
            out = gj.run(c20, c30, _K12_OLD, os.path.join(work, 'out'))
        raw = open(out, 'rb').read()
        for enc in ('utf-8-sig', 'cp932'):
            try:
                txt = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        cls.rows = list(csv.reader(io.StringIO(txt)))
        cls.col = cls.rows[0].index('選択肢の適切さ確認')

    def test_text_change_rows_carry_content_check(self):
        cells = [r[self.col] for r in self.rows[1:] if '選択肢文字修正' in r[self.col]]
        self.assertTrue(cells, '文字修正行が無い')
        for c in cells:
            self.assertIn('文字修正後の選択肢を選んだとき', c)
            self.assertIn('積算基準および設計書どおり', c)
            self.assertIn('文字だけの修正か、内容の変更かを確認', c)

    def test_other_rows_do_not_carry_it(self):
        others = [r[self.col] for r in self.rows[1:] if '選択肢文字修正' not in r[self.col]]
        for c in others:
            self.assertNotIn('文字修正後の選択肢を選んだとき', c)

    def test_row_and_column_counts_unchanged(self):
        # 案C は観点文だけを変える: 12 は TC 3 行 / 14 列 のまま
        self.assertEqual(len(self.rows) - 1, 3)
        self.assertEqual(len(self.rows[0]), 14)


if __name__ == '__main__':
    unittest.main(verbosity=2)
