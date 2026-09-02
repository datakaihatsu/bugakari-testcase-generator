# -*- coding: utf-8 -*-
"""
test_server.py ― server.py のHTTP結合スモークテスト（標準ライブラリのみ）

エフェメラルポートでサーバをスレッド起動し、HTTPで①→③フローを駆動する。
検証:
  - GET /            index.html が返る
  - GET /api/config  バージョン・パス・セッション状態
  - GET /static/app.js 静的資産
  - POST /api/gen_g   実JSON(06改定前)→G条件生成・DLトークン→DL可能
  - POST /api/gen_tc  引継ぎ(session)＋改修後(=06人作成CSVをbase64)→TC生成・DL可能
  - GET /api/download 生成xlsxがxlsxバイト(PK..)で返る
  - GET/POST /api/settings 格納場所の変更（推定→保存→反映→既定に戻す）
    ※保存先は GAIA_TC_SETTINGS_DIR で一時領域へ逃がし、実環境の設定を汚さない
実データ(06/ExpCD)が無ければ該当テストを skip。
"""

import os
import json
import base64
import shutil
import tempfile
import threading
import unittest
import urllib.request

import server
import service
import version

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '運用案件', '06_土のう積工'))
OLD_JSON = os.path.join(BASE, '10_改定前', '32-735.20240401.20240401.json')
CSV30 = os.path.join(BASE, '30_人作成G条件', 'Gaia入力基準表_土のう積工(m2)_人作成.csv')


def _req(url, data=None):
    if data is not None:
        data = json.dumps(data).encode('utf-8')
    r = urllib.request.Request(url, data=data,
                               headers={'Content-Type': 'application/json'} if data else {})
    with urllib.request.urlopen(r) as resp:
        return resp.status, resp.read(), dict(resp.headers)


@unittest.skipUnless(os.path.exists(OLD_JSON), '06実データ未配置')
class TestServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = server.make_server(port=0)  # エフェメラルポート
        cls.port = cls.httpd.server_address[1]
        cls.t = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.t.start()
        cls.base = 'http://127.0.0.1:%d' % cls.port

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def test_1_index_and_static(self):
        st, body, _ = _req(self.base + '/')
        self.assertEqual(st, 200)
        self.assertIn(b'<title>', body)
        st, body, _ = _req(self.base + '/static/app.js')
        self.assertEqual(st, 200)
        self.assertIn(b'postJSON', body)

    def test_2_config(self):
        st, body, _ = _req(self.base + '/api/config')
        c = json.loads(body)
        self.assertIn('expcd_path', c)
        self.assertIn('session', c)
        # 画面ヘッダー右上のバージョン表示（受け渡し先との版の突き合わせに使う）
        self.assertEqual(c.get('app_version'), version.APP_VERSION)
        self.assertEqual(c.get('version_label'), version.VERSION_LABEL)

    def test_3_gen_g_then_download(self):
        st, body, _ = _req(self.base + '/api/gen_g', {'json_path': OLD_JSON})
        r = json.loads(body)
        self.assertIsNone(r.get('error'), r.get('error'))
        self.assertGreater(r['g_count'], 0)
        self.assertTrue(r['download_token'])
        # DL
        st, data, hdr = _req(self.base + '/api/download?token=' + r['download_token'])
        self.assertEqual(st, 200)
        self.assertTrue(data[:2] == b'PK')  # xlsx = zip
        self.assertIn('attachment', hdr.get('Content-Disposition', ''))
        # セッションに引継ぎが立つ
        _, cbody, _ = _req(self.base + '/api/config')
        self.assertTrue(json.loads(cbody)['session']['has_handoff'])

    def test_4_gen_tc_with_handoff(self):
        if not os.path.exists(CSV30):
            self.skipTest('06人作成G条件CSV未配置')
        # ①を先に実行してセッションを立てる
        _req(self.base + '/api/gen_g', {'json_path': OLD_JSON})
        with open(CSV30, 'rb') as f:
            g30_b64 = base64.b64encode(f.read()).decode('ascii')
        st, body, _ = _req(self.base + '/api/gen_tc',
                           {'use_session_g20': True, 'g30_b64': g30_b64, 'g30_name': '30.csv'})
        r = json.loads(body)
        self.assertIsNone(r.get('error'), r.get('error'))
        self.assertGreater(r['tc_count'], 0)
        st, data, _ = _req(self.base + '/api/download?token=' + r['download_token'])
        self.assertEqual(st, 200)
        self.assertTrue(data[:2] == b'PK')

    def test_5_gen_tc_missing_g30(self):
        st, body, _ = _req(self.base + '/api/gen_tc', {'use_session_g20': True})
        self.assertIsNotNone(json.loads(body).get('error'))

    def test_6_settings_save_and_reset(self):
        """格納場所の変更（外付けSSD運用対応）。保存で CFG が即差し替わり、reset で戻る。"""
        tmpdir = tempfile.mkdtemp(prefix='settings_')
        db = os.path.join(tmpdir, 'GaiaCloud', 'DB')
        os.makedirs(os.path.join(db, 'Common'))
        os.makedirs(os.path.join(db, 'Bugakari'))
        with open(os.path.join(db, 'Common', 'ExpCDConvert.json'), 'w') as f:
            f.write('{"ExpCDConvertList": []}')
        old_env = os.environ.get('GAIA_TC_SETTINGS_DIR')
        os.environ['GAIA_TC_SETTINGS_DIR'] = os.path.join(tmpdir, 'settings')
        default_expcd = service.load_config(use_user_settings=False)['expcd_path']
        try:
            # 現状 = 既定
            _, body, _ = _req(self.base + '/api/settings')
            self.assertFalse(json.loads(body)['is_custom'])
            # フォルダ1つから推定
            _, body, _ = _req(self.base + '/api/settings',
                              {'action': 'derive', 'root': tmpdir})
            d = json.loads(body)
            self.assertTrue(d['ok'], d.get('error'))
            self.assertTrue(d['check']['ok'])
            # 存在しないパスは保存させない（force なし）
            _, body, _ = _req(self.base + '/api/settings',
                              {'action': 'save', 'expcd_path': 'X:/nope.json',
                               'bugakari_root': 'X:/nope'})
            r = json.loads(body)
            self.assertIsNotNone(r['error'])
            self.assertTrue(r['can_force'])
            # 保存 → CFG に即反映（/api/config の表示・locate の探索先が変わる）
            _, body, _ = _req(self.base + '/api/settings',
                              {'action': 'save', 'expcd_path': d['expcd_path'],
                               'bugakari_root': d['bugakari_root']})
            r = json.loads(body)
            self.assertIsNone(r['error'], r['error'])
            self.assertTrue(r['is_custom'])
            _, cbody, _ = _req(self.base + '/api/config')
            c = json.loads(cbody)
            self.assertEqual(c['bugakari_root'], d['bugakari_root'])
            self.assertTrue(c['is_custom'])
            # 既定に戻す
            _, body, _ = _req(self.base + '/api/settings', {'action': 'reset'})
            r = json.loads(body)
            self.assertFalse(r['is_custom'])
            _, cbody, _ = _req(self.base + '/api/config')
            self.assertEqual(json.loads(cbody)['expcd_path'], default_expcd)
        finally:
            if old_env is None:
                os.environ.pop('GAIA_TC_SETTINGS_DIR', None)
            else:
                os.environ['GAIA_TC_SETTINGS_DIR'] = old_env
            server._reload_cfg()
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
