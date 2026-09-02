# -*- coding: utf-8 -*-
"""
server.py ― ローカルWebサーバ（UI層 / Python標準ライブラリのみ）

http.server ベース。127.0.0.1 バインドの単一ユーザ用ローカルアプリ。
①(G条件生成) と ③(TC生成) をタブ2枚で提供する。

依存: 標準ライブラリのみ（http.server/json/base64/secrets 等）。
      ※ファイルアップロードは cgi 非依存の base64 JSON 方式（Python3.13+ で cgi 削除のため）。
      xlsx 変換は service→io_xlsx（openpyxl）に閉じる。

エンドポイント:
  GET  /                     index.html
  GET  /static/<file>        静的資産（static/ 配下のみ・パストラバーサル防止）
  GET  /api/config           バージョン・格納場所パス等（表示用）
  GET  /api/download?token=  生成物(xlsx)をトークンでDL（任意パス公開はしない）
  POST /api/locate           {input} → 歩掛キー×版候補
  POST /api/gen_g            {json_path} → G条件生成（商品_xlsx）。セッションに引継情報保存
  POST /api/gen_tc           {g30_b64,g30_name, use_session_g20|g20_b64} → TC生成（テストケース_xlsx）
  GET  /api/settings         現在の格納場所・出所（既定/ユーザ設定）・実在チェック
  POST /api/settings         {action: derive|check|save|reset} 格納場所の変更（画面の「変更」）

設計書: 運用化設計書_2026-07-07.md §9。
"""

import os
import sys
import json
import base64
import secrets
import tempfile
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import service
import version

STATIC_DIR = os.path.join(_HERE, 'static')
CFG = service.load_config()

# ダウンロード用トークン → (path, download_name)。任意パス公開を避けるため必ずトークン経由。
_DOWNLOADS = {}
# ①の結果を③へ引き継ぐための単一ユーザ用セッション（ローカルアプリ前提）。
_SESSION = {'g20_csv': None, 'old_json': None, 'koshu': None}

_CT_XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
_CT_BY_EXT = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
              '.css': 'text/css; charset=utf-8'}


def _reload_cfg():
    """設定変更後に CFG を作り直す。ハンドラは CFG を参照するので **中身を入れ替える**
    （再代入すると既存の参照が古い辞書のままになるため）。"""
    fresh = service.load_config()
    CFG.clear()
    CFG.update(fresh)
    return CFG


def _register_download(path):
    token = secrets.token_hex(8)
    _DOWNLOADS[token] = (path, os.path.basename(path))
    return token


class Handler(BaseHTTPRequestHandler):
    server_version = 'GaiaTC/' + version.APP_VERSION

    def log_message(self, *args):
        pass  # 静音（必要時はここでロギング）

    # -- helpers ---------------------------------------------------------
    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        n = int(self.headers.get('Content-Length', 0) or 0)
        if n <= 0:
            return {}
        return json.loads(self.rfile.read(n).decode('utf-8'))

    def _send_bytes(self, data, content_type, code=200, extra_headers=None):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _serve_static(self, rel):
        # basename のみ許可（ディレクトリ横断防止）
        safe = os.path.basename(rel)
        path = os.path.join(STATIC_DIR, safe)
        if not os.path.isfile(path):
            self._send_json({'error': 'not found'}, 404)
            return
        ext = os.path.splitext(safe)[1].lower()
        with open(path, 'rb') as f:
            data = f.read()
        self._send_bytes(data, _CT_BY_EXT.get(ext, 'application/octet-stream'))

    def _serve_download(self, query):
        token = urllib.parse.parse_qs(query).get('token', [''])[0]
        item = _DOWNLOADS.get(token)
        if not item or not os.path.isfile(item[0]):
            self._send_json({'error': 'ダウンロード対象が見つかりません'}, 404)
            return
        path, name = item
        with open(path, 'rb') as f:
            data = f.read()
        # 日本語ファイル名は RFC5987 (filename*) で。ascii fallback も付ける。
        quoted = urllib.parse.quote(name)
        cd = "attachment; filename=\"file.xlsx\"; filename*=UTF-8''%s" % quoted
        self._send_bytes(data, _CT_XLSX, extra_headers={'Content-Disposition': cd})

    # -- routes ----------------------------------------------------------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        if route == '/' or route == '/index.html':
            self._serve_static('index.html')
        elif route.startswith('/static/'):
            self._serve_static(route[len('/static/'):])
        elif route == '/api/config':
            self._send_json({
                'app_version': version.APP_VERSION,
                'build_date': version.BUILD_DATE,
                'version_label': version.VERSION_LABEL,
                'expcd_path': CFG['expcd_path'],
                'bugakari_root': CFG['bugakari_root'],
                'is_custom': bool(service.load_user_settings()),
                'session': {'has_handoff': bool(_SESSION['g20_csv'] and _SESSION['old_json']),
                            'koshu': _SESSION['koshu']},
            })
        elif route == '/api/settings':
            self._send_json(service.settings_state(CFG))
        elif route == '/api/download':
            self._serve_download(parsed.query)
        else:
            self._send_json({'error': 'not found'}, 404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        try:
            if route == '/api/locate':
                self._api_locate()
            elif route == '/api/gen_g':
                self._api_gen_g()
            elif route == '/api/gen_tc':
                self._api_gen_tc()
            elif route == '/api/gen_tc_new':
                self._api_gen_tc_new()
            elif route == '/api/settings':
                self._api_settings()
            else:
                self._send_json({'error': 'not found'}, 404)
        except Exception as e:  # noqa: BLE001 UIへ返す
            import traceback
            self._send_json({'error': '%s: %s' % (type(e).__name__, e),
                             'trace': traceback.format_exc()}, 500)

    def _api_settings(self):
        """格納場所の変更（GaiaCloudデータを外付けSSD等へ移した端末向け・2026-09-02 要望）。
        action: derive=フォルダ1つから2パスを推定 / check=実在確認のみ /
                save=ユーザ設定へ保存して即時反映 / reset=配布既定へ戻す。
        保存先は配布フォルダの外（%LOCALAPPDATA%）なので、ツール更新で消えない。"""
        body = self._read_json()
        action = body.get('action') or 'check'

        if action == 'derive':
            d = service.derive_from_root(body.get('root'))
            if d['ok']:
                d['check'] = service.check_paths(d['expcd_path'], d['bugakari_root'])
            self._send_json(d)
            return

        if action == 'reset':
            try:
                service.clear_user_settings()
            except OSError as e:
                self._send_json({'error': '設定の削除に失敗しました: %s' % e})
                return
            st = service.settings_state(_reload_cfg())
            st.update({'error': None, 'saved': True,
                       'message': '配布時の既定の格納場所に戻しました'})
            self._send_json(st)
            return

        expcd, bug = service.normalize_setting_paths(
            body.get('expcd_path'), body.get('bugakari_root'))
        check = service.check_paths(expcd, bug)

        if action == 'check':
            self._send_json({'error': None, 'saved': False, 'expcd_path': expcd,
                             'bugakari_root': bug, 'check': check})
            return

        if action != 'save':
            self._send_json({'error': '不明な操作です: %s' % action})
            return

        if not expcd or not bug:
            self._send_json({'error': '2つのパスを両方入力してください',
                             'expcd_path': expcd, 'bugakari_root': bug, 'check': check})
            return
        # 既定は「確認できないパスは保存させない」。ただし外付けSSD未接続のまま
        # 設定だけ先に入れたいケースがあるので force で通せるようにする。
        if not check['ok'] and not body.get('force'):
            self._send_json({'error': '指定の場所を確認できませんでした（下の確認結果を参照）',
                             'expcd_path': expcd, 'bugakari_root': bug, 'check': check,
                             'can_force': True})
            return
        try:
            service.save_user_settings({'expcd_path': expcd, 'bugakari_root': bug})
        except OSError as e:
            self._send_json({'error': '設定の保存に失敗しました: %s' % e})
            return
        st = service.settings_state(_reload_cfg())
        msg = '格納場所を変更しました'
        if not check['ok']:
            msg += '（※この場所は今は確認できていません。ドライブを接続してからご使用ください）'
        st.update({'error': None, 'saved': True, 'message': msg})
        self._send_json(st)

    def _api_locate(self):
        body = self._read_json()
        text = (body.get('input') or '').strip()
        if not text:
            self._send_json({'error': 'キーを入力してください'})
            return
        self._send_json(service.locate_versions(text, CFG))

    def _api_gen_g(self):
        body = self._read_json()
        json_path = body.get('json_path') or ''
        r = service.gen_g(json_path, CFG)
        if r['error']:
            self._send_json(r)
            return
        # ③引継ぎ用にセッション保存 + DLトークン発行
        _SESSION['g20_csv'] = r['csv_path']
        _SESSION['old_json'] = json_path
        _SESSION['koshu'] = r.get('workdir') and os.path.basename(
            r['xlsx_path']).replace('商品_', '').replace('.xlsx', '')
        r['download_token'] = _register_download(r['xlsx_path'])
        r['download_name'] = os.path.basename(r['xlsx_path'])
        self._send_json(r)

    def _decode_upload(self, b64, name, workdir, fallback):
        """base64アップロード → workdir内ファイルに保存し、パスを返す。"""
        raw = base64.b64decode(b64)
        safe = os.path.basename(name or fallback)
        if not safe.lower().endswith(('.xlsx', '.csv')):
            safe += '.xlsx'
        path = os.path.join(workdir, safe)
        with open(path, 'wb') as f:
            f.write(raw)
        return path

    def _api_gen_tc(self):
        body = self._read_json()
        wd = service.new_workdir(CFG, 'upload_')
        # g30（改修後）は必須アップロード
        if not body.get('g30_b64'):
            self._send_json({'error': '改修後G条件(枠B)を指定してください'})
            return
        g30 = self._decode_upload(body['g30_b64'], body.get('g30_name'), wd, '30改修後.xlsx')
        # g20（商品）と old_json: ①引継ぎ or アップロード
        if body.get('use_session_g20'):
            g20 = _SESSION.get('g20_csv')
            old_json = _SESSION.get('old_json')
            if not g20 or not old_json:
                self._send_json({'error': '引継ぎ情報がありません。先に①を実行してください'})
                return
        else:
            if not body.get('g20_b64'):
                self._send_json({'error': '商品G条件(枠A)を指定するか、①の結果を引き継いでください'})
                return
            g20 = self._decode_upload(body['g20_b64'], body.get('g20_name'), wd, '20商品.xlsx')
            old_json = _SESSION.get('old_json')
            if not old_json:
                self._send_json({'error': '改定前JSONが特定できません。先に①を実行してください'})
                return
        r = service.gen_tc(g20, g30, old_json, CFG)
        if not r['error']:
            r['download_token'] = _register_download(r['xlsx_path'])
            r['download_name'] = os.path.basename(r['xlsx_path'])
        self._send_json(r)

    def _api_gen_tc_new(self):
        """新規歩掛: 改修後G条件(枠C)1枚だけからTC生成（改定前JSON/商品G条件なし）。"""
        body = self._read_json()
        if not body.get('g30_b64'):
            self._send_json({'error': '改修後G条件(枠C)を指定してください'})
            return
        wd = service.new_workdir(CFG, 'uploadnew_')
        g30 = self._decode_upload(body['g30_b64'], body.get('g30_name'), wd, '30新規歩掛.xlsx')
        r = service.gen_tc_new(g30, CFG)
        if not r['error']:
            r['download_token'] = _register_download(r['xlsx_path'])
            r['download_name'] = os.path.basename(r['xlsx_path'])
        self._send_json(r)


def make_server(port=None):
    # port=0 は「OSが空きポートを割当」の意味。None のときのみ既定にフォールバック。
    if port is None:
        port = CFG.get('port', 8765)
    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def main(open_browser=True):
    httpd = make_server()
    host, port = httpd.server_address
    url = 'http://127.0.0.1:%d/' % port
    print('Gaia歩掛TCツール', version.VERSION_LABEL, '起動:', url, ' (Ctrl+Cで終了)')
    print('  ExpCD    :', CFG['expcd_path'])
    print('  Bugakari :', CFG['bugakari_root'])
    if service.load_user_settings():
        # 既定と違う場所を見ている端末は、問い合わせ時にここを見れば一発で分かる
        print('  ※ 格納場所は画面の設定で変更されています:', service.user_settings_path())
    # ソケットは make_server の時点で bind+listen 済み → ここで開けば接続拒否にならない
    # （serve_forever 前の接続も listen バックログに積まれる）。
    if open_browser and '--no-browser' not in sys.argv:
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 ブラウザ起動失敗は致命ではない
            print('  ブラウザ自動起動に失敗。手動で上記URLを開いてください。')
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\n終了します。')
        httpd.shutdown()


if __name__ == '__main__':
    main()
