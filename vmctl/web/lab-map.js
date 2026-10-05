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
  let expanded = new Set(), lastShown = [];  // opened rows (by packet id) and the list on screen
  let lastSignature = '';  // the rows are rebuilt only when the list or the opened rows change (a detail under the pointer must not vanish)
  const filters = {proto: 'all', dirs: new Set(['TX', 'RX']), expr: null, error: '', hint: ''};
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
  const haystack = packet => `${packet.protocol} ${endpointText(packet.source, packet.source_port)} ${endpointText(packet.destination, packet.destination_port)} ${packet.info} ${packet.source_mac} ${packet.destination_mac}`.toLowerCase();
  // --- The filter box: a small Wireshark/tcpdump-style language (2026-10-05, Manzolo: "port 80"
  // must not match 8080). `tcp.port == 80`, `port 22`, `host 10.0.2.2`, `ip.src 192.168.0.0/24`,
  // `len > 1000`, `tcp.flags contains SYN`, `icmp.type == 8`, bare `tcp`/`arp`/`ipv6`/`tx`,
  // `and`/`or`/`not`/parentheses. A word that is no field or protocol searches the text as before.
  const layerField = (packet, layer, field) => {
    const names = layer === 'ip' ? ['ipv4', 'ipv6'] : [layer];
    const found = (packet.layers || []).find(l => names.includes(String(l.name).toLowerCase()));
    if (!found) return undefined;
    const entry = (found.fields || []).find(([key]) => String(key).toLowerCase().includes(field));
    return entry ? String(entry[1]) : undefined;
  };
  const FIELDS = {
    port: p => [p.source_port, p.destination_port], 'tcp.port': p => p.protocol === 'TCP' ? [p.source_port, p.destination_port] : [],
    'udp.port': p => p.protocol === 'UDP' ? [p.source_port, p.destination_port] : [],
    srcport: p => p.source_port, sport: p => p.source_port, 'src.port': p => p.source_port,
    'tcp.srcport': p => p.protocol === 'TCP' ? p.source_port : undefined, 'udp.srcport': p => p.protocol === 'UDP' ? p.source_port : undefined,
    dstport: p => p.destination_port, dport: p => p.destination_port, 'dst.port': p => p.destination_port,
    'tcp.dstport': p => p.protocol === 'TCP' ? p.destination_port : undefined, 'udp.dstport': p => p.protocol === 'UDP' ? p.destination_port : undefined,
    host: p => [p.source, p.destination], addr: p => [p.source, p.destination], ip: p => [p.source, p.destination],
    'ip.addr': p => [p.source, p.destination], 'ipv6.addr': p => [p.source, p.destination],
    src: p => p.source, 'ip.src': p => p.source, 'ipv6.src': p => p.source, saddr: p => p.source, 'src.host': p => p.source,
    dst: p => p.destination, 'ip.dst': p => p.destination, 'ipv6.dst': p => p.destination, daddr: p => p.destination, 'dst.host': p => p.destination,
    mac: p => [p.source_mac, p.destination_mac], ether: p => [p.source_mac, p.destination_mac], 'eth.addr': p => [p.source_mac, p.destination_mac],
    'eth.src': p => p.source_mac, 'eth.dst': p => p.destination_mac,
    len: p => p.bytes, length: p => p.bytes, bytes: p => p.bytes, size: p => p.bytes, 'frame.len': p => p.bytes,
    proto: p => p.protocol, protocol: p => p.protocol, dir: p => p.direction, direction: p => p.direction, info: p => p.info, detail: p => p.info,
    flags: p => layerField(p, 'tcp', 'flags'), 'tcp.flags': p => layerField(p, 'tcp', 'flags'),
    'icmp.type': p => layerField(p, 'icmp', 'type'), 'icmpv6.type': p => layerField(p, 'icmpv6', 'type'),
    'icmp.code': p => layerField(p, 'icmp', 'code'), ttl: p => layerField(p, 'ipv4', 'time to live'), 'ip.ttl': p => layerField(p, 'ipv4', 'time to live'),
  };
  const FUZZY = new Set(['flags', 'tcp.flags', 'icmp.type', 'icmpv6.type', 'info', 'detail']);  // "==" means "contains" on texts
  const PROTOS = {
    tcp: p => p.protocol === 'TCP', udp: p => p.protocol === 'UDP', icmp: p => p.protocol === 'ICMP', icmpv6: p => p.protocol === 'ICMPv6',
    arp: p => p.protocol === 'ARP', ip: p => (p.layers || []).some(l => l.name === 'IPv4'), ipv4: p => (p.layers || []).some(l => l.name === 'IPv4'),
    ipv6: p => (p.layers || []).some(l => l.name === 'IPv6'), vlan: p => /VLAN/.test(p.info || ''), eth: () => true, ethernet: () => true,
    tx: p => p.direction === 'TX', rx: p => p.direction === 'RX', multicast: p => !!p.multicast, broadcast: p => p.destination_mac === 'ff:ff:ff:ff:ff:ff',
  };
  const ipv4 = text => { const m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(String(text)); return m && m.slice(1).every(n => +n < 256) ? ((+m[1] << 24) | (+m[2] << 16) | (+m[3] << 8) | +m[4]) >>> 0 : null; };
  const compare = (candidate, op, value, fuzzy) => {
    if (candidate === undefined || candidate === null || candidate === '') return false;
    const left = String(candidate).toLowerCase(), right = String(value).toLowerCase();
    const cidr = /^(.+)\/(\d{1,2})$/.exec(right);
    if (cidr && ipv4(cidr[1]) !== null && ipv4(left) !== null && (op === '==' || op === '!=')) {
      const bits = Math.min(32, +cidr[2]), mask = bits ? (~0 << (32 - bits)) >>> 0 : 0;
      const inside = ((ipv4(left) & mask) >>> 0) === ((ipv4(cidr[1]) & mask) >>> 0);
      return op === '==' ? inside : !inside;
    }
    const numeric = left !== '' && right !== '' && !isNaN(left) && !isNaN(right);
    if (op === '>' || op === '<' || op === '>=' || op === '<=') {
      if (!numeric) return false;
      const a = +left, b = +right;
      return op === '>' ? a > b : op === '<' ? a < b : op === '>=' ? a >= b : a <= b;
    }
    const equal = numeric ? +left === +right : fuzzy ? left.includes(right) : left === right;
    return op === 'contains' ? left.includes(right) : op === '!=' ? !equal : equal;
  };
  const compileFilter = text => {
    const tokens = [], re = /\s*(\(|\)|&&|\|\||==|!=|>=|<=|=|>|<|!|"[^"]*"|'[^']*'|[^\s()!=<>&|"']+)/y;
    let m;
    while (re.lastIndex < text.length && (m = re.exec(text))) tokens.push(m[1]);
    if (re.lastIndex < text.length && text.slice(re.lastIndex).trim()) throw new Error(`Cannot read "${text.slice(re.lastIndex).trim()}"`);
    let i = 0;
    const peek = () => tokens[i], next = () => tokens[i++];
    const lower = t => (t || '').toLowerCase();
    const isOp = t => ['==', '=', '!=', '>=', '<=', '>', '<', 'contains', '~'].includes(lower(t));
    const isKeyword = t => ['and', 'or', 'not', '&&', '||', '!', ')', '(', undefined].includes(lower(t)) || isOp(t);
    const unquote = t => /^(["']).*\1$/.test(t) ? t.slice(1, -1) : t;
    if (tokens.length > 1 && tokens.every(t => !FIELDS[lower(t)] && !PROTOS[lower(t)] && !isKeyword(t))) {
      const needle = tokens.map(unquote).join(' ').toLowerCase();
      return p => haystack(p).includes(needle);
    }
    const term = () => {
      const word = next();
      if (word === undefined) throw new Error('Missing a term at the end');
      const name = lower(word);
      if (isOp(peek())) {
        let op = lower(next()); if (op === '=') op = '=='; if (op === '~') op = 'contains';
        if (peek() === undefined) throw new Error(`Missing a value after "${word} ${op}"`);
        if (isKeyword(peek())) throw new Error(`Unexpected "${peek()}" after "${word} ${op}"`);
        const value = unquote(next());
        const getter = FIELDS[name] || (name.includes('.') ? (p => layerField(p, name.split('.')[0], name.split('.').slice(1).join(' '))) : null);
        if (!getter) throw new Error(`Unknown field "${word}"`);
        const fuzzy = FUZZY.has(name) || !FIELDS[name];
        return p => { const got = getter(p); const list = Array.isArray(got) ? got : [got];
          return op === '!=' ? list.every(c => c === undefined || c === null || c === '' || compare(c, op, value, fuzzy)) && list.some(c => c !== undefined && c !== null && c !== '') : list.some(c => compare(c, op, value, fuzzy)); };
      }
      if (FIELDS[name] && peek() !== undefined && !isKeyword(peek())) {  // tcpdump style: port 80, host 10.0.2.2
        const value = unquote(next()), getter = FIELDS[name], fuzzy = FUZZY.has(name);
        return p => { const got = getter(p); return (Array.isArray(got) ? got : [got]).some(c => compare(c, '==', value, fuzzy)); };
      }
      if (PROTOS[name]) return PROTOS[name];
      if (FIELDS[name]) return p => { const got = FIELDS[name](p); return (Array.isArray(got) ? got : [got]).some(c => c !== undefined && c !== null && c !== ''); };
      const needle = unquote(word).toLowerCase();
      return p => haystack(p).includes(needle);
    };
    const unary = () => {
      const t = lower(peek());
      if (t === 'not' || t === '!') { next(); const inner = unary(); return p => !inner(p); }
      if (t === '(') { next(); const inner = expression(); if (next() !== ')') throw new Error('Missing ")"'); return inner; }
      if (t === ')') throw new Error('Unexpected ")"');
      return term();
    };
    const conjunction = () => {  // two terms side by side are an implicit "and" ("echo reply")
      let left = unary();
      while (peek() !== undefined && !['or', '||', ')'].includes(lower(peek()))) {
        if (['and', '&&'].includes(lower(peek()))) next();
        const right = unary(); const l = left; left = p => l(p) && right(p);
      }
      return left;
    };
    const expression = () => {
      let left = conjunction();
      while (['or', '||'].includes(lower(peek()))) { next(); const right = conjunction(); const l = left; left = p => l(p) || right(p); }
      return left;
    };
    if (!tokens.length) return null;
    const compiled = expression();
    if (i < tokens.length) throw new Error(`Unexpected "${tokens[i]}"`);
    return compiled;
  };
  // Completion uses the same fields/getters as the filter, and only this NIC's history.
  // Token offsets keep the suffix intact when editing in the middle of an expression.
  const suggestions = document.getElementById('packet-suggestions');
  const guide = document.getElementById('packet-filter-guide');
  const helpButton = document.getElementById('packet-filter-help');
  const editor = searchBox.closest('.packet-filter-editor');
  let completions = [], activeCompletion = -1;
  const operators = ['==', '!=', '>', '<', '>=', '<=', 'contains'];
  const isOperator = word => [...operators, '=', '~'].includes(word);
  const isJoin = word => ['and', 'or', '&&', '||'].includes(word);
  const fieldKind = field => /port$/.test(field) ? 'port'
    : /^(len|length|bytes|size|frame.len|ttl|ip.ttl)$/.test(field) ? 'number'
    : /^(mac|ether|eth\.)/.test(field) ? 'MAC address'
    : /^(host|addr|ip|src|dst|saddr|daddr|ip\.(addr|src|dst)|ipv6\.(addr|src|dst)|(src|dst)\.host)$/.test(field) ? 'IP address'
    : 'text';
  const describeField = field => {
    const kind = fieldKind(field);
    const side = /src|^s(port|addr)$/.test(field) ? 'Source' : /dst|^d(port|addr)$/.test(field) ? 'Destination' : 'Source or destination';
    if (kind === 'port') return `${side} ${field.startsWith('tcp.') ? 'TCP ' : field.startsWith('udp.') ? 'UDP ' : ''}port`;
    if (kind.endsWith('address')) return `${side} ${kind}`;
    if (kind === 'number') return /ttl/.test(field) ? 'IPv4 time to live' : 'Frame length in bytes';
    if (/flags/.test(field)) return 'TCP flags, e.g. contains SYN';
    if (/icmp/.test(field)) return 'ICMP header type or code';
    return /dir/.test(field) ? 'Packet direction: TX or RX' : /proto/.test(field) ? 'Packet protocol' : 'Packet detail text';
  };
  const completionContext = () => {
    const text = searchBox.value, cursor = searchBox.selectionStart ?? text.length;
    const tokens = [...text.matchAll(/"[^"]*(?:"|$)|'[^']*(?:'|$)|&&|\|\||==|!=|>=|<=|[()=<>!~]|[^\s()=<>!~&|"']+/g)]
      .map(m => ({word:m[0], start:m.index, end:m.index + m[0].length}));
    const current = tokens.find(t => t.start <= cursor && cursor <= t.end && !['(', ')'].includes(t.word));
    const start = current?.start ?? cursor;
    const end = Math.max(current?.end ?? cursor, searchBox.selectionEnd ?? cursor);
    let phase = 'term', field = '', depth = 0;
    for (const token of tokens.filter(t => t.end <= start)) {
      const word = token.word.toLowerCase();
      if (word === '(') { depth++; phase = 'term'; }
      else if (word === ')') { depth--; phase = 'join'; }
      else if (isJoin(word)) { phase = 'term'; field = ''; }
      else if (phase === 'term' && ['not', '!'].includes(word)) continue;
      else if (phase === 'term') { field = word; phase = FIELDS[word] ? 'field' : 'join'; }
      else if (phase === 'field' && isOperator(word)) phase = 'value';
      else phase = 'join';
    }
    return {phase, field, depth, start, end, cursor, prefix:text.slice(start, cursor).toLowerCase()};
  };
  const observedValues = field => {
    const getter = FIELDS[field];
    if (!getter) return [];
    const values = new Set();
    for (const packet of history.values()) {
      const got = getter(packet);
      for (const value of Array.isArray(got) ? got : [got]) {
        if (value !== undefined && value !== null && value !== '') values.add(String(value));
      }
    }
    return [...values].sort((a, b) => a.localeCompare(b, undefined, {numeric:true}));
  };
  const closeSuggestions = () => {
    suggestions.hidden = true;
    searchBox.setAttribute('aria-expanded', 'false');
    searchBox.removeAttribute('aria-activedescendant');
    completions = []; activeCompletion = -1;
  };
  const closeGuide = () => { guide.hidden = true; helpButton.setAttribute('aria-expanded', 'false'); };
  const sizePopup = popup => {
    const room = inspector.getBoundingClientRect().bottom - editor.getBoundingClientRect().bottom - 16;
    popup.style.maxHeight = `${Math.max(0, Math.min(260, room))}px`;
  };
  const selectCompletion = index => {
    activeCompletion = index;
    [...suggestions.children].forEach((node, i) => node.setAttribute('aria-selected', String(i === index)));
    const node = suggestions.children[index];
    if (node) { searchBox.setAttribute('aria-activedescendant', node.id); node.scrollIntoView({block:'nearest'}); }
    else searchBox.removeAttribute('aria-activedescendant');
  };
  const showSuggestions = () => {
    closeGuide();
    const context = completionContext(), {phase, field, prefix, depth} = context;
    const choices = [];
    const add = (value, description, append = false) => choices.push({value, description, append});
    const joins = (append = false) => {
      add('and', 'Match both conditions', append); add('or', 'Match either condition', append);
      if (depth > 0) add(')', 'Close this group', append);
    };
    if (phase === 'term') {
      const names = prefix ? [...new Set([...Object.keys(FIELDS), ...Object.keys(PROTOS), 'not', '('])]
        : ['tcp', 'udp', 'ip.src', 'ip.dst', 'tcp.port', 'host'];
      for (const name of names) add(name, FIELDS[name] ? describeField(name) : name === 'not' ? 'Exclude a condition' : name === '(' ? 'Group conditions' : `Match ${name.toUpperCase()} packets`);
    } else if (phase === 'field' || phase === 'value') {
      const kind = fieldKind(field);
      if (phase === 'field') {
        const ops = ['port', 'number'].includes(kind) ? operators.slice(0, 6)
          : kind === 'text' ? ['contains', '==', '!='] : ['==', '!='];
        const descriptions = {'==':'Equal to', '!=':'Different from', '>':'Greater than', '<':'Less than', '>=':'At least', '<=':'At most', contains:'Contains text'};
        for (const op of ops) add(op, descriptions[op]);
        if (PROTOS[field]) joins();
      }
      const ports = {'22':'SSH', '53':'DNS', '80':'HTTP', '443':'HTTPS', '67':'DHCP server', '68':'DHCP client'};
      const observed = observedValues(field);
      const defaults = kind === 'port' ? ['53', '80', '443', '22', '67', '68']
        : /flags/.test(field) ? ['SYN', 'ACK', 'FIN', 'RST', 'PSH', 'URG']
        : /^(dir|direction)$/.test(field) ? ['TX', 'RX'] : [];
      for (const value of [...new Set([...observed, ...defaults])]) {
        // Quote text for the existing grammar; never turn packet text into markup.
        const quoted = /[\s()=<>!&|"']/.test(value)
          ? !value.includes('"') ? `"${value}"` : !value.includes("'") ? `'${value}'` : null : value;
        if (quoted === null) continue;
        add(quoted, [kind === 'port' ? ports[value] : '', observed.includes(value) ? 'Observed on this link' : 'Example value'].filter(Boolean).join(' · '));
      }
      if (prefix && ((['port', 'number'].includes(kind) && /^\d+$/.test(prefix)) || observed.some(v => v.toLowerCase() === prefix))) joins(true);
    } else joins();
    const needle = prefix.replace(/^["']/, '');
    completions = choices.filter(c => c.append || c.value.toLowerCase().replace(/^["']/, '').startsWith(needle)).slice(0, 6)
      .map(c => ({...c, start:c.append ? context.end : context.start, end:context.end}));
    if (!completions.length) { closeSuggestions(); return; }
    suggestions.replaceChildren(...completions.map((choice, i) => {
      const node = document.createElement('div');
      node.className = 'packet-filter-option'; node.id = `packet-suggestion-${i}`;
      node.setAttribute('role', 'option'); node.setAttribute('aria-selected', 'false');
      const code = document.createElement('code'), detail = document.createElement('small');
      code.textContent = choice.value; detail.textContent = choice.description;
      node.append(code, detail);
      node.addEventListener('pointerdown', event => event.preventDefault());
      node.addEventListener('click', () => acceptCompletion(i));
      return node;
    }));
    suggestions.hidden = false; sizePopup(suggestions);
    searchBox.setAttribute('aria-expanded', 'true'); selectCompletion(-1);
  };
  const acceptCompletion = index => {
    const choice = completions[index];
    if (!choice) return;
    const before = searchBox.value.slice(0, choice.start), after = searchBox.value.slice(choice.end);
    const text = `${choice.append && before && !/\s$/.test(before) ? ' ' : ''}${choice.value}${/^\s/.test(after) ? '' : ' '}`;
    searchBox.value = before + text + after;
    searchBox.focus();
    const cursor = before.length + text.length + (/^\s/.test(after) ? 1 : 0);
    searchBox.setSelectionRange(cursor, cursor);
    updateFilter(); showSuggestions();
  };
  const updateFilter = () => {
    filters.hint = '';
    try { filters.expr = compileFilter(searchBox.value.trim()); filters.error = ''; }
    catch (error) {
      let quote = '';
      for (const char of searchBox.value) {
        if (char === quote) quote = '';
        else if (!quote && ['"', "'"].includes(char)) quote = char;
      }
      const pending = document.activeElement === searchBox && (quote || /^(Missing a value|Missing a term|Missing "\)")/.test(error.message));
      if (pending) {
        filters.error = '';
        const {field} = completionContext();
        const kind = fieldKind(field);
        filters.hint = quote ? `Close the quoted value with ${quote}` : error.message.startsWith('Missing a value') ? `Enter ${kind === 'IP address' ? 'an IP address or IPv4 subnet' : kind === 'port' ? 'a port number' : 'a value'}`
          : error.message.startsWith('Missing a term') ? 'Add a condition' : 'Close the group with )';
        filters.hint += ' · previous filter remains active';
      } else { filters.expr = null; filters.error = error.message; }
    }
    searchBox.setAttribute('aria-invalid', String(!!filters.error));
    renderInspector();
  };
  searchBox.addEventListener('input', event => { if (!event.isComposing) { updateFilter(); showSuggestions(); } });
  searchBox.addEventListener('compositionend', () => { updateFilter(); showSuggestions(); });
  searchBox.addEventListener('focus', showSuggestions);
  searchBox.addEventListener('click', showSuggestions);
  searchBox.addEventListener('keyup', event => { if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) showSuggestions(); });
  searchBox.addEventListener('blur', () => { closeSuggestions(); if (filters.hint) updateFilter(); });
  searchBox.addEventListener('keydown', event => {
    if (event.isComposing) return;
    if ((event.ctrlKey && event.code === 'Space') || event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (suggestions.hidden || event.code === 'Space') showSuggestions();
      if (completions.length && event.code !== 'Space') {
        const index = activeCompletion < 0 ? (event.key === 'ArrowUp' ? completions.length - 1 : 0)
          : (activeCompletion + (event.key === 'ArrowUp' ? -1 : 1) + completions.length) % completions.length;
        selectCompletion(index);
      }
    } else if (event.key === 'Enter' && !suggestions.hidden) {
      event.preventDefault(); acceptCompletion(activeCompletion < 0 ? 0 : activeCompletion);
    }
  });
  editor.addEventListener('keydown', event => {
    if (event.key === 'Escape' && (!suggestions.hidden || !guide.hidden)) {
      event.preventDefault(); event.stopPropagation(); closeSuggestions(); closeGuide();
    }
  });
  helpButton.addEventListener('click', () => {
    const opening = guide.hidden;
    closeSuggestions(); closeGuide();
    if (opening) { guide.hidden = false; helpButton.setAttribute('aria-expanded', 'true'); sizePopup(guide); }
  });
  guide.querySelectorAll('[data-expression]').forEach(button => button.addEventListener('click', () => {
    searchBox.value = button.dataset.expression;
    searchBox.focus(); searchBox.setSelectionRange(searchBox.value.length, searchBox.value.length);
    closeGuide(); closeSuggestions(); updateFilter();
  }));
  document.addEventListener('pointerdown', event => { if (!editor.contains(event.target)) { closeSuggestions(); closeGuide(); } });
  editor.addEventListener('focusout', event => { if (!editor.contains(event.relatedTarget)) { closeSuggestions(); closeGuide(); } });
  new ResizeObserver(() => { if (!suggestions.hidden) sizePopup(suggestions); if (!guide.hidden) sizePopup(guide); }).observe(inspector);
  const matches = packet => {
    if (filters.proto !== 'all' && protoClass(packet) !== filters.proto) return false;
    if (!filters.dirs.has(packet.direction)) return false;
    return !filters.expr || filters.expr(packet);
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
    state.textContent = filters.hint || (filters.error ? `Filter error: ${filters.error} · showing every packet` : !fresh ? 'Updates unavailable or paused · last observed headers' : paused
      ? 'Paused · the list is frozen, capture goes on' : sample?.packets_available ? 'Live · newest packets first' : 'Packet capture unavailable');
    state.classList.toggle('error', !!filters.error);
    {
      if (!paused) absorb();
      const all = [...history.values()].sort((a, b) => b.id - a.id);
      const shown = paused ? lastShown : (lastShown = all.filter(matches));
      const rows = document.getElementById('packet-rows');
      const signature = JSON.stringify([shown.map(packet => packet.id), [...expanded]]);
      const fragment = document.createDocumentFragment();
      for (const packet of signature === lastSignature ? [] : shown) {
        const row = document.createElement('tr');
        row.className = 'packet-row' + (expanded.has(packet.id) ? ' open' : '');
        row.dataset.id = packet.id;
        row.tabIndex = 0;
        row.setAttribute('aria-expanded', String(expanded.has(packet.id)));
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
        if (expanded.has(packet.id)) fragment.append(detailRow(packet));
      }
      if (signature !== lastSignature) { rows.replaceChildren(fragment); lastSignature = signature; }
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
  // The detail of one packet: every decoded header with its fields, then the header bytes
  // (Ethernet through the transport header: the server never keeps the payload).
  function detailRow(packet) {
    const tr = document.createElement('tr');
    tr.className = 'packet-detail-row';
    const td = document.createElement('td');
    td.colSpan = 5;
    const box = document.createElement('div');
    box.className = 'packet-layers';
    for (const layer of packet.layers || []) {
      const section = document.createElement('section');
      const h = document.createElement('h4');
      h.textContent = layer.name;
      const dl = document.createElement('dl');
      for (const [name, value] of layer.fields || []) {
        const dt = document.createElement('dt'); dt.textContent = name;
        const dd = document.createElement('dd'); dd.textContent = value;
        dl.append(dt, dd);
      }
      section.append(h, dl);
      box.append(section);
    }
    if (!packet.layers?.length) {
      const note = document.createElement('p');
      note.textContent = 'No decoded headers for this packet (captured before the detail view existed).';
      box.append(note);
    }
    if (packet.header_hex) {
      const hex = document.createElement('section');
      hex.className = 'packet-hex';
      const h = document.createElement('h4');
      const bytes = packet.header_hex.length / 2;
      h.textContent = `Header bytes · ${bytes} of ${packet.bytes} on the wire · payload not captured`;
      const pre = document.createElement('pre');
      const lines = [];
      for (let i = 0; i < packet.header_hex.length; i += 32) {
        const chunk = packet.header_hex.slice(i, i + 32).match(/../g);
        const ascii = chunk.map(b => { const c = parseInt(b, 16); return c >= 32 && c < 127 ? String.fromCharCode(c) : '.'; }).join('');
        lines.push(`${(i / 2).toString(16).padStart(4, '0')}  ${chunk.slice(0, 8).join(' ').padEnd(23)}  ${chunk.slice(8).join(' ').padEnd(23)}  ${ascii}`);
      }
      pre.textContent = lines.join('\n');
      hex.append(h, pre);
      box.append(hex);
    }
    td.append(box);
    tr.append(td);
    return tr;
  }
  const toggleRow = row => {
    const id = Number(row.dataset.id);
    if (expanded.has(id)) expanded.delete(id); else expanded.add(id);
    renderInspector();
    inspector.querySelector(`tr.packet-row[data-id="${id}"]`)?.focus({preventScroll:true});
  };
  document.getElementById('packet-rows').addEventListener('click', event => {
    const row = event.target.closest('tr.packet-row');
    if (row && !window.getSelection()?.toString()) toggleRow(row);
  });
  document.getElementById('packet-rows').addEventListener('keydown', event => {
    const row = event.target.closest('tr.packet-row');
    if (row && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); toggleRow(row); }
  });
  function openInspector(button, event) {
    const node = button.closest('.nic');
    const key = keyOf(node);
    if (selected === key && !event?.type?.startsWith('key')) { closeInspector(false); return; }  // the lens toggles
    if (selected !== key) { paused = false; history = new Map(); clearedBelow = 0; expanded = new Set(); lastShown = []; lastSignature = ''; dragged = null; }
    selected = key;
    const rect = button.getBoundingClientRect();
    anchor = {x:rect.right, y:rect.bottom};
    renderInspector();
  }
  const closeInspector = (restoreFocus = false) => {
    closeSuggestions(); closeGuide();
    const trigger = selected ? buttonOf(selected) : null;
    selected = null; paused = false; history = new Map(); clearedBelow = 0; expanded = new Set(); lastShown = []; dragged = null;
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
    history = new Map(); expanded = new Set(); lastShown = []; lastSignature = ''; renderInspector();
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
  // SSH, Console and Manage under a machine open the dashboard's own view of it in a dialog on
  // this page (Manzolo wanted dialogs, not new tabs, 2026-10-05): the embedded detached console,
  // the browser SSH terminal, or the Machine panel alone (`panel=1`). "Open in a tab" keeps the
  // old behaviour at hand. Closing the dialog unloads the frame, so SSH and VNC disconnect.
  const vmDialog = document.getElementById('vm-dialog');
  const vmFrame = document.getElementById('vm-dialog-frame');
  const vmTab = document.getElementById('vm-dialog-tab');
  const ACTIONS = {ssh: 'SSH terminal', console: 'Console', vm: 'Manage'};
  const actionUrl = (action, vm, embedded) => {
    const url = new URL('/', location.href);
    url.searchParams.set('token', endpoint.searchParams.get('token') || '');
    if (action === 'console' || action === 'ssh') url.searchParams.set('detached', '1');
    else if (embedded) url.searchParams.set('panel', '1');
    url.hash = `${action}=${encodeURIComponent(vm)}`;
    return url;
  };
  // SSH is a small window that floats over the map (non-modal, dragged by its header, resized by
  // the browser's handle): a shell on one machine next to the packet inspector is how the
  // analyser gets tested (Manzolo, 2026-10-05). Console and Manage stay modal dialogs.
  const FLOATING = new Set(['ssh']);
  let dialogOpener = null, floatAt = null;
  const placeFloating = () => {
    const width = vmDialog.offsetWidth, height = vmDialog.offsetHeight;
    const at = floatAt || {x: innerWidth - width - 16, y: 16};
    vmDialog.style.left = `${Math.max(8, Math.min(at.x, innerWidth - width - 8))}px`;
    vmDialog.style.top = `${Math.max(8, Math.min(at.y, innerHeight - height - 8))}px`;
  };
  const openVmDialog = (action, vm, opener) => {
    if (!vmDialog || !ACTIONS[action]) return;
    const floating = FLOATING.has(action);
    if (vmDialog.open && vmDialog.classList.contains('floating') !== floating) closeVmDialog();
    dialogOpener = opener || null;
    document.getElementById('vm-dialog-kind').textContent = ACTIONS[action];
    document.getElementById('vm-dialog-title').textContent = vm;
    vmFrame.title = `${ACTIONS[action]} · ${vm}`;
    vmTab.href = actionUrl(action, vm, false).href;
    vmFrame.src = actionUrl(action, vm, true).href;
    vmDialog.dataset.vm = vm; vmDialog.dataset.action = action;
    vmDialog.classList.toggle('floating', floating);
    if (!floating) { vmDialog.style.left = vmDialog.style.top = ''; }
    if (!vmDialog.open) { if (floating) vmDialog.show(); else vmDialog.showModal(); }
    if (floating) placeFloating();
    vmFrame.focus();
  };
  document.getElementById('vm-dialog-drag')?.addEventListener('pointerdown', event => {
    if (!vmDialog.classList.contains('floating') || event.target.closest('button, a')) return;
    const rect = vmDialog.getBoundingClientRect();
    const offset = {x: event.clientX - rect.left, y: event.clientY - rect.top};
    vmDialog.classList.add('dragging');
    const move = e => { floatAt = {x: e.clientX - offset.x, y: e.clientY - offset.y}; placeFloating(); };
    const up = () => { vmDialog.classList.remove('dragging'); window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    event.preventDefault();
  });
  window.addEventListener('resize', () => { if (vmDialog?.open && vmDialog.classList.contains('floating')) placeFloating(); });
  // The `close` event arrives a task later than close(): unload the frame right away, and again
  // on the event for an Escape (the dialog's own cancel), which never comes through here.
  const releaseVmDialog = () => {
    if (vmFrame.getAttribute('src') !== 'about:blank') vmFrame.src = 'about:blank';
    delete vmDialog.dataset.vm; delete vmDialog.dataset.action;
    vmDialog.classList.remove('floating', 'dragging');
    const key = dialogOpener; dialogOpener = null;
    if (key) [...map.querySelectorAll('.vm-action')].find(n => n.dataset.vm === key.vm && n.dataset.action === key.action)?.focus({preventScroll:true});
  };
  const closeVmDialog = () => {
    if (!vmDialog?.open) return;
    vmDialog.close(); releaseVmDialog();
  };
  vmDialog?.addEventListener('close', releaseVmDialog);
  document.getElementById('vm-dialog-close')?.addEventListener('click', closeVmDialog);
  window.addEventListener('message', event => {
    if (event.origin !== location.origin || event.source !== vmFrame?.contentWindow) return;
    if (event.data && event.data.type === 'vmctl-close') closeVmDialog();
  });
  const actionOf = event => {
    const node = event.target.closest?.('.vm-action');
    return node && node.getAttribute('aria-disabled') !== 'true' ? node : null;
  };
  map.addEventListener('click', event => {
    const node = actionOf(event);
    if (!node) return;
    event.preventDefault();
    openVmDialog(node.dataset.action, node.dataset.vm, {vm: node.dataset.vm, action: node.dataset.action});
  });
  map.addEventListener('keydown', event => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    const node = actionOf(event);
    if (!node) return;
    event.preventDefault();
    openVmDialog(node.dataset.action, node.dataset.vm, {vm: node.dataset.vm, action: node.dataset.action});
  });
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
        const actionFocus = document.activeElement?.closest?.('.vm-action')?.dataset;
        map.innerHTML = data.svg; lastSvg = data.svg; prepareCables();
        if (hadFocus) buttonOf(hadFocus)?.focus({preventScroll:true});
        if (actionFocus) {
          const links = [...map.querySelectorAll('.vm-action')].filter(link => link.dataset.vm === actionFocus.vm);
          const target = links.find(link => link.dataset.action === actionFocus.action && link.getAttribute('aria-disabled') !== 'true')
            || links.find(link => link.dataset.action === 'vm');
          target?.focus({preventScroll:true});
        }
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
          // A NAT link of a guest without an agent says why (pfSense: "Traffic unavailable" looked like a fault, 2026-10-05).
          label.textContent = !node.classList.contains('online') ? 'Link off'
            : /guest-agent/.test(sample?.reason || '') ? 'No counters: no guest agent' : 'Traffic unavailable';
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
