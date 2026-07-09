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
    $('cfgbar').textContent = `ExpCD: ${c.expcd_path}　|　歩掛: ${c.bugakari_root}`;
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
    + dlLink(r)
    + '<div class="hint">条件列の見出しは色付き。2件目以降のパターンは、直前の行と変わった選択肢セルが色付きです。生成後は必ず人が目視レビューしてください。</div>'
    + logBlock(r);
});

// init
loadConfig();
syncG20Mode();
