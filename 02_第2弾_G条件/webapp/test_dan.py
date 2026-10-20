# -*- coding: utf-8 -*-
"""test_dan.py ― (注)の段表記とモード切替（2026-10-20 本体実装）

設計: 不具合と要求/20260929_フィードバックより/要求整理と設計_20260929.md（§6〜13）
      不具合と要求/20261020_フィードバックより/要求まとめ.md（モード切替）

  (M) モード: ①の出力形式だけに効く。既定=段なし。ユーザ設定ファイルに記憶し、
      格納場所の保存・「既定に戻す」でも消えない。モードだけの保存は「変更中」に数えない。
  (G) ①: 段なしは従来とバイト一致。段ありは A列「段n」＋通し番号。段の対象外の工種は段なしと同じ。
      xlsx は分岐点の選択肢を太字・A列の段見出しを薄青で塗る。
  (T) ③: 入力に段があるかを自動判別（モードに依存しない）。段なし入力は従来の経路のまま。
      無編集の往復（①段あり→③）でTCの行（条件の組合せ）が段なしと一致し、各段の確認観点が付く。
      太字の無い CSV でも分岐点を決められる。決められないときは利用者向けエラー。
  (U) 依頼者の実ファイル（トラッククレーン）で賃料ルートのTCが出る（実データが無ければ skip）。

実行: python3 02_第2弾_G条件/webapp/test_dan.py
"""
import contextlib
import glob
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
_BASE = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)
sys.path.insert(0, _BASE)
for _p in ('engine', 'step2_proposals', 'step3_csv'):
    sys.path.insert(0, os.path.join(_BASE, _p))

import service                      # noqa: E402  (sys.path を整える)
import io_xlsx                      # noqa: E402
import gen_gjoken                   # noqa: E402
import gen_tc_from_gjoken as T      # noqa: E402
import gjoken_dan_read as DR        # noqa: E402

_KOSHU = os.path.join(os.path.dirname(_BASE), '工種別')
_FB = os.path.join(_BASE, '不具合と要求', '20260929_フィードバックより')
CLIENT_XLSX = os.path.join(_FB, '商品_Gaia入力基準表_トラッククレーン(日)_適用20250401.xlsx')
_DB = 'C:/ProgramData/CoBeing/GaiaCloud/DB/Bugakari/32/016000/'
CLIENT_OLD = (glob.glob(_DB + '32-16644.20250401.*.json') or [''])[0]


def _target(prefix):
    import yaml
    tg = yaml.safe_load(open(os.path.join(_BASE, 'regression', 'targets.yaml'), encoding='utf-8'))
    for t in tg['targets']:
        if t['id'].startswith(prefix) and t.get('mode') == 'diff':
            kd = os.path.join(_KOSHU, t['id'], 'input')
            return os.path.join(kd, t['old']), os.path.join(kd, t['new'])
    return None, None


def _quiet(func, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return func(*a, **kw)


def _bytes(p):
    with open(p, 'rb') as f:
        return f.read()


def _dan_xlsx(json_path, wd):
    """①段ありの xlsx（画面と同じ書式付き）と、③が受け取る CSV（xlsx→csv）を作る。"""
    r = _quiet(gen_gjoken.build_g_ex, json_path, os.path.join(wd, 'g30d'), '', dan=True)
    x = os.path.join(wd, '30_段.xlsx')
    io_xlsx.csv_to_xlsx(r['csv'], x)
    io_xlsx.format_dan_sheet(x, r['dan'])
    c = os.path.join(wd, '30_段.csv')
    io_xlsx.xlsx_to_csv(x, c)
    return r, x, c


class _Settings(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='dan_setting_')
        self._old = os.environ.get('GAIA_TC_SETTINGS_DIR')
        os.environ['GAIA_TC_SETTINGS_DIR'] = self.dir

    def tearDown(self):
        if self._old is None:
            os.environ.pop('GAIA_TC_SETTINGS_DIR', None)
        else:
            os.environ['GAIA_TC_SETTINGS_DIR'] = self._old
        shutil.rmtree(self.dir, ignore_errors=True)


class TestMode(_Settings):
    """(M) モードの記憶。"""

    def test_既定は段なし(self):
        self.assertFalse(service.get_dan_mode())

    def test_記憶して読み戻せる(self):
        service.set_dan_mode(True)
        self.assertTrue(service.get_dan_mode())
        with open(service.user_settings_path(), encoding='utf-8') as f:
            self.assertEqual(json.load(f), {'gjoken_dan_mode': 'on'})
        service.set_dan_mode(False)
        self.assertFalse(service.get_dan_mode())

    def test_格納場所の保存と既定に戻すでモードが消えない(self):
        service.set_dan_mode(True)
        service.save_user_settings({'expcd_path': 'E:/x/ExpCDConvert.json',
                                    'bugakari_root': 'E:/x/Bugakari'})
        self.assertTrue(service.get_dan_mode())
        self.assertTrue(service.has_custom_paths())
        service.clear_user_settings()
        self.assertTrue(service.get_dan_mode())
        self.assertFalse(service.has_custom_paths())
        self.assertNotIn('expcd_path', service.load_user_settings())

    def test_モードだけなら変更中に数えない(self):
        service.set_dan_mode(True)
        self.assertFalse(service.settings_state()['is_custom'])

    def test_モードを段なしに戻し格納場所も既定ならファイルは空にならない(self):
        service.set_dan_mode(False)
        service.clear_user_settings()
        self.assertEqual(service.load_user_settings(), {'gjoken_dan_mode': 'off'})


@unittest.skipUnless(_target('21')[0] and os.path.exists(_target('21')[1]), '21実データ未配置')
class TestGjokenOutput(unittest.TestCase):
    """(G) ①の出力。"""

    @classmethod
    def setUpClass(cls):
        cls.wd = tempfile.mkdtemp(prefix='dan_g_')
        cls.old21, cls.new21 = _target('21')

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.wd, ignore_errors=True)

    def test_段なしは従来とバイト一致(self):
        a = _quiet(gen_gjoken.build_g, self.new21, os.path.join(self.wd, 'a'))
        b = _quiet(gen_gjoken.build_g_ex, self.new21, os.path.join(self.wd, 'b'), None, dan=False)
        self.assertEqual(_bytes(a), _bytes(b['csv']))
        self.assertIsNone(b['dan'])

    def test_段ありはA列に段nと通し番号(self):
        r = _quiet(gen_gjoken.build_g_ex, self.new21, os.path.join(self.wd, 'c'), None, dan=True)
        self.assertIsNotNone(r['dan'])
        self.assertEqual(r['dan']['count'], 2)
        self.assertEqual(r['dan']['branch_name'], '作業区分')
        rows = T._read_csv_any(r['csv'])
        labels = [x[0] for x in rows if x and x[0].startswith('段')]
        self.assertEqual(labels, ['段1', '段2'])
        # 分岐点の列: 段2の先頭は段1の続きの番号（通し番号）
        bc = r['dan']['branch_col']
        s1, s2 = r['dan']['segments']
        first1 = T._marker_index(rows[s1[0]][bc])
        first2 = T._marker_index(rows[s2[0]][bc])
        self.assertIsNotNone(first2)
        self.assertGreater(first2, first1)

    def test_段の対象外は段なしと同じ出力(self):
        old06, new06 = _target('06')
        if not new06 or not os.path.exists(new06):
            self.skipTest('06実データ未配置')
        a = _quiet(gen_gjoken.build_g, new06, os.path.join(self.wd, 'd'))
        b = _quiet(gen_gjoken.build_g_ex, new06, os.path.join(self.wd, 'e'), None, dan=True)
        self.assertIsNone(b['dan'])
        self.assertTrue(b['dan_reason'])
        self.assertEqual(_bytes(a), _bytes(b['csv']))

    def test_xlsxの書式(self):
        import openpyxl
        r, x, _c = _dan_xlsx(self.new21, os.path.join(self.wd, 'f'))
        ws = openpyxl.load_workbook(x).worksheets[0]
        for start, h in r['dan']['segments']:
            a = ws.cell(row=start + 1, column=1)
            self.assertTrue(a.value.startswith('段'))
            self.assertTrue(a.font.bold)
            self.assertEqual(a.fill.fgColor.rgb[-6:], io_xlsx.DAN_LABEL_FILL)
            self.assertEqual(a.border.top.style, 'medium')
            b = ws.cell(row=start + 1, column=r['dan']['branch_col'] + 1)
            self.assertTrue(b.font.bold)
            last = ws.cell(row=start + h, column=2)
            self.assertEqual(last.border.bottom.style, 'medium')

    def test_service_gen_gはモードに従う(self):
        d = tempfile.mkdtemp(prefix='dan_svc_')
        old = os.environ.get('GAIA_TC_SETTINGS_DIR')
        os.environ['GAIA_TC_SETTINGS_DIR'] = d
        try:
            r = service.gen_g(self.new21, out_dir=os.path.join(self.wd, 'g'))
            self.assertIsNone(r['error'], r['error'])
            self.assertFalse(r['dan_mode'])
            self.assertIsNone(r['dan'])
            service.set_dan_mode(True)
            r = service.gen_g(self.new21, out_dir=os.path.join(self.wd, 'h'))
            self.assertIsNone(r['error'], r['error'])
            self.assertTrue(r['dan_mode'])
            self.assertEqual(r['dan']['count'], 2)
            self.assertIn('段ありで出力', r['dan_message'])
        finally:
            if old is None:
                os.environ.pop('GAIA_TC_SETTINGS_DIR', None)
            else:
                os.environ['GAIA_TC_SETTINGS_DIR'] = old
            shutil.rmtree(d, ignore_errors=True)


def _cond_rows(path):
    """TCの条件部分（テストID・観点・期待以外）。"""
    rows = T._read_csv_any(path)
    hdr = rows[0]
    keep = [i for i, h in enumerate(hdr) if i > 1 and h not in
            ('代価表行と数量(数率)', '選択肢の適切さ確認', '規格名計上')]
    return [hdr[i] for i in keep], [tuple(r[i] for i in keep) for r in rows[1:]]


class TestTcRead(unittest.TestCase):
    """(T) ③の段の自動判別と往復。"""

    @classmethod
    def setUpClass(cls):
        cls.wd = tempfile.mkdtemp(prefix='dan_t_')

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.wd, ignore_errors=True)

    def _roundtrip(self, prefix):
        old, new = _target(prefix)
        if not old or not os.path.exists(new):
            self.skipTest('%s実データ未配置' % prefix)
        wd = os.path.join(self.wd, prefix)
        c20 = _quiet(gen_gjoken.build_g, old, os.path.join(wd, '20'))
        c30 = _quiet(gen_gjoken.build_g, new, os.path.join(wd, '30'))
        now = _quiet(T.run, c20, c30, old, os.path.join(wd, 'now'))
        _r, x, c = _dan_xlsx(new, wd)
        dan = _quiet(T.run, c20, c, old, os.path.join(wd, 'dan'), src30=x)
        return c20, c30, now, x, c, dan, old

    def test_段なし入力は判別しない(self):
        old, new = _target('21')
        if not old or not os.path.exists(new):
            self.skipTest('21実データ未配置')
        c30 = _quiet(gen_gjoken.build_g, new, os.path.join(self.wd, 'flat'))
        self.assertFalse(DR.has_dan(c30))
        x = os.path.join(self.wd, 'flat.xlsx')
        io_xlsx.csv_to_xlsx(c30, x)
        self.assertFalse(DR.has_dan(x))
        self.assertEqual(DR.prepare(c30, self.wd, '30', x), (c30, None))

    def test_往復_21側溝(self):
        _c20, _c30, now, x, _c, dan, _old = self._roundtrip('21')
        self.assertTrue(DR.has_dan(x))
        # 21 は段ごとに絞り込まれる選択肢も「－」も無いので、観点まで含めて完全一致
        self.assertEqual(T._read_csv_any(now), T._read_csv_any(dan))

    def test_往復_26目地工(self):
        _c20, _c30, now, _x, _c, dan, _old = self._roundtrip('26')
        self.assertEqual(_cond_rows(now), _cond_rows(dan))
        kan = '\n'.join(r[-2] for r in T._read_csv_any(dan)[1:])
        self.assertIn('は表示されないこと', kan)

    def test_太字の無いCSVでも同じTC(self):
        c20, _c30, _now, _x, c, dan, old = self._roundtrip('21')
        dan_csv = _quiet(T.run, c20, c, old, os.path.join(self.wd, '21', 'dancsv'))
        self.assertEqual(T._read_csv_any(dan), T._read_csv_any(dan_csv))

    def test_分岐点が決まらなければ利用者向けエラー(self):
        p = os.path.join(self.wd, 'amb.csv')
        from bugakari_json import BugakariJSON
        BugakariJSON.write_csv([
            ['施工区分/入力条件', 'G1', 'G2'],
            ['規格名計上', '', ''],
            ['各種(条件名)', '区分', '規格'],
            ['段1', '①甲', '①大'],
            ['', '②乙', '②小'],
            ['段2', '', '①大'],
            [], ['(注)'],
        ], p)
        self.assertTrue(DR.has_dan(p))
        with self.assertRaises(T.GjokenApplyError) as cm:
            DR.parse_dan(p)
        self.assertIn('太字', str(cm.exception))

    def test_新規歩掛モードでも段を読む(self):
        old, new = _target('21')
        if not old or not os.path.exists(new):
            self.skipTest('21実データ未配置')
        wd = os.path.join(self.wd, 'single')
        _r, x, c = _dan_xlsx(new, wd)
        s3 = _quiet(T.run_single, c, os.path.join(wd, 'out'), '側溝', src30=x)
        rows = T._read_csv_any(s3)
        kan = '\n'.join(r[-2] for r in rows[1:])
        self.assertIn('段2（作業区分=', kan)   # 新規は分岐点が新しい扱い＝各段に最低1行


@unittest.skipUnless(os.path.exists(CLIENT_XLSX) and CLIENT_OLD, '依頼者の実ファイル/JSON未配置')
class TestClientFile(unittest.TestCase):
    """(U) 依頼者の実ファイル（見出し行の繰り返し・段ごとの振り直し・G番号の欠け・メモ列）。"""

    def test_賃料ルートのTCが5件(self):
        wd = tempfile.mkdtemp(prefix='dan_u_')
        try:
            c20 = _quiet(gen_gjoken.build_g, CLIENT_OLD, os.path.join(wd, '20'))
            c30 = os.path.join(wd, '30.csv')
            io_xlsx.xlsx_to_csv(CLIENT_XLSX, c30)
            tc = _quiet(T.run, c20, c30, CLIENT_OLD, os.path.join(wd, 'tc'), src30=CLIENT_XLSX)
            hdr, rows = _cond_rows(tc)
            b, u, k = (hdr.index('損料･賃料区分'), hdr.index('運転単価表番号'),
                       hdr.index('規格の選択'))
            got = sorted((r[b], r[u], r[k]) for r in rows)
            self.assertEqual(got, sorted([
                ('損料', '機-3', '油圧伸縮ジブ型・4.9t吊'),
                ('賃料', '機-6', '最大吊上能力120t吊'),
                ('賃料', '機-8', '最大吊上能力120t吊'),
                ('賃料', '機-6', '最大吊上能力160t吊'),
                ('賃料', '機-6', '最大吊上能力200t吊'),
            ]))
        finally:
            shutil.rmtree(wd, ignore_errors=True)


class TestServerDanMode(_Settings):
    """(M) 画面の切替 API: 保存すると /api/config に出る。"""

    def test_api(self):
        import server
        httpd = server.make_server(port=0)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = 'http://127.0.0.1:%d' % httpd.server_address[1]
        try:
            def req(path, data=None):
                body = json.dumps(data).encode('utf-8') if data is not None else None
                r = urllib.request.Request(base + path, data=body, headers={
                    'Content-Type': 'application/json'} if body else {})
                with urllib.request.urlopen(r) as resp:
                    return json.loads(resp.read())
            self.assertFalse(req('/api/config')['gjoken_dan_mode'])
            r = req('/api/dan_mode', {'on': True})
            self.assertIsNone(r['error'])
            self.assertTrue(r['gjoken_dan_mode'])
            c = req('/api/config')
            self.assertTrue(c['gjoken_dan_mode'])
            self.assertFalse(c['is_custom'])
        finally:
            httpd.shutdown()


if __name__ == '__main__':
    unittest.main(verbosity=2)
