// Optional browser regression: PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs node tests/webui_browser.mjs
// Uses a fixture API and a noVNC stub: no real VM, job or disk is touched.
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFileSync, mkdirSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = fileURLToPath(new URL('../', import.meta.url));
const html = readFileSync(root + 'vmctl/web/index.html');
const catalog = JSON.parse(execFileSync('python3', ['-c', 'import json; from vmctl.webui import command_catalog; print(json.dumps(command_catalog()))'], { cwd: root }));
const machine = (name, extra = {}) => ({ name, label: 'Arch Linux + Noctalia 5', family:'arch', memory_mb:8192, cpus:4, firmware:'EFI', running:false, installed:true, prepared:true, install_label:'verified', install_detail:'Boot verified by post-install; installed by bootstrap-archinstall.', disk_host:'7.8G', disk_capacity:'32G', iso_source:'cached', ssh_port:2226, has_ssh:true, has_archinstall:true, unattended_flow:'bootstrap-archinstall', ...extra });
const state = { version:'0.4.0', vms:[machine('arch-noctalia',{running:true}),machine('debian-server'),machine('proxmox-ve',{family:'proxmox'}),machine('proxmox-ve-node2',{family:'proxmox'}),machine('alpine-ci',{installed:false,prepared:false,install_label:'no disk'})], labs:[{group:'proxmox-lab', members:['proxmox-ve','proxmox-ve-node2'],start_order:['proxmox-ve','proxmox-ve-node2'],addresses:{'proxmox-ve':'10.10.10.2/24','proxmox-ve-node2':'10.10.10.3/24'}},{group:'new-lab',members:['alpine-ci'],start_order:['alpine-ci']}] };
let requests = [], failRun = false, failState = false, sshLaunches = 0, overrideRevision = 1, savedOverride = {};
const profileBase = {name:'Arch Linux + Noctalia',memory_mb:8192,cpus:4};
const server = createServer(async (req,res) => {
  if (req.url === '/' || req.url.startsWith('/?')) { res.setHeader('Content-Type','text/html'); return res.end(html); }
  res.setHeader('Content-Type','application/json');
  if (req.url.endsWith('/ssh-terminal')) { sshLaunches++; return res.end(JSON.stringify({terminal:'fixture'})); }
  if (req.url.endsWith('/override')) {
    if (req.method === 'POST') { let body=''; for await (const chunk of req) body+=chunk; const data=JSON.parse(body); assert.equal(data.revision,String(overrideRevision)); savedOverride=data.override; overrideRevision++; }
    return res.end(JSON.stringify({name:'arch-noctalia',base:profileBase,effective:{...profileBase,...savedOverride},override:savedOverride,revision:String(overrideRevision),local_only:false}));
  }
  if (req.url.startsWith('/api/state')) { res.statusCode=failState ? 503 : 200; return res.end(JSON.stringify(failState ? {error:'fixture offline'} : state)); }
  if (req.url === '/api/commands') return res.end(JSON.stringify(catalog));
  if (req.url === '/api/jobs') return res.end(JSON.stringify([{id:'web:fixture',status:'completed',command:'vmctl start arch-noctalia --headless --background',updated:1789900000}]));
  if (req.url.includes('/log?')) return res.end(JSON.stringify({text:'Fixture job completed.\n',offset:23,size:23,status:'completed'}));
  if (req.url === '/api/run') {
    let body=''; for await (const chunk of req) body+=chunk;
    requests.push(JSON.parse(body)); res.statusCode=failRun ? 400 : 200;
    return res.end(JSON.stringify(failRun ? {error:'Fixture rejected the command'} : {job:'web:fixture',command:'fixture command'}));
  }
  res.statusCode=404; res.end('{}');
});
await new Promise((resolve) => server.listen(0,'127.0.0.1',resolve));
let browser;
try {
  browser = await chromium.launch({headless:true});
  const context = await browser.newContext({viewport:{width:1440,height:1000},permissions:['clipboard-read','clipboard-write']});
  await context.route('https://cdn.jsdelivr.net/npm/@novnc/**', route => route.fulfill({contentType:'text/javascript',body:`export default class RFB extends EventTarget { constructor(el) { super(); el.innerHTML='<div class="console-empty">Fixture guest display</div>'; setTimeout(()=>this.dispatchEvent(new Event('connect')),20); } disconnect() {} focus() {} sendCtrlAltDel() { window.sentCAD=true; } }`}));
  if (!process.env.REAL_TERMINAL_ASSETS) await context.route('https://cdn.jsdelivr.net/npm/@xterm/**', route => {
    const url=route.request().url();
    const body=url.endsWith('.css') ? '.xterm {height:100%}' : url.includes('addon-fit') ? 'window.FitAddon={FitAddon:class { fit() {} }};' : `window.Terminal=class { constructor(){this.cols=100;this.rows=30;} loadAddon(){} open(el){this.el=el;el.textContent='Fixture SSH terminal';this.buffer={active:{length:1,getLine:()=>({translateToString:()=>this.el.textContent})}};} onData(fn){this.data=fn;} onResize(fn){this.resize=fn;} write(data){this.el.textContent+=typeof data==='string'?data:new TextDecoder().decode(data);} input(data){this.data(data);} focus(){} dispose(){} };`;
    return route.fulfill({contentType:url.endsWith('.css')?'text/css':'text/javascript',body});
  });
  const page = await context.newPage(), errors=[];
  const terminalMessages=[];
  await page.routeWebSocket(/\/api\/vm\/.*\/ssh\?/, ws=>{ws.onMessage(message=>{const value=JSON.parse(message);terminalMessages.push(value); if(value.type==='input')ws.send('fixture-user');});});
  page.on('pageerror', e=>errors.push(e.message));
  const check = (label) => console.log('PASS',label);
  const shot = async (name) => page.screenshot({path:root+'artifacts/webui-review/'+name+'.png',fullPage:true});
  mkdirSync(root+'artifacts/webui-review',{recursive:true});
  await page.goto(`http://127.0.0.1:${server.address().port}/?token=fixture`);
  await page.locator('#rows [data-vm]').first().waitFor();
  assert.equal(await page.locator('#running-count').textContent(),'1');
  assert.equal(await page.locator('#details .name').textContent(),'arch-noctalia');
  await shot('dashboard'); check('dashboard and profile');
  const states = await page.evaluate(() => {
    const base = S.vms[0];
    return ['start','stop','bootstrap-archinstall','fetch-iso','show'].map(job_command => stateOf({...base,job_status:'running',job_command})[0]);
  });
  assert.deepEqual(states,['Running','Stopping','Installing','Downloading ISO','Working']);
  assert.equal(await page.evaluate(()=>stateOf({...S.vms[0],job_status:'failed (1)',job_command:'show'})[0]),'Running');
  assert(await page.evaluate(()=>actionsFor({...S.vms[0],job_status:'running',job_command:'stop'}).every(a=>!a.label.includes('Installation'))));
  check('start and stop jobs are never mislabeled as installations');
  await page.locator('#ssh-open').click();
  await page.locator('#ssh-copy').click(); assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),'bin/vmctl shell arch-noctalia');
  await page.locator('#ssh-launch').click(); await page.waitForFunction(()=>document.getElementById('ssh-result').textContent.includes('requested')); assert.equal(sshLaunches,1);
  await page.locator('#ssh-browser').click(); await page.waitForFunction(()=>document.getElementById('terminal-status').textContent==='Session open');
  await page.evaluate(()=>terminal.input('whoami\r')); await page.waitForFunction(()=>Array.from({length:terminal.buffer.active.length},(_,i)=>terminal.buffer.active.getLine(i)?.translateToString()).join(' ').includes('fixture-user'));
  assert(terminalMessages.some(m=>m.type==='input'&&m.data==='whoami\r')); assert(terminalMessages.some(m=>m.type==='resize'));
  await shot('ssh-browser'); await page.locator('#terminal-close').click(); check('SSH host launch, copy and browser input/resize');
  await page.locator('#rows [data-vm="arch-noctalia"]').click({button:'right'});
  assert(await page.locator('#vm-context').isVisible());
  await page.locator('#vm-context').getByRole('menuitem',{name:'Customize profile…'}).click();
  await page.locator('#profile-memory:not([disabled])').waitFor();
  await page.locator('#profile-memory').fill('12288'); await page.locator('#profile-cpus').fill('6');
  await page.locator('#profile-save').click(); await page.waitForFunction(()=>document.getElementById('profile-feedback').textContent.startsWith('Saved locally'));
  assert.deepEqual(savedOverride,{memory_mb:12288,cpus:6});
  await page.locator('#profile-advanced summary').click(); await page.locator('#profile-json').fill('{oops'); assert(await page.locator('#profile-save').isDisabled());
  await page.locator('#profile-json').fill('{"memory_mb":12288,"cpus":6,"audio":false}'); assert(!(await page.locator('#profile-save').isDisabled()));
  await page.locator('#profile-save').click(); await page.waitForFunction(()=>document.getElementById('profile-feedback').textContent.startsWith('Saved locally')); assert.equal(savedOverride.audio,false);
  await shot('profile-editor'); await page.locator('#profile-reset').click(); await page.locator('#profile-save').click(); await page.waitForFunction(()=>document.getElementById('profile-feedback').textContent.startsWith('Saved locally')); assert.deepEqual(savedOverride,{});
  await page.locator('#profile-dialog [data-close]').click(); check('context menu, resource overrides, JSON validation, save and reset');
  await page.locator('.install-details summary').click();
  await page.evaluate(()=>render());
  assert.equal(await page.locator('.install-details').getAttribute('open'),''); check('polling preserves expanded profile');
  await page.locator('[data-filter=labs]').click();
  assert.equal(await page.locator('.lab').first().locator('.buttons button').first().textContent(),'Start stack');
  assert.equal(await page.locator('.lab').nth(1).locator('.buttons button').first().textContent(),'Install lab…');
  await shot('labs'); check('installed and new labs have different primary actions');
  await page.evaluate(() => document.addEventListener('contextmenu', e => window.labContextPrevented = e.defaultPrevented, {once:true}));
  await page.locator('[data-member="proxmox-ve-node2"] .member-address').click({button:'right'});
  assert(await page.evaluate(()=>window.labContextPrevented));
  assert.equal(await page.locator('#vm-context .context-title').textContent(),'proxmox-ve-node2');
  assert(await page.locator('#labs-view').isVisible());
  assert(await page.locator('#vm-context').getByRole('menuitem',{name:'Boot headless',exact:true}).isVisible());
  await page.keyboard.press('Escape');
  assert.equal(await page.evaluate(()=>document.activeElement.dataset.member),'proxmox-ve-node2');
  await page.locator('[data-member="proxmox-ve-node2"]').press('Shift+F10');
  await page.locator('#vm-context').getByRole('menuitem',{name:'SSH console…'}).click();
  assert.equal(await page.locator('#ssh-title').textContent(),'SSH · proxmox-ve-node2');
  await page.locator('#ssh-dialog [data-close]').click();
  await page.locator('#labs-view [data-vm="proxmox-ve"] .state').click({button:'right'});
  assert.equal(await page.locator('#vm-context .context-title').textContent(),'proxmox-ve');
  await page.locator('#vm-context').getByRole('menuitem',{name:'All commands for proxmox-ve…',exact:true}).click();
  assert((await page.locator('#cmd-count').textContent()).endsWith('proxmox-ve'));
  await page.locator('#cmd-dialog [data-close]').click();
  assert.equal(requests.length,0);
  check('lab VM context menu handles names, addresses, status and keyboard, targeting the correct VM');
  await page.locator('[data-filter=all]').click();
  await page.locator('#rows [data-vm="arch-noctalia"]').click();
  await page.locator('#all-commands').click();
  assert.equal(await page.locator('#cmd-list [data-cmd]').first().getAttribute('data-cmd'),'stop');
  await page.locator('#cmd-filter').press('Enter'); assert.equal(requests.length,0); check('search Enter does not run a command');
  await page.locator('#cmd-filter').fill('clone'); await page.locator('#arg1').fill('new-machine');
  await page.locator('#cmd-advanced summary').click(); await page.locator('#arg2').fill('2400');
  await page.locator('#cmd-filter').fill('show'); await page.locator('#cmd-filter').fill('clone');
  assert.equal(await page.locator('#arg1').inputValue(),'new-machine'); assert.equal(await page.locator('#arg2').inputValue(),'2400');
  assert.equal(await page.locator('#cmd-advanced').getAttribute('open'),''); check('drafts survive searches and command changes');
  await page.locator('#arg1').fill(''); assert(await page.locator('#cmd-run').isDisabled());
  await page.locator('#arg1').fill('new-machine'); assert(!(await page.locator('#cmd-run').isDisabled())); check('required arguments are validated');
  await page.locator('#cmd-filter').fill(''); await page.locator('#cmd-category').selectOption('Run');
  assert(await page.locator('#cmd-list [data-cmd=start]').count()); assert.equal(await page.locator('#cmd-list [data-cmd=show]').count(),0); check('category filtering');
  await page.locator('#cmd-category').selectOption(''); await page.locator('#cmd-filter').fill('flash');
  assert(await page.locator('#cmd-run').isDisabled()); assert(await page.locator('#cmd-copy').isDisabled());
  await page.locator('#arg1').fill('/dev/test'); await page.locator('#arg2').fill('/dev/wrong'); assert(await page.locator('#cmd-copy').isDisabled());
  await page.locator('#arg2').fill('/dev/test'); assert(!(await page.locator('#cmd-copy').isDisabled()));
  await page.locator('#cmd-advanced summary').click(); await page.locator('#arg5').check(); await page.locator('#arg6').check();
  assert(!(await page.locator('#arg5').isChecked())); assert(await page.locator('#arg6').isChecked());
  assert((await page.locator('#preview').textContent()).includes('--no-expand'));
  await page.locator('#cmd-filter').press('Control+Enter'); assert.equal(requests.length,0); check('flash is copy-only, device confirmation and exclusive expansion flags');
  await page.locator('#cmd-filter').fill('show'); await page.locator('#cmd-copy').click();
  assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),'vmctl show arch-noctalia'); check('copy command');
  await page.locator('#cmd-filter').fill('does-not-exist'); assert(await page.locator('#cmd-run').isDisabled()); check('empty search');
  await page.locator('#cmd-filter').fill('checkpoint'); await page.locator('#arg0').selectOption('restore');
  assert(!(await page.locator('#cmd-warning').evaluate(el=>el.classList.contains('hidden'))));
  page.once('dialog', d=>d.dismiss()); await page.locator('#cmd-run').click(); assert.equal(requests.length,0); check('destructive command confirmation');
  await page.locator('#cmd-filter').fill('show'); failRun=true; await page.locator('#cmd-run').click();
  await page.waitForFunction(()=>!document.getElementById('cmd-run').disabled);
  assert(await page.locator('#cmd-dialog').evaluate(el=>el.open)); check('API failure keeps command and form open');
  failRun=false; await page.locator('#cmd-run').click(); await page.locator('#log-dialog[open]').waitFor();
  assert.deepEqual(requests.at(-1).args,['show','arch-noctalia']);
  await page.locator('#log-dialog [data-close]').click(); check('successful command opens job log');
  await page.locator('#all-commands').click(); assert((await page.locator('#cmd-list').textContent()).includes('Recently used') === false); // show is already suggested, no duplicates
  await page.locator('#cmd-filter').fill('start'); await page.locator('#cmd-advanced summary').click();
  await shot('commands');
  await page.locator('#cmd-dialog [data-close]').click();
  await page.locator('#details [data-action="0"]').click(); await page.waitForFunction(()=>document.getElementById('vnc-status').textContent==='Connected');
  await page.locator('#vnc-fit').click(); assert.equal(await page.locator('#vnc-fit').textContent(),'Actual size');
  await page.locator('#vnc-cad').click(); assert(await page.evaluate(()=>window.sentCAD));
  await page.locator('#vnc-reconnect').click(); await page.waitForFunction(()=>document.getElementById('vnc-status').textContent==='Connected');
  await page.locator('#vnc-full').click(); await page.waitForFunction(()=>document.fullscreenElement?.id==='console-shell');
  await page.locator('#vnc-full').click(); await page.waitForFunction(()=>!document.fullscreenElement); await shot('console'); await page.locator('#vnc-close').click(); check('console controls, reconnect and full screen');
  failState=true; await page.evaluate(()=>refresh()); assert.equal(await page.locator('#connection').textContent(),'Disconnected · retrying');
  failState=false; await page.evaluate(()=>refresh()); assert.equal(await page.locator('#connection').textContent(),'Live'); check('connection recovery');
  for (const width of [909,390]) {
    await page.setViewportSize({width,height:900});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    await shot('dashboard-'+width);
    await page.locator('#all-commands').click();
    await page.locator('#cmd-filter').fill('start');
    const dialog=await page.locator('#cmd-dialog').boundingBox(), run=await page.locator('#cmd-run').boundingBox();
    assert(run.y+run.height<=dialog.y+dialog.height); assert(run.x+run.width<=width);
    assert(await page.locator('#cmd-dialog').evaluate(el=>el.scrollHeight<=el.clientHeight));
    await shot('commands-'+width); await page.locator('#cmd-dialog [data-close]').click();
  }
  check('909px and mobile layouts keep execution footer visible');
  assert.deepEqual(errors,[]); check('no browser JavaScript errors');
} finally { if (browser) await browser.close(); server.closeAllConnections(); await new Promise(resolve=>server.close(resolve)); }
