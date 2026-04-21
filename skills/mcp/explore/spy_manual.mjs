/**
 * 手动操作监听脚本
 * 开启已登录浏览器，实时打印所有 XHS API 请求和响应体
 * 用法: node spy_manual.mjs [起始URL]
 *
 * 目的：观察小红书筛选/排序功能的 API 参数和响应结构
 */

import { chromium } from 'playwright'
import fs from 'fs'
import path from 'path'
import { fileURLToPath } from 'url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

// ── 加载 session ────────────────────────────────────────────────────────────
const SESSION_PATH = path.join(
  process.env.HOME, '.xhs-accounts', 'backup_20260404', 'storage.json'
)
if (!fs.existsSync(SESSION_PATH)) {
  console.error('❌ 找不到 session:', SESSION_PATH)
  process.exit(1)
}

const START_URL = process.argv[2] || 'https://www.xiaohongshu.com/explore'
const LOG_FILE  = path.join(__dirname, '../../output/network_spy',
  `spy_manual_${new Date().toISOString().replace(/[:.]/g,'-').slice(0,19)}.jsonl`)

fs.mkdirSync(path.dirname(LOG_FILE), { recursive: true })
const logStream = fs.createWriteStream(LOG_FILE, { flags: 'a' })

const XHS_HOSTS = new Set(['edith.xiaohongshu.com', 'www.xiaohongshu.com', 'fe-api.xiaohongshu.com'])
const IGNORE_RES = new Set(['image', 'stylesheet', 'font', 'media', 'websocket'])

function isXhsApi(url) {
  try {
    const h = new URL(url).hostname
    return XHS_HOSTS.has(h) && url.includes('/api/')
  } catch { return false }
}

function fmt(obj) { return JSON.stringify(obj, null, 2) }

// ── 启动 ────────────────────────────────────────────────────────────────────
const browser = await chromium.launch({
  headless: false,
  args: ['--disable-blink-features=AutomationControlled', '--window-size=1280,900'],
})

const context = await browser.newContext({
  storageState: SESSION_PATH,
  viewport: { width: 1280, height: 900 },
  userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
})

const page = await context.newPage()
let reqSeq = 0

// ── 请求监听 ────────────────────────────────────────────────────────────────
context.on('request', req => {
  if (!isXhsApi(req.url())) return
  if (IGNORE_RES.has(req.resourceType())) return
  const seq = ++reqSeq
  const entry = {
    seq,
    ts: new Date().toISOString(),
    method: req.method(),
    url: req.url(),
    postData: req.postData() || undefined,
  }
  const line = `\n${'─'.repeat(60)}\n[REQ #${seq}] ${req.method()} ${req.url()}`
  const post = req.postData()
  process.stdout.write(line + (post ? `\n  BODY: ${post}` : '') + '\n')
  logStream.write(JSON.stringify({ type: 'request', ...entry }) + '\n')
})

// ── 响应监听 ────────────────────────────────────────────────────────────────
context.on('response', async res => {
  if (!isXhsApi(res.url())) return
  if (IGNORE_RES.has(res.request().resourceType())) return
  let body = ''
  try {
    const buf = await res.body()
    body = buf.toString('utf8')
  } catch {}
  const entry = {
    ts: new Date().toISOString(),
    status: res.status(),
    url: res.url(),
    body,
  }
  const parsed = (() => { try { return JSON.parse(body) } catch { return null } })()
  const summary = parsed
    ? ` → ${res.status()} | items=${JSON.stringify(parsed).length}B`
    : ` → ${res.status()}`
  process.stdout.write(`[RES] ${res.url().split('?')[0]}${summary}\n`)
  if (body) process.stdout.write(`  ${body.slice(0, 400).replace(/\n/g,' ')}\n`)
  logStream.write(JSON.stringify({ type: 'response', ...entry }) + '\n')
})

// ── 打开页面 ────────────────────────────────────────────────────────────────
console.log(`\n🔍 手动监听模式启动`)
console.log(`📄 日志保存至: ${LOG_FILE}`)
console.log(`🌐 打开: ${START_URL}`)
console.log(`─────────────────────────────────────────`)
console.log(`请在浏览器里手动操作（搜索/筛选/排序）`)
console.log(`所有 XHS API 请求会实时显示在此终端`)
console.log(`Ctrl+C 退出并保存\n`)

await page.goto(START_URL, { waitUntil: 'domcontentloaded' })

// 保持运行直到用户 Ctrl+C
process.on('SIGINT', async () => {
  console.log(`\n\n👋 退出，共捕获 ${reqSeq} 个 API 请求`)
  console.log(`📁 完整日志: ${LOG_FILE}`)
  logStream.end()
  await browser.close()
  process.exit(0)
})

// 防止进程退出
await new Promise(() => {})
