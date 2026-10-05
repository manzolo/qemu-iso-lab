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
  const pinButton = document.getElementById('packet-pin');
  const pauseButton = document.getElementById('packet-pause');
  let samples = new Map(), selected = null, pinned = false, paused = false, fresh = false, hideTimer, restoringFocus = false;
  let anchor = {x:24, y:24};
  const keyOf = node => JSON.stringify([node.dataset.vm, node.dataset.nic]);
  const positionInspector = () => {
    const width = inspector.offsetWidth, height = inspector.offsetHeight;
    inspector.style.left = `${Math.max(12, Math.min(anchor.x + 16, innerWidth - width - 12))}px`;
    inspector.style.top = `${Math.max(12, Math.min(anchor.y + 16, innerHeight - height - 12))}px`;
  };
  function renderInspector() {
    map.querySelectorAll('.nic').forEach(node => {
      node.classList.toggle('inspected', keyOf(node) === selected);
      node.setAttribute('aria-pressed', String(pinned && keyOf(node) === selected));
    });
    if (!selected) { inspector.hidden = true; return; }
    inspector.hidden = false;
    pinButton.textContent = pinned ? 'Pinned' : 'Pin';
    pinButton.setAttribute('aria-pressed', String(pinned));
    pauseButton.textContent = paused ? 'Resume' : 'Pause';
    pauseButton.setAttribute('aria-pressed', String(paused));
    const [vm, nic] = JSON.parse(selected);
    document.getElementById('packet-link').textContent = `${vm} / ${nic}`;
    const sample = samples.get(selected);
    const state = document.getElementById('packet-state');
    state.textContent = !fresh ? 'Updates unavailable or paused · last observed headers' : paused
      ? 'Paused · displayed packet list frozen' : sample?.packets_available ? 'Live · newest packets first' : 'Packet capture unavailable';
    if (!paused) {
      const rows = document.getElementById('packet-rows');
      const fragment = document.createDocumentFragment();
      const packets = sample?.packets_available ? sample.packets || [] : [];
      for (const packet of packets) {
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
        const endpointText = (address, port) => port === undefined ? address : `${address.includes(':') ? `[${address}]` : address}:${port}`;
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
      empty.hidden = packets.length > 0;
      empty.textContent = sample?.packets_available ? 'No recent packets. Try a ping between lab VMs.' : sample?.packet_reason || 'Waiting for capture information…';
    }
    positionInspector();
  }
  function selectCable(node, event, pin = false) {
    if (pinned && !pin) return;
    clearTimeout(hideTimer);
    const key = keyOf(node);
    if (selected !== key) paused = false;
    selected = key;
    pinned = pin;
    const rect = node.getBoundingClientRect();
    anchor = event && event.type !== 'keydown' && event.type !== 'focusin'
      ? {x:event.clientX, y:event.clientY} : {x:rect.left, y:rect.top + Math.min(rect.height, 80)};
    renderInspector();
  }
  const closeInspector = (restoreFocus = false) => {
    const trigger = [...map.querySelectorAll('.nic')].find(node => keyOf(node) === selected);
    clearTimeout(hideTimer);
    selected = null; pinned = false; paused = false;
    renderInspector();
    if (restoreFocus && trigger) {
      restoringFocus = true;
      trigger.focus({preventScroll:true});
      restoringFocus = false;
    }
  };
  const scheduleHide = () => {
    clearTimeout(hideTimer);
    hideTimer = setTimeout(() => {
      const focusedCable = document.activeElement?.closest('.nic');
      if (!pinned && !inspector.matches(':hover') && !inspector.contains(document.activeElement)
          && (!focusedCable || keyOf(focusedCable) !== selected)) closeInspector();
    }, 250);
  };
  map.addEventListener('pointerover', event => {
    const node = event.target.closest('.nic');
    if (node && !node.contains(event.relatedTarget)) selectCable(node, event);
  });
  map.addEventListener('pointerout', event => {
    const node = event.target.closest('.nic');
    if (node && !node.contains(event.relatedTarget)) scheduleHide();
  });
  map.addEventListener('click', event => {
    const node = event.target.closest('.nic');
    if (node) selectCable(node, event, true);
  });
  map.addEventListener('focusin', event => {
    const node = event.target.closest('.nic');
    if (node && !restoringFocus) selectCable(node, event);
  });
  map.addEventListener('focusout', scheduleHide);
  map.addEventListener('keydown', event => {
    const node = event.target.closest('.nic');
    if (node && (event.key === 'Enter' || event.key === ' ')) {
      event.preventDefault(); selectCable(node, event, true); pinButton.focus();
    }
  });
  inspector.addEventListener('pointerenter', () => clearTimeout(hideTimer));
  inspector.addEventListener('pointerleave', scheduleHide);
  inspector.addEventListener('focusout', scheduleHide);
  pinButton.addEventListener('click', () => { pinned = !pinned; renderInspector(); });
  pauseButton.addEventListener('click', () => { paused = !paused; renderInspector(); });
  document.getElementById('packet-close').addEventListener('click', () => closeInspector(true));
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && selected) closeInspector(true); });
  window.addEventListener('resize', positionInspector);
  const prepareCables = () => {
    map.querySelector('svg')?.setAttribute('role', 'group');
    map.querySelectorAll('.nic').forEach(node => {
      node.setAttribute('tabindex', '0');
      node.setAttribute('role', 'button');
      node.setAttribute('aria-controls', 'packet-inspector');
      node.setAttribute('aria-label', `Inspect packets: ${node.dataset.vm} / ${node.dataset.nic}`);
      node.querySelector('title')?.remove();
    });
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
      if (data.svg !== lastSvg) { map.innerHTML = data.svg; lastSvg = data.svg; prepareCables(); }
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
