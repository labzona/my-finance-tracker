// Print the local, self-contained HTML with the installed Chrome.
// Uses Chrome's local debugging pipe, without npm packages or a web server.
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { spawn } from 'node:child_process';

const directory = path.dirname(fileURLToPath(import.meta.url));
const profile = await fs.mkdtemp(path.join(os.tmpdir(), 'finance-pdf-chrome-'));
const chrome = spawn('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', [
  '--headless=new', '--disable-gpu', '--disable-background-networking',
  '--disable-component-update', '--disable-extensions', '--disable-sync',
  '--no-first-run', '--no-default-browser-check', '--remote-debugging-pipe',
  `--user-data-dir=${profile}`, 'about:blank',
], { stdio: ['ignore', 'ignore', 'pipe', 'pipe', 'pipe'] });

let sequence = 0;
let received = '';
let diagnostics = '';
const requests = new Map();
chrome.stderr.on('data', chunk => { diagnostics += chunk.toString(); });
chrome.stdio[4].on('data', chunk => {
  received += chunk.toString();
  let end;
  while ((end = received.indexOf('\0')) >= 0) {
    const item = JSON.parse(received.slice(0, end));
    received = received.slice(end + 1);
    const request = requests.get(item.id);
    if (request) {
      requests.delete(item.id);
      clearTimeout(request.timeout);
      if (item.error) request.reject(new Error(JSON.stringify(item.error)));
      else request.resolve(item.result);
    }
  }
});
function send(method, params = {}, sessionId) {
  return new Promise((resolve, reject) => {
    const id = ++sequence;
    const timeout = setTimeout(() => {
      requests.delete(id);
      reject(new Error(`Timeout: ${method}\n${diagnostics.slice(-1500)}`));
    }, 45000);
    requests.set(id, { resolve, reject, timeout });
    chrome.stdio[3].write(JSON.stringify({ id, method, params,
      ...(sessionId ? { sessionId } : {}) }) + '\0');
  });
}

try {
  const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });
  const call = (method, params) => send(method, params, sessionId);
  await call('Page.enable');
  await call('Emulation.setEmulatedMedia', { media: 'print' });
  await call('Page.navigate', { url: pathToFileURL(path.join(directory, 'product-decision.html')).href });
  for (let attempt = 0; attempt < 100; attempt++) {
    const ready = await call('Runtime.evaluate', {
      expression: 'document.readyState === "complete" && document.querySelectorAll(".page").length === 5',
      returnByValue: true,
    });
    if (ready.result.value) break;
    if (attempt === 99) throw new Error('HTML did not load');
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  const layout = await call('Runtime.evaluate', {
    expression: `(async () => {
      await document.fonts.ready;
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      return [...document.querySelectorAll('.page')].map(page => {
        const box = page.getBoundingClientRect();
        const footer = page.querySelector('.footer').getBoundingClientRect();
        const content = [...page.children].filter(child => !child.classList.contains('footer'));
        const bottom = Math.max(...content.map(child => child.getBoundingClientRect().bottom));
        return { page: Number(page.dataset.page),
          availableHeight: Math.round(footer.top - box.top),
          contentHeight: Math.round(bottom - box.top),
          gapBeforeFooter: Math.round(footer.top - bottom),
          featureIds: [...page.querySelectorAll('.features tbody tr')]
            .map(row => Number(row.cells[0].textContent)),
          links: [...page.querySelectorAll('a')].map(link => link.href) };
      });
    })()`, awaitPromise: true, returnByValue: true,
  });
  if (layout.exceptionDetails) throw new Error(JSON.stringify(layout.exceptionDetails));
  const pages = layout.result.value;
  if (pages.some(page => page.gapBeforeFooter < 8)) {
    throw new Error('Content too close to footer: ' + JSON.stringify(pages));
  }
  const featureIds = pages.flatMap(page => page.featureIds);
  if (featureIds.length !== 18 || new Set(featureIds).size !== 18 ||
      featureIds.some(id => id < 1 || id > 18)) throw new Error('Invalid feature coverage');
  const result = await call('Page.printToPDF', {
    preferCSSPageSize: true, printBackground: true, displayHeaderFooter: false,
    scale: 1, transferMode: 'ReturnAsBase64',
  });
  await fs.writeFile(path.join(directory, 'product-decision.pdf'), Buffer.from(result.data, 'base64'));
  const qaDirectory = path.join(os.tmpdir(), 'my-finance-homework-pdf-qa');
  await fs.mkdir(qaDirectory, { recursive: true });
  await fs.writeFile(path.join(qaDirectory, 'layout.json'), JSON.stringify(pages, null, 2));
  console.log(JSON.stringify({ pdf: 'homework/product-decision.pdf', pages: pages.map(page => ({
    page: page.page, contentHeight: page.contentHeight,
    gapBeforeFooter: page.gapBeforeFooter, links: page.links.length,
  })), uniqueFeatures: new Set(featureIds).size, qaDirectory }, null, 2));
} finally {
  chrome.kill('SIGTERM');
}
