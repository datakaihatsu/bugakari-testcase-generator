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


if __name__ == '__main__':
    unittest.main(verbosity=2)
