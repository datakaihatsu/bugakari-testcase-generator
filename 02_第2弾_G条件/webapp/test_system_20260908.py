# -*- coding: utf-8 -*-
"""test_system_20260908.py ― システムテスト（2026-09-08 定例 4件の受け入れ確認）

実行: python3 02_第2弾_G条件/webapp/test_system_20260908.py

単体テストが個々の関数を固定するのに対し、ここでは**運用者が実際に触る入口**
（webapp のサービス層 = ①タブ / ③タブ / 🆕新規歩掛タブ）を通して、
定例で報告された現象が解消していることを端から端まで確認する。

前提データ:
  - GaiaCloud データ（歩掛JSON）… 無い環境では該当テストをスキップ
  - 定例で提供された実ファイル（不具合と要求/20260908_定例より/）
"""

import os
import sys
import glob
import shutil
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_BASE = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_BASE)
sys.path.insert(0, _HERE)
sys.path.insert(0, _BASE)

import service                      # noqa: E402
import io_xlsx                      # noqa: E402

_TEIREI = os.path.join(_BASE, '不具合と要求', '20260908_定例より')
_PARK = (r'C:/ProgramData/CoBeing/GaiaCloud/DB/Bugakari/160/000000'
         r'/160-822.20230401.20240701.json')
_HASAI = (r'C:/ProgramData/CoBeing/GaiaCloud/DB/Bugakari/32/016000'
          r'/32-16123.20240401.20240801.json')
_TRAVEL = (r'C:/ProgramData/CoBeing/GaiaCloud/DB/Bugakari/64/001000'
           r'/64-1538.20230401.20231001.json')


def _cfg(tmp):
    c = service.load_config(use_user_settings=False)
    c = dict(c)
    c['workdir_root'] = tmp
    return c


def _gen_g(json_path, tmp):
    """①タブ相当。G条件表(CSV)のパスを返す。"""
    r = service.gen_g(json_path, cfg=_cfg(tmp))
    assert r['error'] is None, r['error']
    return r


def _matrix(path):
    m, _enc = io_xlsx.read_csv_matrix(path)
    return m


def _notes_of(matrix):
    out, hit = [], False
    for row in matrix:
        joined = ''.join((c or '').strip() for c in row)
        if not joined:
            continue
        if '設計メモ' in joined:
            break
        if joined == '(注)':
            hit = True
            continue
        if hit:
            out += [c.strip() for c in row if c.strip()]
    return out


def _cond_names(matrix):
    for row in matrix:
        if row and row[0].strip() == '各種(条件名)':
            return [c.strip() for c in row[1:] if c.strip()]
    return []


@unittest.skipIf(not os.path.exists(_PARK), 'GaiaCloud データが無い環境')
class Test2件目_不足していた注が出る(unittest.TestCase):
    """2件目: 商品G条件に「単価計上方法 → 単価は入力不要」の注が出ていなかった。

    向きは「①単価DBより選択を選んだら単価は不要」が正（②は要求文の書き間違い、
    2026-09-09 にユーザ確認済み）。
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        r = _gen_g(_PARK, cls.tmp)
        cls.m = _matrix(r['csv_path'])
        cls.names = _cond_names(cls.m)
        cls.notes = _notes_of(cls.m)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _gi(self, name):
        return self.names.index(name) + 1

    def _has_note(self, src, choice, target):
        import re
        pat = f'G{src}条件で{choice}を選択した場合は'
        return any(re.sub(r'^\d+\.\s*', '', n).startswith(pat) and f'G{target}条件' in n
                   for n in self.notes)

    def test_照明器具の単価の注が出る(self):
        w, p = self._gi('照明器具1台当たりの単価計上方法'), self._gi('照明器具1台当たりの単価')
        self.assertTrue(self._has_note(w, '①', p), f'注が出ていない: {self.notes}')

    def test_灯柱の単価の注が出る(self):
        w, p = self._gi('灯柱1本当たりの単価計上方法'), self._gi('灯柱1本当たりの単価')
        self.assertTrue(self._has_note(w, '①', p), f'注が出ていない: {self.notes}')

    def test_真因でない注は出ない(self):
        """「灯柱の計上方法 → アーム規格区分」は真因が ポール規格区分 なので出ない。"""
        import re
        w, arm = self._gi('灯柱1本当たりの単価計上方法'), self._gi('アーム規格区分')
        for n in (re.sub(r'^\d+\.\s*', '', x) for x in self.notes):
            if n.startswith(f'G{w}条件で'):
                self.assertNotIn(f'G{arm}条件', n, f'偽の注が残っている: {n}')


@unittest.skipIf(not os.path.exists(_HASAI), 'GaiaCloud データが無い環境')
class Test3件目_商品から出す条件が足りていない(unittest.TestCase):
    """3件目: 深い枝にある条件が商品G条件の列にならなかった（7列欠落）。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        r = _gen_g(_HASAI, cls.tmp)
        cls.m = _matrix(r['csv_path'])
        cls.names = _cond_names(cls.m)
        cls.notes = _notes_of(cls.m)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_人作成で足された条件が商品から出る(self):
        for n in ('2次破砕の方法(鉄筋コンクリート)',
                  '岩石の破砕[ベンチカット工法](クローラドリル使用)',
                  '岩石の破砕[トレンチ工法](クローラドリル使用)',
                  '鉄筋コンクリートの破砕(ハンドハンマー)2次ハンドブレーカー',
                  '鉄筋コンクリートの破砕(ハンドハンマー)2次大型機械',
                  '鉄筋コンクリートの破砕(クローラドリル)2次大型機械',
                  '1箱当りの内容量(質量)'):
            self.assertIn(n, self.names, f'列が出ていない: {n}')

    def test_注が短い形で出る(self):
        import re
        self.assertTrue(self.notes)
        for n in self.notes:
            body = re.sub(r'^\d+\.\s*', '', n)
            self.assertRegex(body, r'^G\d+条件で.+を選択した場合は、'
                                   r'G\d+条件(、G\d+条件)*を入力する必要はない。$')


@unittest.skipIf(not os.path.exists(_TRAVEL), 'GaiaCloud データが無い環境')
class Test4件目_表示と範囲(unittest.TestCase):
    """4件目: 選択肢が数値コードで出る / (値なし) / 範囲が出ない。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        r = _gen_g(_TRAVEL, cls.tmp)
        cls.g_matrix = _matrix(r['csv_path'])
        # 🆕新規歩掛タブ: 生成した商品G条件をそのまま入力してTCを作る
        rn = service.gen_tc_new(r['csv_path'], cfg=_cfg(cls.tmp))
        assert rn['error'] is None, rn['error']
        cls.tc = _matrix(rn['csv_path'])
        cls.xlsx = rn['xlsx_path']

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _tc_col(self, name):
        i = self.tc[0].index(name)
        return [r[i] for r in self.tc[1:] if i < len(r)]

    def test_選択肢が名称で出る(self):
        vals = self._tc_col('片道距離区分')
        self.assertIn('30km以上 60km未満', vals)
        self.assertNotIn('0.1', vals)

    def test_値なしが出ない(self):
        flat = [c for row in self.tc for c in row]
        self.assertNotIn('(値なし)', flat)

    def test_G条件表に範囲が出る(self):
        flat = [c for row in self.g_matrix for c in row]
        self.assertTrue(any('(範囲: 0 < 値)' == c for c in flat),
                        'G条件表に範囲セルが無い')

    def test_確認観点に範囲が出る(self):
        checks = '\n'.join(self._tc_col('選択肢の適切さ確認'))
        self.assertIn('0 < 値', checks)
        self.assertIn('警告が出ること', checks)

    def test_G条件と_TCの選択肢表示が一致する(self):
        """①と③が同じ列を選ぶ（4件目①の本質）。"""
        gi = _cond_names(self.g_matrix).index('片道距離区分') + 1
        g_opts = set()
        for row in self.g_matrix[3:]:
            if not row or row[0].strip():
                break
            v = row[gi].strip() if gi < len(row) else ''
            if v:
                g_opts.add(v.lstrip('①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳'))
        tc_vals = {v for v in self._tc_col('片道距離区分') if v not in ('', '-')}
        self.assertTrue(tc_vals <= g_opts, f'TCの値がG条件の選択肢に無い: {tc_vals - g_opts}')


@unittest.skipIf(not os.path.isdir(_TEIREI), '定例の実ファイルが無い環境')
class Test1件目_注のコピペ(unittest.TestCase):
    """1件目: (注)に選択肢をセルからコピペするとエラーになっていた。

    定例で提供された実ファイル（公園照明）3本を ③タブ相当で通す。
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        d = os.path.join(_TEIREI, '1件目', '【試行中】テストケースツール')
        cls.files = {
            'いま通っている形': glob.glob(os.path.join(d, '01_#629353_*.xlsx')),
            'コード付きコピペ': [p for p in glob.glob(os.path.join(d, 'NG-01_*.xlsx'))
                          if 'コピー' not in os.path.basename(p)],
            '印つきコピペ': [p for p in glob.glob(os.path.join(d, 'NG-01_*コピー.xlsx'))],
        }

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _lint(self, path):
        wd = tempfile.mkdtemp(dir=self.tmp)
        csv30 = service._to_csv(path, wd, '30_G条件.csv')
        return service._note_lint(csv30), service._note_sheet(csv30)

    def test_すべての書き方で3件とも反映される(self):
        for label, paths in self.files.items():
            self.assertTrue(paths, f'{label} のファイルが見つからない')
            lint, sheet = self._lint(paths[0])
            errs = [x for x in lint if x['level'] == 'ERROR']
            self.assertEqual(errs, [], f'{label}: {errs}')
            head = [x for x in lint if '(注)' in x['text']]
            self.assertTrue(head and '3件のうち 3件' in head[0]['text'],
                            f'{label}: {head}')
            reflected = [r for r in sheet[1:] if len(r) > 1 and r[1] == '反映']
            self.assertEqual(len(reflected), 3, f'{label}: 解釈シートの反映が3件でない')

    def test_解釈シートが番号を名前に展開する(self):
        _lint, sheet = self._lint(self.files['コード付きコピペ'][0])
        self.assertEqual(sheet[0][:5], ['注', '判定', '条件', '選択肢', '入力不要になる条件'])
        joined = '\n'.join('|'.join(r) for r in sheet[1:])
        self.assertIn('照明器具1台当たりの単価計上方法', joined)
        self.assertIn('材料単価を直接入力', joined)


@unittest.skipIf(not os.path.exists(_PARK), 'GaiaCloud データが無い環境')
class Test出力Excelの2枚目シート(unittest.TestCase):
    """A-R9: 出力Excelに「(注)の解釈」シートが付く。"""

    def test_new_tab_has_note_sheet(self):
        import openpyxl
        tmp = tempfile.mkdtemp()
        try:
            r = _gen_g(_PARK, tmp)
            rn = service.gen_tc_new(r['csv_path'], cfg=_cfg(tmp))
            self.assertIsNone(rn['error'], rn['error'])
            wb = openpyxl.load_workbook(rn['xlsx_path'])
            self.assertIn('(注)の解釈', wb.sheetnames)
            ws = wb['(注)の解釈']
            self.assertEqual([c.value for c in ws[1]][:2], ['注', '判定'])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
