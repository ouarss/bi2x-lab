// Panel view: polls the local state endpoint, renders it, keeps an event log.

const BUTTONS = ['START', 'BT-A', 'BT-B', 'BT-C', 'BT-D', 'FX-L', 'FX-R']
const SYSTEM = ['TEST', 'SERVICE', 'COIN', 'HEADPHONE']
const ALL_INPUTS = [...BUTTONS, ...SYSTEM]
// The page also works when opened from somewhere else (the file itself, or a
// port-less local web server): requests then go to the default panel server
// explicitly. Served by panel.py itself (any --http port), the page talks to
// its own origin.
const API = (location.protocol === 'file:' || !location.port)
  ? 'http://127.0.0.1:8740' : ''
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

// One byte as two uppercase hex digits.
const hex2 = (b) => b.toString(16).padStart(2, '0').toUpperCase()

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
    entete.textContent = state.header.map(hex2).join(' ')
  }
}

// ----------------------------------------------------------- button lamps

// Plain 12 V LEDs, on or off: the protocol carries one intensity byte 0..255 per
// output (field offsets 17..23), but these lamps have no real dimming, hence the
// toggles, written as 0xff or 0x00. The state rides INSIDE the poll frame
// (SetOutputs); the first command switches the server from replayed polls to
// polls it builds itself.
const LAMPS = ['START', 'BT-A', 'BT-B', 'BT-C', 'BT-D', 'FX-L', 'FX-R']

// The output field as it actually left for the board, read back from /state. This
// is the ground truth the toggle can only hope for: if the lamp bytes (17..23) read
// ff here and the lamp still stays dark, the frame is on the wire and the fault is
// downstream. If out_seq never moves, the worker is not building polls at all.
const octets = (hex, from, to) =>
  (hex.slice(from * 2, to * 2).match(/../g) || []).join(' ') || '-'
let dernierOutSeq = null
const showEmitted = (state) => {
  const box = document.getElementById('lamp-emis')
  if (!box) return
  const hex = state.out_field || ''
  if (!state.engaged || !hex) {
    box.textContent = state.engaged ? 'waiting for the board' : 'replaying recorded polls (no output driven yet)'
    return
  }
  const bouge = dernierOutSeq !== null && state.out_seq !== dernierOutSeq
  dernierOutSeq = state.out_seq
  box.innerHTML =
    `lamps 17..23 = ${octets(hex, 17, 24)}<br>` +
    `reader 25..27 = ${octets(hex, 25, 28)}   ${bouge ? 'live' : 'idle'} #${state.out_seq}`
}

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

// ------------------------------------------------------- cabinet patterns

// The patterns come from the server, so the list is whatever it knows how to
// play. "Custom" is the absence of a pattern: the strips stay as they are and
// the bench below takes over.
let currentPattern = null

const renderPatterns = (patterns, active, reader) => {
  const host = document.getElementById('motifs')
  if (!host) return
  if (patterns && !host.dataset.built) {
    host.innerHTML =
      patterns.map((p) =>
        `<button type="button" class="lien motif" data-motif="${p.name}"
                 title="${p.strips}">${p.label}</button>`).join('') +
      '<button type="button" class="lien motif" data-motif="">Custom</button>'
    host.dataset.built = '1'
  }
  currentPattern = active ?? null
  host.querySelectorAll('.motif').forEach((b) => {
    b.classList.toggle('actif', (b.dataset.motif || null) === currentPattern)
  })
  const etat = document.getElementById('motif-etat')
  if (etat) {
    const lit = reader && reader.some((c) => c > 0)
    etat.textContent = currentPattern
      ? `playing ${currentPattern}${lit ? `, reader ${reader.join(', ')}` : ''}`
      : 'custom'
  }
  document.getElementById('banc-led')?.classList.toggle('inactif', Boolean(currentPattern))
}

const postPattern = async (name) => {
  try {
    await fetch(`${API}/led/pattern`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: name || null,
        brightness: Number(document.getElementById('led-intensite').value),
      }),
    })
  } catch { /* ignore */ }
  refreshLed()
}

// While a pattern plays the strips change on their own, so the view has to
// follow. Only while its tab is open, and ten times a second, which is enough
// to read: the frames themselves go out at sixty.
const PATTERN_VIEW_PERIOD = 100
const patternViewLoop = async () => {
  if (activeTab === 'leds' && (currentPattern || currentReaderMode)) await refreshLed()
  setTimeout(patternViewLoop, PATTERN_VIEW_PERIOD)
}

const buildPatterns = () => {
  const host = document.getElementById('motifs')
  host.addEventListener('click', (e) => {
    const bouton = e.target.closest('.motif')
    if (bouton) postPattern(bouton.dataset.motif)
  })
  patternViewLoop()
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
    const { strips, frames, pattern, patterns, reader,
            reader_mode: rmode, reader_modes: rmodes } =
      await (await fetch(`${API}/led/state`, { cache: 'no-store' })).json()
    strips.forEach((strip, s) => {
      const row = document.querySelector(`.led-rangee[data-strip="${s}"]`)
      if (!row) return
      strip.pixels.forEach((px, i) => {
        if (row.children[i]) row.children[i].style.background = pixCss(px)
      })
    })
    showFrames(frames)
    showReader(reader)
    renderReaderModes(rmodes, rmode)
    renderPatterns(patterns, pattern, reader)
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

// The reader LED takes 8-bit levels, so its dot is painted straight from the
// bytes the server holds, without the strips' 5-bit round trip.
const showReader = (reader) => {
  const dot = document.getElementById('lecteur-point')
  if (dot && reader) dot.style.background = `rgb(${reader[0]},${reader[1]},${reader[2]})`
}

// The five reader looks the server can replay (breathing on its own thread);
// "Manual" is the absence of a preset, the brush above takes over.
let currentReaderMode = null

const renderReaderModes = (modes, active) => {
  const host = document.getElementById('lecteur-modes')
  if (!host) return
  if (modes && !host.dataset.built) {
    host.innerHTML =
      modes.map((m) =>
        `<button type="button" class="lien motif" data-rmode="${m.name}">${m.label}</button>`).join('') +
      '<button type="button" class="lien motif" data-rmode="">Manual</button>'
    host.dataset.built = '1'
    host.addEventListener('click', (e) => {
      const bouton = e.target.closest('[data-rmode]')
      if (bouton) postLed('/led/reader/mode', { mode: bouton.dataset.rmode || null })
    })
  }
  currentReaderMode = active ?? null
  host.querySelectorAll('[data-rmode]').forEach((b) => {
    b.classList.toggle('actif', (b.dataset.rmode || null) === currentReaderMode)
  })
}

const readerBrush = () => ({
  colour: document.getElementById('lecteur-couleur').value,
  brightness: Number(document.getElementById('lecteur-intensite').value),
})

const buildReader = () => {
  const slider = document.getElementById('lecteur-intensite')
  const label = document.getElementById('lecteur-intensite-val')
  const couleur = document.getElementById('lecteur-couleur')
  const send = () => envoiThrottle('reader', () => postLed('/led/reader', readerBrush()))
  slider.addEventListener('input', () => {
    label.textContent = `${slider.value}%`
    send()
  })
  slider.addEventListener('change', send)
  couleur.addEventListener('input', send)
  couleur.addEventListener('change', send)
  document.getElementById('lecteur-eteindre').addEventListener('click', () =>
    postLed('/led/reader', { colour: '#000000', brightness: 0 }))
}

// An <input type=color> fires `input` while the picker is open and `change` when
// it closes, but the native Windows picker can close without the page ever seeing
// a `change`: the colour was then only applied on the next slider move. So follow
// `input` too, throttled per control, with the last value always sent.
const SEND_THROTTLE = 120
const enAttente = new Map()

const envoiThrottle = (cle, fn) => {
  if (enAttente.has(cle)) {           // a send already went out in this window
    enAttente.set(cle, fn)            // keep the newest, it goes out at the end
    return
  }
  fn()
  enAttente.set(cle, null)
  setTimeout(() => {
    const dernier = enAttente.get(cle)
    enAttente.delete(cle)
    if (dernier) dernier()
  }, SEND_THROTTLE)
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

  // Live intensity read-out, and apply a whole strip as its colour or slider moves.
  const appliquerBanc = (cible) => {
    const banc = cible.closest('.led-banc')
    if (!banc) return
    const strip = Number(banc.dataset.strip)
    envoiThrottle(`strip${strip}`, () =>
      postLed('/led/strip', { strip, ...stripBrush(banc) }))
  }
  host.addEventListener('input', (e) => {
    if (!e.target.classList.contains('led-col') && !e.target.classList.contains('led-int')) return
    if (e.target.classList.contains('led-int')) {
      e.target.closest('.led-banc').querySelector('.led-int-val').textContent = `${e.target.value}%`
    }
    appliquerBanc(e.target)
  })
  host.addEventListener('change', (e) => {
    if (!e.target.classList.contains('led-col') && !e.target.classList.contains('led-int')) return
    appliquerBanc(e.target)
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

// ------------------------------------------------------------------ onglets

// One page shown at a time; the active tab lives in a plain variable, and the
// URL hash mirrors it so a tab can be opened directly (#leds, #debug, #howto).
const PAGES = ['panel', 'leds', 'debug', 'howto']
let activeTab = PAGES.includes(location.hash.slice(1)) ? location.hash.slice(1) : 'panel'

const showTab = (nom) => {
  activeTab = nom
  for (const page of PAGES) {
    document.getElementById(`page-${page}`).hidden = page !== nom
    document.querySelector(`.onglets [data-page="${page}"]`)
      .classList.toggle('actif', page === nom)
  }
}

const buildTabs = () => {
  document.querySelector('.onglets').addEventListener('click', (e) => {
    const nom = e.target.dataset.page
    if (!nom) return
    showTab(nom)
    history.replaceState(null, '', `#${nom}`)
  })
  showTab(activeTab)
}

// ----------------------------------------------------- debug, layer by layer

// The poll answer decoded level by level by the server (/debug). Refreshed a few
// times per second only: this view is for reading, not for reacting. It starts
// stopped, and only ever runs while its own tab is open, so it costs nothing
// until asked for. Nothing extra reaches the board: /debug returns the poll
// answer the worker has already decoded.
const DEBUG_PERIOD = 300
let debugPaused = true

const fieldSpan = (nom, hexa) =>
  `<span class="champ champ-${nom.toLowerCase()}" data-nom="${nom}">${spaced(hexa.toUpperCase())}</span>`

const chips = (host, entries) => {
  host.innerHTML = entries.length
    ? entries.map(([nom, on]) =>
        `<span class="puce${on ? ' actif' : ''}">${nom}</span>`).join('')
    : '<span class="puce">none</span>'
}

const renderDebug = ({ connected, layers }) => {
  const vide = document.getElementById('debug-vide')
  const corps = document.getElementById('debug-corps')
  if (!layers) {
    vide.hidden = false
    corps.hidden = true
    vide.textContent = connected
      ? 'waiting for a poll answer…'
      : 'board offline, waiting for a poll answer…'
    return
  }
  vide.hidden = true
  corps.hidden = false

  // Layer 0: the raw bytes, header fields labelled.
  document.getElementById('hex-fil').innerHTML =
    layers.wire.map((w) => fieldSpan(w.field, w.hex)).join('')

  // Layer 1: the header read out, and the deobfuscated payload.
  document.getElementById('champs-trame').innerHTML = [
    ['node', `0x${hex2(layers.node)}`],
    ['tag', `0x${hex2(layers.tag)}`],
    ['size', `${layers.size} bytes`],
    ['mode', `${layers.encoding} (${layers.mode})`],
    ['obfuscated', layers.obfuscated ? 'yes' : 'no'],
    ['header CRC4', layers.crc4_ok ? 'ok' : 'BAD'],
  ].map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join('')
  const bloc = layers.payload_hex.slice(layers.prefix_hex.length)
  document.getElementById('hex-payload').innerHTML =
    fieldSpan('echoes', layers.prefix_hex) + spaced(bloc.toUpperCase())

  // Layer 2: the block header, and record 0 cut into digital | analog.
  document.getElementById('nb-records').textContent = layers.records
  document.getElementById('hex-bloc').innerHTML =
    fieldSpan('header', layers.block_header_hex)
  document.getElementById('hex-record').innerHTML =
    fieldSpan('digital', layers.digital_hex) + fieldSpan('analog', layers.analog_hex)

  // Layer 3: what record 0 means.
  chips(document.getElementById('sens-boutons'),
    BUTTONS.map((nom) => [nom, layers.pressed.includes(nom)]))
  document.getElementById('sens-voll').textContent =
    `${layers.volL.raw} raw, ${layers.volL.graduation} / 1023`
  document.getElementById('sens-volr').textContent =
    `${layers.volR.raw} raw, ${layers.volR.graduation} / 1023`
  chips(document.getElementById('sens-system'),
    SYSTEM.map((nom) => [nom, Boolean(layers.system?.[nom])]))
}

const debugLoop = async () => {
  if (activeTab === 'debug' && !debugPaused) {
    try {
      const response = await fetch(`${API}/debug`, { cache: 'no-store' })
      renderDebug(await response.json())
    } catch { /* server not up yet */ }
  }
  setTimeout(debugLoop, DEBUG_PERIOD)
}

const buildDebug = () => {
  const bouton = document.getElementById('pause-debug')
  document.querySelector('.couches').classList.add('fige')
  bouton.addEventListener('click', () => {
    debugPaused = !debugPaused
    bouton.textContent = debugPaused ? 'start' : 'stop'
    document.querySelector('.couches').classList.toggle('fige', debugPaused)
  })
}

// -------------------------------------------- how it works, code snippets

// One shared modal; each step's "code" button fills it by step title.
const SNIPPETS = {
  'Poll':
`sp.write(poll[tag & 0x7F])        # ask for the panel state
tag = (tag + 1) & 0xFF
answer = sp.read(512)             # one 234-byte block, 17 samples deep`,

  'The frame':
`node, tag = raw[1], raw[2]        # AA | node | tag | size | flags | payload | crc7
j, size = 3, 0
while raw[j] & 0x80:              # size is a varint
    size = (size << 6) | (raw[j] & 0x3F); j += 1
size = (size << 7) | (raw[j] & 0x7F)
flags = raw[j + 1]
payload = raw[j + 2 : j + 2 + size]`,

  'Unscramble':
`def unscramble(payload, tag):
    x = (tag ^ 0xAA) & 0xFFFFFFFF
    out = bytearray()
    for b in payload:
        if (~b & 0xAA) & 0xFF == 0:      # odd bits all set (FF, AA): passes through
            out.append(b); continue
        x = (x * 0x41C64E6D + 0x3039) & 0xFFFFFFFF
        out.append(b ^ (x & (0x55 if b & 0x80 else 0x7F)))
    return bytes(out)`,

  'Block and records':
`block = payload[6:6 + 228]                  # skip the 6-byte prefix
header = block[:7]                          # 7-byte block header
records = [block[7 + 13*i : 7 + 13*(i + 1)] # 17 records of 13 bytes
          for i in range(17)]
record0 = records[0]                        # the newest sample`,

  'Meaning':
`BUTTONS = {"START": 6, "BT-A": 7, "BT-B": 8, "BT-C": 9,
           "BT-D": 10, "FX-L": 11, "FX-R": 12}
digital = int.from_bytes(record0[:7], "little")   # 56 bits, active low
pressed = [n for n, bit in BUTTONS.items()
           if not (digital >> bit) & 1]           # bit 0 = pressed
def knob_delta(new, old):                         # wrapped difference
    return ((new - old + 32768) & 0xFFFF) - 32768`,

  'Choose the command':
`def pixel(r, g, b):                    # r, g, b in 0..31
    w = (r << 10) | (g << 5) | b       # 15-bit 5-5-5 RGB
    return bytes([w & 0xFF, w >> 8])   # little-endian
payload = (b"\\x03\\x21" + bytes([strip, 0])         # SetTapeLedData
           + offset.to_bytes(2, "little")
           + bytes([count]) + pixels)

field = bytearray(44)                  # SetOutputs, inside the poll frame
field[0] = 0xFF                        # master
field[17:24] = lamps                   # START, BT-A..D, FX-L, FX-R
field[25:28] = reader_rgb              # card reader LED, 8 bits per channel
poll = b"\\x03\\x11" + bytes(field) + b"\\x03\\x10"`,

  'Pack':
`wire = compress(payload)   # sliding-window pack (85-byte window);
                           # the board unpacks it on its side`,

  'Scramble + checksum':
`stream = compress(payload) + bytes([crc7(payload)])   # crc7 of the PLAIN payload
wire = scramble(stream, tag ^ 0x55)   # same generator, seeded tag ^ 0x55`,

  'Frame it':
`flags = (4 << 5) | 0x10                       # mode 4, scrambled
size = varint(len(payload))
head = bytes([node, tag]) + size + bytes([flags & 0xF0])
flags |= crc4(head)                           # 4-bit header sum in the low nibble
frame = bytes([0xAA, node, tag]) + size + bytes([flags]) + wire`,

  'Send, then latch':
`for payload in pixel_frames:          # fills the board's buffer, shows nothing
    sp.write(encode_frame(tag, payload))
    tag = (tag + 1) & 0xFF

latch = b"\\x03\\x22" if pixel_frames else b""
sp.write(encode_frame(tag, b"\\x03\\x11" + field + latch + b"\\x03\\x10"))
tag = (tag + 1) & 0xFF                # one counter for every frame that goes out`,
}

const openCode = (titre) => {
  document.getElementById('modal-code-titre').textContent = titre
  document.getElementById('modal-code-corps').textContent = SNIPPETS[titre] || ''
  document.getElementById('voile-code').hidden = false
}

const closeCode = () => {
  document.getElementById('voile-code').hidden = true
}

const buildCode = () => {
  for (const bouton of document.querySelectorAll('.btn-code')) {
    bouton.addEventListener('click', () => openCode(bouton.dataset.etape))
  }
  const voile = document.getElementById('voile-code')
  voile.addEventListener('click', (e) => { if (e.target === voile) closeCode() })
  document.getElementById('fermer-code').addEventListener('click', closeCode)
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeCode() })
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

  // The wire readout stays live on every tab: it is the whole point of the diagnostic.
  showEmitted(state)

  // Only touch the DOM of the tab on screen: the header above stays live everywhere,
  // but the panel widgets and the raw view are hidden on the other tabs.
  if (activeTab === 'panel') {
    for (const nom of BUTTONS) updateButton(nom, state.buttons?.[nom])
    updateKnob('volL', state.volL || 0, state.gradL || 0, state.turnsL || 0)
    updateKnob('volR', state.volR || 0, state.gradR || 0, state.turnsR || 0)
    updateSystem(state.system)
    updateTable(state.tracking, state.buttons, state.system)
    appendEvents(state.events)
  } else if (activeTab === 'debug') {
    updateRaw(state)
  }
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

buildTabs()
buildDebug()
buildCode()
buildLamps()
refreshLamps()
buildLedBench()
buildReader()
buildPatterns()
refreshLed()
loadPorts()
setInterval(loadPorts, 5000)
loop()
debugLoop()
