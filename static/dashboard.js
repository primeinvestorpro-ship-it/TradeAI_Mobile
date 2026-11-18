/**
 * StockEdge Options — Dashboard JavaScript
 * NSE Futures Analysis Engine
 *
 * Handles:
 *  - SocketIO real-time events (log, engine_event, tick, login_status)
 *  - Analyze / Execute / Stop / Manual buttons
 *  - Trade Score dashboard rendering
 *  - Lightweight-charts candlestick chart
 *  - Report listing and viewer
 *  - Settings form save
 *  - Live LTP polling (idle)
 */

/* ── SocketIO ─────────────────────────────────── */
const socket = io();

/* ── DOM refs ─────────────────────────────────── */
const $ = id => document.getElementById(id);
const btnAnalyze     = $('btnAnalyze');
const btnExecute     = $('btnExecute');
const btnLogin       = $('btnLogin');
const btnStop        = $('btnStop');
const btnLong        = $('btnLong');
const btnShort       = $('btnShort');
const btnManualLogin = $('btnManualLogin');
const btnRefreshRpts = $('btnRefreshReports');
const btnCloseReport = $('btnCloseReport');
const tickerInput    = $('tickerInput');
const tickerDropdown = $('tickerDropdown');
const adxSetupCard   = $('adxSetupCard');
const adxStatusBadge = $('adxStatusBadge');
const adxEmaStatus   = $('adxEmaStatus');
const adxVolStatus   = $('adxVolStatus');
const adxDiStatus    = $('adxDiStatus');
const adxPivotStatus = $('adxPivotStatus');
const adxSummaryNotes= $('adxSummaryNotes');
const loginDot       = $('loginDot');
const loginStatus    = $('loginStatus');
const engineDot      = $('engineDot');
const engineStatus   = $('engineStatus');
const progressBar    = $('progressBar');
const progressMsg    = $('progressMsg');
const scoreDashboard = $('scoreDashboard');
const pnlCard        = $('pnlCard');
const pnlDisplay     = $('pnlDisplay');
const positionsTable = $('positionsTable');
const livePriceBox   = $('livePriceBox');
const livePriceTicker= $('livePriceTicker');
const modeToggle     = $('modeToggle');
const modeLabel      = $('modeLabel');
const modeTag        = $('modeTag');
const aiProviderSelect = $('aiProviderSelect');
const logContainer   = $('logContainer');
const reportsList    = $('reportsList');
const reportViewer   = $('reportViewer');
const reportViewerTitle   = $('reportViewerTitle');
const reportViewerContent = $('reportViewerContent');
const optionChainCard     = $('optionChainCard');
const viewBtnBoth         = $('viewBtnBoth');
const viewBtnFutures      = $('viewBtnFutures');
const viewBtnOptions      = $('viewBtnOptions');

/* ── State ───────────────────────────────────── */
let isLoggedIn  = (typeof window.SERVER_LOGGED_IN !== 'undefined') ? window.SERVER_LOGGED_IN : false;
let isRunning   = false;
let ltpPollerId = null;
let currentChart = null;
let candleSeries  = null;
let lastTickByToken = {};
let currentViewMode = 'both';
let hasScoreData = false;
let hasOptionData = false;

/* ── View Switcher Modes ─────────────────────── */
function applyViewMode(mode) {
  currentViewMode = mode;
  [viewBtnBoth, viewBtnFutures, viewBtnOptions].forEach(b => {
    if (b) b.classList.remove('active');
  });
  if (mode === 'both' && viewBtnBoth) viewBtnBoth.classList.add('active');
  if (mode === 'futures' && viewBtnFutures) viewBtnFutures.classList.add('active');
  if (mode === 'options' && viewBtnOptions) viewBtnOptions.classList.add('active');

  if (mode === 'both') {
    if (hasScoreData && scoreDashboard) scoreDashboard.classList.remove('hidden');
    if (hasOptionData && optionChainCard) optionChainCard.classList.remove('hidden');
  } else if (mode === 'futures') {
    if (hasScoreData && scoreDashboard) scoreDashboard.classList.remove('hidden');
    if (optionChainCard) optionChainCard.classList.add('hidden');
  } else if (mode === 'options') {
    if (scoreDashboard) scoreDashboard.classList.add('hidden');
    if (hasOptionData && optionChainCard) optionChainCard.classList.remove('hidden');
  }
}

if (viewBtnBoth) viewBtnBoth.addEventListener('click', () => applyViewMode('both'));
if (viewBtnFutures) viewBtnFutures.addEventListener('click', () => applyViewMode('futures'));
if (viewBtnOptions) viewBtnOptions.addEventListener('click', () => applyViewMode('options'));

/* ── Chart setup ─────────────────────────────── */
function initChart() {
  if (currentChart) return;
  const container = $('chart');
  currentChart = LightweightCharts.createChart(container, {
    layout: { background: { color: '#172b4d' }, textColor: '#8898aa' },
    grid: { vertLines: { color: '#1f355d' }, horzLines: { color: '#1f355d' } },
    timeScale: { timeVisible: true, secondsVisible: false },
    width: container.offsetWidth,
    height: 300,
  });
  candleSeries = currentChart.addCandlestickSeries({
    upColor: '#2dce89', downColor: '#f5365c',
    borderUpColor: '#2dce89', borderDownColor: '#f5365c',
    wickUpColor: '#2dce89', wickDownColor: '#f5365c',
  });
  window.addEventListener('resize', () => {
    currentChart.applyOptions({ width: container.offsetWidth });
  });
}
initChart();

/* ── Login state helpers ─────────────────────── */
function setLoggedIn(val, userName) {
  isLoggedIn = val;
  loginDot.className = 'dot ' + (val ? 'green' : '');
  const displayName = userName || (typeof window.SERVER_USER_NAME !== 'undefined' ? window.SERVER_USER_NAME : '');
  loginStatus.textContent = val ? (displayName ? `Connected (${displayName})` : 'Logged in to Kite') : 'Not logged in';
  btnLogin.textContent = val ? (displayName ? `✓ Connected (${displayName})` : '✓ Connected to Kite') : 'Login to Kite';
  if (val) {
    btnLogin.classList.add('btn-success');
    btnLogin.classList.remove('btn-secondary');
    if (displayName) {
      const avatarName = $('userAvatarName');
      if (avatarName) avatarName.textContent = displayName;
    }
  } else {
    btnLogin.classList.remove('btn-success');
    btnLogin.classList.add('btn-secondary');
  }
  btnAnalyze.disabled = !val;
  btnLong.disabled    = !val;
  btnShort.disabled   = !val;
  if (val) startLtpPoller();
  else      stopLtpPoller();
}

function setRunning(val) {
  isRunning = val;
  engineDot.className  = 'dot ' + (val ? 'orange' : 'green');
  engineStatus.textContent = val ? 'Running…' : 'Idle';
  btnStop.disabled    = !val;
  btnAnalyze.disabled = val || !isLoggedIn;
  btnLong.disabled    = val || !isLoggedIn;
  btnShort.disabled   = val || !isLoggedIn;
  if (val) {
    progressBar.classList.remove('hidden');
    stopLtpPoller();
  } else {
    progressBar.classList.add('hidden');
    if (isLoggedIn) startLtpPoller();
  }
}

/* ── Logging ─────────────────────────────────── */
function addLog(msg, isError) {
  const line = document.createElement('div');
  line.className = 'log-line' + (isError ? ' error' : '');
  const ts = new Date().toLocaleTimeString('en-IN', {hour12: false});
  line.textContent = `[${ts}] ${msg}`;
  logContainer.appendChild(line);
  logContainer.scrollTop = logContainer.scrollHeight;
}

/* ── SocketIO events ─────────────────────────── */
socket.on('log', d => addLog(d.message));

socket.on('login_status', d => {
  if (d.status === 'success') {
    setLoggedIn(true, d.user_name);
    addLog(`✓ Kite login successful ${d.user_name ? '(' + d.user_name + ')' : ''}`);
    loadSpotChart();
    loadReports();
  } else if (d.status === 'error') {
    addLog(`✗ Kite login error: ${d.message}`, true);
  } else if (d.status === 'waiting') {
    addLog('→ Waiting for Kite login redirect…');
  }
});

socket.on('engine_status', d => setRunning(d.running));

socket.on('engine_event', evt => {
  const t = evt.type;

  if (t === 'stopped') {
    progressBar.classList.add('hidden');
    addLog(`■ ${evt.message || 'Analysis stopped by user.'}`);
    setRunning(false);
  }

  if (t === 'progress') {
    progressMsg.textContent = evt.message || 'Processing…';
  }

  if (t === 'universe_ready') {
    const sym = evt.ticker || '';
    $('chartTicker').textContent = sym;
    livePriceTicker.textContent = evt.futures_tradingsymbol || sym;
    $('scoreTicker').textContent = sym;
    addLog(`Universe: ${evt.futures_tradingsymbol} | Expiry: ${evt.nearest_expiry} | Lot: ${evt.lot_size}`);
  }

  if (t === 'discovery_complete') {
    const b = evt.discovery_brief || {};
    if (b.ticker) $('scoreTicker').textContent = b.ticker;
    if (b.company_name) $('scoreCompany').textContent = b.company_name;
    addLog(`Discovery: ${b.company_name || ''} @ ₹${b.current_price || 'N/A'} | Sector: ${b.sector || 'N/A'}`);
  }

  if (t === 'agents_complete') {
    addLog('✓ All 5 analysis agents completed');
  }

  if (t === 'option_chain') {
    if (evt.option_chain) renderOptionChain(evt.option_chain);
  }

  if (t === 'decision' || t === 'analysis_complete') {
    const src = evt.result || evt;
    if (src.ticker) $('scoreTicker').textContent = src.ticker;
    renderScoreDashboard(src);
    if (src.option_chain) renderOptionChain(src.option_chain);
    loadReports();
  }

  if (t === 'positions_entered') {
    pnlCard.classList.remove('hidden');
    renderPositions(evt.positions || []);
    addLog(`Position entered: ${(evt.positions || []).map(p => `${p.type} ${p.tradingsymbol}`).join(', ')}`);
  }

  if (t === 'pnl_update') {
    renderPositions(evt.positions || []);
    const total = evt.total_pnl || 0;
    pnlDisplay.textContent = `₹${total.toFixed(2)}`;
    pnlDisplay.className   = 'pnl-display ' + (total >= 0 ? 'pnl-green' : 'pnl-red');
  }

  if (t === 'exit') {
    addLog(`✓ Position exited: ${evt.reason}`);
    pnlCard.classList.add('hidden');
  }

  if (t === 'no_trade') {
    addLog('ℹ NO_TRADE decision — no position entered');
  }

  if (t === 'error') {
    addLog(`✗ Engine error: ${evt.message}`, true);
  }
});

socket.on('tick', tick => {
  if (tick.last_price != null) {
    lastTickByToken[tick.instrument_token] = tick.last_price;
    // Update LTP box (use any tick as price update)
    livePriceBox.textContent = `₹${tick.last_price.toFixed(2)}`;
    // Feed chart
    if (candleSeries) {
      const ts = Math.floor(Date.now() / 1000);
      try {
        candleSeries.update({ time: ts, open: tick.last_price, high: tick.last_price,
                               low: tick.last_price, close: tick.last_price });
      } catch {}
    }
  }
});

/* ── Score Dashboard renderer ────────────────── */
const DIMENSION_LABELS = [
  ['technical',   'Technical Strength',   '25%'],
  ['fundamental', 'Fundamental Quality',  '25%'],
  ['sentiment',   'Sentiment & Momentum', '20%'],
  ['risk',        'Risk Profile',         '15%'],
  ['thesis',      'Thesis Conviction',    '15%'],
];

function scoreColorClass(score) {
  if (score >= 85) return 'score-ap';
  if (score >= 70) return 'score-a';
  if (score >= 55) return 'score-b';
  if (score >= 40) return 'score-c';
  if (score >= 25) return 'score-d';
  return 'score-f';
}

function renderScoreDashboard(data) {
  const ts = data.trade_score || {};
  const db = data.discovery_brief || {};
  const bd = ts.breakdown || {};

  scoreDashboard.classList.remove('hidden');
  $('scoreTicker').textContent  = data.ticker || '—';
  $('scoreCompany').textContent = db.company_name || '—';

  const score  = ts.score  || 0;
  const grade  = ts.grade  || '—';
  const signal = ts.signal || '—';

  $('scoreBig').textContent    = score;
  $('scoreBig').className      = 'score-big ' + scoreColorClass(score);
  $('scoreGrade').textContent  = grade;
  $('scoreSignal').textContent = signal;

  // Table
  const tbody = $('scoreTableBody');
  tbody.innerHTML = '';
  let composite = 0;
  for (const [key, label, weight] of DIMENSION_LABELS) {
    const info = bd[key] || {};
    const s = info.score || 0;
    const w = info.weighted || 0;
    composite += w;
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${label}</td>
      <td><span class="${scoreColorClass(s)}">${s}/100</span></td>
      <td>${weight}</td>
      <td>
        <div class="score-bar-wrap">
          <div class="score-bar" style="width:${Math.min(w*4,100)}%"></div>
        </div>
        ${w.toFixed(1)}
      </td>
    `;
    tbody.appendChild(tr);
  }
  $('scoreTotal').innerHTML = `<strong>${score}/100</strong>`;

  // AI sentiment
  const sent   = data.ai_sentiment || '';
  const reason = data.ai_reason    || '';
  if (sent) {
    $('aiSentimentRow').classList.remove('hidden');
    const badge = $('aiSentimentBadge');
    badge.textContent = sent;
    badge.className   = 'badge badge-' + sent.toLowerCase();
    $('aiSentimentReason').textContent = reason;
  }

  // Chartink Intraday Buy Setup (ADX & Volume Filter)
  const adx = db.adx_volume_setup || (data.technical_ctx && data.technical_ctx.adx_volume_setup) || {};
  if (adxSetupCard && adx.status && adx.status !== 'UNKNOWN') {
    adxSetupCard.classList.remove('hidden');
    adxStatusBadge.textContent = adx.status;
    const badgeCls = adx.status === 'PASSED' ? 'badge-bullish' : (adx.status === 'PARTIAL' ? 'badge-neutral' : 'badge-bearish');
    adxStatusBadge.className = 'badge ' + badgeCls;

    const vals = adx.values || {};
    adxEmaStatus.textContent = adx.ema_alignment || (vals.ema8 ? `Above EMA 8, 34, 50` : '—');
    adxVolStatus.textContent = adx.volume_vs_10wma || (vals.volume_multiple ? `${vals.volume_multiple}x 10-WMA` : (vals.last_volume ? `${vals.last_volume}` : '—'));
    adxDiStatus.textContent  = (adx.plus_di != null) ? `${adx.plus_di}` : ((vals.plus_di != null) ? `${vals.plus_di}` : '—');
    adxPivotStatus.textContent = adx.pivot_status || (vals.pivot ? `Above Pivot (₹${vals.pivot})` : '—');
    adxSummaryNotes.textContent = adx.notes || adx.summary || '';
  } else if (adxSetupCard) {
    adxSetupCard.classList.add('hidden');
  }

  // Post-analysis buttons
  $('postAnalysisBtns').classList.remove('hidden');
  hasScoreData = true;
  applyViewMode(currentViewMode);
}

/* ── Option Chain renderer (ATM +/- 5 strikes) ─ */
function renderOptionChain(chainData) {
  if (!chainData || !optionChainCard) return;
  hasOptionData = true;

  // Metadata
  const sym = chainData.ticker || '—';
  $('optionChainMeta').textContent = `${sym} · ATM Strike: ₹${chainData.atm_strike} · Step: ₹${chainData.strike_interval}`;
  $('optExpiryBadge').textContent = `Expiry: ${chainData.nearest_expiry || '—'}`;
  $('optStepBadge').textContent = `Step: ₹${chainData.strike_interval || '—'}`;
  $('optAtmBadge').textContent = `ATM: ₹${chainData.atm_strike || '—'}`;

  // PCR Chip
  const pcr = chainData.pcr;
  $('optPcrVal').textContent = (pcr != null && isFinite(pcr)) ? pcr.toFixed(2) : '—';
  const pcrBias = chainData.oi_bias || 'NEUTRAL';
  const pcrBadge = $('optPcrBias');
  pcrBadge.textContent = pcrBias;
  pcrBadge.className = 'badge ' + (pcrBias === 'BULLISH' ? 'badge-bullish' : (pcrBias === 'BEARISH' ? 'badge-bearish' : 'badge-neutral'));
  $('optPcrSub').textContent = `Weighted: ${(chainData.weighted_pcr != null && isFinite(chainData.weighted_pcr)) ? chainData.weighted_pcr.toFixed(2) : '—'}`;

  // Also update top Argon KPI PCR Card
  const kpiPcr = $('kpiPcrVal');
  if (kpiPcr && pcr != null && isFinite(pcr)) {
    kpiPcr.textContent = pcr.toFixed(2);
  }
  const kpiBias = $('kpiPcrBias');
  if (kpiBias) {
    kpiBias.textContent = (pcr >= 1.0 ? '↑ ' : '↓ ') + pcrBias;
    kpiBias.className = 'argon-trend ' + (pcrBias === 'BULLISH' ? 'text-success' : (pcrBias === 'BEARISH' ? 'text-danger' : 'text-warning'));
  }
  const kpiW = $('kpiWalls');
  if (kpiW) {
    kpiW.textContent = (chainData.support_strike && chainData.resistance_strike)
      ? `Floor ₹${chainData.support_strike} · Ceil ₹${chainData.resistance_strike}`
      : (pcr >= 1.0 ? 'Put Writing Dominant' : 'Call Writing Dominant');
  }

  // Resistance Wall (Ceiling)
  $('optResStrike').textContent = chainData.resistance_strike ? `₹${chainData.resistance_strike} CE` : '—';
  $('optResOi').textContent = chainData.resistance_oi ? `OI: ${Number(chainData.resistance_oi).toLocaleString()}` : 'OI: —';

  // Support Wall (Floor)
  $('optSupStrike').textContent = chainData.support_strike ? `₹${chainData.support_strike} PE` : '—';
  $('optSupOi').textContent = chainData.support_oi ? `OI: ${Number(chainData.support_oi).toLocaleString()}` : 'OI: —';

  // ATM Straddle Net Premium
  $('optNetPremium').textContent = chainData.net_premium != null ? `₹${Number(chainData.net_premium).toFixed(2)}` : '—';
  const ceP = chainData.ce_premium != null ? `₹${Number(chainData.ce_premium).toFixed(2)}` : '—';
  const peP = chainData.pe_premium != null ? `₹${Number(chainData.pe_premium).toFixed(2)}` : '—';
  $('optPremiumsSplit').textContent = `CE: ${ceP} | PE: ${peP}`;
  $('optRequiredMove').textContent = chainData.required_move_both != null ? `Net Breakeven Move: ±₹${Number(chainData.required_move_both).toFixed(1)} pts` : 'Net Breakeven Move: —';

  // Directional Odds Meter
  const upProb = chainData.upside_probability != null ? chainData.upside_probability : 0.5;
  const dnProb = chainData.downside_probability != null ? chainData.downside_probability : 0.5;
  $('optProbText').textContent = `▲ Upside ${(upProb * 100).toFixed(0)}% | ${(dnProb * 100).toFixed(0)}% Downside ▼`;
  $('optProbUpBar').style.width = `${(upProb * 100).toFixed(1)}%`;
  $('optProbDownBar').style.width = `${(dnProb * 100).toFixed(1)}%`;

  // 11-Row Option Chain Table
  const tbody = $('optionChainTableBody');
  if (chainData.rows && chainData.rows.length) {
    tbody.innerHTML = chainData.rows.map(r => {
      const isAtm = r.is_atm;
      const isRes = r.is_resistance;
      const isSup = r.is_support;
      let rowCls = '';
      if (isAtm) rowCls = 'row-atm';
      else if (isRes) rowCls = 'row-res';
      else if (isSup) rowCls = 'row-sup';

      let strikeLabel = `${r.strike}`;
      if (isAtm) strikeLabel += ` <span class="strike-atm-badge">ATM</span>`;
      if (isRes) strikeLabel += ` <span class="wall-tag wall-tag-res">RES</span>`;
      if (isSup) strikeLabel += ` <span class="wall-tag wall-tag-sup">SUP</span>`;

      const ceOi = (r.ce && r.ce.oi) ? Number(r.ce.oi).toLocaleString() : '0';
      const ceVol = (r.ce && r.ce.volume) ? Number(r.ce.volume).toLocaleString() : '0';
      const ceLtp = (r.ce && r.ce.ltp != null) ? `₹${Number(r.ce.ltp).toFixed(2)}` : '—';

      const peOi = (r.pe && r.pe.oi) ? Number(r.pe.oi).toLocaleString() : '0';
      const peVol = (r.pe && r.pe.volume) ? Number(r.pe.volume).toLocaleString() : '0';
      const peLtp = (r.pe && r.pe.ltp != null) ? `₹${Number(r.pe.ltp).toFixed(2)}` : '—';

      return `
        <tr class="${rowCls}">
          <td>${ceOi}</td>
          <td>${ceVol}</td>
          <td style="color:#86efac;font-weight:600;">${ceLtp}</td>
          <td class="th-strike">${strikeLabel}</td>
          <td style="color:#fca5a5;font-weight:600;">${peLtp}</td>
          <td>${peVol}</td>
          <td>${peOi}</td>
        </tr>
      `;
    }).join('');
  } else {
    tbody.innerHTML = '<tr><td colspan="7" class="text-center muted">No options data available</td></tr>';
  }

  applyViewMode(currentViewMode);
}

function loadOptionChain(ticker) {
  if (!ticker) return;
  fetch(`/api/option_chain?ticker=${encodeURIComponent(ticker)}`)
    .then(r => r.json())
    .then(d => {
      if (d.ok && d.option_chain) {
        renderOptionChain(d.option_chain);
      }
    })
    .catch(() => {});
}

function renderPositions(positions) {
  if (!positions.length) { positionsTable.innerHTML = ''; return; }
  positionsTable.innerHTML = positions.map(p =>
    `<div style="display:flex;justify-content:space-between;margin-top:4px">
       <span>${p.type} ${p.tradingsymbol}</span>
       <span>Entry:₹${(p.entry_price||0).toFixed(2)} LTP:₹${(p.ltp||0).toFixed(2)}
             P&L:<span class="${(p.pnl||0)>=0?'pnl-green':'pnl-red'}">₹${(p.pnl||0).toFixed(2)}</span>
       </span>
     </div>`
  ).join('');
}

/* ── Query Parameter & Selectable Futures ────── */
function checkUrlQueryParam() {
  const params = new URLSearchParams(window.location.search);
  if (params.get('request_token') || params.get('status') === 'success') {
    window.history.replaceState({}, document.title, window.location.pathname);
  }
  const urlTicker = params.get('ticker') || params.get('symbol') || params.get('q');
  if (urlTicker) {
    tickerInput.value = urlTicker.trim().toUpperCase();
    pollLtp();
    loadSpotChart();
    if (params.get('analyze') === '1' || params.get('analyze') === 'true') {
      setTimeout(() => {
        if (!btnAnalyze.disabled) btnAnalyze.click();
      }, 800);
    }
  }
}

function updateUrlQueryParam(symbol) {
  if (!symbol) return;
  const url = new URL(window.location);
  url.searchParams.set('ticker', symbol.toUpperCase());
  window.history.replaceState({}, '', url);
}

let activeDropdownIndex = -1;
let debounceTimer = null;

function fetchAndShowFutures(query = '') {
  fetch(`/api/futures_symbols?q=${encodeURIComponent(query)}`)
    .then(r => r.json())
    .then(d => {
      if (!d.ok || !d.symbols || !d.symbols.length) {
        tickerDropdown.innerHTML = '<div class="ticker-dropdown-item"><span class="muted">No matching futures</span></div>';
        tickerDropdown.classList.remove('hidden');
        return;
      }
      activeDropdownIndex = -1;
      tickerDropdown.innerHTML = d.symbols.slice(0, 15).map((s, idx) => `
        <div class="ticker-dropdown-item" data-index="${idx}" data-symbol="${s.symbol}">
          <div>
            <span class="ticker-item-symbol">${s.symbol}</span>
            <span class="ticker-item-sub"> · Lot: ${s.lot_size || '—'}</span>
          </div>
          <span class="ticker-item-sub">${s.tradingsymbol || (s.expiry ? 'Exp: ' + s.expiry : '')}</span>
        </div>
      `).join('');
      tickerDropdown.classList.remove('hidden');

      tickerDropdown.querySelectorAll('.ticker-dropdown-item').forEach(item => {
        item.addEventListener('click', () => {
          const sym = item.getAttribute('data-symbol');
          if (sym) selectTicker(sym);
        });
      });
    })
    .catch(() => {});
}

function selectTicker(symbol) {
  const sym = symbol.toUpperCase();
  tickerInput.value = sym;
  tickerDropdown.classList.add('hidden');
  updateUrlQueryParam(sym);

  // Immediately reflect selected stock in UI
  livePriceTicker.textContent = sym;
  $('chartTicker').textContent = sym;
  $('scoreTicker').textContent = sym;
  $('scoreCompany').textContent = 'Selected — click Analyze to evaluate';

  // Persist ticker to server settings immediately so engine uses it
  fetch('/api/settings', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ ticker: sym }),
  }).catch(() => {});

  pollLtp();
  loadSpotChart();
  loadOptionChain(sym);
}

tickerInput.addEventListener('input', () => {
  clearTimeout(debounceTimer);
  const q = tickerInput.value.trim();
  if (q) updateUrlQueryParam(q);
  debounceTimer = setTimeout(() => fetchAndShowFutures(q), 120);
});

tickerInput.addEventListener('focus', () => {
  fetchAndShowFutures(tickerInput.value.trim());
});

tickerInput.addEventListener('keydown', e => {
  const items = tickerDropdown.querySelectorAll('.ticker-dropdown-item');
  if (tickerDropdown.classList.contains('hidden') || !items.length) {
    if (e.key === 'Enter') {
      e.preventDefault();
      if (!btnAnalyze.disabled) btnAnalyze.click();
    }
    return;
  }

  if (e.key === 'ArrowDown') {
    e.preventDefault();
    activeDropdownIndex = (activeDropdownIndex + 1) % items.length;
    highlightItem(items);
  } else if (e.key === 'ArrowUp') {
    e.preventDefault();
    activeDropdownIndex = (activeDropdownIndex - 1 + items.length) % items.length;
    highlightItem(items);
  } else if (e.key === 'Enter') {
    e.preventDefault();
    if (activeDropdownIndex >= 0 && items[activeDropdownIndex]) {
      items[activeDropdownIndex].click();
    } else {
      tickerDropdown.classList.add('hidden');
      if (!btnAnalyze.disabled) btnAnalyze.click();
    }
  } else if (e.key === 'Escape') {
    tickerDropdown.classList.add('hidden');
  }
});

function highlightItem(items) {
  items.forEach((it, idx) => {
    it.classList.toggle('active', idx === activeDropdownIndex);
    if (idx === activeDropdownIndex) it.scrollIntoView({ block: 'nearest' });
  });
}

document.addEventListener('click', e => {
  if (!e.target.closest('#tickerSearchContainer')) {
    tickerDropdown.classList.add('hidden');
  }
});

// Quick select pills
document.querySelectorAll('.ticker-pill').forEach(pill => {
  pill.addEventListener('click', () => {
    const sym = pill.getAttribute('data-symbol');
    if (sym) selectTicker(sym);
  });
});

/* ── Analyze button ──────────────────────────── */
btnAnalyze.addEventListener('click', () => {
  const ticker = tickerInput.value.trim().toUpperCase();
  if (!ticker) { alert('Enter a ticker symbol'); return; }
  updateUrlQueryParam(ticker);

  // Immediately reflect active ticker in scorecard header and chart
  $('scoreTicker').textContent = ticker;
  $('scoreCompany').textContent = 'Analyzing...';
  $('chartTicker').textContent = ticker;
  livePriceTicker.textContent = ticker;

  // Persist ticker to settings immediately
  fetch('/api/settings', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ticker}),
  }).catch(() => {});

  // Fetch chart and LTP for this ticker immediately
  pollLtp();
  loadSpotChart();

  progressMsg.textContent = 'Phase 1: Discovery…';
  fetch('/api/analyze', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ticker}),
  }).then(r => r.json()).then(d => {
    if (!d.ok) addLog(`✗ Analyze error: ${d.error}`, true);
  });
});

/* ── Execute button ──────────────────────────── */
btnExecute.addEventListener('click', () => {
  if (!confirm('Execute a REAL futures trade based on the analysis? Make sure you have reviewed the report.')) return;
  const ticker = tickerInput.value.trim().toUpperCase();
  fetch('/api/execute', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ticker}),
  }).then(r => r.json()).then(d => {
    if (!d.ok) addLog(`✗ Execute error: ${d.error}`, true);
  });
});

/* ── Stop button ─────────────────────────────── */
btnStop.addEventListener('click', () => {
  btnStop.disabled = true;
  fetch('/api/stop', {method: 'POST'}).then(r => r.json()).then(d => {
    if (!d.ok) {
      addLog(`✗ Stop error: ${d.error}`, true);
      btnStop.disabled = false;
    } else {
      addLog(`■ ${d.message || 'Stop signal sent'}`);
    }
  }).catch(err => {
    btnStop.disabled = false;
  });
});

/* ── Login ───────────────────────────────────── */
btnLogin.addEventListener('click', () => {
  if (isLoggedIn) {
    if (!confirm('You are currently connected to Kite. Do you want to re-authenticate / login again?')) {
      return;
    }
  }
  addLog('Initiating Kite login...');
  fetch('/api/login', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({force: true})
  }).then(r => r.json()).then(d => {
    if (!d.ok) {
      addLog(`✗ Login error: ${d.error}`, true);
    } else if (d.login_url) {
      addLog(d.message || 'Redirecting to Kite login...');
      window.location.href = d.login_url;
    }
  }).catch(err => {
    addLog(`✗ Login request failed: ${err.message}`, true);
  });
});

// Auto-sync login status whenever user focuses or switches back to this window
window.addEventListener('focus', () => {
  if (!isLoggedIn) {
    fetch('/api/status').then(r => r.json()).then(d => {
      if (d.logged_in) {
        setLoggedIn(true, d.user_name);
        addLog(`✓ Kite login active ${d.user_name ? '(' + d.user_name + ')' : ''}`);
        loadSpotChart();
        loadReports();
      }
    }).catch(() => {});
  }
});

btnManualLogin.addEventListener('click', () => {
  const token_input = $('manualTokenInput').value.trim();
  if (!token_input) {
    addLog('✗ Please enter a request_token or redirect URL', true);
    return;
  }
  addLog('Submitting manual token...');
  btnManualLogin.disabled = true;
  fetch('/api/login/manual', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({token_input}),
  }).then(r => r.json()).then(d => {
    btnManualLogin.disabled = false;
    if (!d.ok) {
      addLog(`✗ Manual login failed: ${d.error}`, true);
    } else {
      addLog(`✓ ${d.message || 'Manual login successful'}`);
      setLoggedIn(true);
      $('manualTokenInput').value = '';
      loadSpotChart();
      loadReports();
    }
  }).catch(err => {
    btnManualLogin.disabled = false;
    addLog(`✗ Manual login request error: ${err.message}`, true);
  });
});

/* ── Manual override ─────────────────────────── */
btnLong.addEventListener('click', () => {
  if (!confirm('Enter a LONG futures position manually (skips analysis)?')) return;
  fetch('/api/manual_trade', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({direction: 'LONG'}),
  }).then(r => r.json()).then(d => {
    if (!d.ok) addLog(`✗ Manual LONG error: ${d.error}`, true);
  });
});

btnShort.addEventListener('click', () => {
  if (!confirm('Enter a SHORT futures position manually (skips analysis)?')) return;
  fetch('/api/manual_trade', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({direction: 'SHORT'}),
  }).then(r => r.json()).then(d => {
    if (!d.ok) addLog(`✗ Manual SHORT error: ${d.error}`, true);
  });
});

/* ── Mode toggle ─────────────────────────────── */
modeToggle.addEventListener('change', () => {
  const live = modeToggle.checked;
  if (live && !confirm('Switch to LIVE mode? Real orders will be placed.')) {
    modeToggle.checked = false;
    return;
  }
  fetch('/api/mode', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({live}),
  }).then(r => r.json()).then(d => {
    if (d.ok) {
      modeLabel.textContent = live ? 'Live Orders' : 'Dry Run';
      modeTag.className  = 'mode-tag ' + (live ? 'mode-live' : 'mode-dry');
      modeTag.textContent = live ? 'LIVE' : 'DRY RUN';
    }
  });
});

/* ── Settings Panel Toggle / Close ───────────── */
const mainLayout = document.querySelector('.main-layout');
const settingsPanelEl = $('settingsPanel');
const btnTopSettings = $('btnTopSettings');
const btnCloseSettings = $('btnCloseSettings');
const settingsToggleBtn = $('settingsToggle');

function openSettingsPanel() {
  if (mainLayout) mainLayout.classList.remove('settings-closed');
}

function closeSettingsPanel() {
  if (mainLayout) mainLayout.classList.add('settings-closed');
}

function toggleSettingsPanel() {
  if (mainLayout) {
    if (mainLayout.classList.contains('settings-closed')) {
      openSettingsPanel();
    } else {
      closeSettingsPanel();
    }
  }
}

if (btnTopSettings) btnTopSettings.addEventListener('click', toggleSettingsPanel);
if (btnCloseSettings) btnCloseSettings.addEventListener('click', closeSettingsPanel);
if (settingsToggleBtn) settingsToggleBtn.addEventListener('click', toggleSettingsPanel);

/* ── Settings form ───────────────────────────── */
$('settingsForm').addEventListener('submit', e => {
  e.preventDefault();
  const formData = new FormData(e.target);
  const payload = {};
  for (const [k, v] of formData.entries()) {
    if (k === 'sl_enabled' || k === 'target_enabled' || k === 'time_exit_enabled') {
      payload[k] = true;
    } else {
      payload[k] = v;
    }
  }
  // checkboxes that are unchecked won't appear in FormData
  ['sl_enabled','target_enabled','time_exit_enabled'].forEach(f => {
    if (!(f in payload)) payload[f] = false;
  });
  fetch('/api/settings', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify(payload),
  }).then(r => r.json()).then(d => {
    if (d.ok) {
      $('settingsSaved').classList.remove('hidden');
      addLog('Settings saved successfully', 'info');
      // Automatically close settings panel once saved
      setTimeout(() => {
        $('settingsSaved').classList.add('hidden');
        closeSettingsPanel();
      }, 500);
    }
  });
});

// Show/hide provider sections
aiProviderSelect.addEventListener('change', () => {
  const isGemini = aiProviderSelect.value === 'gemini';
  $('geminiSection').classList.toggle('hidden', !isGemini);
  $('geminiModelGroup').classList.toggle('hidden', !isGemini);
  $('claudeSection').classList.toggle('hidden', isGemini);
});
// Initial state
aiProviderSelect.dispatchEvent(new Event('change'));

/* ── LTP poller ──────────────────────────────── */
function startLtpPoller() {
  if (ltpPollerId) return;
  ltpPollerId = setInterval(pollLtp, 3000);
  pollLtp();
}
function stopLtpPoller() {
  clearInterval(ltpPollerId);
  ltpPollerId = null;
}
function pollLtp() {
  const sym = (tickerInput.value || '').trim().toUpperCase() || 'RELIANCE';
  fetch(`/api/spot_ltp?ticker=${encodeURIComponent(sym)}`).then(r => r.json()).then(d => {
    if (d.ok) {
      livePriceBox.textContent    = `₹${d.ltp.toFixed(2)}`;
      livePriceTicker.textContent = d.spot_symbol || d.ticker;

      // ── KPI Card 1: TOTAL SPOT / FUTURES ──────────────────────
      const kpiSpot = $('kpiSpotLtp');
      if (kpiSpot) kpiSpot.textContent = `₹${d.ltp.toFixed(2)}`;

      // Real % change from prev close
      const kpiTrend = $('kpiLtpTrend');
      const kpiSub   = $('kpiFutLtp');
      if (kpiTrend && d.change_pct != null) {
        const pct    = d.change_pct;
        const absChg = d.change_abs != null ? d.change_abs : 0;
        const isUp   = pct >= 0;
        const arrow  = isUp ? '↑' : '↓';
        const cls    = isUp ? 'argon-trend text-success' : 'argon-trend text-danger';
        kpiTrend.className   = cls;
        kpiTrend.textContent = `${arrow} ${Math.abs(pct).toFixed(2)}%`;
      }
      if (kpiSub) {
        kpiSub.textContent = `${d.spot_symbol || sym} Live`;
      }
    }
  }).catch(() => {});
}

/* ── Chart backfill ──────────────────────────── */
function loadSpotChart() {
  const sym = (tickerInput.value || '').trim().toUpperCase() || 'RELIANCE';
  $('chartTicker').textContent = sym;
  fetch(`/api/spot_chart?ticker=${encodeURIComponent(sym)}`).then(r => r.json()).then(d => {
    if (d.ok && candleSeries) {
      if (d.candles && d.candles.length) {
        candleSeries.setData(d.candles);
      } else {
        candleSeries.setData([]);
      }
      $('chartTicker').textContent = d.spot_symbol || d.ticker || sym;
    }
  }).catch(() => {});
}

/* ── Reports ─────────────────────────────────── */
function loadReports() {
  fetch('/api/reports').then(r => r.json()).then(d => {
    if (!d.ok || !d.reports.length) {
      reportsList.innerHTML = '<p class="muted">No reports yet — run an analysis first.</p>';
      return;
    }
    reportsList.innerHTML = d.reports.map(r => `
      <div class="report-item" onclick="openReport('${r.filename}', '${r.ticker}')">
        <span class="report-ticker">${r.ticker}</span>
        <span class="report-meta">${r.modified} · ${(r.size/1024).toFixed(1)} KB</span>
      </div>
    `).join('');
  });
}

function openReport(filename, ticker) {
  fetch(`/api/reports/${encodeURIComponent(filename)}`)
    .then(r => r.text())
    .then(text => {
      reportViewerTitle.textContent = `TRADE-ANALYSIS-${ticker}.md`;
      reportViewerContent.textContent = text;
      reportViewer.classList.remove('hidden');
      reportViewer.scrollIntoView({behavior: 'smooth'});
    });
}

btnCloseReport.addEventListener('click', () => reportViewer.classList.add('hidden'));
btnRefreshRpts.addEventListener('click', loadReports);

/* ═══════════════════════════════════════════════
   DUAL SCANNER — UI Logic
═══════════════════════════════════════════════ */

// ── DOM refs ───────────────────────────────────
const btnScannerStart   = $('btnScannerStart');
const btnScannerStop    = $('btnScannerStop');
const scannerDot        = $('scannerDot');
const scannerStatusLbl  = $('scannerStatus');
const scannerSymCount   = $('scannerSymCount');
const scannerSignalsCard = $('scannerSignalsCard');
const scannerSignalsTbody = $('scannerSignalsTbody');
const scannerSignalCount  = $('scannerSignalCount');
const btnClearSignals   = $('btnClearSignals');
const btnQuickStartEF   = $('btnQuickStartEF');
const btnVoiceToggle    = $('btnVoiceToggle');
const scannerAllFnoChk  = $('scannerAllFno');
const scannerCustomSymDiv = $('scannerCustomSymDiv');

// Toggle custom symbol input visibility
if (scannerAllFnoChk) {
  scannerAllFnoChk.addEventListener('change', () => {
    scannerCustomSymDiv.classList.toggle('hidden', scannerAllFnoChk.checked);
  });
}

// Popup refs
const popupOverlay      = $('scannerPopupOverlay');
const popupEl           = $('scannerPopup');
const popupHeader       = $('scannerPopupHeader');
const popupTitle        = $('scannerPopupTitle');
const popupQueueBadge   = $('scannerPopupQueueBadge');
const popSymbol         = $('popSymbol');
const popFutures        = $('popFutures');
const popExpiry         = $('popExpiry');
const popTime           = $('popTime');
const popSpot           = $('popSpot');
const popFutLtp         = $('popFutLtp');
const popAtmBlock       = $('popAtmBlock');
const popAtmTitle       = $('popAtmTitle');
const popAtmSym         = $('popAtmSym');
const popAtmStrike      = $('popAtmStrike');
const popAtmExpiry      = $('popAtmExpiry');
const popAtmLtp         = $('popAtmLtp');
const popAtmLot         = $('popAtmLot');
const popConditions     = $('popConditions');
const popConditionsList = $('popConditionsList');
const popBtnTrade       = $('popBtnTrade');
const popBtnDetails     = $('popBtnDetails');
const popBtnDismiss     = $('popBtnDismiss');
const popBtnCloseIcon   = $('popBtnCloseIcon');
const popTradeConfirm   = $('popTradeConfirm');
const popTradeDetails   = $('popTradeDetails');
const popBtnConfirmOrder = $('popBtnConfirmOrder');
const popBtnCancelConfirm = $('popBtnCancelConfirm');
const popTradeModeBadge = $('popTradeModeBadge');
const popOrderLots      = $('popOrderLots');
const popOrderQtyVal    = $('popOrderQtyVal');
const popOrderFeedback  = $('popOrderFeedback');
const popOrderProduct   = $('popOrderProduct');
const popDetailedValues = $('popDetailedValues');

// ── State ──────────────────────────────────────
let _scannerRunning = false;
let _scannerSignals = [];   // all signals this session
let _popupQueue     = [];   // queued signals to show
let _currentPopupSig = null;

// ── Scanner status helpers ─────────────────────
function setScannerRunning(running, symCount, allFno) {
  _scannerRunning = running;
  if (running) {
    scannerDot.style.background = '#22c55e';
    const label = allFno
      ? `Running — All FNO (${symCount || '~90+'} symbols)`
      : `Running — ${symCount || '?'} symbol${symCount !== 1 ? 's' : ''}`;
    scannerStatusLbl.textContent = label;
    if (scannerSymCount) scannerSymCount.textContent = '';
    btnScannerStart.textContent = '🔄 Update Scanner';
    btnScannerStart.disabled = false;
    btnScannerStop.disabled  = false;
    scannerSignalsCard.classList.remove('hidden');
    const kpiScan = $('kpiScannerVal');
    if (kpiScan) kpiScan.textContent = 'SCANNING';
  } else {
    scannerDot.style.background = '#6b7280';
    scannerStatusLbl.textContent = 'Stopped';
    if (scannerSymCount) scannerSymCount.textContent = '';
    btnScannerStart.textContent = '▶ Start Scanner';
    btnScannerStart.disabled = false;
    btnScannerStop.disabled  = true;
    const kpiScan = $('kpiScannerVal');
    if (kpiScan) kpiScan.textContent = 'STOPPED';
  }
}

// ── Start / Stop buttons ───────────────────────
btnScannerStart.addEventListener('click', () => {
  const allFno    = scannerAllFnoChk ? scannerAllFnoChk.checked : true;
  const enabledA  = $('scannerToggleA') ? $('scannerToggleA').checked : false;
  const enabledB  = $('scannerToggleB') ? $('scannerToggleB').checked : false;
  const enabledC  = $('scannerToggleC') ? $('scannerToggleC').checked : false;
  const enabledD  = $('scannerToggleD') ? $('scannerToggleD').checked : false;
  const enabledE  = $('scannerToggleE') ? $('scannerToggleE').checked : true;
  const enabledF  = $('scannerToggleF') ? $('scannerToggleF').checked : false;
  const enabledG  = $('scannerToggleG') ? $('scannerToggleG').checked : true;

  let payload = { enabled_a: enabledA, enabled_b: enabledB, enabled_c: enabledC, enabled_d: enabledD, enabled_e: enabledE, enabled_f: enabledF, enabled_g: enabledG };
  if (allFno) {
    payload.all_fno = true;
  } else {
    const symbols = ($('scannerSymbols') ? $('scannerSymbols').value || '' : '')
      .split(',').map(s => s.trim()).filter(Boolean);
    payload.symbols = symbols;
  }

  fetch('/api/scanner/start', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload),
  })
    .then(r => r.json())
    .then(d => {
      if (!d.ok) { addLog('Scanner start failed: ' + d.error, 'error'); return; }
      setScannerRunning(true, null, d.all_fno);
      scannerSignalsCard.classList.remove('hidden');
      const modeLabel = d.all_fno ? 'All FNO universe' : 'custom symbols';
      addLog(`[scanner] Scanner started / updated — scanning ${modeLabel}`, 'info');
      refreshSustainedBreakoutDashboard();
      refreshGtiDashboard();
    });
});

// Dedicated Quick Start E & F Button (Always clickable!)
if (btnQuickStartEF) {
  btnQuickStartEF.addEventListener('click', () => {
    if ($('scannerToggleE')) $('scannerToggleE').checked = true;
    if ($('scannerToggleF')) $('scannerToggleF').checked = true;
    if ($('scannerToggleG')) $('scannerToggleG').checked = true;
    if (scannerAllFnoChk) scannerAllFnoChk.checked = true;
    if (scannerCustomSymDiv) scannerCustomSymDiv.classList.add('hidden');

    btnQuickStartEF.disabled = true;
    btnQuickStartEF.textContent = '⏳ Starting E, F & G...';

    const payload = {
      all_fno: true,
      enabled_a: $('scannerToggleA') ? $('scannerToggleA').checked : false,
      enabled_b: $('scannerToggleB') ? $('scannerToggleB').checked : false,
      enabled_c: $('scannerToggleC') ? $('scannerToggleC').checked : false,
      enabled_d: $('scannerToggleD') ? $('scannerToggleD').checked : false,
      enabled_e: true,
      enabled_f: true,
      enabled_g: true,
    };

    fetch('/api/scanner/start', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    })
      .then(r => r.json())
      .then(d => {
        btnQuickStartEF.disabled = false;
        btnQuickStartEF.textContent = '⚡ Start Dual Scanner (E & F)';
        if (!d.ok) { addLog('Scanner start failed: ' + d.error, 'error'); return; }
        setScannerRunning(true, null, true);
        scannerSignalsCard.classList.remove('hidden');
        addLog(`[scanner] ⚡ Dual Scanner started with Strategy E & F on All FNO Universe!`, 'info');
        refreshSustainedBreakoutDashboard();
      })
      .catch(() => {
        btnQuickStartEF.disabled = false;
        btnQuickStartEF.textContent = '⚡ Start Dual Scanner (E & F)';
      });
  });
}

btnScannerStop.addEventListener('click', () => {
  fetch('/api/scanner/stop', { method: 'POST' })
    .then(r => r.json())
    .then(d => {
      if (!d.ok) { addLog('Scanner stop failed: ' + d.error, 'error'); return; }
      setScannerRunning(false);
      addLog('[scanner] Scanner stopped', 'info');
    });
});

// ── Clear signals ──────────────────────────────
btnClearSignals.addEventListener('click', () => {
  _scannerSignals = [];
  scannerSignalsTbody.innerHTML =
    '<tr><td colspan="8" class="text-center muted">Cleared — scanner still running...</td></tr>';
  scannerSignalCount.textContent = '0 signals';
  fetch('/api/scanner/clear', { method: 'POST' }).catch(() => {});
});

// ── Alert audio chime (Web Audio API) ─────────
function _playAlertSound(isBull) {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.type = 'sine';
    osc.frequency.setValueAtTime(isBull ? 880 : 440, ctx.currentTime);
    osc.frequency.exponentialRampToValueAtTime(isBull ? 1320 : 330, ctx.currentTime + 0.28);
    gain.gain.setValueAtTime(0.18, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.28);
    osc.start();
    osc.stop(ctx.currentTime + 0.28);
  } catch (e) {}
}

// ── Voice Alert Announcement (Web Speech API) ──────────────────
let _voiceEnabled = true;
let _voices = [];
function _loadVoices() {
  if ('speechSynthesis' in window) {
    _voices = window.speechSynthesis.getVoices();
  }
}
if ('speechSynthesis' in window) {
  _loadVoices();
  window.speechSynthesis.onvoiceschanged = _loadVoices;
}

if (btnVoiceToggle) {
  btnVoiceToggle.addEventListener('click', () => {
    _voiceEnabled = !_voiceEnabled;
    btnVoiceToggle.textContent = _voiceEnabled ? '🔊 Voice: ON' : '🔇 Voice: OFF';
    btnVoiceToggle.className = 'btn btn-sm ' + (_voiceEnabled ? 'btn-secondary' : 'btn-danger');
    if (!_voiceEnabled && window.speechSynthesis) {
      window.speechSynthesis.cancel();
    }
  });
}

function speakSignal(sig) {
  if (!_voiceEnabled || !('speechSynthesis' in window) || !sig) return;
  try {
    window.speechSynthesis.cancel(); // Stop any pending utterance
    const isBull = sig.direction === 'BULLISH';
    const sym = sig.symbol || 'Stock';
    const action = isBull ? 'Buy' : 'Sell';
    const stratName = sig.strategy_id === 'E' ? 'Smart Money' : (sig.strategy_id === 'F' ? 'Breakout' : (sig.strategy_name || ''));

    // Speak clearly: e.g. "Buy DATAPATTNS! Smart Money" or "Sell NIFTY! Breakout"
    const text = `${action} ${sym}! ${stratName}`;
    const utterance = new SpeechSynthesisUtterance(text);

    if (_voices.length === 0) _loadVoices();
    const voices = _voices || [];

    if (isBull) {
      // Distinct Female / bright voice for BUY
      const femaleVoice = voices.find(v => /female|zira|samantha|karen|victoria|google.*female/i.test(v.name))
                       || voices.find(v => /en[-_]US|en[-_]GB/i.test(v.lang))
                       || voices[0];
      if (femaleVoice) utterance.voice = femaleVoice;
      utterance.pitch = 1.25;
      utterance.rate = 1.05;
    } else {
      // Distinct Male / deeper voice for SELL
      const maleVoice = voices.find(v => /male|david|george|alex|daniel|google.*male/i.test(v.name))
                     || voices.slice().reverse().find(v => /en[-_]US|en[-_]GB/i.test(v.lang))
                     || voices[1] || voices[0];
      if (maleVoice) utterance.voice = maleVoice;
      utterance.pitch = 0.75;
      utterance.rate = 0.98;
    }

    window.speechSynthesis.speak(utterance);
  } catch (err) {
    console.warn('[speech] voice synthesis error:', err);
  }
}

// ── Socket events ──────────────────────────────
socket.on('scanner_signal', sig => {
  _scannerSignals.push(sig);
  _renderSignalRow(sig);
  _playAlertSound(sig.direction === 'BULLISH');
  speakSignal(sig);
  _queuePopup(sig);
  addLog(`[scanner] 🔔 ${sig.direction} signal: ${sig.symbol} (${sig.strategy_name})`, 'info');
});

socket.on('scanner_signal_reversed', data => {
  const sym = data.symbol;
  const oldDir = data.old_direction;
  const stratId = data.strategy_id;

  // Clear reversed rows from the signals table
  const rows = scannerSignalsTbody.querySelectorAll(`tr[data-symbol="${sym}"]`);
  rows.forEach(r => {
    if (oldDir === 'ALL' || r.dataset.dir === oldDir) {
      if (!stratId || r.dataset.stratId === stratId) {
        r.remove();
      }
    }
  });

  // Filter out reversed signals from memory
  _scannerSignals = _scannerSignals.filter(s => {
    const mSym = s.symbol === sym;
    const mDir = (oldDir === 'ALL' || s.direction === oldDir);
    const mStrat = (!stratId || s.strategy_id === stratId);
    return !(mSym && mDir && mStrat);
  });

  if (scannerSignalCount) {
    scannerSignalCount.textContent = `${_scannerSignals.length} signal${_scannerSignals.length !== 1 ? 's' : ''}`;
  }
  addLog(`[scanner] 🔄 Signal reversed for ${sym} (${oldDir} → ${data.new_direction}). Cleared from active list.`, 'info');
});

socket.on('scanner_status', d => {
  setScannerRunning(d.running, d.symbol_count, d.all_fno);
});

// ── Signal table renderer ──────────────────────
function _fmtTime(ts) {
  if (!ts) return '—';
  try {
    if (typeof ts === 'string') {
      if (ts.length === 10 || ts.includes('00:00:00')) {
        return new Date().toLocaleTimeString('en-IN', {hour:'2-digit',minute:'2-digit',second:'2-digit'});
      }
    }
    const d = new Date(ts);
    if (isNaN(d.getTime())) return ts;
    // Guard against pure midnight timestamp (e.g. from daily candle date 00:00:00)
    if (d.getHours() === 0 && d.getMinutes() === 0 && d.getSeconds() === 0) {
      return new Date().toLocaleTimeString('en-IN', {hour:'2-digit',minute:'2-digit',second:'2-digit'});
    }
    return d.toLocaleTimeString('en-IN', {hour:'2-digit',minute:'2-digit',second:'2-digit'});
  }
  catch { return ts; }
}

function _renderSignalRow(sig) {
  const isBull = sig.direction === 'BULLISH';
  const rowCls = isBull ? 'scanner-row-bull' : 'scanner-row-bear';
  const dirCls = isBull ? 'scanner-dir-bull' : 'scanner-dir-bear';
  const dirIcon = isBull ? '▲ BULLISH' : '▼ BEARISH';
  const stratCls = sig.strategy_id === 'A' ? 'scanner-strategy-a' : (sig.strategy_id === 'B' ? 'scanner-strategy-b' : (sig.strategy_id === 'C' ? 'scanner-strategy-c' : (sig.strategy_id === 'D' ? 'scanner-strategy-d' : (sig.strategy_id === 'F' ? 'scanner-strategy-f' : 'scanner-strategy-e'))));
  const stratLabel = `<span class="badge ${stratCls}">${sig.strategy_id}: ${sig.strategy_name}</span>`;
  const atmLabel = sig.atm_option
    ? `<span class="scanner-atm-tag">${sig.atm_option.option_type} ${sig.atm_option.strike} @ ₹${sig.atm_option.ltp ?? '—'}</span>`
    : '<span class="muted small">—</span>';
  const viewBtn = `<button class="btn btn-secondary btn-sm" onclick="showPopupForSig('${sig.id}')">View</button>`;

  // 1. Reversal clearing: if this symbol has an active row with opposite direction, remove it!
  const rowsForSym = scannerSignalsTbody.querySelectorAll(`tr[data-symbol="${sig.symbol}"]`);
  rowsForSym.forEach(r => {
    if (r.dataset.dir && r.dataset.dir !== sig.direction) {
      r.remove();
    }
  });
  _scannerSignals = _scannerSignals.filter(s => !(s.symbol === sig.symbol && s.direction !== sig.direction));

  // 2. In-table deduplication: if this symbol & strategy is already displayed in same direction, update in-place
  const dedupKey = `${sig.strategy_id}_${sig.symbol}`;
  const existingRow = scannerSignalsTbody.querySelector(`tr[data-sym-strat="${dedupKey}"]`);
  if (existingRow && existingRow.dataset.dir === sig.direction) {
    const spotCell = existingRow.children[5];
    if (spotCell) spotCell.textContent = `₹${sig.spot_price ? sig.spot_price.toFixed(2) : '—'}`;
    const timeCell = existingRow.children[0];
    if (timeCell) timeCell.textContent = _fmtTime(sig.timestamp);
    if (sig.atm_option) {
      const atmCell = existingRow.children[6];
      if (atmCell) atmCell.innerHTML = `<span class="scanner-atm-tag">${sig.atm_option.option_type} ${sig.atm_option.strike} @ ₹${sig.atm_option.ltp ?? '—'}</span>`;
    }
    return;
  }
  if (existingRow) {
    existingRow.remove();
  }

  // Remove empty placeholder row
  const placeholder = scannerSignalsTbody.querySelector('td[colspan]');
  if (placeholder) placeholder.closest('tr').remove();

  const tr = document.createElement('tr');
  tr.className = rowCls;
  tr.dataset.sigId = sig.id;
  tr.dataset.symStrat = dedupKey;
  tr.dataset.symbol = sig.symbol;
  tr.dataset.dir = sig.direction;
  tr.dataset.stratId = sig.strategy_id;
  tr.innerHTML = `
    <td class="muted small">${_fmtTime(sig.timestamp)}</td>
    <td>${stratLabel}</td>
    <td class="${dirCls}">${dirIcon}</td>
    <td><strong>${sig.symbol}</strong></td>
    <td class="muted small">${sig.futures_symbol || '—'}</td>
    <td>₹${sig.spot_price ? sig.spot_price.toFixed(2) : '—'}</td>
    <td>${atmLabel}</td>
    <td>${viewBtn}</td>
  `;
  // Prepend newest on top
  scannerSignalsTbody.insertBefore(tr, scannerSignalsTbody.firstChild);
  if (scannerSignalCount) {
    scannerSignalCount.textContent = `${_scannerSignals.length} signal${_scannerSignals.length !== 1 ? 's' : ''}`;
  }

  // If futures_symbol or atm_option is missing, resolve asynchronously
  if (!sig.atm_option || !sig.futures_symbol) {
    const optType = isBull ? 'CE' : 'PE';
    fetch(`/api/scanner/resolve_atm?symbol=${encodeURIComponent(sig.symbol)}&option_type=${optType}&spot_price=${sig.spot_price || ''}`)
      .then(r => r.json())
      .then(d => {
        if (d.ok && d.atm_option) {
          sig.atm_option = d.atm_option;
          if (d.atm_option.futures_symbol) sig.futures_symbol = d.atm_option.futures_symbol;
          if (d.atm_option.expiry) sig.expiry = d.atm_option.expiry;
          const row = document.querySelector(`tr[data-sig-id="${sig.id}"]`);
          if (row) {
            const futCell = row.children[4];
            if (futCell && (futCell.textContent.trim() === '—' || !futCell.textContent.trim()) && sig.futures_symbol) {
              futCell.textContent = sig.futures_symbol;
            }
            const atmCell = row.children[6];
            if (atmCell) {
              atmCell.innerHTML = `<span class="scanner-atm-tag">${d.atm_option.option_type} ${d.atm_option.strike} @ ₹${d.atm_option.ltp ?? '—'}</span>`;
            }
          }
        }
      })
      .catch(() => {});
  }
}

// Dynamic real-time signals synchronization
async function syncLiveSignals() {
  if (!_scannerRunning) return;
  try {
    const res = await fetch('/api/scanner/signals');
    const data = await res.json();
    if (!data.ok || !data.signals) return;

    const activeMap = new Map();
    data.signals.forEach(s => activeMap.set(`${s.strategy_id}_${s.symbol}`, s));

    // Remove rows that no longer exist or were reversed
    const rows = scannerSignalsTbody.querySelectorAll('tr[data-sym-strat]');
    rows.forEach(r => {
      const key = r.dataset.symStrat;
      if (!activeMap.has(key)) {
        r.remove();
      }
    });

    data.signals.forEach(sig => {
      _renderSignalRow(sig);
    });

    _scannerSignals = data.signals;
    if (scannerSignalCount) {
      scannerSignalCount.textContent = `${_scannerSignals.length} signal${_scannerSignals.length !== 1 ? 's' : ''}`;
    }
    const kpiCount = $('kpiSignalsCount');
    if (kpiCount) {
      kpiCount.textContent = _scannerSignals.length > 0 ? `${_scannerSignals.length} Signals Active` : 'All FNO Universe';
    }
  } catch (e) {}
}
setInterval(syncLiveSignals, 5000);

// Allow table view button to reopen popup
window.showPopupForSig = function(sigId) {
  const sig = _scannerSignals.find(s => s.id === sigId);
  if (sig) _showPopup(sig);
};

// ── Popup queue ────────────────────────────────
function _queuePopup(sig) {
  if (!_currentPopupSig) {
    _showPopup(sig);
  } else {
    _popupQueue.push(sig);
    const q = _popupQueue.length;
    popupQueueBadge.textContent = `+${q} queued`;
    popupQueueBadge.classList.remove('hidden');
  }
}

function _nextPopup() {
  if (_popupQueue.length === 0) {
    _currentPopupSig = null;
    popupOverlay.classList.add('hidden');
    popupQueueBadge.classList.add('hidden');
    return;
  }
  const next = _popupQueue.shift();
  if (_popupQueue.length === 0) popupQueueBadge.classList.add('hidden');
  else { popupQueueBadge.textContent = `+${_popupQueue.length} queued`; }
  _showPopup(next);
}

// ── Render popup ───────────────────────────────
function _showPopup(sig) {
  _currentPopupSig = sig;
  const isBull = sig.direction === 'BULLISH';

  // Header style
  popupHeader.className = 'scanner-popup-header ' + (isBull ? 'bullish-header' : 'bearish-header');
  if (sig.strategy_id === 'E') {
    popupTitle.textContent = isBull
      ? '🟢 BOTTOM ACCUMULATION DETECTED — BUY FUTURES'
      : '🔴 TOP DISTRIBUTION DETECTED — SELL FUTURES';
  } else if (sig.strategy_id === 'D') {
    const act = (sig.action || (isBull ? 'BUY' : 'SELL'));
    popupTitle.textContent = isBull
      ? `🟢 BULLISH SIGNAL — ${act}`
      : `🔴 BEARISH SIGNAL — ${act}`;
  } else {
    popupTitle.textContent = isBull
      ? '🟢 BULLISH FUTURES SETUP DETECTED'
      : '🔴 FULLY BEARISH FUTURES SETUP DETECTED';
  }
  popupTitle.style.color = '#ffffff';

  // Meta
  popSymbol.textContent   = sig.symbol || '—';
  popFutures.textContent  = sig.futures_symbol || '—';
  popExpiry.textContent   = sig.expiry || '—';
  popTime.textContent     = _fmtTime(sig.timestamp);
  popSpot.textContent     = sig.spot_price ? `₹${sig.spot_price.toFixed(2)}` : '—';
  popFutLtp.textContent   = sig.futures_price ? `₹${sig.futures_price.toFixed(2)}` : '—';

  // ATM block
  function _renderAtm(atm) {
    if (!atm) return;
    popAtmTitle.textContent = isBull ? 'BUY ATM CALL (CE)' : 'BUY ATM PUT (PE)';
    popAtmSym.textContent    = atm.symbol || '—';
    popAtmStrike.textContent = atm.strike ? `₹${atm.strike}` : '—';
    popAtmExpiry.textContent = atm.expiry || '—';
    popAtmLtp.textContent    = atm.ltp != null ? `₹${atm.ltp.toFixed ? atm.ltp.toFixed(2) : atm.ltp}` : '—';
    popAtmLot.textContent    = atm.lot_size || '—';
    popAtmBlock.className    = 'popup-atm-card mt-12 ' + (isBull ? 'bull-atm' : 'bear-atm');
    popAtmBlock.classList.remove('hidden');

    // Update futures and expiry in popup if they were missing
    if (atm.futures_symbol && (!popFutures.textContent || popFutures.textContent.trim() === '—')) {
      popFutures.textContent = atm.futures_symbol;
      sig.futures_symbol = atm.futures_symbol;
    }
    if (atm.expiry && (!popExpiry.textContent || popExpiry.textContent.trim() === '—')) {
      popExpiry.textContent = atm.expiry;
      sig.expiry = atm.expiry;
    }

    // Update row in live signals table
    const row = document.querySelector(`tr[data-sig-id="${sig.id}"]`);
    if (row) {
      const futCell = row.children[4];
      if (futCell && (futCell.textContent.trim() === '—' || !futCell.textContent.trim()) && sig.futures_symbol) {
        futCell.textContent = sig.futures_symbol;
      }
      const atmCell = row.children[6];
      if (atmCell) {
        atmCell.innerHTML = `<span class="scanner-atm-tag">${atm.option_type} ${atm.strike} @ ₹${atm.ltp ?? '—'}</span>`;
      }
    }
  }

  if (sig.atm_option) {
    _renderAtm(sig.atm_option);
  } else {
    popAtmBlock.classList.add('hidden');
    // Attempt dynamic resolution on-the-fly
    const optType = isBull ? 'CE' : 'PE';
    fetch(`/api/scanner/resolve_atm?symbol=${encodeURIComponent(sig.symbol)}&option_type=${optType}&spot_price=${sig.spot_price || ''}`)
      .then(r => r.json())
      .then(d => {
        if (d.ok && d.atm_option) {
          sig.atm_option = d.atm_option;
          _renderAtm(d.atm_option);
        }
      })
      .catch(() => {});
  }

  // Trade button label
  popBtnTrade.textContent = isBull ? '🟢 BUY ATM CE' : '🔴 BUY ATM PE';
  popBtnTrade.className = isBull ? 'btn btn-primary btn-trade-main' : 'btn btn-danger btn-trade-main';

  // Render conditions summary cleanly (avoids blank "Conditions" label)
  let condItems = [];
  if (sig.conditions && Object.keys(sig.conditions).length > 0) {
    condItems = Object.entries(sig.conditions).map(([k, v]) => {
      const cls = v ? 'cond-pass' : 'cond-fail';
      const icon = v ? '✓' : '✗';
      const label = k.replace(/_/g, ' ').replace(/^[a-z]\d+\s/i, '');
      return `<span class="cond-pill ${cls}">${icon} ${label}</span>`;
    });
  } else if (sig.values && sig.values.reasons_for && sig.values.reasons_for.length > 0) {
    condItems = sig.values.reasons_for.map(r => `<span class="cond-pill cond-pass">✓ ${r}</span>`);
    if (sig.values.reasons_against && sig.values.reasons_against.length > 0) {
      sig.values.reasons_against.forEach(r => condItems.push(`<span class="cond-pill cond-fail">✗ ${r}</span>`));
    }
  } else if (sig.category || sig.signal_name) {
    condItems.push(`<span class="cond-pill cond-pass">✓ ${sig.category || sig.signal_name}</span>`);
    if (sig.score) condItems.push(`<span class="cond-pill cond-pass">★ Confluence: ${sig.score}/100</span>`);
  }

  if (condItems.length > 0) {
    popConditionsList.innerHTML = condItems.join('');
    popConditions.classList.remove('hidden');
  } else {
    popConditions.classList.add('hidden');
  }

  if (popDetailedValues) {
    popDetailedValues.classList.add('hidden');
    popDetailedValues.innerHTML = '';
  }
  if (popBtnDetails) {
    popBtnDetails.textContent = 'VIEW DETAILS';
  }

  popTradeConfirm.classList.add('hidden');

  popupOverlay.classList.remove('hidden');
  speakSignal(sig);
}

// ── Popup action buttons ───────────────────────
function updateOrderQtyDisplay() {
  const sig = _currentPopupSig;
  const atm = sig ? sig.atm_option : null;
  const lotSize = (atm && atm.lot_size) ? atm.lot_size : 1;
  const lots = parseInt(popOrderLots ? popOrderLots.value : 1, 10) || 1;
  if (popOrderQtyVal) {
    popOrderQtyVal.textContent = (lots * lotSize) + (atm && atm.lot_size ? ` (${lots} lot${lots > 1 ? 's' : ''})` : '');
  }
}

if (popOrderLots) {
  popOrderLots.addEventListener('input', updateOrderQtyDisplay);
}

function renderConfirmDetails(sig, atm, isBull) {
  const prod = popOrderProduct ? popOrderProduct.value : 'MIS';
  let html = `<strong>Underlying:</strong> ${sig.symbol}<br>`;
  html += `<strong>Option Type:</strong> ${isBull ? 'CE (Call)' : 'PE (Put)'}<br>`;
  if (atm && atm.symbol) {
    html += `<strong>Contract:</strong> ${atm.symbol}<br>`;
    html += `<strong>Strike:</strong> ₹${atm.strike}<br>`;
    html += `<strong>Expiry:</strong> ${atm.expiry}<br>`;
    html += `<strong>LTP:</strong> ${atm.ltp != null ? '₹' + (atm.ltp.toFixed ? atm.ltp.toFixed(2) : atm.ltp) : '—'}<br>`;
    html += `<strong>Lot Size:</strong> ${atm.lot_size || '—'}<br>`;
    html += `<strong>Spot at Signal:</strong> ₹${sig.spot_price ? sig.spot_price.toFixed(2) : '—'}<br>`;
    html += `<br><em class="muted small">Product: <strong>${prod}</strong> (${prod === 'MIS' ? 'Intraday' : 'Normal'}). Click Execute to place.</em>`;
  } else {
    html += `<br><em style="color:#ef4444;">Could not resolve ATM option contract for this symbol.</em>`;
  }
  popTradeDetails.innerHTML = html;
}

if (popOrderProduct) {
  popOrderProduct.addEventListener('change', () => {
    if (_currentPopupSig) {
      renderConfirmDetails(_currentPopupSig, _currentPopupSig.atm_option, _currentPopupSig.direction === 'BULLISH');
    }
  });
}

popBtnTrade.addEventListener('click', async () => {
  const sig = _currentPopupSig;
  if (!sig) return;
  const isBull = sig.direction === 'BULLISH';
  let atm = sig.atm_option;

  // Update mode badge
  const isLive = modeToggle && modeToggle.checked;
  if (popTradeModeBadge) {
    popTradeModeBadge.className = 'mode-tag ' + (isLive ? 'mode-live' : 'mode-dry');
    popTradeModeBadge.textContent = isLive ? '⚡ LIVE ORDER' : 'DRY RUN';
  }

  if (popOrderLots) popOrderLots.value = '1';
  if (popOrderFeedback) {
    popOrderFeedback.classList.add('hidden');
    popOrderFeedback.textContent = '';
  }

  popTradeConfirm.classList.remove('hidden');
  popBtnTrade.classList.add('hidden');

  // If ATM option was not resolved earlier, resolve it now with loading state
  if (!atm) {
    popTradeDetails.innerHTML = `<strong>Underlying:</strong> ${sig.symbol}<br><strong>Option Type:</strong> ${isBull ? 'CE (Call)' : 'PE (Put)'}<br><br><em>⏳ Resolving ATM option contract...</em>`;
    if (popBtnConfirmOrder) {
      popBtnConfirmOrder.disabled = true;
      popBtnConfirmOrder.textContent = 'Resolving Contract...';
    }
    const optType = isBull ? 'CE' : 'PE';
    try {
      const res = await fetch(`/api/scanner/resolve_atm?symbol=${encodeURIComponent(sig.symbol)}&option_type=${optType}&spot_price=${sig.spot_price || ''}`);
      const data = await res.json();
      if (data.ok && data.atm_option) {
        atm = data.atm_option;
        sig.atm_option = atm;
        // update ATM block on popup
        popAtmSym.textContent    = atm.symbol || '—';
        popAtmStrike.textContent = atm.strike ? `₹${atm.strike}` : '—';
        popAtmExpiry.textContent = atm.expiry || '—';
        popAtmLtp.textContent    = atm.ltp != null ? `₹${atm.ltp.toFixed ? atm.ltp.toFixed(2) : atm.ltp}` : '—';
        popAtmLot.textContent    = atm.lot_size || '—';
        popAtmBlock.classList.remove('hidden');
        if (popExpiry && popExpiry.textContent === '—') {
          popExpiry.textContent = atm.expiry || '—';
        }
      }
    } catch (e) {}
  }

  renderConfirmDetails(sig, atm, isBull);
  updateOrderQtyDisplay();

  if (atm && atm.symbol) {
    if (popBtnConfirmOrder) {
      popBtnConfirmOrder.disabled = false;
      popBtnConfirmOrder.textContent = '🚀 Execute Order';
    }
  } else {
    if (popBtnConfirmOrder) {
      popBtnConfirmOrder.disabled = true;
      popBtnConfirmOrder.textContent = 'Unavailable';
    }
  }
});

popBtnDetails.addEventListener('click', () => {
  const sig = _currentPopupSig;
  if (!sig || !popDetailedValues) return;

  const isHidden = popDetailedValues.classList.contains('hidden');
  if (isHidden) {
    const v = sig.values || {};
    let html = '';

    if (sig.strategy_id === 'E') {
      const b = v.breakdown || {};
      const sma = v.sma_data || {};
      const obv = v.obv_data || {};
      const vol = v.vol_data || {};
      const z = v.zone_data || {};
      const fut = v.futures_data || {};
      html += `<div style="font-weight:700;margin-bottom:6px;color:#facc15;">🏦 Strategy E: Smart Money Confluence (${v.score || 0}/100) — ${v.category || '—'}</div>`;
      html += `<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:11px;">`;
      html += `<div><strong>Score:</strong> <span style="font-weight:700;color:${v.score >= 80 ? '#22c55e' : (v.score >= 65 ? '#facc15' : '#ef4444')}">${v.score} / 100 (${v.category || '—'})</span></div>`;
      html += `<div><strong>Signal:</strong> <strong>${v.signal_name || 'WAIT'}</strong></div>`;
      html += `<div><strong>SMA Structure (${b.sma_structure || 0}/15):</strong> 50: ₹${sma.sma50 || '—'} | 100: ₹${sma.sma100 || '—'} | 200: ₹${sma.sma200 || '—'}</div>`;
      html += `<div><strong>OBV Flow (${b.obv_accumulation || 0}/15):</strong> Trend: ${obv.obv_trend || '—'} | Div: ${obv.divergence || 'NONE'}</div>`;
      html += `<div><strong>Vol Expansion (${b.volume_expansion || 0}/10):</strong> 5-bar ratio: ${vol.expansion_ratio || 1}x</div>`;
      html += `<div><strong>Supply/Demand (${b.zone_confluence || 0}/15):</strong> Demand: ₹${z.demand_level || '—'} | Supply: ₹${z.supply_level || '—'}</div>`;
      html += `<div><strong>Futures Confirmation (${b.equity_futures || 0}/15):</strong> Basis: ₹${fut.basis || 0} (${fut.vwap_alignment || 'NEUTRAL'})</div>`;
      html += `<div><strong>Supertrend (${b.supertrend || 0}/10):</strong> ${v.supertrend?.trend || '—'} (₹${v.supertrend?.val || '—'})</div>`;
      html += `<div><strong>Order Flow & OI (${b.order_flow_oi || 0}/20):</strong> ${fut.buildup || '—'} (${fut.order_flow_status || 'UNAVAILABLE'})</div>`;
      html += `<div><strong>Target & Risk:</strong> Entry: ₹${v.entry_price || '—'} | SL: ₹${v.sl || '—'} | T1: ₹${v.t1 || '—'} | T2: ₹${v.t2 || '—'} (${v.risk_reward || '—'})</div>`;
      html += `</div>`;
    } else if (sig.strategy_id === 'D') {
      html += `<div style="font-weight:700;margin-bottom:6px;color:#38bdf8;">📊 Strategy D: EMA 50/150/200 Trend & Reversal (Swing)</div>`;
      html += `<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:11px;">`;
      html += `<div><strong>Trend:</strong> <span style="font-weight:700;color:${sig.direction === 'BULLISH' ? '#22c55e' : '#ef4444'}">${v.trend_desc || (sig.direction === 'BULLISH' ? 'ABOVE 50/150/200 EMA' : 'BELOW 50/150/200 EMA')}</span></div>`;
      html += `<div><strong>Signal Action:</strong> <strong style="color:${sig.direction === 'BULLISH' ? '#22c55e' : '#ef4444'}">${v.action || (sig.direction === 'BULLISH' ? 'BUY' : 'SELL')}</strong></div>`;
      html += `<div><strong>Signal Type:</strong> <span class="badge" style="background:rgba(2,132,199,0.2);color:#38bdf8;">${v.signal_type || 'ENTRY'}</span></div>`;
      html += `<div><strong>Last Close:</strong> ₹${v.close != null ? v.close.toFixed(2) : '—'}</div>`;
      html += `<div><strong>50 EMA:</strong> ₹${v.ema50 != null ? v.ema50.toFixed(2) : '—'}</div>`;
      html += `<div><strong>150 EMA:</strong> ₹${v.ema150 != null ? v.ema150.toFixed(2) : '—'}</div>`;
      html += `<div><strong>200 EMA:</strong> ₹${v.ema200 != null ? v.ema200.toFixed(2) : '—'}</div>`;
      html += `<div><strong>Prev Day High:</strong> ₹${v.prev_high != null ? v.prev_high.toFixed(2) : '—'}</div>`;
      html += `<div><strong>Prev Day Low:</strong> ₹${v.prev_low != null ? v.prev_low.toFixed(2) : '—'}</div>`;
      html += `<div><strong>State Transition:</strong> ${v.prev_state || 'NEUTRAL'} ➔ <strong>${v.curr_state || sig.direction}</strong></div>`;
      html += `</div>`;
      if (sig.direction === 'BULLISH') {
        html += `<div style="margin-top:6px;color:#22c55e;font-size:11px;font-weight:600;">✓ Price (${v.close?.toFixed(2)}) > 50/150/200 EMA + Sustained above Prev Day High (${v.prev_high?.toFixed(2)})</div>`;
      } else {
        html += `<div style="margin-top:6px;color:#ef4444;font-size:11px;font-weight:600;">✓ Price (${v.close?.toFixed(2)}) < 50/150/200 EMA + Sustained below Prev Day Low (${v.prev_low?.toFixed(2)})</div>`;
      }
    } else if (sig.strategy_id === 'C') {
      html += `<div style="font-weight:700;margin-bottom:6px;color:#a78bfa;">📊 Strategy C: Trend + Supertrend Sustained Setup</div>`;
      html += `<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:11px;">`;
      html += `<div><strong>15m Close:</strong> ₹${v.close != null ? v.close.toFixed(2) : '—'}</div>`;
      html += `<div><strong>Current Open:</strong> ₹${v.open != null ? v.open.toFixed(2) : '—'}</div>`;
      html += `<div><strong>Supertrend (${v.supertrend_period || 7}, ${v.supertrend_mult || 3}):</strong> ₹${v.supertrend != null ? v.supertrend.toFixed(2) : '—'} <span style="font-weight:700;color:${v.supertrend_trend === 'UP' ? '#22c55e' : '#ef4444'}">(${v.supertrend_trend || '—'})</span></div>`;
      html += `<div><strong>Sustained Trend:</strong> ${v.sustained_candles != null ? v.sustained_candles + ' candle(s)' : '—'} (min: ${v.min_sustain_candles || 1})</div>`;
      html += `<div><strong>EMA 20:</strong> ₹${v.ema20 != null ? v.ema20.toFixed(2) : '—'}</div>`;
      html += `<div><strong>EMA 50:</strong> ₹${v.ema50 != null ? v.ema50.toFixed(2) : '—'}</div>`;
      html += `<div><strong>EMA 100:</strong> ₹${v.ema100 != null ? v.ema100.toFixed(2) : '—'}</div>`;
      html += `<div><strong>Signal Ref Price:</strong> ₹${v.ref_price != null ? v.ref_price.toFixed(2) : '—'} (${v.confirm_level || 'high'})</div>`;
      html += `</div>`;
      if (sig.direction === 'BULLISH') {
        html += `<div style="margin-top:6px;color:#22c55e;font-size:11px;font-weight:600;">✓ Price (${v.close?.toFixed(2)}) > EMAs + Supertrend BUY sustained (${v.sustained_candles}c) + Open (${v.open?.toFixed(2)}) > Signal Candle (${v.ref_price?.toFixed(2)})</div>`;
      } else {
        html += `<div style="margin-top:6px;color:#ef4444;font-size:11px;font-weight:600;">✓ Price (${v.close?.toFixed(2)}) < EMAs + Supertrend SELL sustained (${v.sustained_candles}c) + Open (${v.open?.toFixed(2)}) < Signal Candle (${v.ref_price?.toFixed(2)})</div>`;
      }
    } else if (sig.strategy_id === 'A') {
      html += `<div style="font-weight:700;margin-bottom:6px;color:#22c55e;">📊 Strategy A: Volume & Breakout Calculations</div>`;
      html += `<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:11px;">`;
      html += `<div><strong>Spot Price:</strong> ₹${v.spot_price != null ? v.spot_price.toFixed(2) : (sig.spot_price != null ? sig.spot_price.toFixed(2) : '—')}</div>`;
      html += `<div><strong>Today Open:</strong> ₹${v.today_open != null ? v.today_open.toFixed(2) : '—'}</div>`;
      html += `<div><strong>5m Volume:</strong> ${v.vol_5m ? v.vol_5m.toLocaleString() : '—'}</div>`;
      html += `<div><strong>Avg 5m Vol:</strong> ${v.vol_avg ? Math.round(v.vol_avg).toLocaleString() : '—'}</div>`;
      html += `<div><strong>Rel Vol Ratio:</strong> ${v.rel_vol != null ? v.rel_vol.toFixed(2) + 'x' : '—'}</div>`;
      html += `<div><strong>Today Range:</strong> ₹${v.today_range != null ? v.today_range.toFixed(2) : '—'}</div>`;
      html += `<div><strong>Avg Day Range (ADR):</strong> ₹${v.adr != null ? v.adr.toFixed(2) : '—'}</div>`;
      html += `<div><strong>Range Expansion:</strong> ${v.range_pct != null ? v.range_pct.toFixed(2) + '%' : '—'}</div>`;
      html += `<div><strong>Gap:</strong> ${v.gap_pts != null ? (v.gap_pts >= 0 ? '+' : '') + v.gap_pts.toFixed(2) + ' pts' : '—'}</div>`;
      html += `<div><strong>Pivot R1:</strong> ₹${v.r1 != null ? v.r1.toFixed(2) : '—'}</div>`;
      html += `<div><strong>Pivot:</strong> ₹${v.pivot != null ? v.pivot.toFixed(2) : '—'}</div>`;
      html += `<div><strong>Pivot S1:</strong> ₹${v.s1 != null ? v.s1.toFixed(2) : '—'}</div>`;
      html += `</div>`;
    } else if (sig.strategy_id === 'B') {
      html += `<div style="font-weight:700;margin-bottom:6px;color:#ef4444;">📊 Strategy B: Trend & Indicator Calculations</div>`;
      html += `<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:11px;">`;
      html += `<div><strong>Daily Volume:</strong> ${v.daily_volume ? v.daily_volume.toLocaleString() : '—'}</div>`;
      html += `<div><strong>Daily Close:</strong> ₹${v.daily_close != null ? v.daily_close.toFixed(2) : '—'}</div>`;
      html += `<div><strong>Daily Supertrend:</strong> ₹${v.st_daily != null ? v.st_daily.toFixed(2) : '—'} (${v.st_daily_trend || '—'})</div>`;
      html += `<div><strong>15m Supertrend:</strong> ₹${v.st_15m != null ? v.st_15m.toFixed(2) : '—'} (${v.st_15m_trend || '—'})</div>`;
      html += `<div><strong>+DI:</strong> ${v.plus_di != null ? v.plus_di.toFixed(2) : '—'} (Threshold: ${v.c7_threshold != null ? v.c7_threshold : 24})</div>`;
      html += `<div><strong>-DI:</strong> ${v.minus_di != null ? v.minus_di.toFixed(2) : '—'}</div>`;
      html += `<div><strong>5m Supertrend:</strong> ₹${v.st_5m != null ? v.st_5m.toFixed(2) : '—'} (${v.st_5m_trend || '—'})</div>`;
      html += `</div>`;
    } else {
      html += `<pre style="margin:0;font-size:11px;">${JSON.stringify(v, null, 2)}</pre>`;
    }

    popDetailedValues.innerHTML = html;
    popDetailedValues.classList.remove('hidden');
    popBtnDetails.textContent = 'HIDE DETAILS';
  } else {
    popDetailedValues.classList.add('hidden');
    popBtnDetails.textContent = 'VIEW DETAILS';
  }
});

popBtnDismiss.addEventListener('click', () => {
  popBtnTrade.classList.remove('hidden');
  _nextPopup();
});

popBtnConfirmOrder.addEventListener('click', async () => {
  const sig = _currentPopupSig;
  if (!sig) return;
  let atm = sig.atm_option;

  // On-demand resolution fallback
  if (!atm) {
    const optType = sig.direction === 'BULLISH' ? 'CE' : 'PE';
    try {
      const res = await fetch(`/api/scanner/resolve_atm?symbol=${encodeURIComponent(sig.symbol)}&option_type=${optType}&spot_price=${sig.spot_price || ''}`);
      const data = await res.json();
      if (data.ok && data.atm_option) {
        atm = data.atm_option;
        sig.atm_option = atm;
      }
    } catch (e) {}
  }

  if (!atm || !atm.symbol) {
    addLog(`[scanner] ATM contract not available for ${sig.symbol}. Cannot execute order.`, 'error');
    if (popOrderFeedback) {
      popOrderFeedback.className = 'cond-pill cond-fail';
      popOrderFeedback.classList.remove('hidden');
      popOrderFeedback.textContent = '✗ ATM contract could not be resolved.';
    }
    return;
  }

  const lots = parseInt(popOrderLots ? popOrderLots.value : 1, 10) || 1;
  const lotSize = atm.lot_size || 1;
  const quantity = lots * lotSize;
  const product = (popOrderProduct ? popOrderProduct.value : 'MIS').toUpperCase();

  popBtnConfirmOrder.disabled = true;
  popBtnConfirmOrder.textContent = '⏳ Executing Order...';

  try {
    const res = await fetch('/api/scanner/order', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        tradingsymbol: atm.symbol,
        exchange: 'NFO',
        transaction_type: 'BUY',
        quantity: quantity,
        product: product,
      }),
    });
    
    let data;
    const resText = await res.text();
    try {
      data = JSON.parse(resText);
    } catch (e) {
      if (!res.ok) {
        throw new Error(`Server returned error HTTP ${res.status}`);
      }
      throw new Error(`Invalid response from server`);
    }

    if (data.ok) {
      const r = data.result || {};
      const isDry = !!r.dry_run;
      const orderId = r.order_id || 'SIMULATED';
      if (popOrderFeedback) {
        popOrderFeedback.className = 'cond-pill cond-pass';
        popOrderFeedback.classList.remove('hidden');
        popOrderFeedback.textContent = isDry
          ? `✓ [DRY RUN] Order simulated: BUY ${quantity} x ${atm.symbol}`
          : `✓ LIVE Order Triggered! Order ID: ${orderId}`;
      }
      addLog(`[scanner] ${isDry ? '[DRY RUN] ' : '⚡ [LIVE] '}Order executed for ${atm.symbol} (Qty: ${quantity}, ID: ${orderId})`, isDry ? 'info' : 'warn');

      setTimeout(() => {
        popTradeConfirm.classList.add('hidden');
        popBtnTrade.classList.remove('hidden');
        _nextPopup();
      }, 1500);
    } else {
      if (popOrderFeedback) {
        popOrderFeedback.className = 'cond-pill cond-fail';
        popOrderFeedback.classList.remove('hidden');
        popOrderFeedback.textContent = `✗ Order failed: ${data.error || 'Unknown error'}`;
      }
      addLog(`[scanner] Order failed for ${atm.symbol}: ${data.error}`, 'error');
      popBtnConfirmOrder.disabled = false;
      popBtnConfirmOrder.textContent = '🚀 Execute Order';
    }
  } catch (err) {
    if (popOrderFeedback) {
      popOrderFeedback.className = 'cond-pill cond-fail';
      popOrderFeedback.classList.remove('hidden');
      popOrderFeedback.textContent = `✗ Network/server error: ${err.message || err}`;
    }
    addLog(`[scanner] Order network error: ${err.message || err}`, 'error');
    popBtnConfirmOrder.disabled = false;
    popBtnConfirmOrder.textContent = '🚀 Execute Order';
  }
});

popBtnCancelConfirm.addEventListener('click', () => {
  popTradeConfirm.classList.add('hidden');
  popBtnTrade.classList.remove('hidden');
});

// Close on backdrop click or close (X) button
if (popBtnCloseIcon) {
  popBtnCloseIcon.addEventListener('click', () => {
    popBtnTrade.classList.remove('hidden');
    _nextPopup();
  });
}
popupOverlay.addEventListener('click', e => {
  if (e.target === popupOverlay) {
    popBtnTrade.classList.remove('hidden');
    _nextPopup();
  }
});

// ── Restore scanner state on reload ───────────────────
fetch('/api/scanner/status').then(r => r.json()).then(d => {
  if (d.running) {
    setScannerRunning(true, d.symbol_count, d.all_fno);
    scannerSignalsCard.classList.remove('hidden');
    // Sync checkbox state
    if (scannerAllFnoChk) scannerAllFnoChk.checked = !!d.all_fno;
    if (scannerCustomSymDiv) scannerCustomSymDiv.classList.toggle('hidden', !!d.all_fno);
  }
  if (d.signal_count > 0) {
    fetch('/api/scanner/signals').then(r => r.json()).then(sd => {
      (sd.signals || []).forEach(sig => {
        _scannerSignals.push(sig);
        _renderSignalRow(sig);
      });
    });
  }
}).catch(() => {});

/* ── Restore state from server ───────────────── */
function restoreState() {
  fetch('/api/status').then(r => r.json()).then(d => {
    setLoggedIn(d.logged_in, d.user_name);
    setRunning(d.running);
    checkUrlQueryParam();
  });
  fetch('/api/last_analysis').then(r => r.json()).then(d => {
    if (d.universe) {
      $('chartTicker').textContent = d.universe.ticker || '';
      livePriceTicker.textContent = d.universe.futures_tradingsymbol || '';
    }
    if (d.analysis) {
      renderScoreDashboard(d.analysis.result || d.analysis);
    }
    if (d.option_chain) {
      renderOptionChain(d.option_chain);
    } else if (d.analysis && d.analysis.option_chain) {
      renderOptionChain(d.analysis.option_chain);
    }
  });
}

restoreState();
loadReports();

// ── Strategy D Backtest Modal Handlers ──────────────────
const btnScannerBacktest    = $('btnScannerBacktest');
const backtestModalOverlay  = $('backtestModalOverlay');
const btnCloseBacktestModal = $('btnCloseBacktestModal');
const btnRunBacktest        = $('btnRunBacktest');

if (btnScannerBacktest && backtestModalOverlay) {
  btnScannerBacktest.addEventListener('click', () => {
    backtestModalOverlay.classList.remove('hidden');
  });
}

if (btnCloseBacktestModal && backtestModalOverlay) {
  btnCloseBacktestModal.addEventListener('click', () => {
    backtestModalOverlay.classList.add('hidden');
  });
  backtestModalOverlay.addEventListener('click', e => {
    if (e.target === backtestModalOverlay) backtestModalOverlay.classList.add('hidden');
  });
}

if (btnRunBacktest) {
  btnRunBacktest.addEventListener('click', async () => {
    const days = parseInt($('btLookbackDays') ? $('btLookbackDays').value : 180, 10);
    const requireBreakout = $('btRequireBreakout') ? $('btRequireBreakout').checked : true;
    const universe = $('btUniverse') ? $('btUniverse').value : 'ALL_FNO';

    btnRunBacktest.disabled = true;
    btnRunBacktest.textContent = '⏳ Simulating Trades...';
    $('btTradesTbody').innerHTML = '<tr><td colspan="10" class="text-center muted" style="padding:24px;">Analyzing historical daily candles and calculating 50/150/200 EMAs for F&O universe...</td></tr>';

    try {
      const res = await fetch('/api/scanner/backtest', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          days: days,
          symbols: universe,
          require_breakout: requireBreakout,
        }),
      });
      const data = await res.json();
      if (!data.ok) {
        addLog('Backtest error: ' + (data.error || 'Unknown error'), 'error');
        $('btTradesTbody').innerHTML = `<tr><td colspan="10" class="text-center" style="color:#ef4444;padding:20px;">Backtest failed: ${data.error || 'Unknown error'}</td></tr>`;
        return;
      }

      const m = data.metrics || {};
      $('btStatTotalTrades').textContent = m.total_trades || 0;
      $('btStatWinRate').textContent = (m.win_rate_pct != null ? m.win_rate_pct + '%' : '—');
      $('btStatProfitFactor').textContent = m.profit_factor != null ? m.profit_factor : '—';
      $('btStatTotalReturn').textContent = (m.total_return_pct != null ? (m.total_return_pct >= 0 ? '+' : '') + m.total_return_pct + '%' : '—');
      $('btStatTotalReturn').style.color = (m.total_return_pct >= 0 ? '#22c55e' : '#ef4444');
      $('btStatMaxDd').textContent = m.max_drawdown_pct != null ? m.max_drawdown_pct + '%' : '—';
      $('btStatSymbols').textContent = m.total_symbols_scanned || '—';

      const trades = data.trades || [];
      if (trades.length === 0) {
        $('btTradesTbody').innerHTML = '<tr><td colspan="10" class="text-center muted" style="padding:20px;">No trades generated for the selected parameters.</td></tr>';
      } else {
        $('btTradesTbody').innerHTML = trades.map(t => {
          const isWin = t.pnl_pts > 0;
          const pnlColor = isWin ? '#22c55e' : '#ef4444';
          const dirColor = t.direction === 'LONG' ? '#22c55e' : '#ef4444';
          return `
            <tr>
              <td>${t.exit_date}</td>
              <td><strong>${t.symbol}</strong></td>
              <td class="muted small">${t.futures_symbol || '—'}</td>
              <td style="color:${dirColor};font-weight:700;">${t.direction}</td>
              <td><span class="badge" style="background:rgba(2,132,199,0.2);color:#38bdf8;">${t.signal_type || 'ENTRY'}</span></td>
              <td>₹${t.entry_price?.toFixed(2)}</td>
              <td>₹${t.exit_price?.toFixed(2)}</td>
              <td style="color:${pnlColor};font-weight:700;">${t.pnl_pct >= 0 ? '+' : ''}${t.pnl_pct?.toFixed(2)}%</td>
              <td style="color:${pnlColor};">₹${t.pnl_pts?.toFixed(2)}</td>
              <td><span class="muted small">${t.exit_reason || '—'}</span></td>
            </tr>
          `;
        }).join('');
      }
      addLog(`[scanner] Backtest complete: ${m.total_trades} trades, ${m.win_rate_pct}% win rate across ${m.total_symbols_scanned} F&O symbols`, 'info');
    } catch (err) {
      addLog('Backtest request failed: ' + err, 'error');
      $('btTradesTbody').innerHTML = `<tr><td colspan="10" class="text-center" style="color:#ef4444;padding:20px;">Request failed: ${err}</td></tr>`;
    } finally {
      btnRunBacktest.disabled = false;
      btnRunBacktest.textContent = '🚀 Run Backtest';
    }
  });
}

// ── Smart Money Dashboard Interactions (Section 10) ────
let _currentSmTab = 'bottom_accumulation';
let _smDataCache = null;

const smTabAcc   = $('smTabAcc');
const smTabDist  = $('smTabDist');
const smTabConf  = $('smTabConf');
const smTabWatch = $('smTabWatch');
const smTableTbody = $('smTableTbody');
const smSectionDesc = $('smSectionDesc');

function _updateSmTabButtons(activeTab) {
  _currentSmTab = activeTab;
  const tabs = [
    { el: smTabAcc, tab: 'bottom_accumulation', desc: 'Institutional accumulation setups near demand zones with rising OBV, positive volume delta, and futures long buildup.' },
    { el: smTabDist, tab: 'top_distribution', desc: 'Institutional distribution setups near supply zones with falling OBV, selling volume expansion, and futures short buildup.' },
    { el: smTabConf, tab: 'confirmed', desc: 'High-confluence (Score ≥ 80) confirmed BUY & SELL futures signals passing all mandatory confirmation filters.' },
    { el: smTabWatch, tab: 'watchlist', desc: 'Stocks approaching demand/supply zones or showing developing volume/OBV structure awaiting full confirmation.' },
  ];
  tabs.forEach(t => {
    if (!t.el) return;
    if (t.tab === activeTab) {
      t.el.className = 'btn btn-sm btn-primary';
      t.el.style.background = '#eab308';
      t.el.style.borderColor = '#ca8a04';
      t.el.style.color = '#000';
      t.el.style.fontWeight = '700';
      if (smSectionDesc) smSectionDesc.textContent = t.desc;
    } else {
      t.el.className = 'btn btn-sm btn-secondary';
      t.el.style.background = '';
      t.el.style.borderColor = '';
      t.el.style.color = '';
      t.el.style.fontWeight = 'normal';
    }
  });
  _renderSmTable();
}

if (smTabAcc) smTabAcc.addEventListener('click', () => _updateSmTabButtons('bottom_accumulation'));
if (smTabDist) smTabDist.addEventListener('click', () => _updateSmTabButtons('top_distribution'));
if (smTabConf) smTabConf.addEventListener('click', () => _updateSmTabButtons('confirmed'));
if (smTabWatch) smTabWatch.addEventListener('click', () => _updateSmTabButtons('watchlist'));

function _renderSmTable() {
  if (!smTableTbody || !_smDataCache) return;
  const list = _smDataCache[_currentSmTab] || [];
  if (list.length === 0) {
    smTableTbody.innerHTML = `<tr><td colspan="11" class="text-center muted" style="padding:18px;">No candidates currently in ${_currentSmTab.replace(/_/g, ' ')}...</td></tr>`;
    return;
  }

  smTableTbody.innerHTML = list.map(item => {
    const isBull = item.direction === 'BULLISH' || (item.category && item.category.includes('ACCUMULATION'));
    const dirColor = isBull ? '#22c55e' : (item.direction === 'BEARISH' || (item.category && item.category.includes('DISTRIBUTION')) ? '#ef4444' : '#facc15');
    const scoreColor = item.score >= 80 ? '#22c55e' : (item.score >= 65 ? '#facc15' : '#9ca3af');
    const distDemand = item.dist_demand_pct != null ? `${item.dist_demand_pct > 0 ? '+' : ''}${item.dist_demand_pct}% to Demand` : '—';
    const distSupply = item.dist_supply_pct != null ? `${item.dist_supply_pct > 0 ? '+' : ''}${item.dist_supply_pct}% to Supply` : '—';
    const zoneDist = isBull ? distDemand : distSupply;

    return `
      <tr>
        <td><strong>${item.symbol}</strong></td>
        <td class="muted small">${item.futures_symbol || '—'}</td>
        <td>₹${item.spot_price != null ? Number(item.spot_price).toFixed(2) : '—'}</td>
        <td><strong style="color:${scoreColor};font-size:12px;">${item.score}/100</strong></td>
        <td><span class="badge" style="background:${isBull ? 'rgba(34,197,94,0.18)' : 'rgba(239,68,68,0.18)'};color:${dirColor};">${item.category}</span></td>
        <td class="muted small">${zoneDist}</td>
        <td><span class="muted small">${item.obv_trend || '—'}</span></td>
        <td>${item.vol_expansion != null ? item.vol_expansion + 'x' : '—'}</td>
        <td><span class="badge" style="background:rgba(234,179,8,0.18);color:#facc15;">${item.buildup || '—'}</span></td>
        <td><span style="color:${item.supertrend === 'UP' ? '#22c55e' : '#ef4444'};font-weight:700;">${item.supertrend || '—'}</span></td>
        <td><span class="muted small">${item.order_flow || '—'}</span></td>
      </tr>
    `;
  }).join('');
}

async function refreshSmartMoneyDashboard() {
  try {
    const res = await fetch('/api/scanner/smart_money');
    const data = await res.json();
    if (data.ok && data.smart_money) {
      _smDataCache = data.smart_money;
      _renderSmTable();
    }
  } catch (e) {}
}

setInterval(refreshSmartMoneyDashboard, 15000);
setTimeout(refreshSmartMoneyDashboard, 2000);

let currentFScenario = '';
let currentFSymbol = 'NIFTY';

window.setFStrategyScenario = function(sc) {
  currentFScenario = sc;
  refreshSustainedBreakoutDashboard();
};

async function refreshSustainedBreakoutDashboard() {
  try {
    const sel = $('fPanelSymbolSelect');
    if (sel && sel.value) currentFSymbol = sel.value;
    let url = `/api/scanner/sustained_breakout?symbol=${encodeURIComponent(currentFSymbol)}`;
    if (currentFScenario) url += `&scenario=${encodeURIComponent(currentFScenario)}`;

    const res = await fetch(url);
    const d = await res.json();
    if (!d.ok || !d.data) return;

    const p1 = d.data.market_trend || {};
    const p2 = d.data.derivatives || {};
    const p3 = d.data.trading_signal || {};

    // Panel 1: Market Trend
    const fPanelSpotPrice = $('fPanelSpotPrice');
    if (fPanelSpotPrice) fPanelSpotPrice.textContent = `${p1.symbol || currentFSymbol}: ₹${p1.spot_price != null ? Number(p1.spot_price).toFixed(2) : '—'}`;
    const fPanelEmaVal = $('fPanelEmaVal');
    if (fPanelEmaVal) fPanelEmaVal.textContent = `${p1.ema10 ?? '—'} / ${p1.ema21 ?? '—'} / ${p1.ema50 ?? '—'}`;
    const fPanelEmaStatus = $('fPanelEmaStatus');
    if (fPanelEmaStatus) {
      const e10 = Number(p1.ema10);
      const e21 = Number(p1.ema21);
      const e50 = Number(p1.ema50);
      const isBull = p1.stacked_bull || p1.ema_trend === 'BULLISH' || (e10 && e21 && e50 && e10 > e21 && e21 > e50);
      const isBear = p1.stacked_bear || p1.ema_trend === 'BEARISH' || (e10 && e21 && e50 && e10 < e21 && e21 < e50);

      if (isBull) {
        fPanelEmaStatus.textContent = 'BULLISH (10>21>50)';
        fPanelEmaStatus.className = 'f-badge f-badge-bull';
      } else if (isBear) {
        fPanelEmaStatus.textContent = 'BEARISH (10<21<50)';
        fPanelEmaStatus.className = 'f-badge f-badge-bear';
      } else {
        fPanelEmaStatus.textContent = 'MIXED / CONSOLIDATION';
        fPanelEmaStatus.className = 'f-badge f-badge-neutral';
      }
    }
    const fPanelSupertrend = $('fPanelSupertrend');
    if (fPanelSupertrend) {
      const stTrend = p1.supertrend_trend || '—';
      const isUp = stTrend === 'UP';
      const stVal = p1.supertrend != null ? Number(p1.supertrend).toFixed(2) : '—';
      fPanelSupertrend.textContent = `${stVal} (${stTrend})`;
      fPanelSupertrend.className = 'f-badge ' + (isUp ? 'f-badge-bull' : (stTrend === 'DOWN' ? 'f-badge-bear' : 'f-badge-neutral'));
    }
    const fPanelAvwap = $('fPanelAvwap');
    if (fPanelAvwap) {
      const avVal = p1.avwap != null ? `₹${Number(p1.avwap).toFixed(2)}` : '—';
      fPanelAvwap.textContent = `${avVal} (${p1.price_vs_avwap || '—'})`;
    }
    const fPanelRange = $('fPanelRange');
    if (fPanelRange) {
      const rVal = p1.resistance != null ? `₹${Number(p1.resistance).toFixed(2)}` : '—';
      const sVal = p1.support != null ? `₹${Number(p1.support).toFixed(2)}` : '—';
      fPanelRange.textContent = `R: ${rVal} | S: ${sVal}`;
    }
    const fPanelVolume = $('fPanelVolume');
    if (fPanelVolume) {
      if (!p1.volume_valid) {
        fPanelVolume.textContent = 'Volume Not Available';
        fPanelVolume.className = 'f-badge f-badge-warn';
      } else {
        const ratio = p1.volume_ratio != null ? `${Number(p1.volume_ratio).toFixed(2)}x` : '—';
        const isExp = p1.volume_ratio >= 1.5;
        fPanelVolume.textContent = `${ratio} (${isExp ? 'Expansion' : 'Normal'})`;
        fPanelVolume.className = 'f-badge ' + (isExp ? 'f-badge-bull' : 'f-badge-neutral');
      }
    }

    // Panel 2: Derivatives Confirmation
    const fPanelFuturesLtp = $('fPanelFuturesLtp');
    if (fPanelFuturesLtp) fPanelFuturesLtp.textContent = `FUT: ₹${p2.futures_price != null ? Number(p2.futures_price).toFixed(2) : '—'}`;
    const fPanelFutSym = $('fPanelFutSym');
    if (fPanelFutSym) fPanelFutSym.textContent = p2.futures_symbol || '—';
    const fPanelBuildup = $('fPanelBuildup');
    if (fPanelBuildup) {
      const bu = p2.buildup || 'NEUTRAL';
      fPanelBuildup.textContent = bu.replace(/_/g, ' ');
      const isLongB = bu === 'LONG_BUILDUP';
      const isShortB = bu === 'SHORT_BUILDUP';
      fPanelBuildup.className = 'f-badge ' + (isLongB ? 'f-badge-bull' : (isShortB ? 'f-badge-bear' : 'f-badge-neutral'));
    }
    const fPanelOi = $('fPanelOi');
    if (fPanelOi) {
      const oiStr = p2.current_oi != null ? Number(p2.current_oi).toLocaleString('en-IN') : '—';
      const chgStr = p2.oi_change_pct != null ? ` (${p2.oi_change_pct > 0 ? '+' : ''}${p2.oi_change_pct}%)` : '';
      fPanelOi.textContent = `${oiStr}${chgStr}`;
    }
    const fPanelOptionsOi = $('fPanelOptionsOi');
    if (fPanelOptionsOi) {
      const cStr = p2.call_oi != null ? Number(p2.call_oi).toLocaleString('en-IN') : '—';
      const pStr = p2.put_oi != null ? Number(p2.put_oi).toLocaleString('en-IN') : '—';
      fPanelOptionsOi.textContent = `C: ${cStr} | P: ${pStr}`;
    }
    const fPanelPcr = $('fPanelPcr');
    if (fPanelPcr) {
      const biasLabel = (p2.market_bias || 'BALANCED').replace(/_/g, ' ');
      fPanelPcr.textContent = `PCR ${p2.pcr ?? '—'} • ${biasLabel}`;
      const isBullBias = p2.market_bias === 'BULLISH_WRITING';
      const isBearBias = p2.market_bias === 'BEARISH_WRITING';
      fPanelPcr.className = 'f-badge ' + (isBullBias ? 'f-badge-bull' : (isBearBias ? 'f-badge-bear' : 'f-badge-neutral'));
    }
    const fPanelDataValid = $('fPanelDataValid');
    if (fPanelDataValid) {
      const isValid = !!p2.oi_valid;
      fPanelDataValid.textContent = isValid ? '✅ F&O Verified' : '⚠️ Missing / Inactive';
      fPanelDataValid.className = 'f-badge ' + (isValid ? 'f-badge-bull' : 'f-badge-warn');
    }

    // Panel 3: Trading Signal & Targets
    const score = p3.confidence_score ?? 0;
    const fPanelSignalScore = $('fPanelSignalScore');
    if (fPanelSignalScore) {
      fPanelSignalScore.textContent = `Score: ${score} / 100`;
      fPanelSignalScore.className = 'f-panel-pill ' + (score >= 75 ? 'f-pill-green' : 'f-pill-score');
    }
    const fPanelSignalBadge = $('fPanelSignalBadge');
    if (fPanelSignalBadge) {
      const dir = p3.direction || 'WAIT';
      const isLong = dir === 'LONG';
      const isShort = dir === 'SHORT';
      fPanelSignalBadge.textContent = isLong ? '▲ LONG (BUY)' : (isShort ? '▼ SHORT (SELL)' : 'WAIT / NO TRADE');
      fPanelSignalBadge.className = isLong ? 'f-signal-badge-long' : (isShort ? 'f-signal-badge-short' : 'f-signal-badge-wait');
    }
    const fPanelSetupStatus = $('fPanelSetupStatus');
    if (fPanelSetupStatus) fPanelSetupStatus.textContent = p3.setup_status || 'Awaiting Setup';
    const fPanelEntry = $('fPanelEntry');
    if (fPanelEntry) fPanelEntry.textContent = p3.entry_price != null ? `₹${Number(p3.entry_price).toFixed(2)}` : '—';
    const fPanelSl = $('fPanelSl');
    if (fPanelSl) fPanelSl.textContent = p3.stop_loss != null ? `₹${Number(p3.stop_loss).toFixed(2)}` : '—';
    const fPanelTargets = $('fPanelTargets');
    if (fPanelTargets) {
      const t1 = p3.target1 != null ? `₹${Number(p3.target1).toFixed(2)}` : '—';
      const t2 = p3.target2 != null ? `₹${Number(p3.target2).toFixed(2)}` : '—';
      fPanelTargets.textContent = `${t1} / ${t2}`;
    }
    const fPanelRr = $('fPanelRr');
    if (fPanelRr) fPanelRr.textContent = p3.risk_reward || '1:1.5';

    const fPanelReasonsFor = $('fPanelReasonsFor');
    if (fPanelReasonsFor) {
      const list = p3.reasons_for || [];
      fPanelReasonsFor.innerHTML = list.map(r => `<div>✔ ${r}</div>`).join('');
    }
    const fPanelReasonsAgainst = $('fPanelReasonsAgainst');
    if (fPanelReasonsAgainst) {
      const list = p3.reasons_against || [];
      fPanelReasonsAgainst.innerHTML = list.map(r => `<div>✖ ${r}</div>`).join('');
    }

    // ── Update Top KPI Cards Dynamically ──
    const kpiSent = $('kpiSentiment');
    if (kpiSent) {
      const isLong = p3.direction === 'LONG';
      const isShort = p3.direction === 'SHORT';

      if (isLong) {
        kpiSent.textContent = `BULLISH (${score})`;
        kpiSent.style.color = '#2dce89';
      } else if (isShort) {
        kpiSent.textContent = `BEARISH (${score})`;
        kpiSent.style.color = '#f5365c';
      } else if (score >= 65 && p1.price_vs_avwap === 'ABOVE') {
        kpiSent.textContent = `BULLISH BIAS (${score})`;
        kpiSent.style.color = '#2dce89';
      } else if (score <= 35 || p1.price_vs_avwap === 'BELOW') {
        kpiSent.textContent = `BEARISH BIAS (${score})`;
        kpiSent.style.color = '#f5365c';
      } else if (score > 0) {
        kpiSent.textContent = `NEUTRAL (${score})`;
        kpiSent.style.color = '#fb6340';
      } else if (p1.spot_price != null) {
        kpiSent.textContent = 'NEUTRAL';
        kpiSent.style.color = '#fb6340';
      }
    }

    const kpiVwap = $('kpiVwapStatus');
    if (kpiVwap) {
      const isBelow = p1.price_vs_avwap === 'BELOW';
      const isAbove = p1.price_vs_avwap === 'ABOVE';
      kpiVwap.textContent = isBelow ? '↓ Below AVWAP' : (isAbove ? '↑ Above AVWAP' : 'Intraday VWAP');
      kpiVwap.className = 'argon-trend ' + (isAbove ? 'text-success' : (isBelow ? 'text-danger' : 'text-warning'));
    }

    const kpiAvwap = $('kpiAvwapVal');
    if (kpiAvwap) {
      if (p1.avwap != null) {
        kpiAvwap.textContent = `AVWAP ₹${Number(p1.avwap).toFixed(2)}`;
      } else if (p1.spot_price != null) {
        kpiAvwap.textContent = `Spot ₹${Number(p1.spot_price).toFixed(2)}`;
      }
    }

    if (p2.pcr != null) {
      const kpiPcr = $('kpiPcrVal');
      if (kpiPcr) kpiPcr.textContent = Number(p2.pcr).toFixed(2);
      const kpiBias = $('kpiPcrBias');
      if (kpiBias) {
        const isBullPcr = p2.pcr >= 1.1;
        const isBearPcr = p2.pcr <= 0.9;
        kpiBias.textContent = isBullPcr ? '↑ Bullish' : (isBearPcr ? '↓ Bearish' : '→ Neutral');
        kpiBias.className = 'argon-trend ' + (isBullPcr ? 'text-success' : (isBearPcr ? 'text-danger' : 'text-warning'));
      }
      const kpiW = $('kpiWalls');
      if (kpiW) {
        kpiW.textContent = p2.market_bias ? p2.market_bias.replace(/_/g, ' ') : (p2.pcr >= 1.0 ? 'Put Writing Dominant' : 'Call Writing Dominant');
      }
    }

    // ── Card 4: LAST SIGNAL (replace static SCANNING) ──────────────
    const kpiScanVal   = $('kpiScannerVal');
    const kpiStratLine = $('kpiActiveStrategies');
    const kpiSigCount  = $('kpiSignalsCount');
    const signalDir    = p3.direction || 'NO TRADE';
    const signalScore  = p3.confidence_score != null ? p3.confidence_score : (p3.score != null ? p3.score : score);
    const isLongSig    = signalDir === 'LONG';
    const isShortSig   = signalDir === 'SHORT';
    const isNoTrade    = !isLongSig && !isShortSig;

    if (kpiScanVal) {
      if (isLongSig) {
        kpiScanVal.textContent  = '▲ LONG';
        kpiScanVal.style.color  = '#2dce89';
      } else if (isShortSig) {
        kpiScanVal.textContent  = '▼ SHORT';
        kpiScanVal.style.color  = '#f5365c';
      } else {
        kpiScanVal.textContent  = 'NO TRADE';
        kpiScanVal.style.color  = '#fb6340';
      }
    }
    if (kpiStratLine) {
      const sym   = p1.symbol || currentFSymbol || 'NIFTY';
      const scoreLabel = `Score: ${signalScore}/100`;
      kpiStratLine.textContent  = `${scoreLabel} • ${sym}`;
      kpiStratLine.className    = 'argon-trend ' + (isLongSig ? 'text-success' : (isShortSig ? 'text-danger' : 'text-warning'));
    }
    if (kpiSigCount) {
      const setup = p3.setup_status || 'Awaiting Setup';
      const totalSigs = _scannerSignals.length;
      kpiSigCount.textContent = totalSigs > 0 ? `${totalSigs} Signals Active` : setup;
    }
  } catch (e) {}
}

setInterval(refreshSustainedBreakoutDashboard, 3000);
setTimeout(refreshSustainedBreakoutDashboard, 1000);

// ── Strategy G: GTI Smart Money Trader ──────────────────────────────────────
let currentGtiSymbol = 'NIFTY';
let currentGtiScenario = '';

window.setGtiStrategyScenario = function(sc) {
  currentGtiScenario = (sc === 'dynamic' ? '' : sc);
  refreshGtiDashboard();
};

window.onGtiSymbolChanged = function() {
  const sel = $('gtiSymbolSelect');
  if (sel && sel.value) {
    currentGtiSymbol = sel.value;
  }
  currentGtiScenario = '';
  refreshGtiDashboard();
};

async function refreshGtiDashboard() {
  try {
    const sel = $('gtiSymbolSelect');
    if (sel && sel.value) {
      if (currentGtiSymbol !== sel.value) {
        currentGtiSymbol = sel.value;
        currentGtiScenario = '';
      }
    }
    let url = `/api/scanner/gti?symbol=${encodeURIComponent(currentGtiSymbol)}`;
    if (currentGtiScenario) url += `&scenario=${encodeURIComponent(currentGtiScenario)}`;

    const res = await fetch(url);
    const d = await res.json();
    if (!d.ok || !d.data) return;

    const data = d.data;
    const gtiStatus = data.gti_status || (data.current_candle_color ? data : {});
    const signal = data.signal || (data.direction ? data : {});
    const opt = data.suggested_option || {};
    const tail = data.candles_tail || data.classified_candles_tail || [];

    // Panel 1: Candle Engine
    const gtiSpotPrice = $('gtiSpotPrice');
    if (gtiSpotPrice) {
      const sp = gtiStatus.spot_price != null ? Number(gtiStatus.spot_price).toFixed(2) : '—';
      gtiSpotPrice.textContent = `${gtiStatus.symbol || currentGtiSymbol}: ₹${sp}`;
    }

    const gtiCurrentCandleBadge = $('gtiCurrentCandleBadge');
    if (gtiCurrentCandleBadge) {
      const color = gtiStatus.current_candle_color || 'NORMAL';
      if (color === 'YELLOW') {
        gtiCurrentCandleBadge.textContent = '🟡 YELLOW (Accumulation)';
        gtiCurrentCandleBadge.className = 'f-badge gti-badge-yellow';
      } else if (color === 'BLUE') {
        gtiCurrentCandleBadge.textContent = '🔵 BLUE (Bullish Momentum)';
        gtiCurrentCandleBadge.className = 'f-badge gti-badge-blue';
      } else if (color === 'BLACK') {
        gtiCurrentCandleBadge.textContent = '⚫ BLACK (Bearish Momentum)';
        gtiCurrentCandleBadge.className = 'f-badge gti-badge-black';
      } else if (color === 'GREEN') {
        gtiCurrentCandleBadge.textContent = '🟢 GREEN (Standard Bullish)';
        gtiCurrentCandleBadge.className = 'f-badge f-badge-bull';
      } else {
        gtiCurrentCandleBadge.textContent = '🔴 RED (Standard Bearish)';
        gtiCurrentCandleBadge.className = 'f-badge f-badge-bear';
      }
    }

    const gtiYellowStatus = $('gtiYellowStatus');
    if (gtiYellowStatus) {
      if (gtiStatus.has_recent_yellow) {
        gtiYellowStatus.innerHTML = '<span style="color:#d97706;font-weight:700;">🟡 Yellow Candle Found (Smart Money Base Active)</span>';
      } else {
        gtiYellowStatus.textContent = 'Awaiting Yellow Accumulation Candle';
      }
    }

    const gtiYellowLevels = $('gtiYellowLevels');
    if (gtiYellowLevels) {
      const yh = gtiStatus.yellow_candle_high != null ? `H: ₹${Number(gtiStatus.yellow_candle_high).toFixed(2)}` : 'H: —';
      const yl = gtiStatus.yellow_candle_low != null ? `L: ₹${Number(gtiStatus.yellow_candle_low).toFixed(2)}` : 'L: —';
      gtiYellowLevels.textContent = `${yh} | ${yl}`;
    }

    const gtiVolRatio = $('gtiVolRatio');
    if (gtiVolRatio) {
      const vr = gtiStatus.vol_ratio != null ? `${Number(gtiStatus.vol_ratio).toFixed(2)}x` : '—';
      const isSpike = (gtiStatus.vol_ratio || 0) >= 1.45;
      gtiVolRatio.textContent = `${vr} ${isSpike ? '(Volume Spike Verified)' : '(Normal Volume)'}`;
      gtiVolRatio.className = 'f-badge ' + (isSpike ? 'f-badge-bull' : 'f-badge-neutral');
    }

    const gtiAtrVal = $('gtiAtrVal');
    if (gtiAtrVal) {
      const atr = gtiStatus.atr != null ? Number(gtiStatus.atr).toFixed(2) : '—';
      gtiAtrVal.textContent = `ATR: ${atr} pts`;
    }

    // Mini candle stream tail
    const gtiCandlesTail = $('gtiCandlesTail');
    if (gtiCandlesTail && tail && tail.length > 0) {
      gtiCandlesTail.innerHTML = tail.map(c => {
        let bg = '#94a3b8';
        let dot = '⚪';
        if (c.color === 'YELLOW') { bg = '#fef08a'; dot = '🟡'; }
        else if (c.color === 'BLUE') { bg = '#dbeafe'; dot = '🔵'; }
        else if (c.color === 'BLACK') { bg = '#1e293b;color:#f8fafc'; dot = '⚫'; }
        else if (c.color === 'GREEN') { bg = '#dcfce7'; dot = '🟢'; }
        else if (c.color === 'RED') { bg = '#fee2e2'; dot = '🔴'; }
        return `<span style="font-size:10px;padding:2px 6px;border-radius:4px;background:${bg};font-weight:700;display:inline-flex;align-items:center;gap:2px;" title="${c.label || ''}">${dot} ${c.color}</span>`;
      }).join('');
    }

    // Panel 2: Signal & Option Suggestion
    const gtiSignalBadge = $('gtiSignalBadge');
    if (gtiSignalBadge) {
      const dir = signal.direction || 'WAIT';
      if (dir === 'BUY_CALL') {
        gtiSignalBadge.textContent = '🔵 BUY CALL (CONFIRMED)';
        gtiSignalBadge.className = 'f-badge gti-signal-call';
      } else if (dir === 'BUY_PUT') {
        gtiSignalBadge.textContent = '⚫ BUY PUT (CONFIRMED)';
        gtiSignalBadge.className = 'f-badge gti-signal-put';
      } else if (dir === 'ACCUMULATION') {
        gtiSignalBadge.textContent = '🟡 SMART MONEY ACCUMULATION';
        gtiSignalBadge.className = 'f-badge gti-signal-acc';
      } else {
        gtiSignalBadge.textContent = 'WAIT / NO TRADE';
        gtiSignalBadge.className = 'f-badge f-signal-badge-wait';
      }
    }

    const gtiSignalDesc = $('gtiSignalDesc');
    if (gtiSignalDesc) {
      gtiSignalDesc.textContent = signal.signal_status || 'Awaiting setup';
    }

    const gtiSignalConf = $('gtiSignalConf');
    if (gtiSignalConf) {
      const conf = signal.confidence || 'LOW';
      gtiSignalConf.textContent = `CONFIDENCE: ${conf}`;
      if (conf === 'HIGH') gtiSignalConf.className = 'gti-conf-high';
      else if (conf === 'MEDIUM') gtiSignalConf.className = 'gti-conf-med';
      else gtiSignalConf.className = 'gti-conf-low';
    }

    const gtiOptSymbol = $('gtiOptSymbol');
    if (gtiOptSymbol) {
      gtiOptSymbol.textContent = opt.tradingsymbol || (opt.atm_strike ? `${currentGtiSymbol} ${opt.atm_strike} CE/PE` : '—');
    }

    const isConfirmed = (signal.direction === 'BUY_CALL' || signal.direction === 'BUY_PUT');

    const gtiOptLtp = $('gtiOptLtp');
    if (gtiOptLtp) {
      if (opt.estimated_ltp != null) {
        const suffix = isConfirmed ? '' : ' (Est. ATM)';
        gtiOptLtp.textContent = `₹${Number(opt.estimated_ltp).toFixed(2)}${suffix}`;
      } else {
        gtiOptLtp.textContent = '₹—';
      }
    }

    const gtiOptGreeks = $('gtiOptGreeks');
    if (gtiOptGreeks) {
      const dVal = opt.delta != null ? opt.delta : (isConfirmed ? '—' : '±0.50');
      const ivVal = opt.iv != null ? `${opt.iv}%` : '14.5%';
      gtiOptGreeks.textContent = `Δ: ${dVal} | IV: ${ivVal}`;
    }

    const gtiOptSl = $('gtiOptSl');
    if (gtiOptSl) {
      if (opt.option_stop_loss != null) {
        gtiOptSl.textContent = `₹${Number(opt.option_stop_loss).toFixed(2)} (-22% SL)`;
      } else {
        gtiOptSl.textContent = '—';
      }
    }

    const gtiOptTarget = $('gtiOptTarget');
    if (gtiOptTarget) {
      if (opt.option_target != null) {
        gtiOptTarget.textContent = `₹${Number(opt.option_target).toFixed(2)} (1:2 R:R)`;
      } else {
        gtiOptTarget.textContent = '—';
      }
    }

    // Panel 3: Risk & Filters
    const gtiSpotLevels = $('gtiSpotLevels');
    if (gtiSpotLevels) {
      if (signal.direction === 'BUY_CALL' || signal.direction === 'BUY_PUT') {
        const ep = signal.entry_price != null ? `Entry: ₹${Number(signal.entry_price).toFixed(2)}` : 'Entry: —';
        const sl = signal.structure_stop_loss != null ? `SL: ₹${Number(signal.structure_stop_loss).toFixed(2)}` : 'SL: —';
        const t2 = signal.target2 != null ? `Tgt: ₹${Number(signal.target2).toFixed(2)}` : 'Tgt: —';
        gtiSpotLevels.textContent = `${ep} | ${sl} | ${t2}`;
      } else if (signal.direction === 'ACCUMULATION') {
        const yh = gtiStatus.yellow_candle_high != null ? Number(gtiStatus.yellow_candle_high).toFixed(2) : (signal.entry_price != null ? Number(signal.entry_price).toFixed(2) : null);
        const yl = gtiStatus.yellow_candle_low != null ? Number(gtiStatus.yellow_candle_low).toFixed(2) : (signal.structure_stop_loss != null ? Number(signal.structure_stop_loss).toFixed(2) : null);
        if (yh && yl) {
          gtiSpotLevels.textContent = `Bull: >₹${yh} | Bear: <₹${yl} (SL Opposite | 1:2 R:R)`;
        } else {
          const ep = signal.entry_price != null ? `Ref H: ₹${Number(signal.entry_price).toFixed(2)}` : '—';
          const sl = signal.structure_stop_loss != null ? `Ref L: ₹${Number(signal.structure_stop_loss).toFixed(2)}` : '—';
          gtiSpotLevels.textContent = `Range: ${sl} - ${ep} (1:2 R:R Planned)`;
        }
      } else {
        const ep = signal.entry_price != null ? `Ref H: ₹${Number(signal.entry_price).toFixed(2)}` : '—';
        const sl = signal.structure_stop_loss != null ? `Ref L: ₹${Number(signal.structure_stop_loss).toFixed(2)}` : '—';
        gtiSpotLevels.textContent = `Range: ${sl} - ${ep} (1:2 R:R Planned)`;
      }
    }

    const gtiFilterBadge = $('gtiFilterBadge');
    if (gtiFilterBadge) {
      const isFiltered = !!gtiStatus.filter_active;
      gtiFilterBadge.textContent = isFiltered ? 'FILTER PAUSED' : 'ALL FILTERS PASS';
      gtiFilterBadge.className = 'f-panel-pill ' + (isFiltered ? 'f-pill-score' : 'f-pill-green');
    }

    const gtiTimeFilterStatus = $('gtiTimeFilterStatus');
    if (gtiTimeFilterStatus) {
      const isOpen = !!gtiStatus.time_window_open;
      gtiTimeFilterStatus.textContent = gtiStatus.time_window_status || (isOpen ? 'Trading Window Open' : 'Closed');
      gtiTimeFilterStatus.className = 'f-value ' + (isOpen ? 'gti-filter-pass' : 'gti-filter-paused');
    }

    const gtiAtrVolFilterStatus = $('gtiAtrVolFilterStatus');
    if (gtiAtrVolFilterStatus) {
      const reasons = gtiStatus.filter_reasons || [];
      const hasVolFilter = reasons.some(r => r.toLowerCase().includes('volume'));
      const hasAtrFilter = reasons.some(r => r.toLowerCase().includes('volatility') || r.toLowerCase().includes('atr'));
      if (hasAtrFilter) {
        gtiAtrVolFilterStatus.textContent = 'Paused (Low Volatility Filter Active)';
        gtiAtrVolFilterStatus.className = 'f-value gti-filter-paused';
      } else if (hasVolFilter) {
        gtiAtrVolFilterStatus.textContent = 'Paused (Low Volume < 50% 20-MA)';
        gtiAtrVolFilterStatus.className = 'f-value gti-filter-paused';
      } else {
        const atr = gtiStatus.atr != null ? Number(gtiStatus.atr).toFixed(1) : '—';
        const vr = gtiStatus.vol_ratio != null ? `${Number(gtiStatus.vol_ratio).toFixed(2)}x` : '1.0x';
        gtiAtrVolFilterStatus.textContent = `Approved (ATR: ${atr} pts | Vol: ${vr})`;
        gtiAtrVolFilterStatus.className = 'f-value gti-filter-pass';
      }
    }

    const gtiAdxFilterStatus = $('gtiAdxFilterStatus');
    if (gtiAdxFilterStatus) {
      const mStatus = gtiStatus.market_status || 'NORMAL';
      const adxNum = gtiStatus.adx != null ? gtiStatus.adx : '—';
      gtiAdxFilterStatus.textContent = `${mStatus} (ADX: ${adxNum})`;
      gtiAdxFilterStatus.className = 'f-value ' + (mStatus === 'SIDEWAYS' ? 'gti-filter-paused' : 'gti-filter-pass');
    }

    const gtiFilterReasonsBox = $('gtiFilterReasonsBox');
    if (gtiFilterReasonsBox) {
      const reasons = gtiStatus.filter_reasons || [];
      if (reasons.length > 0) {
        gtiFilterReasonsBox.innerHTML = reasons.map(r => `<div>⚠️ ${r}</div>`).join('');
      } else {
        gtiFilterReasonsBox.innerHTML = '';
      }
    }
  } catch (e) {}
}

setInterval(refreshGtiDashboard, 3000);
setTimeout(refreshGtiDashboard, 1200);

// ── Page-load Bootstrap ─────────────────────────────────────────────────────
// If the server already knows we're logged in (from token.json restore),
// immediately activate all pollers without waiting for a SocketIO event.
(function bootstrapOnLoad() {
  if (window.SERVER_LOGGED_IN === true) {
    // Trigger full login state: starts LTP poller, enables buttons, etc.
    setLoggedIn(true, window.SERVER_USER_NAME || '');
    // Also kick off chart and reports
    setTimeout(() => {
      loadSpotChart();
      loadReports();
    }, 500);
  } else {
    // Not logged in via server — do a quick /api/status check on load
    fetch('/api/status').then(r => r.json()).then(d => {
      if (d.logged_in) {
        setLoggedIn(true, d.user_name || '');
        setTimeout(() => { loadSpotChart(); loadReports(); }, 500);
      }
    }).catch(() => {});
  }
})();
