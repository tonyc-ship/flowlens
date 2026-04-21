"""
统一浏览器启动配置：所有 Playwright 入口共用，保证画像一致。

与 mcp/explore/scripts/browser-fingerprint.js 保持同步：
  - NAVIGATOR_INIT_SCRIPT  ← 与 JS 版等价（webdriver/plugins/WebGL/Canvas 噪声）
  - launch_options()       ← 同 launchOptions()
  - launch_options_fallback() ← 同 launchOptionsFallback()
  - context_options()      ← 同 contextOptions()（含随机 viewport / locale / timezone）
"""

import random

# 注入到每个 page（addInitScript），隐藏 Playwright 自动化特征
NAVIGATOR_INIT_SCRIPT = """
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
    { name: 'Chrome PDF Plugin',  filename: 'internal-pdf-viewer',             description: 'Portable Document Format' },
    { name: 'Chrome PDF Viewer',  filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
    { name: 'Native Client',      filename: 'internal-nacl-plugin',             description: '' },
  ];
  const _plugins = Object.create(PluginArray.prototype);
  _pluginData.forEach((d, i) => {
    const p = Object.create(Plugin.prototype);
    Object.defineProperty(p, 'name',        { get: () => d.name });
    Object.defineProperty(p, 'filename',    { get: () => d.filename });
    Object.defineProperty(p, 'description', { get: () => d.description });
    Object.defineProperty(p, 'length',      { get: () => 0 });
    Object.defineProperty(_plugins, i,      { get: () => p });
    Object.defineProperty(_plugins, d.name, { get: () => p });
  });
  Object.defineProperty(_plugins, 'length',      { get: () => _pluginData.length });
  Object.defineProperty(_plugins, 'item',        { value: i => _plugins[i] });
  Object.defineProperty(_plugins, 'namedItem',   { value: name => _plugins[name] });
  Object.defineProperty(_plugins, Symbol.iterator, {
    value: function* () { for (let i = 0; i < _pluginData.length; i++) yield _plugins[i]; }
  });
  Object.defineProperty(navigator, 'plugins', { get: () => _plugins });

  // WebGL vendor / renderer
  try {
    const _getParam = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(param) {
      if (param === 37445) return 'Intel Inc.';               // UNMASKED_VENDOR_WEBGL
      if (param === 37446) return 'Intel Iris OpenGL Engine'; // UNMASKED_RENDERER_WEBGL
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
  try {
    const _noise = parseFloat((Math.random() * 0.04 - 0.02).toFixed(6));
    const _toDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function(type, quality) {
      const ctx = this.getContext('2d');
      if (!ctx || this.width === 0 || this.height === 0) {
        return _toDataURL.call(this, type, quality);
      }
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
"""


def _rand_viewport() -> dict:
    """随机化 viewport 尺寸，避免固定分辨率指纹 (1240–1320 × 770–830)"""
    w = 1280 + random.randint(-40, 40)
    h = 800 + random.randint(-30, 30)
    return {"width": w, "height": h}


def launch_options(headless: bool = False) -> dict:
    """
    返回 playwright.chromium.launch(**launch_options()) 的参数字典。
    优先使用本机真实 Chrome（channel='chrome'），无则回退到 Playwright Chromium。
    调用方负责 try/except 回退：
        try:
            browser = await p.chromium.launch(**launch_options())
        except Exception:
            browser = await p.chromium.launch(**launch_options_fallback(headless))
    """
    return {
        "headless": headless,
        "channel": "chrome",
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
        ],
    }


def launch_options_fallback(headless: bool = False) -> dict:
    """channel='chrome' 不可用时的回退（Playwright 内置 Chromium）。"""
    return {
        "headless": headless,
        "args": [
            "--disable-blink-features=AutomationControlled",
        ],
    }


def context_options(storage_state=None, viewport: dict = None) -> dict:
    """
    返回 browser.new_context(**context_options()) 的参数字典。
    包含随机 viewport、zh-CN locale、Asia/Shanghai timezone，与 JS 版保持一致。
    不硬编码 User-Agent，让浏览器用真实值（与 sec-ch-ua 版本保持一致）。
    """
    opts = {
        "viewport": viewport or _rand_viewport(),
        "locale": "zh-CN",
        "timezone_id": "Asia/Shanghai",
        "color_scheme": "light",
        "extra_http_headers": {"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
    }
    if storage_state:
        opts["storage_state"] = storage_state
    return opts
