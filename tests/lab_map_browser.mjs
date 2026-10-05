// Optional: PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs node tests/lab_map_browser.mjs
// Synthetic telemetry and intercepted HTTP only: no VM is started or probed.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = fileURLToPath(new URL('../', import.meta.url));
const fixture = JSON.parse(execFileSync('python3', ['-c', `
import json
from vmctl import config,labs
cfg=config.load_config()
names=labs.group_members(cfg,'k8s-lab')
states={name:{'running':i<2,'install':'verified'} for i,name in enumerate(names)}
lab=labs.model(cfg,'k8s-lab',states)
result={'html':labs.render_html(lab),'svg':labs._svg(lab),'states':states,'nics':[
{'vm':m['name'],'nic':n['id'],'available':m['running'],'source':'segment frames', 'rx':0,'tx':0,'time':1,'epoch':1}
for m in lab['members'] for n in m['nics']]}
lab['members'][0]['running']=False
result['stopped_svg']=labs._svg(lab)
print(json.dumps(result))
`], {cwd:root}));
const browser = await chromium.launch({headless:true, executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined});
try {
  const page = await browser.newPage({viewport:{width:1280,height:1100}, colorScheme:'dark'});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  let tick=1, fail=false, unavailable=false, stopped=false, requests=0, rx=0, tx=0, epoch=1;
  const target = fixture.nics.find(n=>n.vm==='k8s-lab-main' && n.nic!==fixture.nics[0].nic);
  const packet = () => ({id:tick,time:1720000000+tick,source:'172.20.6.1',destination:'172.20.6.11',
    source_mac:'52:54:00:00:00:01',destination_mac:'52:54:00:00:00:02',direction:'TX',protocol:'ICMP',
    info:`Echo request · id=42 seq=${tick}`,bytes:98});
  assert(target);
  await page.route('http://lab.test/**', async route => {
    const url = new URL(route.request().url());
    if(url.pathname.endsWith('/map')) return route.fulfill({contentType:'text/html',body:fixture.html});
    assert(url.pathname.endsWith('/map-state'));
    assert.equal(url.searchParams.get('token'),'fixture-token');
    requests++;
    if(fail) return route.fulfill({status:503,body:'unavailable'});
    const traffic=fixture.nics.map(n=>({...n,time:tick,epoch,packets_available:false,packets:[],
      packet_reason:'Packet inspection is available on LAN links; NAT has no packet capture.',
      ...(n.vm===target.vm && n.nic===target.nic ? {rx,tx,available:!unavailable && !stopped,
        packets_available:!unavailable&&!stopped, packets:[packet(),
          {...packet(),id:tick-1,time:1720000000+tick-.02,source:'172.20.6.11',destination:'172.20.6.1',direction:'RX',info:`Echo reply · id=42 seq=${tick}`},
          {...packet(),id:tick-2,time:1720000000+tick-.05,protocol:'TCP',source_port:43210,destination_port:16443,info:'SYN ACK',bytes:74}],
        packet_reason:stopped?'VM stopped':'Capture unavailable'}:{} )}));
    return route.fulfill({json:{svg:stopped?fixture.stopped_svg:fixture.svg,
      states:{...fixture.states,'k8s-lab-main':{running:!stopped,install:'verified'}},traffic}});
  });
  await page.clock.install();
  await page.goto('http://lab.test/labs/k8s-lab/map?token=fixture-token');
  await page.waitForFunction(()=>document.getElementById('map-live').textContent.startsWith('Live'));
  const cable = page.locator(`.nic[data-vm="${target.vm}"][data-nic="${target.nic}"]`);
  const advance = async () => {
    const before=requests;
    tick+=2;
    await page.clock.runFor(2100);
    await page.waitForFunction(()=>document.getElementById('map-live').textContent.startsWith('Live'));
    assert(requests>before);
  };
  assert.equal(await page.locator('.transmitting,.receiving').count(),0,'First sample is only a baseline');
  assert.equal(await page.locator('.link.active').count(),0,'VM power alone does not light cables');
  tx=196;
  await advance();
  assert(await cable.evaluate(n=>n.classList.contains('transmitting')));
  assert.equal(await page.locator('.transmitting').count(),1,'Only the NIC carrying traffic lights up');
  assert.equal(await cable.locator('.link.active').count(),1);
  assert.equal(await page.locator('.segment-bus.active').count(),1);
  assert.equal(await page.locator('.nat-bus.active').count(),0);
  assert((await cable.locator('.traffic-label').textContent()).includes('TX 98 B/s'));
  const lowStrength = await cable.locator('.link').evaluate(n=>parseFloat(n.style.getPropertyValue('--traffic-strength')));
  const lowSpeed = await cable.locator('.link').evaluate(n=>parseFloat(n.style.getPropertyValue('--traffic-speed')));
  rx=196;
  await advance();
  assert(await cable.evaluate(n=>n.classList.contains('receiving')&&!n.classList.contains('transmitting')));
  tx+=10000000;
  await advance();
  assert(await cable.locator('.link').evaluate((n,low)=>parseFloat(n.style.getPropertyValue('--traffic-strength'))>low,lowStrength));
  assert(await cable.locator('.link').evaluate((n,low)=>parseFloat(n.style.getPropertyValue('--traffic-speed'))<low,lowSpeed));
  await advance();
  assert.equal(await page.locator('.transmitting,.receiving').count(),0,'Idle cable stops animating');
  assert.equal(await page.locator('.link.active').count(),0,'Idle cables lose their glow');
  tx=1;rx=1;
  await advance();
  assert.equal(await page.locator('.transmitting,.receiving').count(),0,'Counter reset is not traffic');
  tx=1000;epoch=2;
  await advance();
  assert.equal(await page.locator('.transmitting,.receiving').count(),0,'New capture starts a baseline');
  tx=1200;rx=201;
  await advance();
  await page.emulateMedia({reducedMotion:'reduce'});
  assert.equal(await cable.locator('.signal.tx').evaluate(n=>getComputedStyle(n).animationName),'none');
  await page.emulateMedia({reducedMotion:'no-preference'});
  assert.equal(await cable.locator('.signal.tx').evaluate(n=>getComputedStyle(n).animationName),'packet-flow');
  // The wide transparent hit target makes the thin cable usable with a mouse.
  await cable.scrollIntoViewIfNeeded();
  const hit = await cable.locator('.cable-hit').boundingBox();
  await page.mouse.move(hit.x+hit.width/2,hit.y+hit.height/2);
  await page.waitForFunction(()=>!document.getElementById('packet-inspector').hidden);
  assert((await page.locator('#packet-link').textContent()).includes(target.vm));
  assert((await page.locator('#packet-rows').textContent()).includes('Echo request'));
  assert((await page.locator('#packet-rows').textContent()).includes('172.20.6.11'));
  await page.locator('#packet-pin').click();
  await page.mouse.move(5,5);
  await page.clock.runFor(400);
  assert(await page.locator('#packet-inspector').isVisible(),'Pinned inspector survives pointer leave');
  await page.locator('#packet-pause').click();
  const frozen = await page.locator('#packet-rows').textContent();
  await advance();
  assert.equal(await page.locator('#packet-rows').textContent(),frozen);
  await page.locator('#packet-pause').click();
  assert.notEqual(await page.locator('#packet-rows').textContent(),frozen);
  assert.equal(await page.locator('#packet-pin').getAttribute('aria-pressed'),'true');
  tx+=8192;rx+=4096;
  await advance();
  if(process.env.MAP_SCREENSHOT) await page.locator('.map-shell').screenshot({path:process.env.MAP_SCREENSHOT});
  unavailable=true;
  await advance();
  assert.equal(await cable.locator('.traffic-label').textContent(),'Traffic unavailable');
  assert.equal(await page.locator('.transmitting,.receiving').count(),0);
  assert.equal(await page.locator('#packet-rows tr').count(),0,'Unavailable capture clears packet rows');
  stopped=true;
  await advance();
  assert.equal(await cable.locator('.traffic-label').textContent(),'Link off');
  assert.equal(await page.locator('#running-count').textContent(),'1 running');
  assert.equal(await page.locator('[data-member="k8s-lab-main"] .member-state').textContent(),'stopped');
  assert.equal(await page.locator('#packet-empty').textContent(),'VM stopped');
  assert(await page.locator('#packet-inspector').isVisible(),'Pinned inspector survives topology replacement');
  fail=true;
  await page.clock.runFor(2100);
  await page.waitForFunction(()=>document.getElementById('map-live').textContent.startsWith('Updates unavailable'));
  assert.equal(await page.locator('.transmitting,.receiving').count(),0,'Failure clears old traffic');
  assert((await page.locator('#packet-state').textContent()).includes('Updates unavailable'));
  await page.keyboard.press('Escape');
  assert(await page.locator('#packet-inspector').isHidden());
  assert(await cable.evaluate(n=>document.activeElement===n),'Dismissal returns focus to the cable');
  const nat = page.locator('.nat-nic').first();
  await nat.focus();
  await nat.press('Enter');
  assert.equal(await page.locator('#packet-pin').getAttribute('aria-pressed'),'true');
  assert((await page.locator('#packet-empty').textContent()).includes('NAT'));
  await page.setViewportSize({width:390,height:844});
  await page.evaluate(()=>window.dispatchEvent(new Event('resize')));
  const panelBounds = await page.locator('#packet-inspector').boundingBox();
  assert(panelBounds.x>=0 && panelBounds.x+panelBounds.width<=390,'Inspector stays inside mobile viewport');
  assert(await page.locator('.map').evaluate(n=>n.scrollWidth>n.clientWidth),'Small screens scroll the diagram');
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'No page-wide overflow');
  assert.deepEqual(errors,[]);
  console.log('PASS: NIC attribution, live traffic, hover inspector, pin/pause/resume, offline/stale, keyboard, reduced motion, mobile layout');
} finally {
  await browser.close();
}
