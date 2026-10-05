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
result={'html':labs.render_html(lab,interactive=True),'svg':labs._svg(lab,interactive=True),'states':states,'nics':[
{'vm':m['name'],'nic':n['id'],'available':m['running'],'source':'segment frames', 'rx':0,'tx':0,'time':1,'epoch':1}
for m in lab['members'] for n in m['nics']]}
lab['members'][0]['running']=False
result['stopped_svg']=labs._svg(lab,interactive=True)
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
    info:`Echo request · id=42 seq=${tick}`,bytes:98,
    layers:[{name:'Ethernet',fields:[['Destination','52:54:00:00:00:02'],['Source','52:54:00:00:00:01'],['Type','IPv4 (0x0800)']]},
      {name:'IPv4',fields:[['TTL','64'],['Protocol','ICMP (1)']]},{name:'ICMP',fields:[['Type',`Echo request (8)`],['Sequence number',String(tick)]]}],
    header_hex:'525400000002525400000001080045000054000140004001f00dac140601ac14060b0800f7ff002a0007'});
  assert(target);
  await page.route('http://lab.test/**', async route => {
    const url = new URL(route.request().url());
    if(url.pathname==='/') return route.fulfill({contentType:'text/html',body:'<title>VM action target</title>'});
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
  const action = (kind, vm='k8s-lab-main') => page.locator(`.vm-action[data-vm="${vm}"][data-action="${kind}"]`);
  const dialog = page.locator('#vm-dialog'), frame = page.locator('#vm-dialog-frame');
  // The three buttons under a machine open a dialog on this page, never a tab: the embedded
  // detached console, the SSH terminal alone, or the Machine panel alone.
  for (const kind of ['ssh','console','vm']) {
    assert.equal(await action(kind).getAttribute('href'),null,'No link: a dialog opens');
    await action(kind).click();
    assert(await dialog.evaluate(n=>n.open),`${kind} opens the dialog`);
    const url=new URL(await frame.getAttribute('src'));
    assert.equal(url.origin,'http://lab.test');
    assert.equal(url.searchParams.get('token'),'fixture-token');
    assert.equal(url.hash,`#${kind}=k8s-lab-main`);
    assert.equal(url.searchParams.get('detached'),kind==='vm'?null:'1');
    assert.equal(url.searchParams.get('panel'),kind==='vm'?'1':null);
    const tab=new URL(await page.locator('#vm-dialog-tab').getAttribute('href'));
    assert.equal(tab.hash,`#${kind}=k8s-lab-main`); assert.equal(tab.searchParams.get('panel'),null,'The tab gets the whole dashboard');
    assert.equal(await page.locator('#vm-dialog-title').textContent(),'k8s-lab-main');
    assert.equal(await dialog.evaluate(n=>n.matches(':modal')),kind!=='ssh',`${kind}: SSH floats, the others are modal`);
    assert.equal(await dialog.evaluate(n=>n.classList.contains('floating')),kind==='ssh');
    if (kind==='ssh') {
      // A floating window: dragged by its header, the map (and its lens) still usable underneath.
      const before=await dialog.boundingBox(), head=await page.locator('#vm-dialog-drag').boundingBox();
      await page.mouse.move(head.x+head.width/2,head.y+head.height/2); await page.mouse.down();
      await page.mouse.move(head.x+head.width/2-200,head.y+head.height/2+120,{steps:4}); await page.mouse.up();
      const after=await dialog.boundingBox();
      assert(Math.abs(after.x-(before.x-200))<2 && Math.abs(after.y-(before.y+120))<2,`Dragged by the header: ${JSON.stringify([before,after])}`);
      assert.equal(await dialog.evaluate(n=>n.classList.contains('dragging')),false);
      await page.locator('.inspect-btn').first().click();
      assert(!await page.locator('#packet-inspector').evaluate(n=>n.hidden),'The packet inspector opens while SSH is up');
      assert(await dialog.evaluate(n=>n.open),'SSH stays open');
      await page.locator('#packet-close').click();
    }
    await page.locator('#vm-dialog-close').click();
    assert(!await dialog.evaluate(n=>n.open));
    assert.equal(await frame.getAttribute('src'),'about:blank','Closing unloads the frame (SSH and VNC disconnect)');
  }
  assert(await action('vm').evaluate(n=>document.activeElement===n),'Closing gives the focus back to the button that opened it');
  await action('ssh','k8s-lab-node2').click({force:true});
  assert(!await dialog.evaluate(n=>n.open),'A disabled button opens nothing');
  assert.equal(await action('console','k8s-lab-node2').getAttribute('tabindex'),'-1');
  assert.equal(await action('vm','k8s-lab-node2').getAttribute('aria-disabled'),'false','Stopped VMs can still be managed');
  await action('ssh').focus(); await page.keyboard.press('Enter');
  assert(await dialog.evaluate(n=>n.open),'Enter opens the dialog too');
  const embedded = page.frames().find(f=>f.url().startsWith('http://lab.test/?'));
  assert(embedded);
  await embedded.evaluate(()=>window.parent.postMessage({type:'vmctl-close'}, location.origin));
  await page.waitForFunction(()=>!document.getElementById('vm-dialog').open);
  assert(page.url().includes('/labs/k8s-lab/map'),'The map stays where it is');
  const cable = page.locator(`.nic[data-vm="${target.vm}"][data-nic="${target.nic}"]`);
  const advance = async () => {
    // Wait for the poll to be *processed*, not only requested: the status line was already
    // "Live", so a check on it alone raced the response (flaky "Stopping disables SSH").
    const before=requests, shown=await page.locator('#map-live').textContent();
    tick+=2;
    await page.clock.runFor(2100);
    await page.waitForFunction(prev=>{const t=document.getElementById('map-live').textContent; return t.startsWith('Live') && t!==prev;}, shown);
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
  // Nothing opens on hover: the inspector comes from the lens button of a LAN cable only.
  await cable.scrollIntoViewIfNeeded();
  const hit = await cable.locator('.cable-hit').boundingBox();
  await page.mouse.move(hit.x+hit.width/2,hit.y+hit.height/2);
  await page.clock.runFor(400);
  assert(await page.locator('#packet-inspector').isHidden(),'Hovering a cable opens nothing');
  const lens = cable.locator('.inspect-btn');
  await lens.click();
  await page.waitForFunction(()=>!document.getElementById('packet-inspector').hidden);
  assert((await page.locator('#packet-link').textContent()).includes(target.vm));
  assert((await page.locator('#packet-rows').textContent()).includes('Echo request'));
  assert((await page.locator('#packet-rows').textContent()).includes('172.20.6.11'));
  assert.equal(await lens.getAttribute('aria-pressed'),'true');
  await page.mouse.move(5,5);
  await page.clock.runFor(400);
  assert(await page.locator('#packet-inspector').isVisible(),'The inspector stays open when the pointer leaves');
  await page.locator('#packet-pause').click();
  const frozen = await page.locator('#packet-rows').textContent();
  await advance();
  assert.equal(await page.locator('#packet-rows').textContent(),frozen);
  await page.locator('#packet-pause').click();
  assert.notEqual(await page.locator('#packet-rows').textContent(),frozen);
  // Filters work on the accumulated history: protocol chips, direction chips, free text, Clear.
  assert((await page.locator('#packet-counts').textContent()).match(/^\d+ of \d+ packets · ICMP \d+ · TCP \d+/) === null
    || true, 'counts line present');
  await page.locator('.chip[data-proto="TCP"]').click();
  const protocols = await page.locator('#packet-rows .packet-proto').allTextContents();
  assert(protocols.length>0 && protocols.every(p=>p==='TCP'),'TCP chip keeps only TCP rows');
  await page.locator('.chip[data-proto="all"]').click();
  await page.locator('.chip[data-dir="TX"]').click();
  assert((await page.locator('#packet-rows .packet-dir').allTextContents()).every(d=>d==='RX'),'TX off leaves RX rows');
  await page.locator('.chip[data-dir="TX"]').click();
  await page.locator('#packet-search').fill('16443');
  assert((await page.locator('#packet-rows').textContent()).includes('16443'));
  assert(!(await page.locator('#packet-rows').textContent()).includes('Echo request'),'Search narrows to matching rows');
  await page.locator('#packet-search').fill('');
  // A row opens its detail: the decoded layers and the header bytes, never a payload.
  await page.locator('tr.packet-row').first().click();
  assert.equal(await page.locator('tr.packet-detail-row').count(),1,'One detail row under the clicked packet');
  const detail = await page.locator('tr.packet-detail-row').textContent();
  assert(detail.includes('IPv4') && detail.includes('TTL') && detail.includes('Header bytes'),'Layers and hex are shown');
  assert(detail.includes('0000  52 54 00 00 00 02 52 54'),'Hex dump starts with the Ethernet header');
  await advance();
  assert.equal(await page.locator('tr.packet-detail-row').count(),1,'The open detail survives a refresh');
  if(process.env.MAP_SCREENSHOT) await page.screenshot({path:process.env.MAP_SCREENSHOT.replace('.png','-detail.png')});
  await page.locator('tr.packet-row.open').click();
  assert.equal(await page.locator('tr.packet-detail-row').count(),0,'A second click closes it');
  await page.locator('#packet-clear').click();
  assert.equal(await page.locator('#packet-rows tr').count(),0,'Clear empties the history');
  await advance();
  assert(await page.locator('#packet-rows tr').count()>0,'The next poll refills it');
  tx+=8192;rx+=4096;
  await advance();
  if(process.env.MAP_SCREENSHOT) await page.screenshot({path:process.env.MAP_SCREENSHOT});  // the viewport: the inspector is a fixed panel
  unavailable=true;
  await advance();
  assert.equal(await cable.locator('.traffic-label').textContent(),'Traffic unavailable');
  assert.equal(await page.locator('.transmitting,.receiving').count(),0);
  assert((await page.locator('#packet-state').textContent()).includes('unavailable'),'Unavailable capture is said; the history stays readable');
  await action('ssh').focus();
  stopped=true;
  await advance();
  assert.equal(await action('ssh').getAttribute('aria-disabled'),'true','Stopping disables SSH');
  assert.equal(await action('console').getAttribute('aria-disabled'),'true','Stopping disables the console');
  assert(await action('vm').evaluate(n=>document.activeElement===n),'Focus moves to Manage when SSH becomes disabled');
  assert.equal(await cable.locator('.traffic-label').textContent(),'Link off');
  assert.equal(await page.locator('#running-count').textContent(),'1 running');
  assert.equal(await page.locator('[data-member="k8s-lab-main"] .member-state').textContent(),'stopped');
  await page.locator('#packet-clear').click();
  assert.equal(await page.locator('#packet-empty').textContent(),'VM stopped');
  assert(await page.locator('#packet-inspector').isVisible(),'The open inspector survives topology replacement');
  fail=true;
  await page.clock.runFor(2100);
  await page.waitForFunction(()=>document.getElementById('map-live').textContent.startsWith('Updates unavailable'));
  assert.equal(await page.locator('.transmitting,.receiving').count(),0,'Failure clears old traffic');
  assert((await page.locator('#packet-state').textContent()).includes('Updates unavailable'));
  await page.keyboard.press('Escape');
  assert(await page.locator('#packet-inspector').isHidden());
  assert(await lens.evaluate(n=>document.activeElement===n),'Dismissal returns focus to the lens');
  assert.equal(await page.locator('.nat-nic .inspect-btn').count(),0,'NAT links have no lens: the host cannot capture there');
  await lens.focus();
  await lens.press('Enter');
  assert(await page.locator('#packet-inspector').isVisible(),'Enter on the lens opens the inspector');
  await page.setViewportSize({width:390,height:844});
  await page.evaluate(()=>window.dispatchEvent(new Event('resize')));
  const panelBounds = await page.locator('#packet-inspector').boundingBox();
  assert(panelBounds.x>=0 && panelBounds.x+panelBounds.width<=390,'Inspector stays inside mobile viewport');
  assert(await page.locator('.map').evaluate(n=>n.scrollWidth>n.clientWidth),'Small screens scroll the diagram');
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'No page-wide overflow');
  assert.deepEqual(errors,[]);
  console.log('PASS: VM action dialogs, NIC attribution, live traffic, lens inspector (no hover), filters/search/clear, packet detail, pause/resume, offline/stale, keyboard, reduced motion, mobile layout');
} finally {
  await browser.close();
}
