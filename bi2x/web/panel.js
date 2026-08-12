// Panel view: polls the local state endpoint, renders it, keeps an event log.

const BUTTONS = ['START', 'BT-A', 'BT-B', 'BT-C', 'BT-D', 'FX-L', 'FX-R']
const SYSTEM = ['TEST', 'SERVICE', 'COIN', 'HEADPHONE']
const ALL_INPUTS = [...BUTTONS, ...SYSTEM]
// The page also works when opened from somewhere else (a local web server, or the
// file itself): requests then go to the panel server explicitly instead of to the
// page's own origin, which would have no /state to answer with.
const API = location.port === '8740' ? '' : 'http://127.0.0.1:8740'
const PERIOD = 33            // ms, about 30 refreshes per second
const MAX_JOURNAL = 200

const el = {
  lien: document.getElementById('lien'),
  lienTxt: document.getElementById('lien-txt'),
  cadence: document.getElementById('cadence'),
  trames: document.getElementById('trames'),
  crc: document.getElementById('crc'),
  alerte: document.getElementById('alerte'),
  journal: document.getElementById('journal'),
  tableau: document.getElementById('tableau'),
  raz: document.getElementById('raz'),
  filtre: document.getElementById('seulement-appuis'),
}

const knobs = {
  volL: document.getElementById('knob-l'),
  volR: document.getElementById('knob-r'),
}

let lastSeq = 0
const previous = { volL: null, volR: null }
const moving = { volL: 0, volR: 0 }

// ------------------------------------------------------------------ volumes

const wrappedDelta = (nouveau, ancien) =>
  ((nouveau - ancien + 32768) & 0xffff) - 32768

// One revolution spans the whole range, so the needle angle IS the knob angle,
// and the graduation is that same position quartered onto the 0..1023 scale.
const updateKnob = (key, value, graduation, revolutions) => {
  const node = knobs[key]
  if (!node) return
  const angle = (value / 65536) * 360
  node.querySelector('.aiguille').style.transform =
    `translateY(-100%) rotate(${angle.toFixed(1)}deg)`
  node.querySelector('.brut').textContent = value
  node.querySelector('.degres').textContent = `${angle.toFixed(0)}°`
  node.querySelector('.graduation').textContent = graduation
  node.querySelector('.tours').textContent = `${revolutions.toFixed(2)} rev`

  // Dead zone: the ±16 idle jitter must not read as rotation.
  if (previous[key] !== null && Math.abs(wrappedDelta(value, previous[key])) > 32) {
    moving[key] = 6
  }
  previous[key] = value
  if (moving[key] > 0) moving[key] -= 1
  node.classList.toggle('bouge', moving[key] > 0)
}

// ------------------------------------------------------------------ etats

const updateButton = (nom, active) => {
  const node = document.getElementById(`btn-${nom}`)
  if (node) node.classList.toggle('actif', Boolean(active))
}

const updateSystem = (system) => {
  for (const nom of SYSTEM) {
    const node = document.getElementById(`sys-${nom}`)
    if (node) node.classList.toggle('actif', Boolean(system?.[nom]))
  }
}

// ------------------------------------------------------------------ tracking

const formatDuration = (ms) => (ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${ms} ms`)

// Transposed table: one column per input, three rows of measurements.
const updateTable = (tracking, boutons, system) => {
  const active = (nom) =>
    BUTTONS.includes(nom) ? Boolean(boutons?.[nom]) : Boolean(system?.[nom])

  const headRow = el.tableau.querySelector('thead tr')
  if (headRow.children.length !== ALL_INPUTS.length + 1) {
    headRow.insertAdjacentHTML('beforeend',
      ALL_INPUTS.map((nom) => `<th data-nom="${nom}">${nom}</th>`).join(''))
    for (const row of el.tableau.querySelectorAll('tbody tr')) {
      row.insertAdjacentHTML('beforeend',
        ALL_INPUTS.map((nom) => `<td data-nom="${nom}">-</td>`).join(''))
    }
  }

  for (const [i, nom] of ALL_INPUTS.entries()) {
    const s = tracking?.[nom] || { presses: 0, total_ms: 0, last_ms: 0 }
    const vu = active(nom)
    headRow.children[i + 1].classList.toggle('actif', vu)
    const values = {
      presses: String(s.presses),
      last: s.last_ms ? formatDuration(s.last_ms) : '-',
      total: s.total_ms ? formatDuration(s.total_ms) : '-',
    }
    for (const row of el.tableau.querySelectorAll('tbody tr')) {
      const cell = row.children[i + 1]
      cell.textContent = values[row.dataset.row]
      cell.classList.toggle('actif', vu)
      cell.classList.toggle('jamais', !s.presses && !vu)
    }
  }
}

// ------------------------------------------------------------------ journal

const appendEvents = (evenements) => {
  if (!evenements?.length) return
  const pressesOnly = el.filtre.checked
  const fragment = document.createDocumentFragment()
  for (const e of evenements) {
    lastSeq = Math.max(lastSeq, e.seq)
    if (pressesOnly && !e.active) continue
    const li = document.createElement('li')
    li.className = e.active ? 'on' : 'off'
    li.innerHTML =
      `<span class="stamp">${e.t.toFixed(2)}s</span>` +
      `<span class="nom">${e.input}</span>` +
      `<span class="action">${e.active ? 'pressed' : 'released'}</span>` +
      `<span class="duree">${e.duration_ms ? formatDuration(e.duration_ms) : ''}</span>`
    fragment.prepend(li)
  }
  el.journal.prepend(fragment)
  while (el.journal.children.length > MAX_JOURNAL) el.journal.lastChild.remove()
}

// ----------------------------------------------------------- raw input view

// Identified on this cabinet. The remaining bits are free and light up if anything
// else is wired, which is exactly how to find an extra input.
const KNOWN_BIT = { 6: 'START', 7: 'BT-A', 8: 'BT-B', 9: 'BT-C', 10: 'BT-D', 11: 'FX-L', 12: 'FX-R' }
// Bit 47 always reads 0: it is the top bit of a 7-bit field, not an input.
const PADDING_BIT = { 47: 'field padding' }

const updateBits = (bits) => {
  const grid = document.getElementById('bits')
  if (!bits?.length) return
  if (grid.children.length !== bits.length) {
    grid.innerHTML = bits.map((_, i) => {
      const nom = KNOWN_BIT[i] || PADDING_BIT[i]
      return `<i data-i="${i}" title="bit ${i}${nom ? `, ${nom}` : ', unassigned'}"></i>`
    }).join('')
  }
  bits.forEach((v, i) => {
    // Inputs are active low, so a bit at 0 means that input is being driven.
    const cell = grid.children[i]
    cell.classList.toggle('bas', v === 0)
    cell.classList.toggle('connu', Boolean(KNOWN_BIT[i]))
    cell.classList.toggle('structurel', Boolean(PADDING_BIT[i]))
  })
}

const updateRaw = (state) => {
  updateBits(state.bits)
  const c = state.channels || []
  for (let i = 0; i < 4; i += 1) {
    const node = document.getElementById(`ch${i}`)
    if (node) node.textContent = c[i] ?? 0
  }
  const entete = document.getElementById('entete')
  if (entete && state.header) {
    entete.textContent = state.header
      .map((b) => b.toString(16).padStart(2, '0').toUpperCase()).join(' ')
  }
}

// ----------------------------------------------------------- button lamps

// Plain 12 V LEDs, on or off: the protocol carries one intensity byte 0..255 per
// output (field offsets 17..23), but these lamps have no real dimming, hence the
// toggles, written as 0xff or 0x00. The state rides INSIDE the poll frame
// (SetOutputs); the first command switches the server from replayed polls to
// polls it builds itself.
const LAMPS = ['START', 'BT-A', 'BT-B', 'BT-C', 'BT-D', 'FX-L', 'FX-R']

const showLampFrame = (frame) => {
  const box = document.getElementById('lamp-trame-hex')
  if (box) box.textContent = frame ? spaced(frame) : '-'
}

// Repaint the rows from the server's lamp state, so the view matches what is sent.
const refreshLamps = async () => {
  try {
    const { lamps, frame } = await (await fetch(`${API}/lamp/state`, { cache: 'no-store' })).json()
    for (const nom of LAMPS) {
      const li = document.querySelector(`.lampes li[data-lampe="${nom}"]`)
      if (!li) continue
      const on = Boolean(lamps?.[nom])
      li.querySelector('.lamp-on').checked = on
      li.classList.toggle('allume', on)
    }
    showLampFrame(frame)
  } catch { /* server not up yet */ }
}

const postLamp = async (route, body) => {
  try {
    const r = await fetch(`${API}${route}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    showLampFrame((await r.json()).frame)
  } catch { /* ignore */ }
  refreshLamps()
}

const rowLamp = (nom) => {
  const li = document.createElement('li')
  li.dataset.lampe = nom
  li.innerHTML =
    `<label class="bascule"><input type="checkbox" class="lamp-on">` +
    `<span class="glissiere"></span></label>` +
    `<span class="output-name">${nom}</span>`
  li.querySelector('.lamp-on').addEventListener('change', (e) =>
    postLamp('/lamp/set', { lamp: nom, on: e.target.checked }))
  return li
}

const buildLamps = () => {
  const host = document.getElementById('lamps')
  for (const nom of LAMPS) host.append(rowLamp(nom))
  document.getElementById('lamp-allumer').addEventListener('click', () =>
    postLamp('/lamp/all', { on: true }))
  document.getElementById('lamp-eteindre').addEventListener('click', () =>
    postLamp('/lamp/all', { on: false }))
}

// ----------------------------------------------------------- LED test bench

// The eight addressable outputs and their LED counts (from the board's own frames).
const STRIP_LEDS = [74, 12, 12, 56, 56, 94, 38, 86]
const to8 = (v) => Math.round((v * 255) / 31)      // 5-bit channel back to 8-bit
const pixCss = ([r, g, b]) => `rgb(${to8(r)},${to8(g)},${to8(b)})`
const spaced = (h) => (h.match(/../g) || []).join(' ')

const showFrames = (frames) => {
  const box = document.getElementById('led-trame-hex')
  if (box) box.textContent = frames?.length ? frames.map(spaced).join('\n') : '-'
}

// Repaint the dots from the server's LED state, so the view matches what was sent.
const refreshLed = async () => {
  try {
    const { strips, frames } = await (await fetch(`${API}/led/state`, { cache: 'no-store' })).json()
    strips.forEach((strip, s) => {
      const row = document.querySelector(`.led-rangee[data-strip="${s}"]`)
      if (!row) return
      strip.pixels.forEach((px, i) => {
        if (row.children[i]) row.children[i].style.background = pixCss(px)
      })
    })
    showFrames(frames)
  } catch { /* server not up yet */ }
}

const postLed = async (route, body) => {
  try {
    const r = await fetch(`${API}${route}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    showFrames((await r.json()).frames)
  } catch { /* ignore */ }
  refreshLed()
}

const globalBrush = () => ({
  colour: document.getElementById('led-couleur').value,
  brightness: Number(document.getElementById('led-intensite').value),
})
const stripBrush = (banc) => ({
  colour: banc.querySelector('.led-col').value,
  brightness: Number(banc.querySelector('.led-int').value),
})

const buildLedBench = () => {
  const host = document.getElementById('led-bancs')
  host.innerHTML = STRIP_LEDS.map((n, s) =>
    `<div class="led-banc" data-strip="${s}">` +
    `<span class="led-nom">strip ${s} <i>${n} LED</i></span>` +
    `<label class="pinceau"><input type="color" class="led-col" value="#ff3040"></label>` +
    `<label class="pinceau intensite">` +
    `<input type="range" class="led-int" min="0" max="100" value="100">` +
    `<b class="led-int-val">100%</b></label>` +
    `<div class="led-rangee" data-strip="${s}">` +
    Array.from({ length: n }, (_, i) =>
      `<span class="led-point" title="strip ${s}, LED ${i}"></span>`).join('') +
    '</div></div>').join('')

  // Live intensity read-out, and apply a whole strip when its colour or slider changes.
  host.addEventListener('input', (e) => {
    if (!e.target.classList.contains('led-int')) return
    e.target.closest('.led-banc').querySelector('.led-int-val').textContent = `${e.target.value}%`
  })
  host.addEventListener('change', (e) => {
    if (!e.target.classList.contains('led-col') && !e.target.classList.contains('led-int')) return
    const banc = e.target.closest('.led-banc')
    postLed('/led/strip', { strip: Number(banc.dataset.strip), ...stripBrush(banc) })
  })

  // Global: apply the top colour to every strip, mirroring it onto each strip's controls.
  document.getElementById('led-tout').addEventListener('click', () => {
    const g = globalBrush()
    document.querySelectorAll('.led-banc').forEach((banc) => {
      banc.querySelector('.led-col').value = g.colour
      banc.querySelector('.led-int').value = g.brightness
      banc.querySelector('.led-int-val').textContent = `${g.brightness}%`
    })
    postLed('/led/all', g)
  })
  document.getElementById('led-eteindre').addEventListener('click', () => postLed('/led/clear', {}))
  const slider = document.getElementById('led-intensite')
  const label = document.getElementById('led-intensite-val')
  slider.addEventListener('input', () => { label.textContent = `${slider.value}%` })
}

// ------------------------------------------------------------------ loop

const render = (state) => {
  el.lien.classList.toggle('en-ligne', state.connected)
  el.lienTxt.textContent = state.connected ? 'board connected' : 'board offline'

  if (state.error) {
    el.alerte.textContent = state.error
    el.alerte.hidden = false
  } else {
    el.alerte.hidden = true
  }

  el.cadence.textContent = (state.rate || 0).toFixed(0)
  el.trames.textContent = state.frames || 0
  el.crc.textContent = state.frames
    ? `${Math.round((100 * state.crc_ok) / state.frames)} %`
    : '-'

  for (const nom of BUTTONS) updateButton(nom, state.buttons?.[nom])
  updateKnob('volL', state.volL || 0, state.gradL || 0, state.turnsL || 0)
  updateKnob('volR', state.volR || 0, state.gradR || 0, state.turnsR || 0)
  updateSystem(state.system)
  updateTable(state.tracking, state.buttons, state.system)
  updateRaw(state)
  appendEvents(state.events)
}

const loop = async () => {
  try {
    const response = await fetch(`${API}/state?since=${lastSeq}`, { cache: 'no-store' })
    render(await response.json())
  } catch {
    el.lien.classList.remove('en-ligne')
    el.lienTxt.textContent = 'server stopped'
    el.alerte.innerHTML =
      'No answer from the panel server on port 8740. Start it with ' +
      '<code>python bi2x/panel.py</code>, then open ' +
      '<a href="http://127.0.0.1:8740">http://127.0.0.1:8740</a>.'
    el.alerte.hidden = false
  }
  setTimeout(loop, PERIOD)
}

// Port picker: filled on load, reconnects on the fly when changed.
const portSelect = document.getElementById('port')

const loadPorts = async () => {
  try {
    const { ports, current } = await (await fetch(`${API}/ports`, { cache: 'no-store' })).json()
    portSelect.innerHTML = ports.length
      ? ports.map((p) => `<option value="${p.port}"${p.port === current ? ' selected' : ''}>` +
          `${p.port}${p.likely ? ' ★' : ''}</option>`).join('')
      : '<option value="">no port found</option>'
  } catch { portSelect.innerHTML = '<option value="">unavailable</option>' }
}

portSelect.addEventListener('change', async () => {
  if (!portSelect.value) return
  await fetch(`${API}/connect`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ port: portSelect.value }),
  })
})

el.raz.addEventListener('click', async () => {
  await fetch(`${API}/reset`, { cache: 'no-store' })
  el.journal.innerHTML = ''
  lastSeq = 0
})

buildLamps()
refreshLamps()
buildLedBench()
refreshLed()
loadPorts()
setInterval(loadPorts, 5000)
loop()
