# -*- coding: utf-8 -*-
"""
test_locate.py ― locate.py の単体テスト（標準ライブラリ unittest のみ）

実行: python3 02_第2弾_G条件/webapp/test_locate.py
検証対象:
  - parse_gaia9_key / classify_key : 3/4要素・65先頭・歩掛キー2要素・異常系
  - find_bugakari_versions : 今日以下で最大版 / 全未来なら最小版 / UserMD優先 / 派生除外
  - KeyMapping / resolve : ExpCDConvert.json 変換・歩掛キー直接指定・未ヒット
"""

import os
import json
import shutil
import tempfile
import unittest

import locate  # 同ディレクトリ実行を想定（下部の __main__ で sys.path を通す）


class TestKeyParse(unittest.TestCase):
    def test_gaia9_3_elem(self):
        self.assertEqual(locate.parse_gaia9_key('14-160-20350'), (14, 160, 20350))

    def test_gaia9_4_elem_ok(self):
        self.assertEqual(locate.parse_gaia9_key('65-14-160-20350'), (14, 160, 20350))

    def test_gaia9_4_elem_wrong_prefix(self):
        with self.assertRaises(ValueError):
            locate.parse_gaia9_key('99-14-160-20350')

    def test_gaia9_non_numeric(self):
        with self.assertRaises(ValueError):
            locate.parse_gaia9_key('14-abc-20350')

    def test_gaia9_wrong_count(self):
        with self.assertRaises(ValueError):
            locate.parse_gaia9_key('160-1351')  # 2要素はGaia9キーとして不正

    def test_classify_bugakari(self):
        self.assertEqual(locate.classify_key('160-1351'), ('bugakari', '160-1351'))

    def test_classify_gaia9(self):
        self.assertEqual(locate.classify_key('65-14-160-20350'), ('gaia9', (14, 160, 20350)))

    def test_classify_full_width_and_space(self):
        # 全角/空白区切りも許容（27 の _split_nums 同等）
        self.assertEqual(locate.classify_key('160 1351'), ('bugakari', '160-1351'))


class TestVersionSelect(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='bugakari_')

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _touch(self, relpath):
        full = os.path.join(self.root, relpath)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'w', encoding='utf-8') as f:
            f.write('{}')
        return full

    def test_picks_latest_not_after_today(self):
        # 今日=20260401。20260101 と 20260320 が過去、20260601 は未来
        self._touch('160/160-1351.2025.20260101.json')
        self._touch('160/160-1351.2026.20260320.json')
        self._touch('160/160-1351.2026.20260601.json')
        r = locate.find_bugakari_versions(self.root, '160-1351', today_int=20260401)
        self.assertEqual(len(r['candidates']), 3)
        self.assertEqual(r['chosen']['ymd'], 20260320)  # 今日以下で最大

    def test_all_future_picks_nearest_min(self):
        self._touch('160/160-1351.2026.20260601.json')
        self._touch('160/160-1351.2026.20260701.json')
        r = locate.find_bugakari_versions(self.root, '160-1351', today_int=20260401)
        self.assertEqual(r['chosen']['ymd'], 20260601)  # 全未来なら最小

    def test_ignores_derivative_files(self):
        # <key>.<年度>.<年月日>.json 以外（Memo等）は除外
        self._touch('160/160-1351.2026.20260320.json')
        self._touch('160/160-1351.Memo.json')
        self._touch('160/160-1351.2026.20260320.Memo.json')
        r = locate.find_bugakari_versions(self.root, '160-1351', today_int=20260401)
        self.assertEqual(len(r['candidates']), 1)
        self.assertEqual(r['chosen']['ymd'], 20260320)

    def test_not_found(self):
        r = locate.find_bugakari_versions(self.root, '999-9999', today_int=20260401)
        self.assertEqual(r['candidates'], [])
        self.assertIsNone(r['chosen'])

    def test_fallback_recursive_when_no_usermd_subfolder(self):
        # UserMDサブフォルダが無くても全体再帰で拾う
        self._touch('別階層/160-1351.2026.20260320.json')
        r = locate.find_bugakari_versions(self.root, '160-1351', today_int=20260401)
        self.assertEqual(r['chosen']['ymd'], 20260320)


class TestResolve(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='bugakari_')
        self.expcd = os.path.join(self.root, 'ExpCDConvert.json')
        with open(self.expcd, 'w', encoding='utf-8') as f:
            json.dump({'ExpCDConvertList': [
                {'Gaia9ShochoCD': 14, 'Gaia9UserMD': 160, 'Gaia9CD': 20350,
                 'UserMD': 160, 'CD': 1351},
            ]}, f, ensure_ascii=False)
        self._touch('160/160-1351.2026.20260320.json')

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _touch(self, relpath):
        full = os.path.join(self.root, relpath)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'w', encoding='utf-8') as f:
            f.write('{}')

    def test_resolve_gaia9(self):
        r = locate.resolve('65-14-160-20350', expcd_path=self.expcd,
                           bugakari_root=self.root, today_int=20260401)
        self.assertIsNone(r['error'])
        self.assertEqual(r['kind'], 'gaia9')
        self.assertEqual(r['bugakari_keys'], ['160-1351'])
        self.assertEqual(r['results'][0]['chosen']['ymd'], 20260320)

    def test_resolve_bugakari_direct(self):
        r = locate.resolve('160-1351', bugakari_root=self.root, today_int=20260401)
        self.assertIsNone(r['error'])
        self.assertEqual(r['kind'], 'bugakari')
        self.assertEqual(r['results'][0]['chosen']['ymd'], 20260320)

    def test_resolve_unknown_gaia9(self):
        r = locate.resolve('65-99-999-99999', expcd_path=self.expcd,
                           bugakari_root=self.root, today_int=20260401)
        self.assertIsNotNone(r['error'])

    def test_resolve_bad_input(self):
        r = locate.resolve('abc', bugakari_root=self.root, today_int=20260401)
        self.assertIsNotNone(r['error'])


if __name__ == '__main__':
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    unittest.main(verbosity=2)
