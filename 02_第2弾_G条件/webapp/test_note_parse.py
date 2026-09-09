# -*- coding: utf-8 -*-
"""
test_note_parse.py ― G条件表 (注) の解釈（gen_tc_from_gjoken.read_gjoken / note_lint）

実行: python3 02_第2弾_G条件/webapp/test_note_parse.py

背景 (2026-08-17 運用FB / 水押し工 65-28-32-172):
  改修後G条件表(30)は人が手編集するため、(注) の語尾が揺れ、また列を挿入すると
  Gn番号だけがずれる。旧実装は「…を入力する必要はない」以外を黙って捨て、参照先を
  番号のみで解決していたため、
    - 「1ステージなら2番方は選択できない」が効かず 2番方が '-' にならない
    - ゲート源泉列が vary 昇格せず 2ステージのTCが1件も出ない
    - 番号ずれで無関係の列が '入力対象外' にされる
  という不具合が発生した。ここでは語尾の許容・名称優先解決・可視化を固定する。
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gen_tc_from_gjoken as G  # noqa: E402

# G1..G3 の3列。注は G1=区分 / G2=作業量 / G3=2番方 を想定。
_BASE = [
    '施工区分/入力条件,G1,G2,G3',
    '規格名計上,○,,',
    '各種(条件名),賃金対象時間の採用区分,日当り作業量,2番方労働時間',
    ',①標準,①1ステージ,①時間外0h',
    ',②実注入作業時間より算出,②2ステージ,②時間外2.0h',
    '(注)',
]


def _write(tmpdir, notes, name='g.csv'):
    """_BASE ＋ 注行 の G条件CSVを書いて返す。注はB列（先頭カンマ）に置く。"""
    path = os.path.join(tmpdir, name)
    lines = list(_BASE) + [',"%s"' % n.replace('"', '""') for n in notes]
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        f.write('\n'.join(lines) + '\n')
    return path


class TestNoteTailVariants(unittest.TestCase):
    """語尾の揺れ: 人が書く同義表現をすべて受け入れる。"""

    TAILS = [
        'を入力する必要はない',
        'を入力する必要はありません',
        'を選択する必要はない',
        'を選択できない',
        'を入力できない',
        'は表示されない',
        'は不要',
    ]

    def test_all_tails_parsed(self):
        with tempfile.TemporaryDirectory() as td:
            for tail in self.TAILS:
                note = ('2. G2条件「日当り作業量」で①「1ステージ」を選択した場合は、'
                        'G3条件「2番方労働時間」%s。' % tail)
                g = G.read_gjoken(_write(td, [note], 'g_%d.csv' % self.TAILS.index(tail)))
                self.assertEqual(len(g['notes']), 1, '語尾「%s」が解釈できていない' % tail)
                nt = g['notes'][0]
                self.assertEqual(nt['src_g'], 1)
                self.assertEqual(nt['src_choice'], '1ステージ')
                self.assertEqual(nt['targets'], [2])

    def test_unknown_form_is_reported_not_dropped(self):
        """想定外の形は黙って捨てず ERROR として報告する。"""
        with tempfile.TemporaryDirectory() as td:
            note = '2. G2条件「日当り作業量」で①「1ステージ」のときは G3条件「2番方労働時間」に注意すること。'
            g = G.read_gjoken(_write(td, [note]))
            self.assertEqual(g['notes'], [])
            self.assertTrue(any(i['level'] == 'ERROR' for i in g['note_issues']))
            head = G.note_lint(g)[0]
            self.assertEqual(head['level'], 'ERROR')
            self.assertIn('1件のうち 0件', head['text'])


class TestNameOverNumber(unittest.TestCase):
    """番号ずれ: 列挿入でGn番号だけが古いまま残るので名称を優先する。"""

    def test_number_mismatch_uses_name_and_warns(self):
        with tempfile.TemporaryDirectory() as td:
            # 対象側の番号がG1(=賃金対象時間の採用区分)を指しているが名称は2番方労働時間
            note = ('2. G2条件「日当り作業量」で①「1ステージ」を選択した場合は、'
                    'G1条件「2番方労働時間」を入力する必要はない。')
            g = G.read_gjoken(_write(td, [note]))
            self.assertEqual(g['notes'][0]['targets'], [2], '名称(G3)ではなく番号(G1)で解決された')
            warns = [i for i in g['note_issues'] if i['level'] == 'WARN']
            self.assertEqual(len(warns), 1)
            self.assertIn('不一致', warns[0]['text'])

    def test_src_number_mismatch_uses_name(self):
        with tempfile.TemporaryDirectory() as td:
            note = ('2. G9条件「日当り作業量」で①「1ステージ」を選択した場合は、'
                    'G3条件「2番方労働時間」を入力する必要はない。')
            g = G.read_gjoken(_write(td, [note]))
            self.assertEqual(g['notes'][0]['src_g'], 1)
            self.assertTrue(any(i['level'] == 'WARN' for i in g['note_issues']))

    def test_unknown_name_falls_back_to_number(self):
        with tempfile.TemporaryDirectory() as td:
            note = ('2. G2条件「存在しない条件名」で①「1ステージ」を選択した場合は、'
                    'G3条件「2番方労働時間」を入力する必要はない。')
            g = G.read_gjoken(_write(td, [note]))
            self.assertEqual(g['notes'][0]['src_g'], 1)
            self.assertTrue(any('見つかりません' in i['text'] for i in g['note_issues']))

    def test_no_warning_when_consistent(self):
        with tempfile.TemporaryDirectory() as td:
            note = ('2. G2条件「日当り作業量」で①「1ステージ」を選択した場合は、'
                    'G3条件「2番方労働時間」を入力する必要はない。')
            g = G.read_gjoken(_write(td, [note]))
            self.assertEqual(g['note_issues'], [])
            self.assertEqual(G.note_lint(g)[0]['level'], 'INFO')


class TestChoiceValidation(unittest.TestCase):
    def test_unknown_choice_label_is_error(self):
        """選択肢名の書き間違いはゲートが無言で効かなくなるので ERROR にする。"""
        with tempfile.TemporaryDirectory() as td:
            note = ('2. G2条件「日当り作業量」で①「1ステージの場合」を選択した場合は、'
                    'G3条件「2番方労働時間」を入力する必要はない。')
            g = G.read_gjoken(_write(td, [note]))
            self.assertEqual(g['notes'], [])
            self.assertTrue(any('選択肢' in i['text'] and i['level'] == 'ERROR'
                                for i in g['note_issues']))


class TestMultiChoiceForm(unittest.TestCase):
    """統合形式(#21要望)の回帰: 「①「A」・②「B」のいずれか」「①「A」～②「B」」。"""

    def test_enumerated_choices(self):
        with tempfile.TemporaryDirectory() as td:
            note = ('1. G2条件「日当り作業量」で①「1ステージ」・②「2ステージ」のいずれかを'
                    '選択した場合は、G3条件「2番方労働時間」を入力する必要はない。')
            g = G.read_gjoken(_write(td, [note]))
            self.assertEqual([n['src_choice'] for n in g['notes']], ['1ステージ', '2ステージ'])
            self.assertEqual(g['note_parsed_count'], 1, '注1件は1件として数える')

    def test_range_choices(self):
        with tempfile.TemporaryDirectory() as td:
            note = ('1. G2条件「日当り作業量」で①「1ステージ」～②「2ステージ」のいずれかを'
                    '選択した場合は、G3条件「2番方労働時間」を入力する必要はない。')
            g = G.read_gjoken(_write(td, [note]))
            self.assertEqual([n['src_choice'] for n in g['notes']], ['1ステージ', '2ステージ'])


class TestMultiTarget(unittest.TestCase):
    def test_two_targets(self):
        with tempfile.TemporaryDirectory() as td:
            note = ('1. G2条件「日当り作業量」で①「1ステージ」を選択した場合は、'
                    'G3条件「2番方労働時間」・G1条件「賃金対象時間の採用区分」'
                    'を入力する必要はない。')
            g = G.read_gjoken(_write(td, [note]))
            self.assertEqual(g['notes'][0]['targets'], [2, 0])


class TestNestedQuotes(unittest.TestCase):
    """選択肢名・条件名それ自体が「」を含む場合(2026-08-27 発覚 / 例 09養生マット
    「「m2」単位の材料単価」)。非貪欲な 「(.+?)」 だと「m2 で切れ、注が丸ごと
    無効化されていた。"""

    BASE = [
        '施工区分/入力条件,G1,G2',
        '規格名計上,,',
        '各種(条件名),養生マット材料の単位選択,1m2当り「養生マット」使用量',
        ',①「m2」単位の材料単価,(実数入力)',
        ',②「m2」単位以外の材料単価,',
        '(注)',
    ]

    def _write(self, td, note):
        path = os.path.join(td, 'g.csv')
        lines = list(self.BASE) + [',"%s"' % note.replace('"', '""')]
        with open(path, 'w', encoding='utf-8-sig', newline='') as f:
            f.write(chr(10).join(lines) + chr(10))
        return path

    def test_choice_with_inner_quotes(self):
        with tempfile.TemporaryDirectory() as td:
            note = ('1. G1条件「養生マット材料の単位選択」で①「「m2」単位の材料単価」を'
                    '選択した場合は、G2条件「1m2当り「養生マット」使用量」'
                    'を入力する必要はない。')
            g = G.read_gjoken(self._write(td, note))
            self.assertEqual(len(g['notes']), 1, '注が解釈できていない')
            nt = g['notes'][0]
            self.assertEqual(nt['src_choice'], '「m2」単位の材料単価')
            self.assertEqual(nt['targets'], [1], '対象列名の「」入れ子で切れている')
            self.assertEqual(g['note_parsed_count'], 1)
            self.assertEqual([i for i in g['note_issues'] if i['level'] == 'ERROR'], [])

    def test_quoted_spans_helper(self):
        self.assertEqual(G._quoted_spans('①「「m2」単位の材料単価」'),
                         ['「m2」単位の材料単価'])
        self.assertEqual(G._quoted_spans('①「A」・②「B」'), ['A', 'B'])
        self.assertEqual(G._quoted_spans('対応の取れない」は無視'), [])


class TestNumericSource(unittest.TestCase):
    """実数入力の条件を起点にした注(2026-08-27)。

    ①(gen_gjoken)は実数列を起点にする注を自分で「…で「任意」を選択した場合は」と
    書き出すのに、③(read_gjoken)がそれを選択肢に無いと弾いてERRORにしていた
    (例 26目地工: 注16件中3件が未反映)。ツール内の自己不整合の是正。
    「任意」以外の不一致は従来どおりERROR(誤記の検知力は落とさない)。"""

    BASE = [
        '施工区分/入力条件,G1,G2',
        '規格名計上,,',
        '各種(条件名),1m当りチェアーの使用量,X3:チェアー1m当りの現場着価格',
        ',(実数入力),(実数入力)',
        ',(kg/m),(円/m)',
        '(注)',
    ]

    def _read(self, td, note):
        path = os.path.join(td, 'g.csv')
        lines = list(self.BASE) + [',"%s"' % note.replace('"', '""')]
        with open(path, 'w', encoding='utf-8-sig', newline='') as f:
            f.write(chr(10).join(lines) + chr(10))
        return G.read_gjoken(path)

    def test_any_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            g = self._read(td, '1. G1条件「1m当りチェアーの使用量」で「任意」を選択した'
                               '場合は、G2条件「X3:チェアー1m当りの現場着価格」'
                               'を入力する必要はない。')
            self.assertEqual(len(g['notes']), 1)
            nt = g['notes'][0]
            # TC側は実数列を「任意」と出力するので突合キーも「任意」に寄せる
            self.assertEqual(nt['src_choice'], '任意')
            self.assertEqual(nt['targets'], [1])
            self.assertEqual([i for i in g['note_issues'] if i['level'] == 'ERROR'], [])

    def test_unit_label_normalized_to_any(self):
        with tempfile.TemporaryDirectory() as td:
            g = self._read(td, '1. G1条件「1m当りチェアーの使用量」で「(実数入力)」を選択'
                               'した場合は、G2条件「X3:チェアー1m当りの現場着価格」'
                               'を入力する必要はない。')
            self.assertEqual([n['src_choice'] for n in g['notes']], ['任意'])

    def test_typo_still_errors(self):
        with tempfile.TemporaryDirectory() as td:
            g = self._read(td, '1. G1条件「1m当りチェアーの使用量」で「にんい」を選択した'
                               '場合は、G2条件「X3:チェアー1m当りの現場着価格」'
                               'を入力する必要はない。')
            self.assertEqual(g['notes'], [])
            self.assertTrue([i for i in g['note_issues'] if i['level'] == 'ERROR'],
                            '誤記が検知されなくなっている')


# ======================================================================
# 確定設計A（2026-09-09 / 1件目 公園照明 160-822）
#   (注)は条件名を書かない短い形を正式書式にした（積算基準=赤本と同じ書き方）。
#   セルからそのままコピペした選択肢（丸数字・【A=1】等のコード付き）も受理する。
# ======================================================================

_BASE_A = [
    '施工区分/入力条件,G1,G2,G3',
    '規格名計上,,,',
    '各種(条件名),照明器具1台当たりの単価計上方法,照明器具1台当たりの単価,クレーン賃料施工条件',
    ',①単価DBより選択,(実数入力),①【H=1】 補正無し',
    ',②【B条件】 材料単価を直接入力,,②【H=2】 夜間割増',
    '(注)',
]


def _write_a(td, notes, base=None, name='g.csv'):
    path = os.path.join(td, name)
    lines = list(base or _BASE_A) + [',"%s"' % n.replace('"', '""') for n in notes]
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        f.write('\n'.join(lines) + '\n')
    return path


class TestOptKey(unittest.TestCase):
    """A-3(1): 表のセルと注の「」内を同じ手順で正規化する。"""

    def test_strips_marker_and_code(self):
        cases = {
            '②【B条件】 材料単価を直接入力': '材料単価を直接入力',
            '② 材料単価を直接入力': '材料単価を直接入力',
            '②材料単価を直接入力': '材料単価を直接入力',
            '①【H=1】 補正無し': '補正無し',
            '(21)【A=1】 一般照明器具': '一般照明器具',
            '材料単価を直接入力': '材料単価を直接入力',
        }
        for src, want in cases.items():
            self.assertEqual(G._opt_key(src), want, src)

    def test_inner_space_is_kept(self):
        """内部の空白は触らない（v1.2.2 の完全一致方針と整合）。"""
        self.assertEqual(G._opt_key('①材料 単価'), '材料 単価')


class TestCopyPasteFromCell(unittest.TestCase):
    """1件目の受け入れ条件: 4通りの書き方がすべて同じ解釈になる。"""

    PATTERNS = [
        '1. G1条件「照明器具1台当たりの単価計上方法」で②「 材料単価を直接入力」を選択した場合は、'
        'G2条件「照明器具1台当たりの単価」 を入力する必要はない。',
        '1. G1条件「照明器具1台当たりの単価計上方法」で②「【B条件】 材料単価を直接入力」を選択した場合は、'
        'G2条件「照明器具1台当たりの単価」 を入力する必要はない。',
        '1. G1条件「照明器具1台当たりの単価計上方法」で「② 材料単価を直接入力」を選択した場合は、'
        'G2条件「照明器具1台当たりの単価」 を入力する必要はない。',
        '1. G1条件で②を選択した場合は、G2条件を入力する必要はない。',
    ]

    def test_all_patterns_resolve_identically(self):
        with tempfile.TemporaryDirectory() as td:
            got = []
            for i, note in enumerate(self.PATTERNS):
                g = G.read_gjoken(_write_a(td, [note], name='g%d.csv' % i))
                self.assertEqual(g['note_parsed_count'], 1, note)
                self.assertEqual([x for x in g['note_issues']
                                  if x['level'] == 'ERROR'], [], note)
                got.append(g['notes'])
            for other in got[1:]:
                self.assertEqual(other, got[0])
            self.assertEqual(got[0][0]['src_choice'], '材料単価を直接入力')
            self.assertEqual(got[0][0]['targets'], [1])

    def test_marker_with_embedded_code(self):
        """①「①【H=1】 補正無し」のような二重付与も通る。"""
        with tempfile.TemporaryDirectory() as td:
            g = G.read_gjoken(_write_a(td, [
                '1. G3条件「クレーン賃料施工条件」で①「①【H=1】 補正無し」を選択した場合は、'
                'G2条件を入力する必要はない。']))
            self.assertEqual(g['note_parsed_count'], 1)
            self.assertEqual(g['notes'][0]['src_choice'], '補正無し')


class TestShortFormat(unittest.TestCase):
    """A-R1〜R3: 条件名・選択肢名を省いた短い形。"""

    def test_no_warn_when_name_omitted(self):
        """A-R5: 名称の省略は正式書式なので WARN を出さない。"""
        with tempfile.TemporaryDirectory() as td:
            g = G.read_gjoken(_write_a(td, ['1. G1条件で②を選択した場合は、'
                                            'G2条件を入力する必要はない。']))
            self.assertEqual(g['note_parsed_count'], 1)
            self.assertEqual(g['note_issues'], [])

    def test_multiple_targets(self):
        with tempfile.TemporaryDirectory() as td:
            g = G.read_gjoken(_write_a(td, ['1. G1条件で②を選択した場合は、'
                                            'G2条件、G3条件を入力する必要はない。']))
            self.assertEqual(g['notes'][0]['targets'], [1, 2])

    def test_marker_range(self):
        with tempfile.TemporaryDirectory() as td:
            g = G.read_gjoken(_write_a(td, ['1. G3条件で①～②のいずれかを選択した場合は、'
                                            'G2条件を入力する必要はない。']))
            self.assertEqual([n['src_choice'] for n in g['notes']],
                             ['補正無し', '夜間割増'])

    def test_marker_list(self):
        with tempfile.TemporaryDirectory() as td:
            g = G.read_gjoken(_write_a(td, ['1. G3条件で①・②のいずれかを選択した場合は、'
                                            'G2条件を入力する必要はない。']))
            self.assertEqual(len(g['notes']), 2)

    def test_unknown_marker_is_error(self):
        """A-R7: 存在しない番号は従来どおり ERROR（誤記の検知力を落とさない）。"""
        with tempfile.TemporaryDirectory() as td:
            g = G.read_gjoken(_write_a(td, ['1. G1条件で⑨を選択した場合は、'
                                            'G2条件を入力する必要はない。']))
            self.assertEqual(g['notes'], [])
            self.assertTrue([i for i in g['note_issues'] if i['level'] == 'ERROR'])

    def test_missing_column_is_error(self):
        with tempfile.TemporaryDirectory() as td:
            g = G.read_gjoken(_write_a(td, ['1. G9条件で①を選択した場合は、'
                                            'G2条件を入力する必要はない。']))
            self.assertEqual(g['notes'], [])
            self.assertTrue([i for i in g['note_issues'] if i['level'] == 'ERROR'])


class TestNoteSheet(unittest.TestCase):
    """A-R9: 出力Excelの2枚目シート「(注)の解釈」の行。"""

    def test_rows_expand_numbers_to_names(self):
        with tempfile.TemporaryDirectory() as td:
            g = G.read_gjoken(_write_a(td, [
                '1. G1条件で②を選択した場合は、G2条件を入力する必要はない。',
                '2. G1条件で② → G2条件（形の崩れた注）']))
            rows = G.note_sheet_rows(g)
        self.assertEqual(rows[0], ['注', '判定', '条件', '選択肢',
                                   '入力不要になる条件', '原文'])
        first = rows[1]
        self.assertEqual(first[1], '反映')
        self.assertIn('照明器具1台当たりの単価計上方法', first[2])
        self.assertIn('材料単価を直接入力', first[3])
        self.assertIn('照明器具1台当たりの単価', first[4])
        self.assertTrue(any(r and len(r) > 1 and r[1] == '未反映' for r in rows[1:]),
                        '読めなかった注が「未反映」で残ること')


class TestHeaderGNumbers(unittest.TestCase):
    """ヘッダ行の G番号ラベルで注の参照を解決する（人が G22 等に振り直すため）。"""

    BASE = [
        '施工区分/入力条件,G1,G22,G23',
        '規格名計上,,,',
        '各種(条件名),資材計上区分,単位選択,内容量',
        ',①材料費+施工費,①「kg」単位,(実数入力)',
        ',②施工費のみ,②「箱」単位,',
        '(注)',
    ]

    def test_non_sequential_g_numbers(self):
        with tempfile.TemporaryDirectory() as td:
            g = G.read_gjoken(_write_a(td, [
                '1. G1条件で②を選択した場合は、G22条件、G23条件を入力する必要はない。'],
                base=self.BASE))
        self.assertEqual(g['gnums'], [1, 22, 23])
        self.assertEqual(g['note_parsed_count'], 1)
        self.assertEqual(g['notes'][0]['targets'], [1, 2])


class TestTargetRangeNotation(unittest.TestCase):
    """3件目(a2): 対象側の範囲表記 `G3条件～G5条件` を展開する（2026-09-09 追加）。

    静的破砕工 01_#631696 の人作成表はこの書き方をしている。従来は
    `G(\\d+)条件` を拾うだけで G3 と G5 しか取らず、**間の G4 が WARN も出さずに
    落ちて**いた（③のTCが静かに間違う）。`～` が無ければ従来どおりの解釈なので、
    ①が出す「、」区切りの注には影響しない。
    """

    BASE = [
        '施工区分/入力条件,G1,G2,G3,G4,G5',
        '規格名計上,○,,,,',
        '各種(条件名),施工区分,条件B,条件C,条件D,条件E',
        ',①転石の破砕,①b1,①c1,①d1,①e1',
        ',②コンクリートの破砕,②b2,②c2,②d2,②e2',
        '(注)',
    ]

    def _targets(self, note):
        with tempfile.TemporaryDirectory() as td:
            g = G.read_gjoken(_write_a(td, [note], base=self.BASE))
            self.assertEqual(g['note_parsed_count'], 1, note)
            self.assertEqual([i for i in g['note_issues'] if i['level'] == 'ERROR'],
                             [], note)
            return g['notes'][0]['targets']

    def test_range_with_joken_on_both_sides(self):
        self.assertEqual(self._targets(
            'G1条件で①を選択した場合は、G3条件～G5条件を選択する必要はない。'), [2, 3, 4])

    def test_range_with_joken_only_at_end(self):
        self.assertEqual(self._targets(
            'G1条件で②を選択した場合は、G2～G4条件を選択する必要はない。'), [1, 2, 3])

    def test_range_mixed_with_list(self):
        self.assertEqual(self._targets(
            'G1条件で①を選択した場合は、G2条件、G4条件～G5条件を選択する必要はない。'),
            [1, 3, 4])

    def test_range_with_names(self):
        self.assertEqual(self._targets(
            'G1条件で②を選択した場合は、G2条件「条件B」～G4条件「条件D」を'
            '選択する必要はない。'), [1, 2, 3])

    def test_reversed_range(self):
        """G5条件～G3条件 のように逆順でも同じ範囲として扱う。"""
        self.assertEqual(self._targets(
            'G1条件で①を選択した場合は、G5条件～G3条件を選択する必要はない。'), [2, 3, 4])

    def test_list_is_unaffected(self):
        """`、`区切り（①が出す形）は従来どおり。"""
        self.assertEqual(self._targets(
            'G1条件で①を選択した場合は、G3、G5条件を選択する必要はない。'), [2, 4])

    def test_single_target_is_unaffected(self):
        self.assertEqual(self._targets(
            'G1条件で①を選択した場合は、G3条件を入力する必要はない。'), [2])


if __name__ == '__main__':
    unittest.main(verbosity=2)
