# -*- coding: utf-8 -*-
"""
service.py ― サービス層（UIとロジックの糊）

UI(server.py)から呼びやすい形にロジック層を束ねる。ロジック本体は書き換えない:
  - locate.resolve           : ①キー→歩掛キー×版候補
  - gen_gjoken.build_g        : ①JSON→G条件CSV
  - gen_tc_from_gjoken.run     : ③G条件→改定後TC CSV
  - io_xlsx                    : 境界(CSV⇔xlsx・条件列色付け)

責務:
  - config.json 読込（格納場所パス等）
  - 一時ディレクトリ管理
  - build_g / run の print を捕捉してログ化（本体を汚さない）
  - 例外を UI 表示用に整形（error 文字列＋tracebackはlogへ）

設計書: 運用化設計書_2026-07-07.md §6/§7。純粋にファイルパス指向（テスト容易）。
バイト授受は server.py が担当（ここはパスで完結）。
"""

import os
import io
import sys
import json
import traceback
import contextlib
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)  # 02_第2弾_G条件（gen_gjoken/gen_tc_from_gjoken の場所）
for _p in (_HERE, _PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import locate
import io_xlsx
import gen_gjoken
import gen_tc_from_gjoken

DEFAULT_CONFIG = {
    'expcd_path': locate.DEFAULT_EXPCD,
    'bugakari_root': locate.DEFAULT_BUGAKARI,
    'port': 8765,
    'workdir_root': None,  # None → システム一時領域
}


# ----------------------------------------------------------------------------
# config
# ----------------------------------------------------------------------------
def load_config(path=None):
    """config.json を読み既定にマージ。無ければ既定のまま。"""
    cfg = dict(DEFAULT_CONFIG)
    path = path or os.path.join(_HERE, 'config.json')
    if os.path.exists(path):
        try:
            with open(path, encoding='utf-8-sig') as f:
                user = json.load(f)
            cfg.update({k: v for k, v in user.items() if v is not None})
        except (OSError, ValueError):
            pass  # 壊れたconfigは無視して既定で動く
    return cfg


# ----------------------------------------------------------------------------
# 共通ユーティリティ
# ----------------------------------------------------------------------------
def _capture(func, *args, **kwargs):
    """func の stdout を捕捉して (戻り値, ログ文字列) を返す。build_g/run のprint対策。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = func(*args, **kwargs)
    return result, buf.getvalue()


def _fmt_err(e):
    return '%s: %s' % (type(e).__name__, e)


def new_workdir(cfg, prefix):
    base = cfg.get('workdir_root')
    if base:
        os.makedirs(base, exist_ok=True)
    return tempfile.mkdtemp(prefix=prefix, dir=base)


def _to_csv(path, workdir, name):
    """入力(xlsx or csv) を workdir 内のCSVに正規化して返す。"""
    if path.lower().endswith('.xlsx'):
        dst = os.path.join(workdir, name)
        io_xlsx.xlsx_to_csv(path, dst)
        return dst
    return path  # 既にCSV（テスト・内部利用）


def _apply_ymd(json_path):
    """歩掛JSONファイル名 <key>.<年度>.<適用年月日>.json から適用年月日を取り出す。無ければ''。"""
    import re
    m = re.fullmatch(r'.+\.(\d+)\.(\d+)\.json', os.path.basename(json_path))
    return m.group(2) if m else ''


def _note_lint(csv30):
    """改修後G条件CSVの(注)を解釈し、UI表示用の指摘リストを返す。
    [{level: INFO/WARN/ERROR, text}]。読めなかった注を黙って捨てないための可視化
    (2026-08-17 運用FB: 語尾の揺れ・注番号ずれで注が無効化され往復が発生した)。"""
    try:
        g = gen_tc_from_gjoken.read_gjoken(csv30)
        return gen_tc_from_gjoken.note_lint(g)
    except Exception:  # noqa: BLE001  表示用の付加情報なので生成自体は止めない
        return []


def _note_count(matrix):
    nb = io_xlsx._note_boundary(matrix)
    if nb >= len(matrix):
        return 0
    cnt = 0
    for r in matrix[nb + 1:]:
        # (外部設計メモ)以降は自由記述欄なので注の件数に数えない（旧名(設計メモ)も拾う）
        if r and '設計メモ' in str(r[0]):
            break
        if len(r) > 1 and str(r[1]).strip():
            cnt += 1
    return cnt


# ----------------------------------------------------------------------------
# ① キー→版候補
# ----------------------------------------------------------------------------
def locate_versions(input_text, cfg=None):
    """①タブ: 入力(Gaia9キー/歩掛キー) → 歩掛キー群×版候補（locate.resolve のラッパ）。"""
    cfg = cfg or load_config()
    return locate.resolve(input_text, cfg['expcd_path'], cfg['bugakari_root'])


# ----------------------------------------------------------------------------
# ① G条件生成
# ----------------------------------------------------------------------------
def gen_g(json_path, cfg=None, out_dir=None):
    """①タブ: 歩掛JSON → G条件CSV＋「商品_」xlsx。
    戻り値: {error, csv_path, xlsx_path, log, g_count, note_count, workdir}"""
    cfg = cfg or load_config()
    out = {'error': None, 'csv_path': None, 'xlsx_path': None, 'log': '',
           'g_count': None, 'note_count': None, 'workdir': None}
    if not os.path.exists(json_path):
        out['error'] = 'JSONが見つかりません: %s' % json_path
        return out
    try:
        wd = out_dir or new_workdir(cfg, 'geng_')
        out['workdir'] = wd
        # label='' → 素の名前「Gaia入力基準表_<工種>(<単位>).csv」
        csv_path, log = _capture(gen_gjoken.build_g, json_path, wd, '')
        out['csv_path'] = csv_path
        out['log'] = log
        base = os.path.splitext(os.path.basename(csv_path))[0]
        # 版によって出力が変わるため、適用日をファイル名に付ける（同名衝突・混同を防ぐ）
        ymd = _apply_ymd(json_path)
        out['apply_ymd'] = ymd
        suffix = '_適用%s' % ymd if ymd else ''
        xlsx = os.path.join(wd, '商品_%s%s.xlsx' % (base, suffix))  # 運用者向け名称=商品
        io_xlsx.csv_to_xlsx(csv_path, xlsx)
        out['xlsx_path'] = xlsx
        matrix, _ = io_xlsx.read_csv_matrix(csv_path)
        if matrix:
            out['g_count'] = max(0, len(matrix[0]) - 1)
            out['note_count'] = _note_count(matrix)
    except Exception as e:  # noqa: BLE001 UIに返すため広く捕捉
        out['error'] = _fmt_err(e)
        out['log'] += '\n' + traceback.format_exc()
    return out


# ----------------------------------------------------------------------------
# ③ 改定後TC生成
# ----------------------------------------------------------------------------
def gen_tc(g20, g30, old_json, cfg=None, out_dir=None):
    """③タブ: 商品G条件(g20)＋改修後G条件(g30)＋改定前JSON → 改定後TC CSV＋「テストケース_」xlsx。
    g20/g30 は xlsx または csv パス。
    戻り値: {error, csv_path, xlsx_path, log, tc_count, col_count, workdir}"""
    cfg = cfg or load_config()
    out = {'error': None, 'csv_path': None, 'xlsx_path': None, 'log': '',
           'tc_count': None, 'col_count': None, 'workdir': None, 'note_lint': []}
    for label, p in (('商品G条件', g20), ('改修後G条件', g30), ('改定前JSON', old_json)):
        if not p or not os.path.exists(p):
            out['error'] = '%s が見つかりません: %s' % (label, p)
            return out
    try:
        wd = out_dir or new_workdir(cfg, 'gentc_')
        out['workdir'] = wd
        csv20 = _to_csv(g20, wd, '20_G条件.csv')
        csv30 = _to_csv(g30, wd, '30_G条件.csv')
        out['note_lint'] = _note_lint(csv30)
        s3, log = _capture(gen_tc_from_gjoken.run, csv20, csv30, old_json, wd)
        out['csv_path'] = s3
        out['log'] = log
        koshu = os.path.splitext(os.path.basename(s3))[0]
        koshu = koshu.replace('step3.0_テストケース_', '').replace('step3.0_テストケース', '')
        xlsx = os.path.join(wd, 'テストケース_%s.xlsx' % (koshu or '出力'))
        io_xlsx.csv_to_xlsx(s3, xlsx)  # 条件列見出しを自動色付け
        out['xlsx_path'] = xlsx
        matrix, _ = io_xlsx.read_csv_matrix(s3)
        if matrix:
            out['tc_count'] = max(0, len(matrix) - 1)
            out['col_count'] = len(matrix[0])
    except Exception as e:  # noqa: BLE001
        out['error'] = _fmt_err(e)
        out['log'] += '\n' + traceback.format_exc()
    return out


# ----------------------------------------------------------------------------
# 新規歩掛: 改修後G条件だけから TC生成（改定前JSON/商品G条件なし）
# ----------------------------------------------------------------------------
def gen_tc_new(g30, cfg=None, out_dir=None):
    """新規歩掛タブ: 改修後G条件(g30, xlsx/csv) 1枚 → 改定後TC CSV＋「テストケース_」xlsx。
    改定前JSON・商品G条件は使わない（表の列・選択肢・注から直接展開）。
    戻り値: {error, csv_path, xlsx_path, log, tc_count, col_count, workdir}"""
    cfg = cfg or load_config()
    out = {'error': None, 'csv_path': None, 'xlsx_path': None, 'log': '',
           'tc_count': None, 'col_count': None, 'workdir': None, 'note_lint': []}
    if not g30 or not os.path.exists(g30):
        out['error'] = '改修後G条件が見つかりません: %s' % g30
        return out
    try:
        wd = out_dir or new_workdir(cfg, 'gentcnew_')
        out['workdir'] = wd
        # 工種名は元ファイル名から拾う（_to_csv でリネームされる前に）
        koshu = gen_tc_from_gjoken._koshu_from_gname(g30)
        csv30 = _to_csv(g30, wd, '30_G条件.csv')
        out['note_lint'] = _note_lint(csv30)
        s3, log = _capture(gen_tc_from_gjoken.run_single, csv30, wd, koshu)
        out['csv_path'] = s3
        out['log'] = log
        xlsx = os.path.join(wd, 'テストケース_%s.xlsx' % (koshu or '出力'))
        io_xlsx.csv_to_xlsx(s3, xlsx)  # 条件列見出し＋変更セルを自動色付け
        out['xlsx_path'] = xlsx
        matrix, _ = io_xlsx.read_csv_matrix(s3)
        if matrix:
            out['tc_count'] = max(0, len(matrix) - 1)
            out['col_count'] = len(matrix[0])
    except Exception as e:  # noqa: BLE001
        out['error'] = _fmt_err(e)
        out['log'] += '\n' + traceback.format_exc()
    return out


if __name__ == '__main__':
    # 簡易CLI: python3 service.py locate <キー> / geng <json> / gentc <g20> <g30> <oldjson>
    #          / gentcnew <g30>
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    if cmd == 'locate':
        print(json.dumps(locate_versions(sys.argv[2]), ensure_ascii=False, indent=2))
    elif cmd == 'geng':
        r = gen_g(sys.argv[2])
        print(json.dumps({k: v for k, v in r.items() if k != 'log'}, ensure_ascii=False, indent=2))
        print('--- log ---\n' + r['log'])
    elif cmd == 'gentc':
        r = gen_tc(sys.argv[2], sys.argv[3], sys.argv[4])
        print(json.dumps({k: v for k, v in r.items() if k != 'log'}, ensure_ascii=False, indent=2))
        print('--- log ---\n' + r['log'])
    elif cmd == 'gentcnew':
        r = gen_tc_new(sys.argv[2])
        print(json.dumps({k: v for k, v in r.items() if k != 'log'}, ensure_ascii=False, indent=2))
        print('--- log ---\n' + r['log'])
    else:
        print('Usage: python3 service.py locate <key> | geng <json> '
              '| gentc <g20> <g30> <oldjson> | gentcnew <g30>')
