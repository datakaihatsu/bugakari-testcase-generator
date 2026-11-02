# -*- coding: utf-8 -*-
"""
test_rule_notes.py ― 「かつ」「固定される」「外的要因により」の(注)と (外的要因) 列

背景 (2026-10-22 高松市 大型土のう工(袋)):
  「G1条件で…を選択した場合 かつ G4条件が①…の場合はG12条件は①「10m3」で固定される。」が
  文の形が想定外として無視された（2026-10-20 FB会で前田さんが話していた実例）。
  設計: 不具合と要求/20261022_高松_大型土のう工_かつ固定/要求整理と設計_20261022.md
  外的要因の書式は 20260918 設計A（`外的要因により G3=② に固定される` の1型）。

検証:
  - 受領データの注7・8が「固定」として読める（8件中8件反映）
  - 既存の1条件・入力不要の注は従来どおり notes に入る（rules に入らない）
  - TC: 条件が成り立つ行は「<値>(固定)」、成り立たない発注者では対象列を選べる行が残る
  - 外的要因による固定: 区分「外的要因」の行が足され、連鎖先が既存の注で「-」になる
  - (外的要因) 列: 通常の行は「-」、選択肢ごとに区分「外的要因」の行
  - 固定値が選択肢に無い／入力不要と固定の矛盾 → ERROR で名指し
  - 注も (外的要因) 列も無ければ apply_rule_notes は何もしない
  - 開く書き方「…の場合は、G4条件を選択する。」は「それ以外なら入力不要」に裏返して notes に入る
    （2026-11-02 前田さん 除雪グレーダ運転 注25）。「かつ」付きは ERROR で案内
"""

import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

import gen_tc_from_gjoken as G  # noqa: E402

RECV = os.path.join(os.path.dirname(_HERE), '不具合と要求', '20261022_高松_大型土のう工_かつ固定',
                    '受領データ', '加工_Gaia入力基準表_大型土のう工(袋)_適用20260401.xlsx')
RECV20 = os.path.join(os.path.dirname(RECV), '商品_Gaia入力基準表_大型土のう工(袋)_適用20260401 - コピー.xlsx')
OLD_JSON = 'C:/ProgramData/CoBeing/GaiaCloud/DB/Bugakari/32/000000/32-733.20260401.20260401.json'

# 発注者(G1)・袋材区分(G2)・作業区分(G3)・土砂数量(G4)
_BASE = [
    '施工区分/入力条件,G1,G2,G3,G4',
    '規格名計上,,,,',
    '各種(条件名),発注者,袋材区分,作業区分,土砂数量',
    ',整備局,①1t土のう,①製作・設置,①10m3',
    ',香川県,②耐候性,②撤去,②6m3',
    ',高松市,,,',
    '(注)',
]


def _write(td, notes, base=None, name='g.csv'):
    path = os.path.join(td, name)
    lines = list(base or _BASE) + [',"%s"' % n.replace('"', '""') for n in notes]
    with open(path, 'w', encoding='utf-8-sig') as f:
        f.write('\n'.join(lines) + '\n')
    return path


def _col(rows, name):
    h = [str(x).replace('(固定)', '') for x in rows[0]]
    i = h.index(name)
    return [r[i] for r in rows[1:]]


class TestParse(unittest.TestCase):
    def test_katsu_fix(self):
        with tempfile.TemporaryDirectory() as td:
            g = G.read_gjoken(_write(td, [
                '1.G1条件で「整備局」を選択した場合 かつ G2条件が①「1t土のう」の場合はG4条件は①「10m3」で固定される。',
                '2.G1条件で②「香川県」を選択した場合 かつ G2条件が①の場合は、G4条件は②で固定される。']))
        self.assertEqual(g['note_parsed_count'], 2)
        self.assertEqual(g['notes'], [])
        r1, r2 = g['rules']
        self.assertEqual(r1['kind'], 'fix')
        self.assertEqual(r1['conds'], [(0, '整備局'), (1, '1t土のう')])
        self.assertEqual((r1['targets'], r1['fix']), ([3], '10m3'))
        self.assertEqual(r2['fix'], '6m3')
        self.assertFalse([i for i in g['note_issues'] if i['level'] == 'ERROR'])
        sheet = G.note_sheet_rows(g)
        self.assertEqual(sheet[1][6], '固定')
        self.assertIn('10m3 に固定', sheet[1][4])

    def test_existing_single_close_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            g = G.read_gjoken(_write(td, [
                '1. G3条件で②を選択した場合は、G4条件を入力する必要はない。']))
        self.assertEqual(g['rules'], [])
        self.assertEqual(len(g['notes']), 1)

    def test_katsu_close(self):
        with tempfile.TemporaryDirectory() as td:
            g = G.read_gjoken(_write(td, [
                'G1条件で③を選択した場合 かつ G3条件で②を選択した場合は、G4条件を入力する必要はない。']))
        self.assertEqual(g['rules'][0]['kind'], 'close')
        self.assertEqual(g['rules'][0]['conds'], [(0, '高松市'), (2, '撤去')])

    def test_external_fix(self):
        with tempfile.TemporaryDirectory() as td:
            g = G.read_gjoken(_write(td, ['外的要因により G3=② に固定される']))
        r = g['rules'][0]
        self.assertEqual((r['kind'], r['targets'], r['fix']), ('ext', [2], '撤去'))

    def test_fix_value_not_in_options_is_error(self):
        with tempfile.TemporaryDirectory() as td:
            g = G.read_gjoken(_write(td, [
                'G1条件で①を選択した場合 かつ G2条件が①の場合は、G4条件は③で固定される。']))
        self.assertEqual(g['rules'], [])
        self.assertTrue([i for i in g['note_issues'] if i['level'] == 'ERROR'])

    def test_unreadable_lists_three_forms(self):
        with tempfile.TemporaryDirectory() as td:
            g = G.read_gjoken(_write(td, ['G1条件 または G2条件 で何か']))
        err = [i['text'] for i in g['note_issues'] if i['level'] == 'ERROR']
        self.assertTrue(err)
        self.assertIn('固定される', err[0])
        self.assertIn('外的要因により', err[0])

    def test_open_note_inverted_to_close(self):
        """開く書き方（2026-11-02 前田さん 除雪グレーダ運転 注25）: ③のときだけ G4 が出る。"""
        with tempfile.TemporaryDirectory() as td:
            g = G.read_gjoken(_write(td, ['25. G1条件で③を選択した場合は、G4条件を選択する。']))
        self.assertEqual(g['note_parsed_count'], 1)
        self.assertFalse([i for i in g['note_issues'] if i['level'] == 'ERROR'])
        self.assertEqual(g['rules'], [])
        self.assertEqual(sorted((n['src_choice'], n['targets']) for n in g['notes']),
                         [('整備局', [3]), ('香川県', [3])])
        self.assertEqual(g['opens'][0]['chosen'], [2])
        sheet = G.note_sheet_rows(g)
        self.assertIn('入力が必要', sheet[1][6])

    def test_open_note_tail_variants(self):
        for tail in ['G4条件を入力する。', 'G4条件を選択する必要がある。', 'G4条件を入力（選択）する必要がある',
                     'G4条件を選択します。']:
            with tempfile.TemporaryDirectory() as td:
                g = G.read_gjoken(_write(td, ['G1条件で①・②のいずれかを選択した場合は、' + tail]))
            self.assertEqual([n['src_choice'] for n in g['notes']], ['高松市'], tail)

    def test_open_note_with_katsu_is_error(self):
        with tempfile.TemporaryDirectory() as td:
            g = G.read_gjoken(_write(td, [
                'G1条件で①を選択した場合 かつ G2条件で①を選択した場合は、G4条件を選択する。']))
        self.assertEqual((g['notes'], g['rules'], g['note_parsed_count']), ([], [], 0))
        err = [i['text'] for i in g['note_issues'] if i['level'] == 'ERROR']
        self.assertEqual(len(err), 1)
        self.assertIn('かつ', err[0])

    def test_open_note_tc(self):
        with tempfile.TemporaryDirectory() as td:
            g = G.read_gjoken(_write(td, ['G1条件で③を選択した場合は、G4条件を選択する。']))
        rows = G.build_tc_from_single_gjoken(g)
        for a, d in zip(_col(rows, '発注者'), _col(rows, '土砂数量')):
            self.assertEqual(d == '-', a != '高松市', (a, d))

    def test_contradiction_named(self):
        with tempfile.TemporaryDirectory() as td:
            g = G.read_gjoken(_write(td, [
                'G2条件で②を選択した場合は、G4条件を入力する必要はない。',
                'G1条件で①を選択した場合 かつ G2条件が②の場合は、G4条件は①で固定される。']))
        self.assertTrue([i for i in g['note_issues']
                         if i['level'] == 'ERROR' and '両方' in i['text']])


class TestTC(unittest.TestCase):
    def _build(self, notes, base=None):
        with tempfile.TemporaryDirectory() as td:
            g = G.read_gjoken(_write(td, notes, base))
        return G.build_tc_from_single_gjoken(g)

    def test_fix_rows(self):
        rows = self._build([
            'G1条件で①を選択した場合 かつ G2条件が①の場合は、G4条件は①で固定される。',
            'G1条件で②を選択した場合 かつ G2条件が①の場合は、G4条件は②で固定される。',
            'G2条件で②を選択した場合は、G4条件を入力する必要はない。'])
        combos = {(a, b, d) for a, b, d in zip(_col(rows, '発注者'), _col(rows, '袋材区分'),
                                                _col(rows, '土砂数量'))}
        self.assertIn(('整備局', '1t土のう', '10m3(固定)'), combos)
        self.assertIn(('香川県', '1t土のう', '6m3(固定)'), combos)
        # 固定されない発注者では両方選べる
        self.assertIn(('高松市', '1t土のう', '6m3'), combos)
        self.assertTrue(any(a == '高松市' and d == '10m3' for a, _, d in combos))
        # 固定が効く行で「選べる」形の別値は残らない
        self.assertNotIn(('整備局', '1t土のう', '6m3'), combos)
        kanten = '\n'.join(_col(rows, '選択肢の適切さ確認'))
        self.assertEqual(kanten.count('「①10m3」に自動で決まり、選べないこと'), 1)

    def test_external_fix_row_and_chain(self):
        rows = self._build(['G3条件で②を選択した場合は、G4条件を入力する必要はない。',
                            '外的要因により G3=② に固定される'])
        ext = [r for r in rows[1:] if r[1] == '外的要因']
        self.assertEqual(len(ext), 1)
        h = rows[0]
        self.assertEqual(ext[0][h.index('作業区分')], '撤去(固定)')
        self.assertEqual(ext[0][h.index('土砂数量')], '-')   # 連鎖先は既存の注で閉じる
        self.assertIn('外的要因', ext[0][h.index('選択肢の適切さ確認')])

    def test_external_column(self):
        base = list(_BASE)
        base[0] += ',G5'
        base[1] += ','
        base[2] += ',割増区分(外的要因)'
        base[3] += ',①あり'
        base[4] += ',②なし'
        base[5] += ','
        rows = self._build([], base=base)
        h = rows[0]
        ci = h.index('割増区分(外的要因)')
        normal = [r for r in rows[1:] if r[1] != '外的要因']
        ext = [r for r in rows[1:] if r[1] == '外的要因']
        self.assertTrue(all(r[ci] == '-' for r in normal))
        self.assertEqual(sorted(r[ci] for r in ext), ['あり', 'なし'])
        # 「-」にしたことで同じ並びになった通常行は落ちる
        keys = [tuple(r[2:len(h) - 3]) for r in normal]
        self.assertEqual(len(keys), len(set(keys)))

    def test_no_rules_no_change(self):
        rows = [['テストID', 'テスト区分', 'X', '代価表行と数量(数率)', '選択肢の適切さ確認', '規格名計上'],
                ['TC-001', '回帰', 'a', '', '', '']]
        g = {'cols': [{'name': 'X', 'opts': ['a'], 'opts_raw': ['a']}], 'notes': [],
             'rules': [], 'ext_cols': []}
        self.assertIs(G.apply_rule_notes(rows, g), rows)


@unittest.skipUnless(os.path.exists(RECV), '受領データ未配置')
class TestReceivedData(unittest.TestCase):
    """高松市 大型土のう工(袋) の受領データそのもの。"""

    def test_all_notes_reflected_and_tc(self):
        import io_xlsx
        with tempfile.TemporaryDirectory() as td:
            csv30 = os.path.join(td, '30.csv')
            io_xlsx.xlsx_to_csv(RECV, csv30)
            g = G.read_gjoken(csv30)
            self.assertEqual((g['note_raw_count'], g['note_parsed_count']), (8, 8))
            rows = G.build_tc_from_single_gjoken(g)
        combos = set(zip(_col(rows, '発注者'), _col(rows, '袋材区分'), _col(rows, '1t袋時土砂数量')))
        t1 = '1t土のう 丸型 φ110cm×108cm'
        self.assertIn(('整備局（香川）', t1, '10m3(固定)'), combos)
        self.assertIn(('香川県', t1, '6m3(固定)'), combos)
        self.assertIn(('高松市', t1, '6m3'), combos)
        self.assertIn(('高松市', t1, '10m3'), combos)

    @unittest.skipUnless(os.path.exists(OLD_JSON), '改定前JSON(32-733)がこのPCに無い')
    def test_full_pipeline_with_old_json(self):
        """③本体（商品＋加工＋改定前JSON）でも同じ組合せが出て、重複行が無いこと。"""
        import io
        import contextlib
        import io_xlsx
        with tempfile.TemporaryDirectory() as td:
            c20, c30 = os.path.join(td, '20.csv'), os.path.join(td, '30.csv')
            io_xlsx.xlsx_to_csv(RECV20, c20)
            io_xlsx.xlsx_to_csv(RECV, c30)
            with contextlib.redirect_stdout(io.StringIO()):
                s3 = G.run(c20, c30, OLD_JSON, os.path.join(td, 'out'),
                           src20=RECV20, src30=RECV)
            rows = G._read_csv_any(s3)
        t1 = '1t土のう 丸型 φ110cm×108cm'
        combos = list(zip(_col(rows, '発注者'), _col(rows, '袋材区分'), _col(rows, '1t袋時土砂数量')))
        for want in [('整備局（香川）', t1, '10m3(固定)'), ('香川県', t1, '6m3(固定)'),
                     ('高松市', t1, '6m3'), ('高松市', t1, '10m3')]:
            self.assertIn(want, combos)
        self.assertNotIn(('整備局（香川）', t1, '6m3'), combos)
        self.assertNotIn(('香川県', t1, '10m3'), combos)
        lead, tail = 2, 3
        keys = [tuple(r[lead:len(rows[0]) - tail]) for r in rows[1:] if r[1] != '回帰']
        self.assertEqual(len(keys), len(set(keys)))



if __name__ == '__main__':
    unittest.main(verbosity=2)
