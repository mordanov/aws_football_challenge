/* Match Replay Viewer — match.js
 * Works with schema_version:2, kind:"match-replay" replays.
 * No external dependencies; pure DOM + SVG.
 */
'use strict';

// ── State ─────────────────────────────────────────────────────────────────────
let replay = null;
let currentTick = 0;
let playing = false;
let speed = 1;
let animTimer = null;
let selectedPlayer = null; // { team_id, player_id } | null

const SVG_NS = 'http://www.w3.org/2000/svg';

// ── DOM refs ─────────────────────────────────────────────────────────────────
const loadOverlay   = document.getElementById('load-overlay');
const loadBtn       = document.getElementById('load-btn');
const loadAgainBtn  = document.getElementById('load-again-btn');
const fileInput     = document.getElementById('file-input');
const loadErr       = document.getElementById('load-err');
const statusBar     = document.getElementById('status');
const pitchWrap     = document.getElementById('pitch-wrap');
const asidePanel    = document.getElementById('aside');
const controlsBar   = document.getElementById('controls');
const playersLayer  = document.getElementById('players-layer');
const ballLayer     = document.getElementById('ball-layer');
const playBtn       = document.getElementById('play-btn');
const prevBtn       = document.getElementById('prev-btn');
const nextBtn       = document.getElementById('next-btn');
const seekEl        = document.getElementById('seek');
const seekLabel     = document.getElementById('seek-label');
const speedSel      = document.getElementById('speed-sel');
const stTick        = document.getElementById('st-tick');
const stTime        = document.getElementById('st-time');
const stScore       = document.getElementById('st-score');
const stMode        = document.getElementById('st-mode');
const stSeed        = document.getElementById('st-seed');
const asideContent  = document.getElementById('aside-content');
const eventsPanel   = document.getElementById('events-panel');
const eventsList    = document.getElementById('events-list');

// ── Load ──────────────────────────────────────────────────────────────────────
loadBtn.addEventListener('click', () => fileInput.click());
loadAgainBtn.addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', () => {
  const file = fileInput.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const data = JSON.parse(reader.result);
      if (data.schema_version !== 2) throw new Error(`Expected schema_version 2, got ${data.schema_version}`);
      if (data.kind !== 'match-replay') throw new Error(`Expected kind "match-replay", got "${data.kind}"`);
      if (!Array.isArray(data.frames) || data.frames.length === 0) throw new Error('No frames in replay');
      replay = data;
      currentTick = 0;
      selectedPlayer = null;
      initViewer();
    } catch (e) {
      showErr(e.message);
    }
  };
  reader.onerror = () => showErr('Failed to read file');
  reader.readAsText(file);
  fileInput.value = '';
});

function showErr(msg) {
  loadErr.textContent = msg;
  loadErr.hidden = false;
}

function initViewer() {
  loadErr.hidden = true;
  loadOverlay.hidden = true;
  statusBar.hidden = false;
  pitchWrap.hidden = false;
  asidePanel.hidden = false;
  controlsBar.hidden = false;

  seekEl.max = replay.duration_ticks;
  stSeed.textContent = `seed ${replay.seed}`;
  stopPlay();
  buildPlayerCircles();
  renderFrame(0);
}

// ── Player circles (reused across frames) ────────────────────────────────────
const playerCircles = {}; // key: "t-p" → { circle, ring, label }

function buildPlayerCircles() {
  while (playersLayer.firstChild) playersLayer.removeChild(playersLayer.firstChild);
  Object.keys(playerCircles).forEach(k => delete playerCircles[k]);

  if (!replay || !replay.frames.length) return;
  const frame0 = replay.frames[0];
  frame0.players.forEach(p => {
    const key = `${p.team_id}-${p.player_id}`;
    const g = document.createElementNS(SVG_NS, 'g');

    const ring = document.createElementNS(SVG_NS, 'circle');
    ring.setAttribute('r', '2.2');
    ring.setAttribute('class', 'selected-ring');
    ring.style.display = 'none';

    const circle = document.createElementNS(SVG_NS, 'circle');
    circle.setAttribute('r', '1.6');
    circle.setAttribute('class', p.team_id === 0 ? 'player0' : 'player1');
    circle.setAttribute('tabindex', '0');
    circle.setAttribute('role', 'button');
    circle.setAttribute('aria-label', `Team ${p.team_id} player ${p.player_id}`);

    const label = document.createElementNS(SVG_NS, 'text');
    label.setAttribute('class', 'player-label');
    label.textContent = roleLabel(p.player_id);

    g.appendChild(ring);
    g.appendChild(circle);
    g.appendChild(label);
    playersLayer.appendChild(g);
    playerCircles[key] = { g, circle, ring, label };

    const tid = p.team_id, pid = p.player_id;
    circle.addEventListener('click', () => selectPlayer(tid, pid));
    circle.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') selectPlayer(tid, pid); });
  });
}

function roleLabel(id) { return ['GK','D','M','F','F'][id] || id; }

// ── Render ────────────────────────────────────────────────────────────────────
function renderFrame(tick) {
  tick = Math.max(0, Math.min(tick, replay.duration_ticks));
  currentTick = tick;
  seekEl.value = tick;
  seekLabel.textContent = `${tick} / ${replay.duration_ticks}`;

  const frame = replay.frames[tick];
  if (!frame) return;

  // Status bar
  const secs = tick * 0.05; // 20 ticks/s → 0.05s/tick
  const mm = Math.floor(secs / 60);
  const ss = (secs % 60).toFixed(1).padStart(4, '0');
  stTick.textContent = tick;
  stTime.textContent = `${mm}:${ss}`;
  stScore.textContent = `${frame.score[0]} – ${frame.score[1]}`;
  stMode.textContent = frame.play_mode || '';

  // Players
  frame.players.forEach(p => {
    const key = `${p.team_id}-${p.player_id}`;
    const els = playerCircles[key];
    if (!els) return;
    els.g.setAttribute('transform', `translate(${p.x.toFixed(3)},${p.y.toFixed(3)})`);
    const isSelected = selectedPlayer &&
      selectedPlayer.team_id === p.team_id &&
      selectedPlayer.player_id === p.player_id;
    els.ring.style.display = isSelected ? '' : 'none';
  });

  // Ball
  while (ballLayer.firstChild) ballLayer.removeChild(ballLayer.firstChild);
  const b = frame.ball;
  const bc = document.createElementNS(SVG_NS, 'circle');
  bc.setAttribute('cx', b.x.toFixed(3));
  bc.setAttribute('cy', b.y.toFixed(3));
  bc.setAttribute('r', '0.9');
  bc.setAttribute('class', 'ball-circle');
  ballLayer.appendChild(bc);

  // Shadow under ball when in_flight
  if (b.in_flight && b.z > 0) {
    const shadow = document.createElementNS(SVG_NS, 'ellipse');
    shadow.setAttribute('cx', b.x.toFixed(3));
    shadow.setAttribute('cy', b.y.toFixed(3));
    shadow.setAttribute('rx', '0.9');
    shadow.setAttribute('ry', '0.5');
    shadow.setAttribute('fill', 'rgba(0,0,0,0.3)');
    ballLayer.insertBefore(shadow, bc);
  }

  // Aside
  if (selectedPlayer) renderAside(tick);
  renderEvents(tick);
}

// ── Aside ─────────────────────────────────────────────────────────────────────
function selectPlayer(team_id, player_id) {
  selectedPlayer = { team_id, player_id };
  renderFrame(currentTick);
}

function renderAside(tick) {
  if (!selectedPlayer) return;
  const { team_id, player_id } = selectedPlayer;
  const frame = replay.frames[tick];
  const p = frame && frame.players.find(x => x.team_id === team_id && x.player_id === player_id);
  if (!p) return;

  // Action result for this decision window
  const windowTick = Math.floor(tick / 40) * 40;
  const ar = replay.action_results.find(r =>
    r.tick === windowTick && r.team_id === team_id && r.player_id === player_id
  );

  // Build aside safely (text nodes only, no innerHTML with replay data)
  const card = document.createElement('div');
  card.className = 'player-card';

  const nameRow = document.createElement('div');
  nameRow.className = 'name';
  const dot = document.createElement('span');
  dot.className = `dot dot${team_id}`;
  nameRow.appendChild(dot);
  const nameText = document.createTextNode(
    `Team ${team_id} · ${['GK','DEF','MID','FWD','FWD'][player_id] || player_id}`
  );
  nameRow.appendChild(nameText);
  card.appendChild(nameRow);

  // Stamina bar
  const staminaWrap = document.createElement('div');
  staminaWrap.className = 'stamina-bar';
  const staminaFill = document.createElement('div');
  staminaFill.className = 'stamina-fill';
  staminaFill.style.width = `${(p.stamina * 100).toFixed(1)}%`;
  if (p.stamina < 0.3) staminaFill.style.background = '#ff6b6b';
  else if (p.stamina < 0.6) staminaFill.style.background = '#fcc419';
  staminaWrap.appendChild(staminaFill);
  card.appendChild(staminaWrap);

  // Key-value pairs
  const kv = document.createElement('div');
  kv.className = 'kv';
  kv.style.marginTop = '6px';
  const kvData = [
    ['x', p.x.toFixed(1)],
    ['y', p.y.toFixed(1)],
    ['stamina', (p.stamina * 100).toFixed(0) + '%'],
  ];
  kvData.forEach(([k, v]) => {
    const kn = document.createElement('span');
    kn.textContent = k;
    const vn = document.createElement('span');
    vn.className = 'v';
    vn.textContent = v;
    kv.appendChild(kn);
    kv.appendChild(vn);
  });
  card.appendChild(kv);

  // Action result
  if (ar) {
    const arDiv = document.createElement('div');
    arDiv.style.marginTop = '6px';
    const tag = document.createElement('span');
    tag.className = ar.accepted ? 'tag tag-ok' : 'tag tag-bad';
    tag.textContent = ar.accepted ? '✓ accepted' : '✗ rejected';
    arDiv.appendChild(tag);

    const cmdText = document.createElement('div');
    cmdText.style.marginTop = '4px';
    cmdText.style.fontFamily = 'var(--font-ui)';
    cmdText.style.fontSize = '11px';
    cmdText.style.color = 'var(--muted)';
    cmdText.textContent = ar.attempted_type;
    arDiv.appendChild(cmdText);

    if (!ar.accepted && ar.rejection_reason) {
      const reason = document.createElement('div');
      reason.style.color = 'var(--bad)';
      reason.style.fontSize = '10px';
      reason.style.fontFamily = 'var(--font-ui)';
      reason.textContent = ar.rejection_reason;
      arDiv.appendChild(reason);
    }
    card.appendChild(arDiv);
  }

  asideContent.replaceChildren(card);
}

function renderEvents(tick) {
  const nearby = replay.events.filter(e => Math.abs(e.tick - tick) <= 2);
  if (nearby.length === 0) {
    eventsPanel.hidden = true;
    return;
  }
  eventsPanel.hidden = false;
  eventsList.replaceChildren();
  nearby.forEach(ev => {
    const row = document.createElement('div');
    row.className = 'event-row';
    const ek = document.createElement('span');
    ek.className = 'ek';
    ek.textContent = ev.kind;
    const et = document.createElement('span');
    et.className = 'et';
    et.textContent = `t${ev.tick}`;
    row.appendChild(ek);
    if (ev.team_id !== null && ev.team_id !== undefined) {
      const em = document.createElement('span');
      em.textContent = ` tm${ev.team_id}`;
      row.appendChild(em);
    }
    row.appendChild(et);
    eventsList.appendChild(row);
  });
}

// ── Playback ──────────────────────────────────────────────────────────────────
function startPlay() {
  playing = true;
  playBtn.textContent = '⏸ Pause';
  playBtn.classList.add('primary');
  scheduleNext();
}

function stopPlay() {
  playing = false;
  playBtn.textContent = '▶ Play';
  if (animTimer) { clearTimeout(animTimer); animTimer = null; }
}

function scheduleNext() {
  if (!playing) return;
  // 20 ticks/s at ×1 → 50ms/tick; scale by speed
  animTimer = setTimeout(() => {
    if (!replay) return;
    if (currentTick >= replay.duration_ticks) { stopPlay(); return; }
    renderFrame(currentTick + 1);
    scheduleNext();
  }, 50 / speed);
}

playBtn.addEventListener('click', () => {
  if (!replay) return;
  if (playing) stopPlay();
  else {
    if (currentTick >= replay.duration_ticks) renderFrame(0);
    startPlay();
  }
});

prevBtn.addEventListener('click', () => { if (replay) renderFrame(currentTick - 40); });
nextBtn.addEventListener('click', () => { if (replay) renderFrame(currentTick + 40); });

seekEl.addEventListener('input', () => {
  if (!replay) return;
  stopPlay();
  renderFrame(parseInt(seekEl.value, 10));
});

speedSel.addEventListener('change', () => {
  speed = parseFloat(speedSel.value);
  if (playing) { stopPlay(); startPlay(); }
});

// ── Keyboard ──────────────────────────────────────────────────────────────────
document.addEventListener('keydown', e => {
  if (!replay) return;
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
  if (e.key === ' ') { e.preventDefault(); playBtn.click(); }
  else if (e.key === 'ArrowLeft')  renderFrame(currentTick - (e.shiftKey ? 40 : 1));
  else if (e.key === 'ArrowRight') renderFrame(currentTick + (e.shiftKey ? 40 : 1));
});
