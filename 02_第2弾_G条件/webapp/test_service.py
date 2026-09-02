# -*- coding: utf-8 -*-
"""
test_service.py ― service.py の結合テスト（実データ 06土のう / 実ExpCD・Bugakari）

実行: python3 02_第2弾_G条件/webapp/test_service.py
検証:
  - load_config: 既定マージ・壊れconfig無視
  - ユーザ設定(格納場所の変更): 保存→load_configへ反映→リセット / パス推定・実在チェック
  - locate_versions: 実キーで版候補が返る
  - gen_g: 実JSON → G条件CSV＋商品_xlsx（g_count>0）
  - gen_tc: 06の20/30 G条件＋改定前JSON → TC CSV＋テストケース_xlsx（条件列色付き）
  - エラー整形: 不在パスで error が返り例外を投げない
実データが無い環境では該当テストを skip。
"""

import os
import json
import tempfile
import unittest

import service
import io_xlsx

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '運用案件', '06_土のう積工'))
OLD_JSON = os.path.join(BASE, '10_改定前', '32-735.20240401.20240401.json')
CSV20 = os.path.join(BASE, '20_叩き台G条件', 'Gaia入力基準表_土のう積工(m2)_叩き台.csv')
CSV30 = os.path.join(BASE, '30_人作成G条件', 'Gaia入力基準表_土のう積工(m2)_人作成.csv')
EXPCD = service.DEFAULT_CONFIG['expcd_path']


class TestConfig(unittest.TestCase):
    def test_defaults_when_missing(self):
        cfg = service.load_config(os.path.join(tempfile.mkdtemp(), 'nope.json'))
        self.assertEqual(cfg['port'], 8765)
        self.assertIn('expcd_path', cfg)

    def test_merge_and_bad_config_ignored(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, 'config.json')
        with open(p, 'w', encoding='utf-8') as f:
            f.write('{ this is broken json ')
        cfg = service.load_config(p)  # 壊れていても例外なく既定
        self.assertEqual(cfg['port'], 8765)
        with open(p, 'w', encoding='utf-8') as f:
            json.dump({'port': 9999, 'bugakari_root': 'X:/b'}, f)
        cfg = service.load_config(p)
        self.assertEqual(cfg['port'], 9999)
        self.assertEqual(cfg['bugakari_root'], 'X:/b')


class TestUserSettings(unittest.TestCase):
    """格納場所の変更（画面の「変更」）。保存先は GAIA_TC_SETTINGS_DIR で差し替えて隔離する。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='setting_')
        self._old = os.environ.get('GAIA_TC_SETTINGS_DIR')
        os.environ['GAIA_TC_SETTINGS_DIR'] = self.dir

    def tearDown(self):
        if self._old is None:
            os.environ.pop('GAIA_TC_SETTINGS_DIR', None)
        else:
            os.environ['GAIA_TC_SETTINGS_DIR'] = self._old

    def test_save_load_clear(self):
        self.assertEqual(service.load_user_settings(), {})
        default_expcd = service.load_config()['expcd_path']
        service.save_user_settings({'expcd_path': 'E:/x/ExpCDConvert.json',
                                    'bugakari_root': 'E:/x/Bugakari',
                                    'port': 1})  # 既知キー以外は保存しない
        self.assertTrue(os.path.exists(service.user_settings_path()))
        self.assertEqual(service.load_user_settings(),
                         {'expcd_path': 'E:/x/ExpCDConvert.json',
                          'bugakari_root': 'E:/x/Bugakari'})
        cfg = service.load_config()
        self.assertEqual(cfg['expcd_path'], 'E:/x/ExpCDConvert.json')
        self.assertEqual(cfg['port'], 8765)  # port はユーザ設定の対象外
        # 既定側は汚染されない
        self.assertEqual(service.load_config(use_user_settings=False)['expcd_path'],
                         default_expcd)
        service.clear_user_settings()
        self.assertEqual(service.load_user_settings(), {})
        self.assertEqual(service.load_config()['expcd_path'], default_expcd)

    def test_broken_settings_ignored(self):
        path = service.user_settings_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write('{broken')
        self.assertEqual(service.load_user_settings(), {})  # 例外を投げない

    def test_settings_state_flags_custom(self):
        self.assertFalse(service.settings_state()['is_custom'])
        service.save_user_settings({'expcd_path': 'E:/x/ExpCDConvert.json',
                                    'bugakari_root': 'E:/x/Bugakari'})
        st = service.settings_state()
        self.assertTrue(st['is_custom'])
        self.assertEqual(st['expcd_path'], 'E:/x/ExpCDConvert.json')
        self.assertFalse(st['check']['ok'])  # 存在しないので NG


class TestPathHelpers(unittest.TestCase):
    def test_derive_from_root(self):
        db = os.path.join(tempfile.mkdtemp(prefix='gaiadb_'), 'GaiaCloud', 'DB')
        os.makedirs(os.path.join(db, 'Common'))
        os.makedirs(os.path.join(db, 'Bugakari'))
        with open(os.path.join(db, 'Common', 'ExpCDConvert.json'), 'w') as f:
            f.write('{}')
        parent = os.path.dirname(os.path.dirname(db))
        for root in (db, parent):  # DB直下でも1つ上でも辿れる
            r = service.derive_from_root(root)
            self.assertTrue(r['ok'], r['error'])
            self.assertTrue(r['expcd_path'].endswith('Common/ExpCDConvert.json'))
            self.assertTrue(r['bugakari_root'].endswith('Bugakari'))
            self.assertTrue(service.check_paths(r['expcd_path'], r['bugakari_root'])['ok'])

    def test_derive_errors(self):
        self.assertIsNotNone(service.derive_from_root('')['error'])
        self.assertIsNotNone(service.derive_from_root('X:/nope/nope')['error'])
        empty = tempfile.mkdtemp(prefix='empty_')
        r = service.derive_from_root(empty)
        self.assertFalse(r['ok'])
        self.assertIn('見つかりません', r['error'])

    def test_normalize_paths(self):
        db = tempfile.mkdtemp(prefix='norm_')
        os.makedirs(os.path.join(db, 'Common'))
        with open(os.path.join(db, 'Common', 'ExpCDConvert.json'), 'w') as f:
            f.write('{}')
        # フォルダ指定 → Common/ExpCDConvert.json を補う。引用符・空白も落とす
        e, b = service.normalize_setting_paths('  "%s"  ' % db, ' E:/x/Bugakari/ ')
        self.assertTrue(e.endswith('ExpCDConvert.json'))
        self.assertEqual(b, 'E:/x/Bugakari')

    def test_check_paths_ng(self):
        c = service.check_paths('X:/nope.json', 'X:/nope')
        self.assertFalse(c['ok'])
        self.assertFalse(c['expcd']['ok'])
        self.assertIn('NG', c['bugakari']['text'])


class TestErrors(unittest.TestCase):
    def test_gen_g_missing_json(self):
        r = service.gen_g('C:/nope/x.json')
        self.assertIsNotNone(r['error'])

    def test_gen_tc_missing_input(self):
        r = service.gen_tc('C:/nope/a.xlsx', 'C:/nope/b.xlsx', 'C:/nope/c.json')
        self.assertIsNotNone(r['error'])

    def test_locate_bad_key(self):
        r = service.locate_versions('abc')
        self.assertIsNotNone(r['error'])


@unittest.skipUnless(os.path.exists(EXPCD), '実ExpCD未配置')
class TestLocateReal(unittest.TestCase):
    def test_locate_bugakari_key(self):
        r = service.locate_versions('160-1')
        self.assertIsNone(r['error'])
        self.assertTrue(r['results'][0]['candidates'])


@unittest.skipUnless(os.path.exists(OLD_JSON), '06実データ未配置')
class TestGenReal(unittest.TestCase):
    def test_gen_g(self):
        r = service.gen_g(OLD_JSON)
        self.assertIsNone(r['error'], r['error'])
        self.assertTrue(os.path.exists(r['xlsx_path']))
        self.assertGreater(r['g_count'], 0)
        self.assertIn('商品_', os.path.basename(r['xlsx_path']))

    def test_gen_tc(self):
        if not (os.path.exists(CSV20) and os.path.exists(CSV30)):
            self.skipTest('06 G条件CSV未配置')
        r = service.gen_tc(CSV20, CSV30, OLD_JSON)
        self.assertIsNone(r['error'], r['error'])
        self.assertTrue(os.path.exists(r['xlsx_path']))
        self.assertGreater(r['tc_count'], 0)
        self.assertIn('テストケース_', os.path.basename(r['xlsx_path']))
        # 条件列が色付けされていること（TC自動判定）
        m, _ = io_xlsx.read_csv_matrix(r['csv_path'])
        self.assertTrue(io_xlsx.detect_tc_condition_cols(m))


if __name__ == '__main__':
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    unittest.main(verbosity=2)
