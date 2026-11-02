# -*- coding: utf-8 -*-
"""
test_progress.py ― 生成中の進捗表示（/api/progress）

背景 (2026-10-30 報告箱 前田さん 除雪グレーダ運転):
  「なかなかテストケース作成されないな～とおもって報告したら報告画面閉じてからできあがりました。
   エラーで止まっているのか、処理は動いているのか？がステータスで見えると嬉しい」
  ①は 49秒（到達走査 5,266回が 40秒）かかるが、画面は「生成中…」だけだった。

検証:
  - 実行中は get_progress が段階・経過秒・到達走査の回数を返す（実行ログの目印の行で段階が進む）
  - 実行が終われば running=False（例外で終わっても）
  - 進捗を指定しない _capture は従来どおり（ログも戻り値も同じ）
  - サーバの GET /api/progress が JSON を返す
"""

import json
import os
import sys
import threading
import unittest
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

import gjoken_reach  # noqa: E402
import service  # noqa: E402


class TestProgress(unittest.TestCase):
    def test_stages_and_walks_while_running(self):
        seen = {}

        def fake_gen():
            seen['start'] = service.get_progress()
            print('テスト計画生成完了 (新規工種モード): plan.csv')
            for n in range(1, 4):
                gjoken_reach.on_walk(n)
            seen['walking'] = service.get_progress()
            print('  [到達走査] 歩行 3 回 / 列 2 / 注 1')
            seen['writing'] = service.get_progress()
            return 'ok'

        res, log = service._capture(fake_gen, _tab='g')
        self.assertEqual(res, 'ok')
        self.assertIn('[到達走査] 歩行 3 回', log)
        self.assertEqual((seen['start']['running'], seen['start']['stage']), (True, 0))
        self.assertEqual(seen['start']['stages'], [s for s, _ in service.PROGRESS_STAGES['g']])
        self.assertEqual((seen['walking']['stage'], seen['walking']['walks']), (1, 3))
        self.assertEqual((seen['writing']['stage'], seen['writing']['walks']), (2, 0))
        self.assertEqual(service.get_progress(), {'running': False})
        self.assertIsNone(gjoken_reach.on_walk)

    def test_ends_on_exception(self):
        def boom():
            raise ValueError('x')
        with self.assertRaises(ValueError):
            service._capture(boom, _tab='tc')
        self.assertEqual(service.get_progress(), {'running': False})
        self.assertIsNone(gjoken_reach.on_walk)

    def test_without_tab_unchanged(self):
        def f(a, b=0):
            print('hello')
            return a + b
        self.assertEqual(service._capture(f, 1, b=2), (3, 'hello\n'))
        self.assertEqual(service.get_progress(), {'running': False})

    def test_server_route(self):
        import server
        httpd = server.make_server(port=0)
        th = threading.Thread(target=httpd.serve_forever, daemon=True)
        th.start()
        try:
            host, port = httpd.server_address
            with urllib.request.urlopen(f'http://{host}:{port}/api/progress') as r:
                self.assertEqual(json.loads(r.read().decode('utf-8')), {'running': False})
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
