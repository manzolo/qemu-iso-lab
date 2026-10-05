(() => {
  'use strict';
  // Exported HTML remains a self-contained snapshot. Only the authenticated web
  // map polls its own origin, preserving the token query parameter.
  if (!/^https?:$/.test(location.protocol) || !/^\/labs\/[^/]+\/map$/.test(location.pathname)) return;
  const endpoint = new URL(location.href);
  endpoint.pathname += '-state';
  endpoint.hash = '';
  const map = document.getElementById('lab-topology');
  const status = document.getElementById('map-live');
  const inspector = document.getElementById('packet-inspector');
  const pauseButton = document.getElementById('packet-pause');
  const searchBox = document.getElementById('packet-search');
  // The inspector opens only from a cable's lens button and stays until closed: nothing pops
  // up while the pointer crosses the map (2026-10-05). It accumulates what the polls bring
  // (the server hands the last 24 headers per NIC each time) up to HISTORY rows, so the
  // filters have something to work on.
  const HISTORY = 500;
  let samples = new Map(), selected = null, paused = false, fresh = false, restoringFocus = false;
  let history = new Map();  // packet id -> packet, for the selected NIC
  let clearedBelow = 0;     // Clear drops what the current sample still carries, until newer ids arrive
  const filters = {proto: 'all', dirs: new Set(['TX', 'RX']), text: ''};
  let anchor = {x:24, y:24}, dragged = null;
  const keyOf = node => JSON.stringify([node.dataset.vm, node.dataset.nic]);
  const buttonOf = key => [...map.querySelectorAll('.nic')].find(node => keyOf(node) === key)?.querySelector('.inspect-btn');
  const positionInspector = () => {
    const width = inspector.offsetWidth, height = inspector.offsetHeight;
    const at = dragged || {x: anchor.x + 16, y: anchor.y + 16};
    inspector.style.left = `${Math.max(12, Math.min(at.x, innerWidth - width - 12))}px`;
    inspector.style.top = `${Math.max(12, Math.min(at.y, innerHeight - height - 12))}px`;
  };
  const protoClass = packet => ['TCP', 'UDP', 'ARP'].includes(packet.protocol) ? packet.protocol
    : packet.protocol.startsWith('ICMP') ? 'ICMP' : 'other';
  const endpointText = (address, port) => port === undefined ? address : `${address.includes(':') ? `[${address}]` : address}:${port}`;
  const matches = packet => {
    if (filters.proto !== 'all' && protoClass(packet) !== filters.proto) return false;
    if (!filters.dirs.has(packet.direction)) return false;
    if (!filters.text) return true;
    const hay = `${packet.protocol} ${endpointText(packet.source, packet.source_port)} ${endpointText(packet.destination, packet.destination_port)} ${packet.info} ${packet.source_mac} ${packet.destination_mac}`.toLowerCase();
    return hay.includes(filters.text);
  };
  const absorb = () => {
    const sample = samples.get(selected);
    if (!sample?.packets_available) return;
    for (const packet of sample.packets || []) if (packet.id > clearedBelow) history.set(packet.id, packet);
    if (history.size > HISTORY) {
      const ids = [...history.keys()].sort((a, b) => a - b);
      for (const id of ids.slice(0, history.size - HISTORY)) history.delete(id);
    }
  };
  function renderInspector() {
    map.querySelectorAll('.nic').forEach(node => {
      const open = keyOf(node) === selected;
      node.classList.toggle('inspected', open);
      node.querySelector('.inspect-btn')?.setAttribute('aria-pressed', String(open));
    });
    if (!selected) { inspector.hidden = true; return; }
    inspector.hidden = false;
    pauseButton.textContent = paused ? 'Resume' : 'Pause';
    pauseButton.setAttribute('aria-pressed', String(paused));
    const [vm, nic] = JSON.parse(selected);
    document.getElementById('packet-link').textContent = `${vm} / ${nic}`;
    const sample = samples.get(selected);
    const state = document.getElementById('packet-state');
    state.textContent = !fresh ? 'Updates unavailable or paused · last observed headers' : paused
      ? 'Paused · the list is frozen, capture goes on' : sample?.packets_available ? 'Live · newest packets first' : 'Packet capture unavailable';
    if (!paused) {
      absorb();
      const all = [...history.values()].sort((a, b) => b.id - a.id);
      const shown = all.filter(matches);
      const rows = document.getElementById('packet-rows');
      const fragment = document.createDocumentFragment();
      for (const packet of shown) {
        const row = document.createElement('tr');
        const cell = (text, className = '') => {
          const td = document.createElement('td');
          td.textContent = text;
          td.className = className;
          row.append(td);
          return td;
        };
        cell(new Date(packet.time * 1000).toLocaleTimeString('en-GB'), 'packet-time');
        cell(packet.direction, `packet-dir ${packet.direction === 'TX' ? 'packet-tx' : 'packet-rx'}`);
        cell(packet.protocol, 'packet-proto');
        const route = cell(`${endpointText(packet.source, packet.source_port)} → ${endpointText(packet.destination, packet.destination_port)}`, 'packet-route');
        const detail = document.createElement('div');
        detail.className = 'packet-detail';
        detail.textContent = packet.info;
        route.append(detail);
        route.title = `${packet.source_mac} → ${packet.destination_mac}`;
        cell(packet.bytes, 'packet-size');
        fragment.append(row);
      }
      rows.replaceChildren(fragment);
      const empty = document.getElementById('packet-empty');
      empty.hidden = shown.length > 0;
      empty.textContent = !sample?.packets_available && !all.length ? (sample?.packet_reason || 'Waiting for capture information…')
        : all.length && !shown.length ? 'No packet matches the filters.' : 'No recent packets. Try a ping between lab VMs.';
      const counts = ['TCP', 'UDP', 'ICMP', 'ARP', 'other'].map(kind => [kind, all.filter(packet => protoClass(packet) === kind).length])
        .filter(([, n]) => n).map(([kind, n]) => `${kind} ${n}`).join(' · ');
      document.getElementById('packet-counts').textContent = all.length ? `${shown.length} of ${all.length} packets${counts ? ' · ' + counts : ''}` : '';
    }
    positionInspector();
  }
  function openInspector(button, event) {
    const node = button.closest('.nic');
    const key = keyOf(node);
    if (selected === key && !event?.type?.startsWith('key')) { closeInspector(false); return; }  // the lens toggles
    if (selected !== key) { paused = false; history = new Map(); clearedBelow = 0; dragged = null; }
    selected = key;
    const rect = button.getBoundingClientRect();
    anchor = {x:rect.right, y:rect.bottom};
    renderInspector();
  }
  const closeInspector = (restoreFocus = false) => {
    const trigger = selected ? buttonOf(selected) : null;
    selected = null; paused = false; history = new Map(); clearedBelow = 0; dragged = null;
    renderInspector();
    if (restoreFocus && trigger) {
      restoringFocus = true;
      trigger.focus({preventScroll:true});
      restoringFocus = false;
    }
  };
  map.addEventListener('click', event => {
    const button = event.target.closest('.inspect-btn');
    if (button) openInspector(button, event);
  });
  map.addEventListener('keydown', event => {
    const button = event.target.closest('.inspect-btn');
    if (button && (event.key === 'Enter' || event.key === ' ')) {
      event.preventDefault(); openInspector(button, event); pauseButton.focus();
    }
  });
  pauseButton.addEventListener('click', () => { paused = !paused; renderInspector(); });
  document.getElementById('packet-clear').addEventListener('click', () => {
    clearedBelow = Math.max(0, ...[...history.keys()], ...((samples.get(selected)?.packets || []).map(p => p.id)));
    history = new Map(); renderInspector();
  });
  document.getElementById('packet-close').addEventListener('click', () => closeInspector(true));
  inspector.querySelectorAll('.chip[data-proto]').forEach(chip => chip.addEventListener('click', () => {
    filters.proto = chip.dataset.proto;
    inspector.querySelectorAll('.chip[data-proto]').forEach(other => other.setAttribute('aria-pressed', String(other === chip)));
    renderInspector();
  }));
  inspector.querySelectorAll('.chip[data-dir]').forEach(chip => chip.addEventListener('click', () => {
    const dir = chip.dataset.dir;
    if (filters.dirs.has(dir)) { if (filters.dirs.size > 1) filters.dirs.delete(dir); } else filters.dirs.add(dir);
    inspector.querySelectorAll('.chip[data-dir]').forEach(other => other.setAttribute('aria-pressed', String(filters.dirs.has(other.dataset.dir))));
    renderInspector();
  }));
  searchBox.addEventListener('input', () => { filters.text = searchBox.value.trim().toLowerCase(); renderInspector(); });
  // Drag by the header; the resize handle is the browser's own (CSS resize).
  const head = document.getElementById('packet-drag');
  head.addEventListener('pointerdown', event => {
    if (event.target.closest('button')) return;
    const rect = inspector.getBoundingClientRect();
    const offset = {x: event.clientX - rect.left, y: event.clientY - rect.top};
    const move = e => { dragged = {x: e.clientX - offset.x, y: e.clientY - offset.y}; positionInspector(); };
    const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    event.preventDefault();
  });
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && selected) closeInspector(true); });
  window.addEventListener('resize', positionInspector);
  const prepareCables = () => {
    map.querySelector('svg')?.setAttribute('role', 'group');
    map.querySelectorAll('.nic title').forEach(title => title.remove());
  };
  const illuminate = (link, bytes) => {
    if (!link) return;
    link.classList.toggle('active', bytes > 0);
    // A log scale keeps ping visible without making it look like a large transfer.
    const strength = Math.min(1, Math.log10(1 + bytes) / 6);
    link.style.setProperty('--traffic-strength', String(.45 + strength * .55));
    link.style.setProperty('--traffic-halo', String(.08 + strength * .3));
    link.style.setProperty('--traffic-speed', `${2 - strength * 1.6}s`);
  };
  prepareCables();
  let previous = new Map(), lastSvg = '', lastUpdate = '', timer;
  const clearTraffic = () => {
    map.querySelectorAll('.nic').forEach(node => {
      node.classList.remove('transmitting', 'receiving');
      node.querySelector('.traffic-label').textContent = '';
    });
    previous.clear();
    map.querySelectorAll('.link.active').forEach(link => illuminate(link, 0));
    fresh = false;
    renderInspector();
  };
  const rate = bytes => {
    if (bytes < 1024) return `${Math.round(bytes)} B/s`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB/s`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MiB/s`;
  };
  async function refresh() {
    if (document.hidden) {
      clearTraffic();
      status.textContent = 'Updates paused · tab hidden';
      timer = setTimeout(refresh, 2000);
      return;
    }
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8000);
    try {
      const response = await fetch(endpoint, {cache: 'no-store', signal: controller.signal});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      if (typeof data.svg !== 'string' || !data.states || !Array.isArray(data.traffic)) throw new Error('Invalid snapshot');
      if (document.hidden) { clearTraffic(); return; }
      if (data.svg !== lastSvg) {
        const hadFocus = document.activeElement?.closest?.('.inspect-btn') ? keyOf(document.activeElement.closest('.nic')) : null;
        map.innerHTML = data.svg; lastSvg = data.svg; prepareCables();
        if (hadFocus) buttonOf(hadFocus)?.focus({preventScroll:true});
      }
      samples = new Map(data.traffic.map(entry => [JSON.stringify([entry.vm, entry.nic]), entry]));
      const next = new Map();
      const segmentRates = new Map();
      let natRate = 0;
      map.querySelectorAll('.nic').forEach(node => {
        const key = JSON.stringify([node.dataset.vm, node.dataset.nic]);
        const sample = samples.get(key), old = previous.get(key);
        const label = node.querySelector('.traffic-label');
        node.classList.remove('transmitting', 'receiving');
        illuminate(node.querySelector('.link'), 0);
        if (!sample?.available || !node.classList.contains('online')) {
          label.textContent = node.classList.contains('online') ? 'Traffic unavailable' : 'Link off';
          return;
        }
        next.set(key, sample);
        const seconds = old ? sample.time - old.time : 0;
        // Resets, source changes and the initial sample establish a new baseline.
        const valid = old && sample.epoch === old.epoch && sample.source === old.source && seconds > 0
          && sample.rx >= old.rx && sample.tx >= old.tx;
        const tx = valid ? (sample.tx - old.tx) / seconds : 0;
        const rx = valid ? (sample.rx - old.rx) / seconds : 0;
        node.classList.toggle('transmitting', tx > 0);
        node.classList.toggle('receiving', rx > 0);
        illuminate(node.querySelector('.link'), tx + rx);
        if (node.dataset.segment) segmentRates.set(node.dataset.segment, Math.max(segmentRates.get(node.dataset.segment) || 0, tx + rx));
        else natRate = Math.max(natRate, tx + rx);
        label.textContent = valid ? `TX ${rate(tx)} / RX ${rate(rx)}` : 'Measuring…';
      });
      previous = next;
      map.querySelectorAll('.segment-track').forEach(track => illuminate(track.querySelector('.link'), segmentRates.get(track.dataset.segment) || 0));
      illuminate(map.querySelector('.nat-bus'), natRate);
      fresh = true;
      renderInspector();
      document.getElementById('running-count').textContent = `${Object.values(data.states).filter(vm => vm.running).length} running`;
      document.querySelectorAll('[data-member]').forEach(row => {
        const vm = data.states[row.dataset.member];
        if (vm) {
          row.querySelector('.member-state').textContent = vm.running ? 'running' : 'stopped';
          row.querySelector('.member-install').textContent = vm.install;
        }
      });
      lastUpdate = new Date().toLocaleTimeString();
      status.textContent = `Live · refreshed ${lastUpdate}`;
    } catch {
      clearTraffic();
      status.textContent = `Updates unavailable${lastUpdate ? ` · last update ${lastUpdate}` : ''}`;
    } finally {
      clearTimeout(timeout);
      timer = setTimeout(refresh, 2000);
    }
  }
  window.addEventListener('pagehide', () => { clearTimeout(timer); clearTraffic(); });
  refresh();
})();
