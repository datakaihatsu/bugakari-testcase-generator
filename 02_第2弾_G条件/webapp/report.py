# -*- coding: utf-8 -*-
"""
report.py ― 「問題・要望を送る」（報告パック）

利用者が「注が弾かれた」「こう出てほしかった」を、再現データ付きで開発者へ渡すための口
（2026-10-20 フィードバック会: 困ったケースを開発者に簡単に共有できない問題への対応）。

- 直近の実行（①/③/新規）の入力G条件・(注)の解釈・ログ・出力TCを zip 1個にまとめ、
  共有ドライブ（パソコン版Googleドライブ）の「報告箱」へ保存する。
- **歩掛JSONは同梱しない**（ファイル名だけ meta.json に残す。再現は G条件で足りる想定）。
- 共有ドライブに書けない端末では PC 内（%LOCALAPPDATA%/GaiaKoshuTC/報告_未送信）へ保存し、
  チャンネル添付を案内する（ここで失敗して報告自体を諦めさせないため）。
- 送信者は Windows のログイン名を自動で入れる（入力の手間を省く・2026-10-20 決定）。

meta.json は開発側（Claude の定期巡回）が一覧・台帳化に使う。キーを変えるときは巡回側も直す。
"""

import os
import re
import json
import getpass
import zipfile
import datetime

import version

# 既定の報告箱（配布先と同じフォルダの下）。config.json の report_dir で上書き可。
DEFAULT_REPORT_DIR = ('G:/共有ドライブ/Gaia開発部_データ開発課/社内共有(調整中)/99_その他/Tool/'
                      '5.外部設計/G条件とテストケース作成/報告箱')
_APP_DIRNAME = 'GaiaKoshuTC'

KINDS = ('問題', '要望', 'その他')
TAB_LABELS = {'g': '①G条件生成', 'tc': '③テストケース生成（改定）',
              'tcnew': '新規歩掛', 'general': '（実行データなし）'}
META_SCHEMA = 1


def report_dir(cfg):
    """報告箱のパス。テストは GAIA_TC_REPORT_DIR で一時領域へ逃がす。"""
    return (os.environ.get('GAIA_TC_REPORT_DIR') or (cfg or {}).get('report_dir')
            or DEFAULT_REPORT_DIR)


def fallback_dir():
    """共有ドライブに書けないときの PC 内の保存先。"""
    base = os.environ.get('GAIA_TC_SETTINGS_DIR') or os.environ.get('LOCALAPPDATA') \
        or os.path.join(os.path.expanduser('~'), '.' + _APP_DIRNAME)
    return os.path.join(base, _APP_DIRNAME, '報告_未送信')


def login_user():
    try:
        return os.environ.get('USERNAME') or getpass.getuser() or 'unknown'
    except Exception:  # noqa: BLE001 名前が取れなくても報告は止めない
        return 'unknown'


def _safe(s, limit=40):
    s = re.sub(r'[\\/:*?"<>|\s]+', '_', str(s or '')).strip('_.')
    return s[:limit]


def flagged_notes(run):
    """ダイアログでチェックさせる候補 = WARN/ERROR の (注) 解釈結果。"""
    return [l['text'] for l in (run or {}).get('note_lint') or []
            if l.get('level') in ('WARN', 'ERROR') and l.get('text')]


def pack_files(run):
    """zip に入れるファイル [(zip内の名前, 実パス)]。存在するものだけ。"""
    out = []
    for arc, path in (run or {}).get('files') or []:
        if path and os.path.isfile(path):
            out.append((arc, path))
    return out


def preview(run, cfg):
    """ダイアログ表示用: 送られる内容・送信者・保存先。"""
    d = report_dir(cfg)
    run = run or {}
    return {
        'user': login_user(),
        'report_dir': d,
        'reachable': os.path.isdir(os.path.dirname(d.rstrip('/\\'))) or os.path.isdir(d),
        'tab': run.get('tab') or 'general',
        'tab_label': TAB_LABELS.get(run.get('tab') or 'general'),
        'koshu': run.get('koshu') or '',
        'has_error': bool(run.get('error')),
        'files': [arc for arc, _ in pack_files(run)]
                 + (['注の解釈.json'] if run.get('note_lint') else [])
                 + (['実行ログ.txt'] if run.get('log') or run.get('error') else [])
                 + ['meta.json'],
        'notes': flagged_notes(run),
    }


def _write_zip(dst, run, meta):
    tmp = dst + '.part'
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('meta.json', json.dumps(meta, ensure_ascii=False, indent=2))
        if run.get('note_lint'):
            z.writestr('注の解釈.json', json.dumps(run['note_lint'], ensure_ascii=False, indent=2))
        log = run.get('log') or ''
        if run.get('error'):
            log = 'エラー: %s\n\n%s' % (run['error'], log)
        if log:
            z.writestr('実行ログ.txt', log)
        for arc, path in pack_files(run):
            z.write(path, arc)
    # 書きかけの zip を巡回側に拾わせないため、完成してから名前を付ける
    os.replace(tmp, dst)


def save_report(run, kind, comment, notes, cfg, now=None):
    """報告パックを保存する。
    戻り値: {error, path, shared(共有ドライブに保存できたか), fallback_reason}"""
    run = run or {'tab': 'general'}
    kind = kind if kind in KINDS else 'その他'
    now = now or datetime.datetime.now()
    user = login_user()
    koshu = run.get('koshu') or ''
    rid = '%s_%s_%s_%s' % (now.strftime('%Y%m%d_%H%M%S'), _safe(user, 20),
                          _safe(koshu) or _safe(TAB_LABELS.get(run.get('tab'), '')), kind)
    meta = {
        'schema': META_SCHEMA,
        'id': rid,
        'created_at': now.isoformat(timespec='seconds'),
        'user': user,
        'app_version': version.APP_VERSION,
        'version_label': version.VERSION_LABEL,
        'kind': kind,
        'comment': str(comment or '').strip(),
        'selected_notes': [str(n) for n in (notes or [])],
        'tab': run.get('tab') or 'general',
        'tab_label': TAB_LABELS.get(run.get('tab') or 'general'),
        'koshu': koshu,
        'json_name': run.get('json_name') or '',   # JSON本体は同梱しない（名前だけ）
        'error': run.get('error') or '',
        'tc_count': run.get('tc_count'),
        'files': [arc for arc, _ in pack_files(run)],
    }
    out = {'error': None, 'path': None, 'shared': False, 'fallback_reason': None}
    shared = report_dir(cfg)
    try:
        os.makedirs(shared, exist_ok=True)
        dst = os.path.join(shared, rid + '.zip')
        _write_zip(dst, run, meta)
        out.update({'path': dst, 'shared': True})
        return out
    except OSError as e:
        out['fallback_reason'] = '共有ドライブに保存できませんでした（%s）' % e
    try:
        local = fallback_dir()
        os.makedirs(local, exist_ok=True)
        dst = os.path.join(local, rid + '.zip')
        _write_zip(dst, run, meta)
        out['path'] = dst
    except OSError as e:
        out['error'] = '報告を保存できませんでした: %s' % e
    return out
