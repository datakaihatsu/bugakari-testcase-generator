'use strict';

const $ = (id) => document.getElementById(id);
let selectedJsonPath = null;   // ①で選んだ版のJSONパス

// ---- 共通 ----
async function postJSON(url, body) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  return res.json();
}
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"]/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}
function dlLink(r) {
  if (!r.download_token) return '';
  return `<a class="dl" href="/api/download?token=${encodeURIComponent(r.download_token)}">⬇ ${esc(r.download_name)} をダウンロード</a>`;
}
// 改修後G条件の (注) 解釈結果。読めなかった注／番号ずれをここで名指しする
// （黙って捨てるとTCに反映されず、後工程で気づけないため）。
function noteLintBlock(r) {
  const lines = r.note_lint || [];
  if (!lines.length) return '';
  const worst = lines.some((l) => l.level === 'ERROR') ? 'err'
    : (lines.some((l) => l.level === 'WARN') ? 'warn' : 'info');
  const items = lines.map((l) => {
    const cls = l.level === 'ERROR' ? 'err' : (l.level === 'WARN' ? 'warn' : 'info');
    return `<li class="${cls}">${esc(l.text)}</li>`;
  }).join('');
  return `<div class="notelint ${worst}"><div class="notelint-h">(注)の解釈</div><ul>${items}</ul></div>`;
}
function logBlock(r) {
  if (!r.log) return '';
  return `<details class="log"><summary>実行ログ</summary><pre>${esc(r.log)}</pre></details>`;
}
function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => resolve(String(fr.result).split(',')[1]);
    fr.onerror = reject;
    fr.readAsDataURL(file);
  });
}

// ---- タブ ----
document.querySelectorAll('.tab').forEach((b) => {
  b.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach((x) => x.classList.remove('active'));
    document.querySelectorAll('.panel').forEach((x) => x.classList.remove('active'));
    b.classList.add('active');
    $(b.dataset.tab).classList.add('active');
    if (b.dataset.tab === 't3') refreshHandoff();
  });
});

// ---- config / handoff ----
async function loadConfig() {
  try {
    const c = await (await fetch('/api/config')).json();
    $('appver').textContent = c.version_label || '';
    $('cfgbar').innerHTML = (c.is_custom ? '<span class="custombadge">変更中</span> ' : '')
      + esc(`ExpCD: ${c.expcd_path}　|　歩掛: ${c.bugakari_root}`);
    updateHandoff(c.session);
  } catch (e) {
    $('cfgbar').textContent = '設定の読込に失敗しました';
  }
}
function updateHandoff(session) {
  const box = $('handoffBox');
  const sessionRadio = document.querySelector('input[name=g20mode][value=session]');
  if (session && session.has_handoff) {
    box.innerHTML = `<span class="chip">①の結果を使用: ${esc(session.koshu || '商品G条件')}</span>`;
    sessionRadio.disabled = false;
  } else {
    box.innerHTML = '<span class="err">①をまだ実行していません（改定前JSONの特定に必要）。枠Aでファイルを選ぶ場合も先に①の実行が必要です。</span>';
    sessionRadio.disabled = true;
    document.querySelector('input[name=g20mode][value=upload]').checked = true;
    syncG20Mode();
  }
}
async function refreshHandoff() {
  try {
    const c = await (await fetch('/api/config')).json();
    updateHandoff(c.session);
  } catch (e) { /* noop */ }
}

// ---- ① locate ----
$('btnLocate').addEventListener('click', async () => {
  const text = $('keyInput').value.trim();
  const box = $('locateResult');
  $('btnGenG').disabled = true;
  selectedJsonPath = null;
  if (!text) { box.innerHTML = '<span class="err">キーを入力してください</span>'; return; }
  box.innerHTML = '検索中…';
  const r = await postJSON('/api/locate', { input: text });
  if (r.error) { box.innerHTML = `<span class="err">${esc(r.error)}</span>`; return; }
  renderLocate(r, box);
});
$('keyInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('btnLocate').click(); });

function renderLocate(r, box) {
  let html = `<div>判定: ${r.kind === 'gaia9' ? 'Gaia9キー' : '歩掛キー'}　歩掛キー: ${esc(r.bugakari_keys.join(', '))}</div>`;
  r.results.forEach((res) => {
    html += `<div class="vergroup"><h3>歩掛キー ${esc(res.bugakari_key)}</h3>`;
    if (!res.candidates.length) {
      html += '<div class="err">この歩掛キーのJSONが見つかりません</div>';
    } else {
      res.candidates.slice().reverse().forEach((c) => {
        const latest = c.is_chosen ? '<span class="latest">◀ 既定(最新の適用版)</span>' : '';
        html += `<label class="ver"><input type="radio" name="ver" value="${esc(c.path)}" ${c.is_chosen ? 'checked' : ''}> 適用 ${esc(c.ymd)}（年度 ${esc(c.nendo)}）${latest}</label>`;
      });
    }
    html += '</div>';
  });
  box.innerHTML = html;
  box.querySelectorAll('input[name=ver]').forEach((el) => {
    el.addEventListener('change', () => { selectedJsonPath = el.value; $('btnGenG').disabled = false; });
  });
  const checked = box.querySelector('input[name=ver]:checked');
  if (checked) { selectedJsonPath = checked.value; $('btnGenG').disabled = false; }
}

// ---- ① gen_g ----
$('btnGenG').addEventListener('click', async () => {
  const box = $('genGResult');
  if (!selectedJsonPath) { box.innerHTML = '<span class="err">版を選択してください</span>'; return; }
  box.innerHTML = '生成中…';
  $('btnGenG').disabled = true;
  const r = await postJSON('/api/gen_g', { json_path: selectedJsonPath });
  $('btnGenG').disabled = false;
  if (r.error) { box.innerHTML = `<span class="err">${esc(r.error)}</span>${logBlock(r)}`; return; }
  const verLabel = r.apply_ymd ? `適用 ${r.apply_ymd} 版・` : '';
  box.innerHTML = `<div class="summary ok">G条件を生成しました（${verLabel}条件 ${r.g_count} 個 / 注 ${r.note_count} 件）</div>`
    + dlLink(r)
    + '<div class="hint">この「商品」xlsxをExcelで改修後の内容に編集し、③の枠Bに投入してください。</div>'
    + logBlock(r);
  refreshHandoff();
});

// ---- ③ g20 mode ----
function syncG20Mode() {
  const mode = document.querySelector('input[name=g20mode]:checked').value;
  $('g20File').disabled = (mode !== 'upload');
}
document.querySelectorAll('input[name=g20mode]').forEach((el) => el.addEventListener('change', syncG20Mode));

// ---- ③ gen_tc ----
$('btnGenTC').addEventListener('click', async () => {
  const box = $('genTCResult');
  const g30f = $('g30File').files[0];
  if (!g30f) { box.innerHTML = '<span class="err">枠B（改修後G条件）のファイルを選んでください</span>'; return; }
  const mode = document.querySelector('input[name=g20mode]:checked').value;
  const body = { g30_b64: await fileToBase64(g30f), g30_name: g30f.name };
  if (mode === 'session') {
    body.use_session_g20 = true;
  } else {
    const g20f = $('g20File').files[0];
    if (!g20f) { box.innerHTML = '<span class="err">枠A（商品G条件）のファイルを選んでください</span>'; return; }
    body.g20_b64 = await fileToBase64(g20f);
    body.g20_name = g20f.name;
  }
  box.innerHTML = '';
  $('tcSpinner').classList.remove('hidden');
  $('btnGenTC').disabled = true;
  const r = await postJSON('/api/gen_tc', body);
  $('tcSpinner').classList.add('hidden');
  $('btnGenTC').disabled = false;
  if (r.error) { box.innerHTML = `<span class="err">${esc(r.error)}</span>${logBlock(r)}`; return; }
  box.innerHTML = `<div class="summary ok">テストケースを生成しました（TC ${r.tc_count} 件 / 列 ${r.col_count}）</div>`
    + noteLintBlock(r)
    + dlLink(r)
    + '<div class="hint">条件（積算で選ぶ）列の見出しは色付き。生成後は必ず人が目視レビューしてください。</div>'
    + logBlock(r);
});

// ---- 🆕 新規歩掛 gen_tc_new ----
$('btnGenTCNew').addEventListener('click', async () => {
  const box = $('genTCNewResult');
  const f = $('gnewFile').files[0];
  if (!f) { box.innerHTML = '<span class="err">枠C（改修後G条件）のファイルを選んでください</span>'; return; }
  box.innerHTML = '';
  $('tcNewSpinner').classList.remove('hidden');
  $('btnGenTCNew').disabled = true;
  const r = await postJSON('/api/gen_tc_new', { g30_b64: await fileToBase64(f), g30_name: f.name });
  $('tcNewSpinner').classList.add('hidden');
  $('btnGenTCNew').disabled = false;
  if (r.error) { box.innerHTML = `<span class="err">${esc(r.error)}</span>${logBlock(r)}`; return; }
  box.innerHTML = `<div class="summary ok">テストケースを生成しました（TC ${r.tc_count} 件 / 列 ${r.col_count}）</div>`
    + noteLintBlock(r)
    + dlLink(r)
    + '<div class="hint">条件列の見出しは色付き。2件目以降のパターンは、直前の行と変わった選択肢セルが色付きです。生成後は必ず人が目視レビューしてください。</div>'
    + logBlock(r);
});

// ---- 設定: 格納場所（GaiaCloudデータ）の変更 ----
// 既定は固定のまま。データを外付けSSD等へ移した端末だけがここを使う（2026-09-02 要望）。
function setMsg(html) { $('setResult').innerHTML = html; }

function checkBlock(check) {
  if (!check) return '';
  const li = (label, c) =>
    `<li class="${c.ok ? 'ok' : 'err'}">${label}: ${esc(c.text)}</li>`;
  return `<ul class="checklist">${li('ExpCDConvert.json', check.expcd)}`
    + `${li('Bugakari フォルダ', check.bugakari)}</ul>`;
}

async function openSettings() {
  $('settingsPanel').classList.remove('hidden');
  setMsg('');
  const st = await (await fetch('/api/settings')).json();
  $('setExpcd').value = st.expcd_path || '';
  $('setBugakari').value = st.bugakari_root || '';
  $('setFile').textContent = `設定の保存先: ${st.settings_file}`
    + (st.is_custom ? '（現在この設定が有効）' : '（未作成＝配布既定を使用中）');
  setMsg(checkBlock(st.check));
  $('setRoot').focus();
}

$('btnSettings').addEventListener('click', () => {
  const p = $('settingsPanel');
  if (p.classList.contains('hidden')) { openSettings(); } else { p.classList.add('hidden'); }
});
$('btnSetClose').addEventListener('click', () => $('settingsPanel').classList.add('hidden'));

// フォルダ1つ（DB）から2つのパスを推定して詳細欄へ流し込む
$('btnDerive').addEventListener('click', async () => {
  const root = $('setRoot').value.trim();
  if (!root) { setMsg('<span class="err">データフォルダを入力してください</span>'); return; }
  setMsg('確認中…');
  const r = await postJSON('/api/settings', { action: 'derive', root });
  if (!r.ok) { setMsg(`<span class="err">${esc(r.error)}</span>`); return; }
  $('setExpcd').value = r.expcd_path;
  $('setBugakari').value = r.bugakari_root;
  $('setDetail').open = true;
  setMsg(`<div>この場所を使います: <code>${esc(r.db_root)}</code></div>${checkBlock(r.check)}`
    + '<div class="hint">問題なければ「保存して反映」を押してください。</div>');
});

$('btnSetCheck').addEventListener('click', async () => {
  setMsg('確認中…');
  const r = await postJSON('/api/settings', {
    action: 'check', expcd_path: $('setExpcd').value, bugakari_root: $('setBugakari').value });
  $('setExpcd').value = r.expcd_path || $('setExpcd').value;
  setMsg(checkBlock(r.check));
});

async function saveSettings(force) {
  setMsg('保存中…');
  const r = await postJSON('/api/settings', {
    action: 'save', force: !!force,
    expcd_path: $('setExpcd').value, bugakari_root: $('setBugakari').value });
  if (r.error) {
    setMsg(`<span class="err">${esc(r.error)}</span>${checkBlock(r.check)}`
      + (r.can_force ? '<div class="row"><button type="button" id="btnSetForce" class="secondary">'
        + '確認できないまま保存する（外付けドライブ未接続の場合など）</button></div>' : ''));
    const f = $('btnSetForce');
    if (f) f.addEventListener('click', () => saveSettings(true));
    return;
  }
  setMsg(`<div class="summary ok">${esc(r.message)}</div>${checkBlock(r.check)}`);
  $('setFile').textContent = `設定の保存先: ${r.settings_file}`
    + (r.is_custom ? '（現在この設定が有効）' : '（未作成＝配布既定を使用中）');
  $('setExpcd').value = r.expcd_path || '';
  $('setBugakari').value = r.bugakari_root || '';
  loadConfig();  // ヘッダーの表示を更新
}
$('btnSetSave').addEventListener('click', () => saveSettings(false));

$('btnSetReset').addEventListener('click', async () => {
  if (!confirm('配布時の既定の格納場所に戻します。よろしいですか？')) return;
  setMsg('戻しています…');
  const r = await postJSON('/api/settings', { action: 'reset' });
  if (r.error) { setMsg(`<span class="err">${esc(r.error)}</span>`); return; }
  $('setExpcd').value = r.expcd_path || '';
  $('setBugakari').value = r.bugakari_root || '';
  $('setRoot').value = '';
  $('setFile').textContent = `設定の保存先: ${r.settings_file}（未作成＝配布既定を使用中）`;
  setMsg(`<div class="summary ok">${esc(r.message)}</div>${checkBlock(r.check)}`);
  loadConfig();
});

// init
loadConfig();
syncG20Mode();
