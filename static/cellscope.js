'use strict';

const byId = (name) => document.getElementById(name);
const state = {
  modes: {}, mode: 'cells', panel: null, options: null, image: null, result: null,
  tool: null, anchor: null, hover: null, marker: null, plotCursor: null, plotNote: null, serial: 0,
};
const photoCanvas = byId('photo');
const photoContext = photoCanvas.getContext('2d');
const plotCanvas = byId('plot');
const plotContext = plotCanvas.getContext('2d');
const tools = [...document.querySelectorAll('[data-tool]')];
const inputNames = ['mm_per_pixel', 'rows', 'columns', 'dark_cutoff', 'hot_cutoff', 'min_area', 'band'];

function notice(message, error = false) {
  const node = byId('status');
  node.textContent = message;
  node.classList.toggle('error', error);
  clearTimeout(notice.timer);
  notice.timer = setTimeout(() => { node.textContent = ''; }, 5500);
}

async function request(path, method = 'GET', data) {
  const response = await fetch('/el' + path, {
    method,
    headers: data && !(data instanceof FormData) ? {'Content-Type': 'application/json'} : {},
    body: data instanceof FormData ? data : data === undefined ? undefined : JSON.stringify(data),
  });
  const result = await response.json();
  if (!response.ok) throw Error(result.error || '检查请求未完成。');
  return result;
}

function safe(fn) {
  return async (...args) => {
    try {
      await fn(...args);
    } catch (error) {
      notice(error.message, true);
    }
  };
}

function piece(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function panelPath(path = '') {
  return '/panels/' + state.panel.id + path;
}

function sourceTag(panel) {
  return {synthetic: '合成练习', prepared: '照片准备结果', upload: '上传原片'}[panel.provenance.kind];
}

async function listPanels() {
  const records = await request('/panels');
  const list = byId('library');
  list.replaceChildren();
  if (!records.length) list.append(piece('p', 'quiet', '还没有照片'));
  for (const panel of records) {
    const select = piece('button', 'panel-chip');
    select.classList.toggle('chosen', panel.id === state.panel?.id);
    select.append(piece('strong', '', panel.title),
                  piece('small', '', `${sourceTag(panel)} · ${panel.width} × ${panel.height}`));
    select.addEventListener('click', safe(() => openPanel(panel.id)));
    list.append(select);
  }
  return records;
}

function listChecks() {
  const list = byId('saved');
  list.replaceChildren();
  const checks = state.panel?.checks || [];
  if (!checks.length) list.append(piece('p', 'quiet', '还没有保存的检查'));
  for (const check of checks) {
    const select = piece('button', 'saved-chip');
    select.classList.toggle('chosen', check.id === state.result?.id);
    select.append(piece('strong', '', check.heading),
                  piece('small', '', new Date(check.created).toLocaleString('zh-CN', {hour12: false})));
    select.addEventListener('click', safe(() => openCheck(check.id)));
    list.append(select);
  }
}

function inputValues() {
  const form = byId('settings');
  return {...state.options, ...Object.fromEntries(inputNames.map(name => [name, form.elements[name].valueAsNumber]))};
}

function drawInputs() {
  const form = byId('settings');
  for (const name of inputNames) form.elements[name].value = state.options[name];
  drawPins();
}

function renderModes() {
  const list = byId('modes');
  list.replaceChildren();
  for (const [mode, label] of Object.entries(state.modes)) {
    const button = piece('button', state.mode === mode ? 'selected' : '', label);
    button.disabled = !state.panel;
    button.setAttribute('aria-pressed', String(state.mode === mode));
    button.addEventListener('click', safe(async () => {
      state.mode = mode;
      renderModes();
      await run();
    }));
    list.append(button);
  }
}

function enableDesk(enabled) {
  for (const button of tools) button.disabled = !enabled;
  for (const name of ['inputs', 'save', 'run', 'apply', 'pin']) byId(name).disabled = !enabled;
}

async function openPanel(id) {
  const serial = ++state.serial;
  const panel = await request('/panels/' + id);
  const image = new Image();
  image.src = '/el/panels/' + id + '/image';
  await image.decode();
  if (serial !== state.serial) return;
  state.panel = panel;
  state.image = image;
  state.options = {links: [], ...panel.settings};
  state.result = null;
  localStorage.setItem('cellscope-panel', id);
  byId('title').textContent = panel.title;
  byId('source-info').textContent = `${sourceTag(panel)} · ${panel.filename} · ${panel.width} × ${panel.height} px`;
  const scale = Math.min(1, 1700 / Math.max(panel.width, panel.height));
  photoCanvas.width = Math.round(panel.width * scale);
  photoCanvas.height = Math.round(panel.height * scale);
  photoCanvas.hidden = false;
  byId('empty').hidden = true;
  enableDesk(true);
  selectTool(null);
  drawInputs();
  renderModes();
  drawPhoto();
  listChecks();
  await run();
  await listPanels();
}

function drawPins() {
  const pins = byId('pins');
  pins.replaceChildren();
  state.options.pins.forEach((points, index) => {
    const select = piece('button', '', `查看暂留 ${index + 1}`);
    select.setAttribute('aria-label', `查看暂留剖线 ${index + 1}`);
    select.addEventListener('click', safe(async () => {
      state.options = {...inputValues(), trace: points};
      state.mode = 'trace';
      renderModes();
      await run();
    }));
    const remove = piece('button', '', '×');
    remove.setAttribute('aria-label', `移除暂留剖线 ${index + 1}`);
    remove.addEventListener('click', safe(async () => {
      state.options = {...inputValues(), pins: state.options.pins.filter((_, i) => i !== index)};
      drawPins();
      await run();
    }));
    pins.append(select, remove);
  });
  (state.options.links || []).forEach((points, index) => {
    const tag = piece('span', 'linked', `续接 ${index + 1}`);
    const remove = piece('button', '', '×');
    remove.setAttribute('aria-label', `移除续接 ${index + 1}，移回暂留`);
    remove.addEventListener('click', safe(async () => {
      const links = [...state.options.links];
      const [back] = links.splice(index, 1);
      state.options = {...inputValues(), links, pins: [...state.options.pins, back]};
      drawPins();
      await run();
    }));
    pins.append(tag, remove);
  });
}

function drawMark(mark, scaleX, scaleY) {
  const points = mark.points.map(([x, y]) => [x * scaleX, y * scaleY]);
  photoContext.strokeStyle = mark.color;
  photoContext.fillStyle = mark.color;
  photoContext.lineWidth = Math.max(1, photoCanvas.width / 700);
  const [[left, top], [right, bottom]] = points;
  if (mark.type === 'box') {
    photoContext.strokeRect(left, top, right - left, bottom - top);
  } else {
    photoContext.save();
    if (mark.dashed) photoContext.setLineDash([6, 4]);
    photoContext.beginPath();
    photoContext.moveTo(left, top);
    points.slice(1).forEach(([x, y]) => photoContext.lineTo(x, y));
    if (mark.type === 'polygon') photoContext.closePath();
    photoContext.stroke();
    photoContext.restore();
  }
  if (mark.label) {
    photoContext.font = `${Math.max(10, photoCanvas.width / 80)}px system-ui`;
    photoContext.lineWidth = 3;
    photoContext.strokeStyle = '#121c18';
    photoContext.strokeText(mark.label, left + 4, Math.max(12, top - 4));
    photoContext.fillText(mark.label, left + 4, Math.max(12, top - 4));
  }
}

function drawPhoto() {
  if (!state.image) return;
  const {width, height} = photoCanvas;
  photoContext.drawImage(state.image, 0, 0, width, height);
  const scaleX = width / state.panel.width;
  const scaleY = height / state.panel.height;
  if (byId('marks').checked) {
    for (const mark of state.result?.marks || []) drawMark(mark, scaleX, scaleY);
  }
  if (state.anchor && state.hover) {
    const [ax, ay] = state.anchor, [bx, by] = state.hover;
    const points = state.tool === 'trace' ? [[ax, ay], [bx, by]] :
      [[Math.min(ax, bx), Math.min(ay, by)], [Math.max(ax, bx), Math.max(ay, by)]];
    drawMark({type: state.tool === 'trace' ? 'line' : 'box', points, color: '#e8fc90'},
             width, height);
  }
  if (state.marker) {
    const [x, y] = [state.marker[0] * scaleX, state.marker[1] * scaleY];
    const radius = Math.max(5, width / 100);
    photoContext.strokeStyle = '#ecff96';
    photoContext.lineWidth = Math.max(2, width / 600);
    photoContext.beginPath();
    photoContext.arc(x, y, radius, 0, Math.PI * 2);
    photoContext.moveTo(x - radius * 1.5, y);
    photoContext.lineTo(x + radius * 1.5, y);
    photoContext.moveTo(x, y - radius * 1.5);
    photoContext.lineTo(x, y + radius * 1.5);
    photoContext.stroke();
  }
}

function showAt(at) {
  state.marker = at;
  if (at) byId('position').textContent = `原片 ${at[0]}, ${at[1]} px`;
  drawPhoto();
}

function drawPlot() {
  const graph = state.result?.plot;
  byId('plot-box').hidden = !graph?.samples.length;
  if (!graph?.samples.length) return;
  const {width, height} = plotCanvas;
  plotContext.clearRect(0, 0, width, height);
  byId('plot-title').textContent = graph.label;
  byId('sample-read').textContent = state.plotCursor
    ? `${state.plotCursor.x} · ${state.plotCursor.y}`
    : state.plotNote || (graph.trace_ends ? '点击曲线回到原片' : '');
  const samples = graph.samples;
  const readings = samples.filter(s => s.y !== null);
  const [left, right, top, bottom] = [47, width - 27, 13, height - 28];
  const minX = samples[0].x;
  const maxX = samples.at(-1).x;
  const minY = Math.min(...readings.map(s => s.y));
  const maxY = Math.max(...readings.map(s => s.y));
  const toX = (x) => left + (x - minX) * (right - left) / Math.max(maxX - minX, 1e-5);
  const toY = (y) => bottom - (y - minY) * (bottom - top) / Math.max(maxY - minY, 1e-5);
  plotContext.font = '12px system-ui';
  plotContext.fillStyle = '#52675e';
  for (let step = 0; step <= 4; step++) {
    const y = top + (bottom - top) * step / 4;
    plotContext.strokeStyle = '#e1eae5';
    plotContext.beginPath();
    plotContext.moveTo(left, y);
    plotContext.lineTo(right, y);
    plotContext.stroke();
    plotContext.fillText(String(+(maxY - (maxY - minY) * step / 4).toPrecision(3)), 1, y + 4);
    const value = minX + (maxX - minX) * step / 4;
    plotContext.fillText(String(+value.toPrecision(4)), toX(value), bottom + 21);
  }
  for (const span of graph.covered || []) {
    plotContext.fillStyle = 'rgba(83,106,96,.14)';
    plotContext.fillRect(toX(span.from), top, Math.max(1, toX(span.to) - toX(span.from)), bottom - top);
  }
  plotContext.beginPath();
  plotContext.strokeStyle = '#166958';
  plotContext.lineWidth = 2;
  let pen = false;
  for (const sample of samples) {
    if (sample.y === null) {
      pen = false;
    } else if (pen) {
      plotContext.lineTo(toX(sample.x), toY(sample.y));
    } else {
      plotContext.moveTo(toX(sample.x), toY(sample.y));
      pen = true;
    }
  }
  plotContext.stroke();
  plotContext.lineWidth = 1;
  if (state.plotCursor) {
    plotContext.fillStyle = '#ac5619';
    plotContext.beginPath();
    plotContext.arc(toX(state.plotCursor.x), toY(state.plotCursor.y), 5, 0, Math.PI * 2);
    plotContext.fill();
  }
}

function renderResult() {
  const result = state.result;
  if (!result) return;
  byId('result-title').textContent = result.heading;
  byId('result-state').textContent = result.id ? '已保存' : '预览';
  const metrics = byId('metrics');
  metrics.replaceChildren();
  for (const metric of result.metrics) {
    const card = piece('div', 'metric');
    card.append(piece('small', '', metric.label),
                piece('b', '', [metric.value, metric.units].filter(v => v !== '').join(' ')));
    metrics.append(card);
  }
  const rows = byId('rows');
  rows.replaceChildren();
  if (result.continuation) {
    const cont = result.continuation;
    const block = piece('div', 'adopt');
    const adopt = piece('button', '', `采用续接 · ${cont.label}`);
    adopt.addEventListener('click', safe(async () => {
      const parked = [...state.options.pins];
      const [chosen] = parked.splice(cont.pin, 1);
      if (!chosen) return;
      state.options = {...inputValues(), pins: parked, links: [...(state.options.links || []), chosen]};
      drawPins();
      await run();
      notice(`已采用 ${cont.label} 作为续接段`);
    }));
    block.append(adopt, piece('small', '', cont.reason));
    rows.append(block);
  }
  for (const row of result.rows) {
    const element = piece(row.at ? 'button' : 'div', 'result-row');
    element.append(piece('span', '', row.label), piece('b', '', Array.isArray(row.value) ? row.value.join(', ') : row.value));
    if (row.detail) element.append(piece('small', '', row.detail));
    if (row.at) element.addEventListener('click', () => showAt(row.at));
    rows.append(element);
  }
  for (const [name, ext] of [['csv', 'report.csv'], ['png', 'overlay.png']]) {
    const download = byId(name);
    download.hidden = !result.id;
    if (result.id) download.href = '/el' + panelPath(`/checks/${result.id}/${ext}`);
    else download.removeAttribute('href');
  }
  drawPhoto();
  drawPlot();
  listChecks();
}

async function run() {
  if (!state.panel || !byId('settings').reportValidity()) return;
  const serial = ++state.serial;
  const chosen = inputValues();
  const result = await request(panelPath('/preview'), 'POST', {mode: state.mode, settings: chosen});
  if (serial !== state.serial) return;
  state.options = result.settings;
  state.result = result;
  state.marker = null;
  state.plotCursor = null;
  state.plotNote = null;
  renderResult();
}

async function openCheck(id) {
  const serial = ++state.serial;
  const saved = await request(panelPath('/checks/' + id));
  if (serial !== state.serial) return;
  state.result = saved;
  state.mode = saved.mode;
  state.options = {links: [], ...saved.settings};
  state.marker = null;
  state.plotCursor = null;
  state.plotNote = null;
  drawInputs();
  renderModes();
  renderResult();
}

function selectTool(name) {
  state.tool = name;
  state.anchor = null;
  state.hover = null;
  photoCanvas.classList.toggle('active-tool', Boolean(name));
  for (const button of tools) button.classList.toggle('chosen', name === button.dataset.tool);
  byId('position').textContent = name ? '在照片上点第一处，再点第二处' : '';
  drawPhoto();
}

function normalPoint(event) {
  const rect = photoCanvas.getBoundingClientRect();
  const scale = Math.min(rect.width / photoCanvas.width, rect.height / photoCanvas.height);
  const width = photoCanvas.width * scale, height = photoCanvas.height * scale;
  const left = rect.left + (rect.width - width) / 2, top = rect.top + (rect.height - height) / 2;
  const x = (event.clientX - left) / width, y = (event.clientY - top) / height;
  return x >= 0 && x <= 1 && y >= 0 && y <= 1 ? [x, y] : null;
}

photoCanvas.addEventListener('pointermove', (event) => {
  if (!state.panel) return;
  const point = normalPoint(event);
  if (!point) return;
  byId('position').textContent = `原片 ${Math.round(point[0] * (state.panel.width - 1))}, ${Math.round(point[1] * (state.panel.height - 1))} px`;
  if (state.anchor) {
    state.hover = point;
    drawPhoto();
  }
});

photoCanvas.addEventListener('click', safe(async (event) => {
  const point = normalPoint(event);
  if (!state.tool || !point) return;
  if (!state.anchor) {
    state.anchor = point;
    return;
  }
  const initial = state.anchor;
  const name = state.tool;
  const [ax, ay] = initial, [bx, by] = point;
  if (name === 'trace' && Math.hypot((bx - ax) * state.panel.width, (by - ay) * state.panel.height) < 2) {
    throw Error('请选择有可见间距的第二个端点。');
  }
  if (name !== 'trace' && (Math.abs(ax - bx) < .01 || Math.abs(ay - by) < .01)) {
    throw Error('定位框过小，请重新选第二点。');
  }
  const coords = name === 'trace' ? [initial, point] :
    [Math.min(ax, bx), Math.min(ay, by), Math.max(ax, bx), Math.max(ay, by)];
  state.options = {...inputValues(), [name]: coords};
  if (name !== 'roi') state.mode = name === 'trace' ? 'trace' : 'rails';
  selectTool(null);
  renderModes();
  await run();
}));

plotCanvas.addEventListener('click', (event) => {
  const graph = state.result?.plot;
  if (!graph?.samples.length) return;
  const rect = plotCanvas.getBoundingClientRect();
  const left = 47, right = plotCanvas.width - 27;
  const x = (event.clientX - rect.left - 10) * plotCanvas.width / (rect.width - 20);
  const t = Math.max(0, Math.min(1, (x - left) / (right - left)));
  state.plotNote = null;
  if (graph.trace_parts) {
    const minX = graph.samples[0].x, maxX = graph.samples.at(-1).x;
    const at = minX + t * (maxX - minX);
    const part = graph.trace_parts.find(p => at >= p.from && at <= p.to);
    if (!part) {
      state.plotCursor = null;
      state.plotNote = '遮住段 · 无灰度读数 · 原片无落点';
      showAt(null);
      byId('position').textContent = '遮住段 · 原片无落点';
      drawPlot();
      return;
    }
    const frac = part.to > part.from ? (at - part.from) / (part.to - part.from) : 0;
    const [a, b] = part.ends;
    let best = null;
    for (const sample of graph.samples) {
      if (sample.y !== null && (!best || Math.abs(sample.x - at) < Math.abs(best.x - at))) best = sample;
    }
    state.plotCursor = best;
    showAt([Math.round(a[0] + frac * (b[0] - a[0])), Math.round(a[1] + frac * (b[1] - a[1]))]);
    drawPlot();
    return;
  }
  state.plotCursor = graph.samples[Math.round(t * (graph.samples.length - 1))];
  if (graph.trace_ends) {
    const [a, b] = graph.trace_ends;
    showAt(a.map((value, index) => Math.round(value + t * (b[index] - value))));
  }
  drawPlot();
});

for (const button of tools) button.addEventListener('click', () => selectTool(button.dataset.tool));
byId('marks').addEventListener('change', drawPhoto);
byId('settings').addEventListener('submit', safe(async (event) => { event.preventDefault(); await run(); }));
byId('upload').addEventListener('click', () => { byId('file').value = ''; byId('file').click(); });
byId('file').addEventListener('change', safe(async () => {
  const selected = byId('file').files[0];
  if (!selected) return;
  const data = new FormData();
  data.append('file', selected);
  const panel = await request('/panels', 'POST', data);
  await openPanel(panel.id);
}));
byId('demo').addEventListener('click', safe(async () => {
  const panel = await request('/demo', 'POST');
  await openPanel(panel.id);
}));
byId('pin').addEventListener('click', safe(async () => {
  state.options = inputValues();
  if (state.options.pins.length >= 12) throw Error('一张照片最多暂留 12 段剖线。');
  state.options = {...state.options, pins: [...state.options.pins, state.options.trace]};
  drawPins();
  await run();
}));
byId('apply').addEventListener('click', safe(async () => {
  const id = state.panel.id;
  const panel = await request(panelPath('/settings'), 'PUT', {revision: state.panel.revision, settings: inputValues()});
  if (state.panel.id !== id) return;
  state.panel = panel;
  state.options = panel.settings;
  notice('照片检查设置已保留');
  await run();
}));
byId('save').addEventListener('click', safe(async () => {
  const id = state.panel.id;
  const saved = await request(panelPath('/checks'), 'POST', {
    revision: state.panel.revision, mode: state.mode, settings: inputValues(),
  });
  if (state.panel.id !== id) return;
  state.panel = saved.panel;
  state.options = saved.check.settings;
  state.result = saved.check;
  state.marker = null;
  state.plotCursor = null;
  renderResult();
  notice('检查已保存，可下载 CSV 或标记 PNG');
}));

safe(async () => {
  const config = await request('/config');
  state.modes = config.modes;
  renderModes();
  listChecks();
  const records = await listPanels();
  const id = localStorage.getItem('cellscope-panel');
  const chosen = records.find(p => p.id === id) || records[0];
  if (chosen) await openPanel(chosen.id);
})();
