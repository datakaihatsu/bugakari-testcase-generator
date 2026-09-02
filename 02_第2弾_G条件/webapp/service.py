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

_ENGINE = os.path.join(_PARENT, 'engine')
if _ENGINE not in sys.path:
    sys.path.insert(0, _ENGINE)
import flow_walker  # 生成の時間上限(組合せ爆発時の最終防壁)を設定するため

DEFAULT_CONFIG = {
    'expcd_path': locate.DEFAULT_EXPCD,
    'bugakari_root': locate.DEFAULT_BUGAKARI,
    'port': 8765,
    'workdir_root': None,  # None → システム一時領域
    # 生成1回の時間上限(秒)。超過時はエラーを返してスレッドを解放する
    # (2026-08-31 不具合: 65-546-6435-22 で「生成中」のまま固まった対策)。
    'gen_timeout_sec': 300,
}


# ----------------------------------------------------------------------------
# config
# ----------------------------------------------------------------------------
def load_config(path=None, use_user_settings=True):
    """config.json を読み既定にマージ。無ければ既定のまま。
    さらに（use_user_settings=True のとき）ユーザ設定ファイル（格納場所の変更）を最後に重ねる。
    優先順: DEFAULT_CONFIG < config.json（配布既定） < ユーザ設定（画面の「格納場所を変更」）。"""
    cfg = dict(DEFAULT_CONFIG)
    path = path or os.path.join(_HERE, 'config.json')
    if os.path.exists(path):
        try:
            with open(path, encoding='utf-8-sig') as f:
                user = json.load(f)
            cfg.update({k: v for k, v in user.items() if v is not None})
        except (OSError, ValueError):
            pass  # 壊れたconfigは無視して既定で動く
    if use_user_settings:
        cfg.update(load_user_settings())
    return cfg


# ----------------------------------------------------------------------------
# ユーザ設定（格納場所の変更）
#   GaiaCloud のデータを外付けSSD等へ移した端末向け（2026-09-02 要望）。
#   保存先は **配布フォルダの外**（%LOCALAPPDATA%）。差分リビルドで app/ を丸ごと
#   上書きしてもユーザの設定が消えないようにするため（config.json に書かない）。
# ----------------------------------------------------------------------------
USER_SETTING_KEYS = ('expcd_path', 'bugakari_root')
_APP_DIRNAME = 'GaiaKoshuTC'


def user_settings_path():
    """ユーザ設定ファイルのパス。Windowsは %LOCALAPPDATA%/GaiaKoshuTC/settings.json。"""
    base = os.environ.get('GAIA_TC_SETTINGS_DIR') or os.environ.get('LOCALAPPDATA')
    if not base:
        base = os.path.join(os.path.expanduser('~'), '.' + _APP_DIRNAME)
        return os.path.join(base, 'settings.json')
    return os.path.join(base, _APP_DIRNAME, 'settings.json')


def load_user_settings():
    """ユーザ設定を読む。無い/壊れている場合は {}（既定で動く）。"""
    path = user_settings_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding='utf-8-sig') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for k in USER_SETTING_KEYS:
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            out[k] = v.strip()
    return out


def save_user_settings(values):
    """ユーザ設定を保存する。既知キーのみ書き込む。失敗時は OSError。"""
    path = user_settings_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = {k: str(values[k]).strip() for k in USER_SETTING_KEYS
            if values.get(k) and str(values[k]).strip()}
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def clear_user_settings():
    """ユーザ設定を削除し、配布既定（config.json）へ戻す。"""
    path = user_settings_path()
    if os.path.exists(path):
        os.remove(path)
    return path


def normalize_setting_paths(expcd_path, bugakari_root):
    """入力の揺れを吸収する。
      - 前後空白 / 引用符 / 末尾の区切り記号を除去
      - ExpCD にフォルダを指定された場合は ExpCDConvert.json / Common/ExpCDConvert.json を補う
    戻り値: (expcd_path, bugakari_root)"""
    def _clean(s):
        s = str(s or '').strip().strip('"').strip("'")
        return s.rstrip('\\/') if len(s) > 3 else s

    e, b = _clean(expcd_path), _clean(bugakari_root)
    if e and os.path.isdir(e):
        for cand in (os.path.join(e, 'ExpCDConvert.json'),
                     os.path.join(e, 'Common', 'ExpCDConvert.json')):
            if os.path.isfile(cand):
                e = cand
                break
    return e, b


def derive_from_root(root):
    """フォルダ1つ（例: E:/GaiaCloud/DB や E:/GaiaCloud）から2つのパスを導く。
    `<DB>/Common/ExpCDConvert.json` と `<DB>/Bugakari` の並びを手掛かりに DB 階層を探す。
    戻り値: {'ok', 'db_root', 'expcd_path', 'bugakari_root', 'error'}"""
    out = {'ok': False, 'db_root': None, 'expcd_path': None, 'bugakari_root': None,
           'error': None}
    root = str(root or '').strip().strip('"').strip("'")
    if len(root) > 3:
        root = root.rstrip('\\/')
    if not root:
        out['error'] = 'フォルダを入力してください'
        return out
    if not os.path.isdir(root):
        out['error'] = 'フォルダが見つかりません: %s' % root
        return out
    # 指定フォルダ自身 → DB → GaiaCloud/DB → CoBeing/GaiaCloud/DB の順で探す
    cands = [root,
             os.path.join(root, 'DB'),
             os.path.join(root, 'GaiaCloud', 'DB'),
             os.path.join(root, 'CoBeing', 'GaiaCloud', 'DB')]
    for db in cands:
        expcd = os.path.join(db, 'Common', 'ExpCDConvert.json')
        bug = os.path.join(db, 'Bugakari')
        if os.path.isfile(expcd) or os.path.isdir(bug):
            out.update({'ok': True, 'db_root': db.replace(os.sep, '/'),
                        'expcd_path': expcd.replace(os.sep, '/'),
                        'bugakari_root': bug.replace(os.sep, '/')})
            return out
    out['error'] = ('このフォルダの下に Common/ExpCDConvert.json と Bugakari が見つかりません: %s'
                    % root)
    return out


def check_paths(expcd_path, bugakari_root):
    """指定パスの実在チェック。UI表示用の判定を返す（保存の可否判断にも使う）。
    戻り値: {'ok', 'expcd': {'ok','text'}, 'bugakari': {'ok','text'}}"""
    e_ok = bool(expcd_path) and os.path.isfile(expcd_path)
    b_ok = bool(bugakari_root) and os.path.isdir(bugakari_root)
    b_text = 'NG: フォルダが見つかりません'
    if b_ok:
        b_text = 'OK: フォルダを確認しました'
        try:
            with os.scandir(bugakari_root) as it:
                n = sum(1 for _ in it)
            b_text += '（直下 %d 項目）' % n
        except OSError:
            pass  # 件数は付加情報。取れなくてもOK判定は変えない
    return {
        'ok': e_ok and b_ok,
        'expcd': {'ok': e_ok,
                  'text': 'OK: ファイルを確認しました' if e_ok else 'NG: ファイルが見つかりません'},
        'bugakari': {'ok': b_ok, 'text': b_text},
    }


def settings_state(cfg=None):
    """現在の格納場所と、その出所（既定 / ユーザ設定）をまとめて返す（画面表示用）。"""
    defaults = load_config(use_user_settings=False)
    user = load_user_settings()
    cfg = cfg or load_config()
    return {
        'expcd_path': cfg['expcd_path'],
        'bugakari_root': cfg['bugakari_root'],
        'defaults': {'expcd_path': defaults['expcd_path'],
                     'bugakari_root': defaults['bugakari_root']},
        'is_custom': bool(user),
        'settings_file': user_settings_path(),
        'check': check_paths(cfg['expcd_path'], cfg['bugakari_root']),
    }


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
        flow_walker.set_time_budget(cfg.get('gen_timeout_sec', 300))
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
    finally:
        flow_walker.set_time_budget(None)
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
        flow_walker.set_time_budget(cfg.get('gen_timeout_sec', 300))
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
    finally:
        flow_walker.set_time_budget(None)
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
        flow_walker.set_time_budget(cfg.get('gen_timeout_sec', 300))
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
    finally:
        flow_walker.set_time_budget(None)
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
