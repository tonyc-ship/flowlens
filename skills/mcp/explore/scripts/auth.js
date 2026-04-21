import { readFileSync, writeFileSync, mkdirSync, chmodSync, existsSync } from 'fs'
import { homedir } from 'os'
import { join, resolve } from 'path'
import {
  NAVIGATOR_INIT_SCRIPT,
  launchOptions,
  launchOptionsFallback,
  contextOptions,
} from './browser-fingerprint.js'

const ACCOUNTS_PATH = join(homedir(), 'Desktop/Auto-Redbook-Skills/accounts.json')
const STORAGE_BASE = join(homedir(), '.xhs-accounts')
const SESSION_TTL_DAYS = 7

// 纯函数：只检查有效期，不做 I/O
export function isSessionValid(account) {
  if (!account) return false
  if (!account.session_expires_at) return false
  return new Date(account.session_expires_at) > new Date()
}

function loadAccounts() {
  try {
    if (!existsSync(ACCOUNTS_PATH)) return []
    const data = JSON.parse(readFileSync(ACCOUNTS_PATH, 'utf-8'))
    return data.accounts || []
  } catch {
    return []
  }
}

// 从 .env 文件加载 XHS_COOKIE（无需 dotenv 依赖）
function loadEnvCookie() {
  const envPaths = [
    join(process.cwd(), '.env'),
    join(homedir(), 'Desktop/Auto-Redbook-Skills/.env'),
  ]
  for (const envPath of envPaths) {
    if (!existsSync(envPath)) continue
    try {
      const lines = readFileSync(envPath, 'utf-8').split('\n')
      for (const line of lines) {
        const m = line.match(/^XHS_COOKIE=(.+)$/)
        if (m) return m[1].trim()
      }
    } catch {}
  }
  return process.env.XHS_COOKIE || null
}

// 将明文 cookie 字符串转为 Playwright storageState 格式（内存对象，不写磁盘）
export function cookieStringToStorageState(cookieStr) {
  const cookieAttrs = {
    web_session: [true, 'Lax'],
    'customer-sso-sid': [true, 'Lax'],
    websectiga: [true, 'Lax'],
    sec_poison_id: [true, 'Lax'],
    a1: [false, 'None'],
    webId: [false, 'None'],
    gid: [false, 'None'],
    xsecappid: [false, 'None'],
  }
  const cookies = cookieStr.split(';').flatMap(item => {
    item = item.trim()
    const eqIdx = item.indexOf('=')
    if (eqIdx < 0) return []
    const name = item.slice(0, eqIdx).trim()
    const value = item.slice(eqIdx + 1).trim()
    const [httpOnly, sameSite] = cookieAttrs[name] || [false, 'Lax']
    return [{
      name,
      value,
      domain: '.xiaohongshu.com',
      path: '/',
      expires: -1,
      httpOnly,
      secure: true,
      sameSite,
    }]
  })
  return { cookies, origins: [] }
}

// 轮询选取下一个可用账号。
// 返回 { storagePath, accountName } 或 env 回退 { cookieStr, accountName: '__env__' } 或 null
export function getValidSession(preferredAccountName = '') {
  const accounts = loadAccounts()
  const active = accounts.filter(a => a.active)

  if (preferredAccountName) {
    const target = active.find(a => a.name === preferredAccountName)
    if (!target) return null
    if (!isSessionValid(target)) return null
    const storagePath = resolve(target.storage_path.replace('~', homedir()))
    if (!existsSync(storagePath)) return null
    return { storagePath, accountName: target.name }
  }

  // last_used_at 升序（null 最优先）
  active.sort((a, b) => {
    if (!a.last_used_at) return -1
    if (!b.last_used_at) return 1
    return new Date(a.last_used_at) - new Date(b.last_used_at)
  })

  const expired = []
  for (const account of active) {
    if (!isSessionValid(account)) { expired.push(account.name); continue }
    const storagePath = resolve(account.storage_path.replace('~', homedir()))
    if (!existsSync(storagePath)) { expired.push(account.name); continue }
    if (expired.length) {
      console.warn(`⚠️  已跳过过期账号：${expired.join(', ')}，请运行 auth.js login --name <账号名>`)
    }
    return { storagePath, accountName: account.name }
  }

  // 所有账号均无效，回退到 .env
  if (expired.length) {
    console.warn(`⚠️  所有账号已过期：${expired.join(', ')}，尝试 XHS_COOKIE 环境变量回退`)
  }
  const cookieStr = loadEnvCookie()
  if (cookieStr) return { cookieStr, accountName: '__env__' }
  return null
}

// 使用成功后更新 last_used_at，维持轮询公平性
export function markAccountUsed(accountName) {
  if (accountName === '__env__') return
  try {
    if (!existsSync(ACCOUNTS_PATH)) return
    const data = JSON.parse(readFileSync(ACCOUNTS_PATH, 'utf-8'))
    const account = (data.accounts || []).find(a => a.name === accountName)
    if (account) {
      account.last_used_at = new Date().toISOString()
      writeFileSync(ACCOUNTS_PATH, JSON.stringify(data, null, 2), 'utf-8')
    }
  } catch {}
}

export function getSessionError() {
  return {
    error: 'session_expired',
    message: `请在终端运行：node ${join(homedir(), 'xhs-explore-mcp/scripts/auth.js')} login --name <账号名>`
  }
}

// CLI 登录入口（仅在直接运行且参数为 login 时执行）
if (process.argv[1]?.endsWith('auth.js') && process.argv[2] === 'login') {
  const nameIdx = process.argv.indexOf('--name')
  const accountName = nameIdx >= 0 ? process.argv[nameIdx + 1] : 'default'

  const { chromium } = await import('playwright')
  const storageDir = join(STORAGE_BASE, accountName)
  const storagePath = join(storageDir, 'storage.json')

  mkdirSync(storageDir, { recursive: true })

  console.log(`正在打开小红书，请在 120 秒内完成手机扫码登录...（账号：${accountName}）`)
  let browser
  try {
    browser = await chromium.launch(launchOptions(false))
  } catch {
    browser = await chromium.launch(launchOptionsFallback(false))
  }
  const context = await browser.newContext(contextOptions())
  await context.addInitScript(NAVIGATOR_INIT_SCRIPT)
  const page = await context.newPage()

  await page.goto('https://www.xiaohongshu.com/login')

  try {
    await page.waitForURL(url => {
      const s = String(url)
      return !s.includes('/login') && !s.includes('/web-login') && !s.includes('/website-login')
    }, { timeout: 120000 })
    await page.waitForTimeout(2000)  // 等 Cookie 落齐

    const currentUrl = page.url()
    if (
      currentUrl.includes('/website-login') ||
      currentUrl.includes('/web-login') ||
      currentUrl.includes('/login') ||
      currentUrl.includes('error_code=300012')
    ) {
      throw new Error(`命中风控/登录页：${currentUrl}`)
    }

    const loginEl = await page.$(
      '.login-container, .qrcode-img, [class*="LoginModal"], [class*="login-modal"], .reds-login, #login-page'
    )
    if (loginEl) {
      throw new Error('页面仍存在登录弹窗，登录未完成')
    }

    const bodyText = await page.evaluate(() => document.body?.innerText || '')
    if (bodyText.includes('IP存在风险') || bodyText.includes('扫码登录') || bodyText.includes('请登录后继续')) {
      throw new Error('页面提示未登录或风控拦截（IP存在风险）')
    }

    await context.storageState({ path: storagePath })
    try { chmodSync(storagePath, 0o600) } catch {}

    const expiresAt = new Date(Date.now() + SESSION_TTL_DAYS * 86400000).toISOString()
    let data = { accounts: [] }
    try {
      if (existsSync(ACCOUNTS_PATH)) data = JSON.parse(readFileSync(ACCOUNTS_PATH, 'utf-8'))
    } catch {}

    const existing = data.accounts.find(a => a.name === accountName)
    if (existing) {
      existing.session_expires_at = expiresAt
      existing.active = true
    } else {
      data.accounts.push({
        name: accountName,
        storage_path: `~/.xhs-accounts/${accountName}/storage.json`,
        last_used_at: null,
        session_expires_at: expiresAt,
        added_at: new Date().toISOString(),
        active: true,
      })
    }
    writeFileSync(ACCOUNTS_PATH, JSON.stringify(data, null, 2), 'utf-8')
    console.log(`登录成功！storageState 已保存至 ${storagePath}（${SESSION_TTL_DAYS} 天有效）`)
  } catch (err) {
    console.error('登录超时或失败，请重试:', err.message)
  } finally {
    await browser.close()
  }
}
