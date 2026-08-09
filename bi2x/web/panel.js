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

const updateKnob = (key, value) => {
  const node = knobs[key]
  if (!node) return
  const angle = (value / 65536) * 360
  node.querySelector('.aiguille').style.transform =
    `translateY(-100%) rotate(${angle.toFixed(1)}deg)`
  node.querySelector('.brut').textContent = value
  node.querySelector('.degres').textContent = `${angle.toFixed(0)}°`

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
    const s = tracking?.[nom] || { appuis: 0, previous_ms: 0, cumul_ms: 0 }
    const vu = active(nom)
    headRow.children[i + 1].classList.toggle('actif', vu)
    const values = {
      appuis: String(s.appuis),
      previous: s.previous_ms ? formatDuration(s.previous_ms) : '-',
      cumul: s.cumul_ms ? formatDuration(s.cumul_ms) : '-',
    }
    for (const row of el.tableau.querySelectorAll('tbody tr')) {
      const cell = row.children[i + 1]
      cell.textContent = values[row.dataset.row]
      cell.classList.toggle('actif', vu)
      cell.classList.toggle('jamais', !s.appuis && !vu)
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
      `<span class="nom">${e.entree}</span>` +
      `<span class="action">${e.active ? 'pressed' : 'released'}</span>` +
      `<span class="duree">${e.duree_ms ? formatDuration(e.duree_ms) : ''}</span>`
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

// ------------------------------------------------------- sorties (LED)

// Button lamps: plain 12 V drivers on the board's first output bank.
const LAMPS = ['START', 'BT-A', 'BT-B', 'BT-C', 'BT-D', 'FX-L', 'FX-R']
// Addressable strips: one data line each, on the board's eight data outputs.
// Supply is not uniform: most are WS2812B at 5 V off the board's 5 V input rail,
// a few are 12 V parts. LED counts are those of the installed strips.
const STRIPS = [
  ['TITLE', 44], ['TITLE-W1', 36], ['TITLE-W2', 36], ['E.LED-L1', 7],
  ['E.LED-L2', 18], ['E.LED-L3', 14], ['E.LED-R1', 7], ['E.LED-R2', 18],
  ['E.LED-CEN', 30], ['WING-L', 56], ['WING-R', 56], ['V-LED', 39],
  ['NLED', 39], ['CON-LED', 8],
]

const outputStates = {}          // nom -> bool

const sendOutput = async (nom, active) => {
  outputStates[nom] = active
  try {
    await fetch(`${API}/output`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ canal: nom, active, couleur: document.getElementById('couleur').value }),
    })
  } catch { /* the server reports whether it can emit */ }
}

const rowSortie = (nom, detail) => {
  const li = document.createElement('li')
  li.innerHTML =
    `<label class="bascule"><input type="checkbox" data-canal="${nom}">` +
    `<span class="glissiere"></span></label>` +
    `<span class="output-name">${nom}</span>` +
    `<span class="channel">${detail}</span>`
  li.querySelector('input').addEventListener('change', (e) => {
    sendOutput(nom, e.target.checked)
    li.classList.toggle('allume', e.target.checked)
  })
  return li
}

const buildOutputs = () => {
  const lamps = document.getElementById('lamps')
  const strips = document.getElementById('strips')
  for (const nom of LAMPS) lamps.append(rowSortie(nom, 'lamp'))
  for (const [nom, n] of STRIPS) strips.append(rowSortie(nom, `${n} LED`))

  document.getElementById('tout-eteindre').addEventListener('click', () => {
    for (const c of document.querySelectorAll('.sorties input[type=checkbox]')) {
      if (c.checked) { c.checked = false; c.dispatchEvent(new Event('change')) }
    }
  })
  document.getElementById('couleur').addEventListener('change', () => {
    for (const [nom, active] of Object.entries(outputStates)) if (active) sendOutput(nom, true)
  })
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
  updateKnob('volL', state.volL || 0)
  updateKnob('volR', state.volR || 0)
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

buildOutputs()
loadPorts()
setInterval(loadPorts, 5000)
loop()
