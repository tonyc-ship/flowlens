/**
 * Shared browser fingerprint settings for XHS explore flows.
 * Keep launch/context/init-script consistent across auth/explore scripts.
 */

// Randomize viewport ±40px width / ±30px height to avoid fixed-size fingerprint
function _randViewport() {
  const w = 1280 + Math.floor(Math.random() * 81) - 40;  // 1240–1320
  const h = 800  + Math.floor(Math.random() * 61) - 30;  // 770–830
  return { width: w, height: h };
}

export const NAVIGATOR_INIT_SCRIPT = `
(function() {
  // webdriver
  Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

  // language / platform
  Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
  Object.defineProperty(navigator, 'platform',  { get: () => 'MacIntel' });

  // hardware concurrency & device memory — realistic Mac values
  Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
  Object.defineProperty(navigator, 'deviceMemory',        { get: () => 8 });

  // plugins — non-empty list prevents headless detection
  const _pluginData = [
    { name: 'Chrome PDF Plugin',          filename: 'internal-pdf-viewer',   description: 'Portable Document Format' },
    { name: 'Chrome PDF Viewer',          filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
    { name: 'Native Client',             filename: 'internal-nacl-plugin',   description: '' },
  ];
  const _plugins = Object.create(PluginArray.prototype);
  _pluginData.forEach((d, i) => {
    const p = Object.create(Plugin.prototype);
    Object.defineProperty(p, 'name',        { get: () => d.name });
    Object.defineProperty(p, 'filename',    { get: () => d.filename });
    Object.defineProperty(p, 'description', { get: () => d.description });
    Object.defineProperty(p, 'length',      { get: () => 0 });
    Object.defineProperty(_plugins, i, { get: () => p });
    Object.defineProperty(_plugins, d.name, { get: () => p });
  });
  Object.defineProperty(_plugins, 'length', { get: () => _pluginData.length });
  Object.defineProperty(_plugins, 'item',   { value: i => _plugins[i] });
  Object.defineProperty(_plugins, 'namedItem', { value: name => _plugins[name] });
  // 支持 for...of 和 spread 遍历，防止检测库用迭代器探测真实性
  Object.defineProperty(_plugins, Symbol.iterator, {
    value: function* () { for (let i = 0; i < _pluginData.length; i++) yield _plugins[i]; }
  });
  Object.defineProperty(navigator, 'plugins', { get: () => _plugins });

  // WebGL vendor / renderer
  try {
    const _getParam = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(param) {
      if (param === 37445) return 'Intel Inc.';                        // UNMASKED_VENDOR_WEBGL
      if (param === 37446) return 'Intel Iris OpenGL Engine';          // UNMASKED_RENDERER_WEBGL
      return _getParam.call(this, param);
    };
    const _getParam2 = WebGL2RenderingContext.prototype.getParameter;
    WebGL2RenderingContext.prototype.getParameter = function(param) {
      if (param === 37445) return 'Intel Inc.';
      if (param === 37446) return 'Intel Iris OpenGL Engine';
      return _getParam2.call(this, param);
    };
  } catch(e) {}

  // Canvas — add stable per-session noise so fingerprint differs from headless baseline.
  // 关键：在离屏 scratch canvas 上叠噪声后导出，不污染原始画布内容。
  try {
    const _noise = parseFloat((Math.random() * 0.04 - 0.02).toFixed(6));  // ±2% 亮度偏移（float）
    const _toDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function(type, quality) {
      const ctx = this.getContext('2d');
      if (!ctx || this.width === 0 || this.height === 0) {
        return _toDataURL.call(this, type, quality);
      }
      // 读取原始像素，在离屏 canvas 上叠噪声，原始画布不受影响
      const id = ctx.getImageData(0, 0, this.width, this.height);
      const delta = Math.round(_noise * 255);
      for (let i = 0; i < id.data.length; i += 4) {
        id.data[i]   = Math.min(255, Math.max(0, id.data[i]   + delta));
        id.data[i+1] = Math.min(255, Math.max(0, id.data[i+1] + delta));
        id.data[i+2] = Math.min(255, Math.max(0, id.data[i+2] + delta));
      }
      const scratch = document.createElement('canvas');
      scratch.width  = this.width;
      scratch.height = this.height;
      scratch.getContext('2d').putImageData(id, 0, 0);
      return _toDataURL.call(scratch, type, quality);
    };
  } catch(e) {}
})();
`;

export const WARMUP_PATHS = [
  '/api/sns/web/v2/user/me',
  '/api/sns/web/v1/system/config',
  '/api/sns/web/v1/zones',
  '/api/sns/web/unread_count',
  '/api/sns/web/v1/search/querytrending',
];

export function launchOptions(headless = false) {
  return {
    headless,
    channel: 'chrome',
    args: [
      '--disable-blink-features=AutomationControlled',
      '--no-first-run',
      '--no-default-browser-check',
    ],
  };
}

export function launchOptionsFallback(headless = false) {
  return {
    headless,
    args: ['--disable-blink-features=AutomationControlled'],
  };
}

export function contextOptions(storageState = null) {
  const opts = {
    viewport: _randViewport(),
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
    colorScheme: 'light',
    extraHTTPHeaders: { 'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8' },
  };
  if (storageState) opts.storageState = storageState;
  return opts;
}

export async function warmupSession(page, timeoutMs = 12000) {
  const hits = new Set();
  const onResponse = (resp) => {
    const url = resp.url();
    for (const p of WARMUP_PATHS) {
      if (url.includes(p)) hits.add(p);
    }
  };
  page.on('response', onResponse);
  try {
    await page.goto('https://www.xiaohongshu.com/', { waitUntil: 'domcontentloaded', timeout: 30000 });
    try {
      await page.waitForResponse(r => r.url().includes('/api/sns/web/v2/user/me'), { timeout: timeoutMs });
    } catch {
      // Best-effort warmup, don't fail the whole flow.
    }
    await page.goto('https://www.xiaohongshu.com/explore', { waitUntil: 'domcontentloaded', timeout: 30000 });
    try {
      await page.waitForResponse(r => r.url().includes('/api/sns/web/unread_count'), { timeout: 8000 });
    } catch {
      // Best-effort warmup.
    }
  } finally {
    page.off('response', onResponse);
  }
  return [...hits];
}
