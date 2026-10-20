# -*- coding: utf-8 -*-
"""
test_report.py ― 「問題・要望を送る」（報告パック）のテスト

検証:
  - 直近の実行データ（入力G条件・注の解釈・ログ・出力）が zip に入り、meta.json が揃う
  - 歩掛JSONは同梱しない（ファイル名だけ meta に残る）
  - 共有ドライブに書けないときは PC 内へ保存し、shared=False と理由を返す
  - 実行データなし（ヘッダーのボタン）でも送れる
  - HTTP: ③実行 → /api/report_info に弾かれた注候補 → /api/report で報告箱に zip
保存先は GAIA_TC_REPORT_DIR / GAIA_TC_SETTINGS_DIR で一時領域へ逃がす（実環境を汚さない）。
"""

import os
import json
import base64
import shutil
import zipfile
import tempfile
import threading
import unittest
import urllib.request

import report
import server
import version

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '運用案件', '06_土のう積工'))
OLD_JSON = os.path.join(BASE, '10_改定前', '32-735.20240401.20240401.json')
CSV30 = os.path.join(BASE, '30_人作成G条件', 'Gaia入力基準表_土のう積工(m2)_人作成.csv')


class _EnvMixin:
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='report_')
        self.box = os.path.join(self.tmp, '報告箱')
        self._old = {k: os.environ.get(k) for k in ('GAIA_TC_REPORT_DIR', 'GAIA_TC_SETTINGS_DIR')}
        os.environ['GAIA_TC_REPORT_DIR'] = self.box
        os.environ['GAIA_TC_SETTINGS_DIR'] = os.path.join(self.tmp, 'local')

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestSaveReport(_EnvMixin, unittest.TestCase):
    def _run(self):
        g30 = os.path.join(self.tmp, '改修後.xlsx')
        out = os.path.join(self.tmp, 'テストケース_土のう積工.xlsx')
        for p in (g30, out):
            with open(p, 'wb') as f:
                f.write(b'PK-dummy')
        return {'tab': 'tc', 'koshu': '土のう積工(m2)', 'json_name': '32-735.20240401.20240401.json',
                'files': [('入力/枠B_改修後.xlsx', g30), ('出力/テストケース_土のう積工.xlsx', out),
                          ('入力/消えた.xlsx', os.path.join(self.tmp, 'nope.xlsx'))],
                'note_lint': [{'level': 'ERROR', 'text': '(注) 3件のうち 2件を反映'},
                              {'level': 'WARN', 'text': '(注)G3で固定する… は無視されます'},
                              {'level': 'INFO', 'text': '情報'}],
                'log': 'ログ本文', 'error': '', 'tc_count': 5}

    def test_zip_contents_and_meta(self):
        run = self._run()
        r = report.save_report(run, '問題', ' 固定の意味です ', ['(注)G3で固定する… は無視されます'], {})
        self.assertIsNone(r['error'])
        self.assertTrue(r['shared'])
        self.assertTrue(r['path'].startswith(self.box))
        self.assertFalse(os.path.exists(r['path'] + '.part'))
        with zipfile.ZipFile(r['path']) as z:
            names = set(z.namelist())
            meta = json.loads(z.read('meta.json').decode('utf-8'))
        self.assertEqual(names, {'meta.json', '注の解釈.json', '実行ログ.txt',
                                 '入力/枠B_改修後.xlsx', '出力/テストケース_土のう積工.xlsx'})
        self.assertFalse(any(n.endswith('.json') and n.startswith('入力') for n in names))
        self.assertEqual(meta['kind'], '問題')
        self.assertEqual(meta['comment'], '固定の意味です')
        self.assertEqual(meta['selected_notes'], ['(注)G3で固定する… は無視されます'])
        self.assertEqual(meta['json_name'], '32-735.20240401.20240401.json')
        self.assertEqual(meta['app_version'], version.APP_VERSION)
        self.assertEqual(meta['user'], report.login_user())
        self.assertIn('土のう積工', os.path.basename(r['path']))

    def test_preview_lists_flagged_notes(self):
        p = report.preview(self._run(), {})
        self.assertEqual(len(p['notes']), 2)  # WARN/ERROR だけ
        self.assertIn('入力/枠B_改修後.xlsx', p['files'])
        self.assertNotIn('入力/消えた.xlsx', p['files'])
        self.assertIn('meta.json', p['files'])

    def test_fallback_to_local_when_shared_unwritable(self):
        # 報告箱の場所にファイルを置いて makedirs を失敗させる（共有ドライブ未接続の代わり）
        os.makedirs(self.tmp, exist_ok=True)
        with open(self.box, 'w') as f:
            f.write('x')
        r = report.save_report(self._run(), '要望', '', [], {})
        self.assertIsNone(r['error'])
        self.assertFalse(r['shared'])
        self.assertTrue(r['fallback_reason'])
        self.assertTrue(r['path'].startswith(report.fallback_dir()))
        self.assertTrue(zipfile.is_zipfile(r['path']))

    def test_general_without_run(self):
        r = report.save_report(None, '要望', '見た目をポップに', [], {})
        self.assertTrue(r['shared'])
        with zipfile.ZipFile(r['path']) as z:
            self.assertEqual(z.namelist(), ['meta.json'])
            meta = json.loads(z.read('meta.json').decode('utf-8'))
        self.assertEqual(meta['tab'], 'general')
        self.assertEqual(meta['comment'], '見た目をポップに')

    def test_unknown_kind_becomes_other(self):
        r = report.save_report(None, '<script>', '', [], {})
        self.assertTrue(r['path'].endswith('_その他.zip'))


def _req(url, data=None):
    if data is not None:
        data = json.dumps(data).encode('utf-8')
    r = urllib.request.Request(url, data=data,
                               headers={'Content-Type': 'application/json'} if data else {})
    with urllib.request.urlopen(r) as resp:
        return json.loads(resp.read())


@unittest.skipUnless(os.path.exists(OLD_JSON) and os.path.exists(CSV30), '06実データ未配置')
class TestReportHTTP(_EnvMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = server.make_server(port=0)
        cls.t = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.t.start()
        cls.base = 'http://127.0.0.1:%d' % cls.httpd.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def test_gen_tc_then_report(self):
        _req(self.base + '/api/gen_g', {'json_path': OLD_JSON})
        with open(CSV30, 'rb') as f:
            g30_b64 = base64.b64encode(f.read()).decode('ascii')
        r = _req(self.base + '/api/gen_tc',
                 {'use_session_g20': True, 'g30_b64': g30_b64, 'g30_name': '30.csv'})
        self.assertIsNone(r.get('error'), r.get('error'))

        info = _req(self.base + '/api/report_info?tab=tc')
        self.assertEqual(info['tab'], 'tc')
        self.assertIn('入力/枠B_30.csv', info['files'])

        res = _req(self.base + '/api/report', {'tab': 'tc', 'kind': '問題', 'comment': 'テスト',
                                               'notes': []})
        self.assertIsNone(res['error'], res['error'])
        self.assertTrue(res['shared'])
        with zipfile.ZipFile(res['path']) as z:
            names = z.namelist()
            meta = json.loads(z.read('meta.json').decode('utf-8'))
        self.assertIn('入力/枠B_30.csv', names)
        self.assertTrue(any(n.startswith('入力/枠A_') for n in names))
        self.assertTrue(any(n.startswith('出力/テストケース_') for n in names))
        self.assertFalse(any(n.endswith('.json') and n not in ('meta.json', '注の解釈.json')
                             for n in names))  # 歩掛JSONは入らない
        self.assertEqual(meta['json_name'], os.path.basename(OLD_JSON))
        # JSON本体の代わりに指紋（sha256・サイズ）で「同じファイルか」を突き合わせられる
        self.assertEqual(meta['json_sha256'], report.json_fingerprint(OLD_JSON)['json_sha256'])
        self.assertEqual(meta['json_size'], os.path.getsize(OLD_JSON))
        self.assertGreater(meta['tc_count'], 0)

        # ヘッダーのボタン(latest)は、タブを問わず直近の実行（=この③）を添付候補にする
        latest = _req(self.base + '/api/report_info?tab=latest')
        self.assertTrue(latest['has_run'])
        self.assertEqual(latest['tab'], 'tc')
        res = _req(self.base + '/api/report', {'tab': 'latest', 'kind': '要望', 'comment': ''})
        with zipfile.ZipFile(res['path']) as z:
            self.assertIn('入力/枠B_30.csv', z.namelist())

    def test_header_report_without_run(self):
        info = _req(self.base + '/api/report_info?tab=general')
        self.assertEqual(info['files'], ['meta.json'])
        self.assertFalse(info['has_run'])
        res = _req(self.base + '/api/report', {'tab': 'general', 'kind': '要望', 'comment': 'x'})
        self.assertTrue(res['shared'])


class TestFingerprint(unittest.TestCase):
    def test_missing_or_empty_path(self):
        self.assertEqual(report.json_fingerprint('')['json_name'], '')
        fp = report.json_fingerprint('X:/存在しない.json')
        self.assertEqual(fp['json_name'], '存在しない.json')
        self.assertEqual(fp['json_sha256'], '')


if __name__ == '__main__':
    unittest.main(verbosity=2)
