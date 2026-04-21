/**
 * 有头浏览器测试运行器
 * 用法: node run_headed_test.mjs [keyword] [limit]
 * 例如: node run_headed_test.mjs "海外求职" 30
 *
 * 通过 monkey-patch chromium.launch 强制 headless: false，
 * 让用户看到浏览器实时操作。
 */

import { chromium as _chromium } from 'playwright'
import { mkdirSync, writeFileSync } from 'fs'
import { dirname, resolve } from 'path'
import { getValidSession, markAccountUsed, cookieStringToStorageState } from './scripts/auth.js'
import { scorePost, selectPostsForDetail } from './scripts/viral-filter.js'
import { parseRelativeDate } from './scripts/explore.js'
import {
  NAVIGATOR_INIT_SCRIPT,
  launchOptions,
  launchOptionsFallback,
  contextOptions,
  warmupSession,
} from './scripts/browser-fingerprint.js'

const rawArgs = process.argv.slice(2)
const positionalArgs = []
for (let i = 0; i < rawArgs.length; i++) {
  const cur = rawArgs[i]
  if (cur.startsWith('--')) {
    const next = rawArgs[i + 1]
    if (next && !next.startsWith('--')) i++
    continue
  }
  positionalArgs.push(cur)
}

function getFlagValue(flagName) {
  const idx = rawArgs.indexOf(flagName)
  if (idx < 0) return ''
  const v = rawArgs[idx + 1]
  if (!v || v.startsWith('--')) return ''
  return v
}

function parseBool(value, defaultValue = false) {
  if (value === undefined || value === null || value === '') return defaultValue
  const v = String(value).trim().toLowerCase()
  if (['1', 'true', 'yes', 'on'].includes(v)) return true
  if (['0', 'false', 'no', 'off'].includes(v)) return false
  return defaultValue
}

const keyword = positionalArgs[0] || '海外求职'
const searchLimit = parseInt(positionalArgs[1] || '30')
const viralThreshold = parseInt(positionalArgs[2] || '50')
const authorLimit = parseInt(positionalArgs[3] || '10')
const perAuthorNoteLimit = Math.max(1, parseInt(getFlagValue('--per-author-notes') || '10') || 10)
const preferredAccount = getFlagValue('--account')
const httpLogPathInput = getFlagValue('--http-log')
const httpLogPath = httpLogPathInput ? resolve(process.cwd(), httpLogPathInput) : ''
const keepOpen  = rawArgs.includes('--keep-open')
const listOnly  = rawArgs.includes('--list-only')   // 只跑阶段一二，打印作者候选后退出
const detailOpenTimeoutMs = Math.max(
  3000,
  parseInt(getFlagValue('--detail-open-timeout-ms') || process.env.XHS_DETAIL_OPEN_TIMEOUT_MS || '7000') || 7000
)
const navTimeoutMs = Math.max(
  4000,
  parseInt(getFlagValue('--nav-timeout-ms') || process.env.XHS_NAV_TIMEOUT_MS || '9000') || 9000
)
const clickActionTimeoutMs = Math.max(
  2500,
  parseInt(getFlagValue('--click-timeout-ms') || process.env.XHS_CLICK_TIMEOUT_MS || '4500') || 4500
)
const cardReadyTimeoutMs = Math.max(
  600,
  parseInt(getFlagValue('--card-ready-timeout-ms') || process.env.XHS_CARD_READY_TIMEOUT_MS || '1200') || 1200
)
const authorNoteDelayMinMs = Math.max(
  150,
  parseInt(getFlagValue('--author-note-delay-min-ms') || process.env.XHS_AUTHOR_NOTE_DELAY_MIN_MS || '1500') || 1500
)
const authorNoteDelayMaxMs = Math.max(
  authorNoteDelayMinMs,
  parseInt(getFlagValue('--author-note-delay-max-ms') || process.env.XHS_AUTHOR_NOTE_DELAY_MAX_MS || '3500') || 3500
)
const searchNavTimeoutMs = Math.max(
  8000,
  parseInt(getFlagValue('--search-nav-timeout-ms') || process.env.XHS_SEARCH_NAV_TIMEOUT_MS || '18000') || 18000
)
const searchResponseTimeoutMs = Math.max(
  2000,
  parseInt(getFlagValue('--search-response-timeout-ms') || process.env.XHS_SEARCH_RESPONSE_TIMEOUT_MS || '5500') || 5500
)
const searchOpenRetries = Math.max(
  1,
  parseInt(getFlagValue('--search-open-retries') || process.env.XHS_SEARCH_OPEN_RETRIES || '2') || 2
)
const listStagnationRounds = Math.max(
  2,
  parseInt(getFlagValue('--list-stagnation-rounds') || process.env.XHS_LIST_STAGNATION_ROUNDS || '3') || 3
)
const listRoundDelayMinMs = Math.max(
  120,
  parseInt(getFlagValue('--list-round-delay-min-ms') || process.env.XHS_LIST_ROUND_DELAY_MIN_MS || '2000') || 2000
)
const listRoundDelayMaxMs = Math.max(
  listRoundDelayMinMs,
  parseInt(getFlagValue('--list-round-delay-max-ms') || process.env.XHS_LIST_ROUND_DELAY_MAX_MS || '4500') || 4500
)
const captchaProbeLimit = Math.max(
  6,
  parseInt(getFlagValue('--captcha-probe-limit') || process.env.XHS_CAPTCHA_PROBE_LIMIT || '20') || 20
)
const allowStaleFallback = parseBool(
  getFlagValue('--allow-stale-fallback') || process.env.XHS_ALLOW_STALE_FALLBACK,
  false
)

const SEARCH_URL = 'https://www.xiaohongshu.com/search_result?keyword='
const EXPLORE_URL = 'https://www.xiaohongshu.com/explore'
const XHS_API_HOSTS = new Set(['edith.xiaohongshu.com', 'www.xiaohongshu.com', 'fe-api.xiaohongshu.com'])
const SEARCH_NOTES_API_RE = /\/api\/sns\/web\/v1\/search\/notes\b/
const LOGIN_SELECTORS = [
  '.login-container',
  '.qrcode-img',
  '[class*="LoginModal"]',
  '[class*="login-modal"]',
  '.reds-login',
  '#login-page',
]
const SEARCH_CARD_SELECTOR = '[data-v-a264d] .note-item, .search-feed-item, .note-list-item, section.note-item'

function isXhsApiUrl(url) {
  try {
    const u = new URL(url)
    return XHS_API_HOSTS.has(u.host)
  } catch {
    return false
  }
}

class LoginRequiredError extends Error {
  constructor(message = '检测到未登录状态，需要重新扫码登录') {
    super(message)
    this.name = 'LoginRequiredError'
  }
}

class CaptchaDetectedError extends Error {
  constructor(message = '检测到安全验证，终止本轮并输出部分结果') {
    super(message)
    this.name = 'CaptchaDetectedError'
  }
}

const xsecTokenMap = new Map() // note_id -> { token, source }
const apiNoteData   = new Map() // note_id -> { likes, saves, title, author, author_id, cover_url } from API response

function randomDelay(minMs = 2000, maxMs = 5000) {
  return new Promise(r => setTimeout(r, minMs + Math.random() * (maxMs - minMs)))
}

function cleanAuthor(name) {
  if (!name) return ''
  return String(name)
    .replace(/\d+小时前$/, '')
    .replace(/\d+天前$/, '')
    .replace(/\d+个月前$/, '')
    .replace(/\d{2}-\d{2}$/, '')
    .replace(/\d{4}-\d{2}-\d{2}$/, '')
    .trim()
}

function buildExploreUrl(noteId, tokenInfo = null, fallbackUrl = '') {
  if (!noteId) return fallbackUrl || ''
  if (!tokenInfo?.token) return fallbackUrl || `https://www.xiaohongshu.com/explore/${noteId}`
  return `https://www.xiaohongshu.com/explore/${noteId}` +
    `?xsec_token=${encodeURIComponent(tokenInfo.token)}` +
    `&xsec_source=${encodeURIComponent(tokenInfo.source || 'pc_search')}`
}

function stripProfileUrl(rawUrl) {
  if (!rawUrl) return ''
  try {
    const u = new URL(rawUrl, 'https://www.xiaohongshu.com')
    if (!u.pathname.includes('/user/profile/')) return ''
    return `https://www.xiaohongshu.com${u.pathname}`
  } catch {
    return ''
  }
}

function extractProfileIdFromUrl(rawUrl) {
  if (!rawUrl) return ''
  try {
    const u = new URL(rawUrl, 'https://www.xiaohongshu.com')
    return u.pathname.match(/\/user\/profile\/([^?/]+)/)?.[1] || ''
  } catch {
    return ''
  }
}

function matchesAnyPattern(url, patterns) {
  return patterns.some(p => p.test(url))
}

function extractTokenInfoFromUrl(rawUrl) {
  if (!rawUrl || typeof rawUrl !== 'string') return null
  const m = rawUrl.match(/[?&]xsec_token=([^&]+)/)
  if (!m) return null
  const sm = rawUrl.match(/[?&]xsec_source=([^&]+)/)
  return {
    token: decodeURIComponent(m[1] || ''),
    source: decodeURIComponent(sm?.[1] || 'pc_search'),
  }
}

async function navigateInPage(
  page,
  url,
  timeout = navTimeoutMs,
  successPatterns = [/\/explore\//, /\/404\//]
) {
  if (!url) return false
  try {
    await page.evaluate((targetUrl) => {
      window.location.href = targetUrl
    }, url)
  } catch {
    return false
  }
  try {
    await page.waitForURL(u => {
      const s = String(u)
      return matchesAnyPattern(s, successPatterns) || s.includes('login') || s.includes('captcha')
    }, { timeout })
  } catch {
    // best-effort
  }
  try {
    await page.waitForLoadState('domcontentloaded', { timeout: Math.min(timeout, 8000) })
  } catch {
    // best-effort
  }
  return matchesAnyPattern(page.url(), successPatterns)
}

async function hasDetailDom(page) {
  try {
    return await page.evaluate(() => {
      const detailRoot = document.querySelector(
        '.note-detail-mask, #noteContainer, [class*="note-detail"], .note-scroller, .note-content'
      )
      if (!detailRoot) return false
      const hasDesc = Boolean(detailRoot.querySelector('.desc, #detail-desc, [class*="desc"]'))
      const hasAction = Boolean(document.querySelector(
        '.like-wrapper .count, .interactions .like .count, .comment-wrapper .count, .interactions .comment .count'
      ))
      return hasDesc || hasAction
    })
  } catch {
    return false
  }
}

async function waitForDetailOpened(page, noteId, timeout = 6000) {
  try {
    await page.waitForFunction((nid) => {
      const href = window.location.href || ''
      if (href.includes(`/explore/${nid}`)) return true
      const detailRoot = document.querySelector(
        '.note-detail-mask, #noteContainer, [class*="note-detail"], .note-scroller, .note-content'
      )
      if (!detailRoot) return false
      const hasDesc = Boolean(detailRoot.querySelector('.desc, #detail-desc, [class*="desc"]'))
      const hasAction = Boolean(document.querySelector(
        '.like-wrapper .count, .interactions .like .count, .comment-wrapper .count, .interactions .comment .count'
      ))
      return hasDesc || hasAction
    }, noteId, { timeout })
    return true
  } catch {
    return await hasDetailDom(page)
  }
}

async function waitForCardInteractive(page, locator, timeout = cardReadyTimeoutMs) {
  try { await locator.scrollIntoViewIfNeeded() } catch {}
  try {
    await page.waitForFunction(
      (el) => {
        const st = window.getComputedStyle(el)
        const rect = el.getBoundingClientRect()
        const visible = st.display !== 'none' && st.visibility !== 'hidden' && Number(st.opacity || '1') > 0.01
        const interactable = st.pointerEvents !== 'none'
        return visible && interactable && rect.width > 1 && rect.height > 1
      },
      await locator.elementHandle(),
      { timeout }
    )
  } catch {
    // best-effort
  }
  await page.waitForTimeout(120 + Math.random() * 160)
}

async function extractTokenFromPageContext(page, noteId) {
  if (!noteId) return null
  try {
    const token = await page.evaluate((nid) => {
      const state = window.__INITIAL_STATE__ || {}
      const pools = []
      if (Array.isArray(state?.user?.notes)) pools.push(state.user.notes)
      if (Array.isArray(state?.feed?.items)) pools.push(state.feed.items)
      if (Array.isArray(state?.note?.items)) pools.push(state.note.items)
      for (const arr of pools) {
        for (const row of arr) {
          const id = row?.id || row?.note_id || row?.noteId || row?.note_card?.note_id
          if (String(id || '').includes(nid)) {
            return row?.xsec_token || row?.xsecToken || row?.note_card?.xsec_token || null
          }
        }
      }
      const links = Array.from(document.querySelectorAll(`a[href*="${nid}"]`))
      for (const a of links) {
        const href = a.href || a.getAttribute('href') || ''
        const m = href.match(/[?&]xsec_token=([^&]+)/)
        if (m) return decodeURIComponent(m[1])
      }
      return null
    }, noteId)
    if (token) return { token, source: 'pc_user' }
  } catch {
    // ignore
  }
  return null
}

async function clickAndCaptureNoteDetail(page, locator, noteId, timeout = detailOpenTimeoutMs) {
  let captured = null
  let feedFired = false
  let commentFired = false
  let metricsFired = false

  const onRequest = (req) => {
    const url = req.url() || ''
    if (!captured && url.includes(noteId)) {
      const info = extractTokenInfoFromUrl(url)
      if (info?.token) captured = info
    }
    if (url.includes('/api/sns/web/v1/feed')) feedFired = true
    if (url.includes('/api/sns/web/v2/comment/page')) commentFired = true
    if (url.includes('/api/sns/web/v1/note/metrics_report')) metricsFired = true
  }

  page.on('request', onRequest)
  try {
    await waitForCardInteractive(page, locator)
    try { await locator.evaluate(el => el.removeAttribute('target')) } catch {}
    await locator.click({ timeout: Math.min(timeout, clickActionTimeoutMs), delay: 35 + Math.random() * 30 })
    const opened = await waitForDetailOpened(page, noteId, timeout)
    await page.waitForTimeout(120 + Math.random() * 180)
    return { opened, captured, feedFired, commentFired, metricsFired }
  } catch {
    return { opened: false, captured, feedFired, commentFired, metricsFired }
  } finally {
    page.off('request', onRequest)
  }
}

async function simulateDetailPageStay(page) {
  try {
    await page.waitForTimeout(180 + Math.random() * 160)
    await page.evaluate(() => {
      const c = document.querySelector('.note-detail-mask, .note-scroller') || document.scrollingElement
      if (c) c.scrollTop += 280
    })
    await page.waitForTimeout(180 + Math.random() * 220)
  } catch {
    // ignore
  }
}

async function needsLogin(page) {
  try {
    const url = page.url()
    if (/login|sign-in|signin|web-login|website-login/.test(url)) {
      console.error(`[WARN] URL 指示需要登录: ${url.slice(0, 100)}`)
      return true
    }
    const loginVisible = await page.evaluate((selectors) => {
      const isVisible = (el) => {
        const s = window.getComputedStyle(el)
        const r = el.getBoundingClientRect()
        return (
          s.display !== 'none' &&
          s.visibility !== 'hidden' &&
          Number(s.opacity || '1') > 0.01 &&
          r.width > 1 &&
          r.height > 1
        )
      }
      return selectors.some(sel => [...document.querySelectorAll(sel)].some(isVisible))
    }, LOGIN_SELECTORS)
    if (loginVisible) {
      console.error(`[WARN] 页面中检测到可见登录弹窗，URL=${url.slice(0, 120)}`)
      return true
    }
    const text = await page.evaluate(() => document.body?.innerText?.slice(0, 5000) || '')
    if ((text.includes('扫码登录') || text.includes('请登录后继续')) && !text.includes('搜索结果')) {
      console.error('[WARN] 页面文案提示需要登录')
      return true
    }
  } catch (e) {
    const msg = String(e?.message || '')
    if (
      msg.includes('Execution context was destroyed') ||
      msg.includes('Cannot find context with specified id')
    ) {
      return false
    }
    console.error(`[ERROR] needsLogin: ${msg}`)
  }
  return false
}

async function needsCaptcha(page) {
  try {
    const url = page.url()
    console.error(`[DEBUG] 当前 URL: ${url}`)
    if (/verify|captcha|security-check/.test(url)) {
      console.error(`[WARN] URL 包含验证特征`)
      return true
    }
    const found = await page.$('[class*="captcha"],[class*="verify"],[class*="security"],[id*="captcha"],[id*="verify"]')
    if (found) { console.error(`[WARN] 找到验证码元素`); return true }
    const text = await page.evaluate(() => document.body?.innerText || '')
    if (text.includes('安全验证') && text.includes('刷新')) { console.error(`[WARN] 安全验证文本`); return true }
  } catch (e) {
    // 页面跳转时 context 被销毁属于正常情况，不记录 ERROR
    if (!e.message.includes('Execution context was destroyed') &&
        !e.message.includes('Target page, context or browser has been closed')) {
      console.error(`[ERROR] needsCaptcha: ${e.message}`)
    }
  }
  return false
}

async function waitForCaptcha(page) {
  const url = page.url()
  throw new CaptchaDetectedError(`检测到安全验证并立即退出: ${url}`)
}

async function hasSearchCards(page) {
  try {
    return await page.$$eval(SEARCH_CARD_SELECTOR, els => els.length > 0)
  } catch {
    return false
  }
}

async function waitForSearchReady(page) {
  let apiReady = false
  try {
    await page.waitForResponse(
      r => SEARCH_NOTES_API_RE.test(r.url()) && r.status() >= 200 && r.status() < 500,
      { timeout: searchResponseTimeoutMs }
    )
    apiReady = true
  } catch {
    // best-effort: 有些情况下 DOM 已经有卡片但响应监听会错过
  }
  if (await needsCaptcha(page)) await waitForCaptcha(page)
  if (await needsLogin(page)) throw new LoginRequiredError()
  const cardsReady = await hasSearchCards(page)
  return { apiReady, cardsReady }
}

async function openSearchPageWithRetry(page, url) {
  for (let attempt = 1; attempt <= searchOpenRetries; attempt++) {
    throwIfRiskAbort()
    if (attempt > 1) {
      const coolDown = 1800 + Math.floor(Math.random() * 2200)
      console.error(`[WARN] 搜索页重试 ${attempt}/${searchOpenRetries}，冷却 ${coolDown}ms`)
      await page.waitForTimeout(coolDown)
      // 轻量回到 explore，避免连续直冲 search_result 触发风控
      try {
        await page.goto(EXPLORE_URL, {
          waitUntil: 'domcontentloaded',
          timeout: Math.min(searchNavTimeoutMs, 12000),
        })
      } catch {
        // ignore
      }
      await page.waitForTimeout(250 + Math.floor(Math.random() * 300))
    }

    let gotoTimeout = false
    try {
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: searchNavTimeoutMs })
    } catch (e) {
      gotoTimeout = true
      const msg = String(e?.message || '')
      if (msg.toLowerCase().includes('timeout')) {
        console.error(`[WARN] 搜索页 goto 超时（attempt=${attempt}），尝试就地恢复`)
      } else {
        throw e
      }
    }

    if (await needsLogin(page)) throw new LoginRequiredError()
    const { apiReady, cardsReady } = await waitForSearchReady(page)
    const onSearchUrl = /\/search_result/.test(page.url())

    if (onSearchUrl && (cardsReady || apiReady)) {
      if (gotoTimeout) {
        console.error('[INFO] goto 超时但页面已可用，继续采集')
      }
      return
    }
  }
  throw new Error(`搜索页进入失败：重试 ${searchOpenRetries} 次后仍未就绪`)
}

async function clickSortFilter(page, sortId) {
  /** 点击搜索结果页的排序按钮，例如 popularity_descending（最多点赞） */
  try {
    // 滚回顶部确保筛选栏可见
    await page.evaluate(() => window.scrollTo(0, 0))
    await page.waitForTimeout(800 + Math.random() * 400)

    const textMap = { popularity_descending: '最多点赞', collect_descending: '最多收藏' }
    const label = textMap[sortId]

    // 方案1：data-id 属性
    let clicked = await page.evaluate((id) => {
      const el = document.querySelector(`[data-id="${id}"], [data-filter-id="${id}"]`)
      if (el) { el.click(); return `data-id=${id}` }
      return null
    }, sortId)

    // 方案2：Playwright locator 按文本精确匹配
    if (!clicked && label) {
      const loc = page.locator(`span, div, button`).filter({ hasText: new RegExp(`^${label}$`) }).first()
      const visible = await loc.isVisible().catch(() => false)
      if (visible) {
        await loc.click({ delay: 40 })
        clicked = `locator:text=${label}`
      }
    }

    // 方案3：JS 全量文字搜索（兜底）
    if (!clicked && label) {
      clicked = await page.evaluate((lbl) => {
        const el = [...document.querySelectorAll('span, div, button')]
          .find(e => e.textContent.trim() === lbl && e.offsetParent !== null)
        if (el) { el.click(); return `js:text=${lbl}` }
        return null
      }, label)
    }

    if (clicked) {
      console.error(`[INFO] 已点击排序筛选: ${clicked}`)
      await page.waitForResponse(r => SEARCH_NOTES_API_RE.test(r.url()), { timeout: 8000 }).catch(() => null)
    } else {
      console.error(`[WARN] 未找到排序筛选按钮: ${sortId}，已退回 XHS 默认排序（阶段二将在内存中按点赞/收藏重排）`)
    }
  } catch (e) {
    console.error(`[WARN] clickSortFilter 失败: ${e.message}`)
  }
}

async function scrapeListPage(page, limit) {
  const url = SEARCH_URL + encodeURIComponent(keyword)
  console.error(`[INFO] 打开搜索页: ${url}`)
  await openSearchPageWithRetry(page, url)

  // 切换到「最多点赞」排序，让 XHS 直接返回高赞内容
  await clickSortFilter(page, 'popularity_descending')

  const posts = []
  let attempts = 0
  let stagnantRounds = 0
  while (posts.length < limit && attempts < 10) {
    throwIfRiskAbort()
    if (await needsLogin(page)) throw new LoginRequiredError()
    if (await needsCaptcha(page)) await waitForCaptcha(page)
    console.error(`[DEBUG] 采集轮次 ${attempts + 1}/10，已获取 ${posts.length}/${limit}`)
    try {
      const items = await page.$$eval(
        SEARCH_CARD_SELECTOR,
        els => els.map(el => {
          const a = el.querySelector('a[href*="/explore/"]') || el.querySelector('a')
          const authorLink = (
            el.querySelector('a[href*="/user/profile/"]') ||
            el.querySelector('a[href*="/user/"]')
          )
          const href = a?.href || ''
          const profileUrl = authorLink?.href || ''
          let parsed = null
          try {
            parsed = href ? new URL(href, location.origin) : null
          } catch {
            parsed = null
          }
          const xsecToken = (
            parsed?.searchParams?.get('xsec_token') ||
            a?.getAttribute('xsec_token') ||
            a?.getAttribute('data-xsec-token') ||
            el.getAttribute('xsec_token') ||
            el.getAttribute('data-xsec-token') ||
            ''
          )
          const xsecSource = (
            parsed?.searchParams?.get('xsec_source') ||
            a?.getAttribute('xsec_source') ||
            a?.getAttribute('data-xsec-source') ||
            'pc_search'
          )
          const coverImg = (
            el.querySelector('img.cover, .note-cover img, .cover img, .cover-container img, img[src*="xhscdn"], img[src*="xiaohongshu"]') ||
            el.querySelector('img')
          )
          return {
            id: el.getAttribute('data-id') || parsed?.pathname?.match(/\/explore\/([a-z0-9]+)/)?.[1],
            title: el.querySelector('.title, .note-title, h3')?.textContent?.trim(),
            likes: parseInt(el.querySelector('.like-count, .count')?.textContent?.replace(/[^0-9]/g, '')) || 0,
            saves: parseInt(el.querySelector('.collect-count, .save-count')?.textContent?.replace(/[^0-9]/g, '')) || 0,
            comments: parseInt(el.querySelector('.comment-count')?.textContent?.replace(/[^0-9]/g, '')) || 0,
            author: el.querySelector('.author, .user-name, .name')?.textContent?.trim(),
            author_id: profileUrl.match(/\/user\/profile\/([^?/]+)/)?.[1] || null,
            author_profile_url: profileUrl || null,
            cover_url: coverImg?.src || coverImg?.getAttribute('data-src') || '',
            url: href,
            xsec_token: xsecToken,
            xsec_source: xsecSource,
            date: el.querySelector('.date, .time, .publish-time')?.textContent?.trim(),
          }
        })
      )
      const newPosts = items.filter(p => p.id && (p.title || apiNoteData.has(p.id)) && !posts.find(x => x.id === p.id))
      for (const p of newPosts) {
        if (p.id && p.xsec_token) {
          xsecTokenMap.set(p.id, { token: p.xsec_token, source: p.xsec_source || 'pc_search' })
        }
        // 用 API 返回的精确数据覆盖 DOM 抓取的粗略数据
        const api = apiNoteData.get(p.id)
        if (api) {
          if (api.likes > 0)    p.likes    = api.likes
          if (api.saves > 0)    p.saves    = api.saves
          if (api.comments > 0) p.comments = api.comments
          if (api.title)        p.title    = api.title
          if (api.author)       p.author   = api.author
          if (api.author_id)    p.author_id = api.author_id
          if (api.cover_url)    p.cover_url = api.cover_url
        }
      }
      posts.push(...newPosts)
      console.error(`[DEBUG] 本轮新增 ${newPosts.length}，合计 ${posts.length}`)
      stagnantRounds = newPosts.length === 0 ? stagnantRounds + 1 : 0
    } catch (e) { console.error(`[ERROR] $$eval: ${e.message}`) }

    if (stagnantRounds >= listStagnationRounds) {
      console.error(`[WARN] 连续 ${stagnantRounds} 轮无新增，提前结束列表采集`)
      break
    }

    if (posts.length < limit) {
      console.error(`[DEBUG] 滚动加载更多...`)
      await page.evaluate(() => window.scrollBy(0, window.innerHeight * 2))
      try {
        await page.waitForResponse(r => SEARCH_NOTES_API_RE.test(r.url()), { timeout: 2600 })
      } catch {
        await page.waitForTimeout(220 + Math.floor(Math.random() * 220))
      }
      await page.waitForTimeout(
        listRoundDelayMinMs + Math.floor(Math.random() * (listRoundDelayMaxMs - listRoundDelayMinMs + 1))
      )
    }
    attempts++
  }
  return posts.map(p => {
    const parsedDate = parseRelativeDate(p.date)
    return { ...p, date: parsedDate, published_at: parsedDate }
  })
}

function mergeDetailIntoPost(post, detail) {
  return {
    ...post,
    likes: detail.likes || post.likes,
    saves: detail.saves || post.saves,
    comments: detail.comments || post.comments,
    shares: detail.shares || 0,
    author: detail.author || post.author,
    author_id: detail.author_id || post.author_id || null,
    author_redid: detail.author_redid || null,
    content: detail.content,
    tags: detail.tags,
    date: parseRelativeDate(detail.date) || post.date,
  }
}

async function extractDetailFromCurrentPage(page, post) {
  const landedUrl = page.url()
  const detailDomReady = await hasDetailDom(page)
  if (!/\/explore\/[a-z0-9]+/.test(landedUrl) && !detailDomReady) {
    return { ...post, detail_blocked_reason: `redirected:${landedUrl}` }
  }
  if (await needsLogin(page)) throw new LoginRequiredError()
  await page.waitForTimeout(400)
  const detail = await page.evaluate(() => {
    const get = (sel) => document.querySelector(sel)?.textContent?.trim()
    const getNum = (sel) => parseInt((get(sel) || '0').replace(/[^0-9]/g, '')) || 0
    return {
      likes: getNum('.like-wrapper .count, .interactions .like .count'),
      saves: getNum('.collect-wrapper .count, .interactions .collect .count'),
      comments: getNum('.comment-wrapper .count, .interactions .comment .count'),
      shares: getNum('.share-wrapper .count'),
      author: get('.author-wrapper .name, .info-container .name, .username'),
      author_id: document.querySelector('a[href*="/user/profile/"]')
        ?.href?.match(/\/user\/profile\/([^?/]+)/)?.[1] || null,
      author_redid: get('.user-redId')?.replace(/^小红书号[:：]?\s*/, '') || null,
      content: get('.note-content .desc, #detail-desc'),
      tags: [...document.querySelectorAll('.tag-item, .tag')].map(t => t.textContent.trim()),
      date: get('.date, .publish-time'),
    }
  })
  return mergeDetailIntoPost(post, detail)
}

let _riskAbortReason = ''

function throwIfRiskAbort() {
  if (_riskAbortReason) throw new CaptchaDetectedError(_riskAbortReason)
}

async function ensureOnAuthorProfile(page, canonicalProfileUrl, expectedAuthorId = '') {
  if (!canonicalProfileUrl) return false
  if (await needsCaptcha(page)) await waitForCaptcha(page)
  const curUrl = page.url()
  const curId = extractProfileIdFromUrl(curUrl)
  const onExpected = curUrl.includes('/user/profile/') && (!expectedAuthorId || curId === expectedAuthorId)
  if (onExpected) return true

  const opened = await navigateInPage(page, canonicalProfileUrl, navTimeoutMs, [/\/user\/profile\//])
  if (!opened) return false
  await page.waitForTimeout(180 + Math.floor(Math.random() * 220))
  if (await needsCaptcha(page)) await waitForCaptcha(page)
  const landedUrl = page.url()
  const landedId = extractProfileIdFromUrl(landedUrl)
  return landedUrl.includes('/user/profile/') && (!expectedAuthorId || landedId === expectedAuthorId)
}

async function scrapeDetailPage(page, post, refererUrl) {
  if (!post.id && !post.url) return post
  throwIfRiskAbort()
  try {
    if (await needsCaptcha(page)) await waitForCaptcha(page)
    console.error(`[DEBUG] 抓取详情: ${post.title?.slice(0, 20)}...`)

    const tokenInfo = (
      (post.id && xsecTokenMap.get(post.id)) ||
      (post.xsec_token ? { token: post.xsec_token, source: post.xsec_source || 'pc_search' } : null)
    )
    const fallbackUrl = post.url || ''
    const targetUrl = buildExploreUrl(post.id, tokenInfo, fallbackUrl)
    if (!tokenInfo && post.id) {
      console.error(`[WARN] ${post.id} 无 xsec_token，跳过详情降级导航`)
      return { ...post, detail_blocked_reason: 'no_token_seed_fallback' }
    }

    const opened = await navigateInPage(page, targetUrl, navTimeoutMs + 4000)
    if (!opened) {
      return { ...post, detail_blocked_reason: `inpage_nav_failed:${page.url()}` }
    }
    return await extractDetailFromCurrentPage(page, post)
  } catch (e) {
    if (e instanceof CaptchaDetectedError) throw e
    console.error(`[WARN] 详情抓取失败: ${e.message}`)
    return post
  }
}

async function openAuthorProfileViaClick(page, author, searchUrl) {
  throwIfRiskAbort()
  if (await needsLogin(page)) throw new LoginRequiredError()
  if (await needsCaptcha(page)) await waitForCaptcha(page)

  const currentUrl = page.url()
  const onSearchPage = currentUrl.includes('/search_result')
  const cleanProfileUrl = stripProfileUrl(author.author_profile_url)
  const profilePatterns = [/\/user\/profile\//]

  // 已有 profile URL：优先用 navigateInPage 直接跳转（保持 same-origin，省去搜索页重载）
  if (cleanProfileUrl && !onSearchPage) {
    const opened = await navigateInPage(page, cleanProfileUrl, navTimeoutMs, profilePatterns)
    if (opened && page.url().includes('/user/profile/')) return page.url()
    if (await needsCaptcha(page)) await waitForCaptcha(page)
    console.error(`[WARN] 作者 ${author.name} 主页跳转失败，跳过（禁用 profile goto fallback）`)
    return ''
  }

  // 在搜索页：尝试点击作者链接（第一个作者的路径，保留真人点击行为）
  if (!onSearchPage) {
    await page.goto(searchUrl, { waitUntil: 'domcontentloaded', timeout: 20000 })
    await page.waitForTimeout(600)
    if (await needsCaptcha(page)) await waitForCaptcha(page)
  }

  let clicked = false
  if (author.author_id) {
    try {
      const loc = page.locator(`a[href*="/user/profile/${author.author_id}"]`).first()
      if (await loc.count()) {
        try { await loc.evaluate(el => el.removeAttribute('target')) } catch {}
        try { await loc.scrollIntoViewIfNeeded() } catch {}
        await loc.click({ timeout: 8000 })
        clicked = true
      }
    } catch {
      // fall through
    }
  }

  if (!clicked) {
    clicked = await page.evaluate((authorInfo) => {
      const links = [...document.querySelectorAll('a[href*="/user/profile/"]')]
      if (!links.length) return false
      const normalizedName = String(authorInfo.name || '').replace(/\s+/g, '')
      const byName = normalizedName
        ? links.find(a => String(a.textContent || '').replace(/\s+/g, '').includes(normalizedName))
        : null
      const target = byName || null
      if (!target) return false
      try { target.removeAttribute('target') } catch {}
      try { target.scrollIntoView({ behavior: 'instant', block: 'center' }) } catch {}
      target.click()
      return true
    }, author)
  }

  if (clicked) {
    try {
      await page.waitForURL(url => url.includes('/user/profile/'), { timeout: 10000 })
      if (await needsCaptcha(page)) await waitForCaptcha(page)
      return page.url()
    } catch {
      // fall through
    }
  }

  if (!cleanProfileUrl) return ''
  const opened = await navigateInPage(page, cleanProfileUrl, navTimeoutMs, profilePatterns)
  if (await needsCaptcha(page)) await waitForCaptcha(page)
  if (opened) return page.url()
  console.error(`[WARN] 作者 ${author.name} 点击失败且 in-page 导航失败，跳过（禁用 profile goto fallback）`)
  return ''
}

async function collectProfilePosts(page, maxNotes = 10) {
  const notes = []
  const seen = new Set()
  let attempts = 0

  while (notes.length < maxNotes && attempts < 8) {
    const items = await page.$$eval(
      'a[href*="/explore/"], a[href*="/discovery/item/"], [data-note-id]',
      nodes => nodes.map(node => {
        const isLink = node.tagName?.toLowerCase() === 'a'
        const link = isLink ? node : (node.querySelector('a[href*="/explore/"], a[href*="/discovery/item/"]') || null)
        const href = link?.href || link?.getAttribute?.('href') || ''
        let parsed = null
        try {
          parsed = href ? new URL(href, location.origin) : null
        } catch {
          parsed = null
        }
        const card = node.closest('[class*="note"], article, section, div') || node
        const title = card?.querySelector?.('.title, .note-title, h3')?.textContent?.trim() || ''
        const noteIdFromHref = (
          parsed?.pathname?.match(/\/explore\/([a-z0-9]+)/)?.[1] ||
          parsed?.pathname?.match(/\/discovery\/item\/([a-z0-9]+)/)?.[1] ||
          ''
        )
        const noteIdFromData = (
          node.getAttribute?.('data-note-id') ||
          card?.getAttribute?.('data-note-id') ||
          card?.getAttribute?.('note-id') ||
          ''
        )
        const id = noteIdFromHref || noteIdFromData || ''
        const xsecToken = (
          parsed?.searchParams?.get('xsec_token') ||
          link?.getAttribute?.('xsec_token') ||
          link?.getAttribute?.('data-xsec-token') ||
          card?.getAttribute?.('xsec_token') ||
          card?.getAttribute?.('data-xsec-token') ||
          ''
        )
        const xsecSource = (
          parsed?.searchParams?.get('xsec_source') ||
          link?.getAttribute?.('xsec_source') ||
          link?.getAttribute?.('data-xsec-source') ||
          'pc_user'
        )
        return { id, url: href, title, xsec_token: xsecToken, xsec_source: xsecSource }
      })
    )

    for (const item of items) {
      if (!item.id || seen.has(item.id)) continue
      seen.add(item.id)
      notes.push(item)
      if (item.xsec_token) {
        xsecTokenMap.set(item.id, { token: item.xsec_token, source: item.xsec_source || 'pc_search' })
      }
      if (notes.length >= maxNotes) break
    }
    if (notes.length >= maxNotes) break

    await page.evaluate(() => window.scrollBy(0, window.innerHeight * 1.8))
    await page.waitForTimeout(350)
    attempts++
  }

  return notes.slice(0, maxNotes)
}

async function clickNoteFromProfile(page, post) {
  if (!post.id) return { opened: false, captured: null, feedFired: false, commentFired: false, metricsFired: false }
  const selectors = [
    `a[href*="/explore/${post.id}"]`,
    `a[href*="/discovery/item/${post.id}"]`,
    `[data-note-id="${post.id}"] a`,
    `[data-note-id="${post.id}"]`,
  ]
  for (const selector of selectors) {
    try {
      const loc = page.locator(selector).first()
      if (await loc.count()) {
        return await clickAndCaptureNoteDetail(page, loc, post.id, detailOpenTimeoutMs)
      }
    } catch {
      // next selector
    }
  }

  // 标题片段兜底：部分作者页链接不含 noteId，但卡片文本可定位
  if (post.title) {
    const snippet = String(post.title).replace(/\s+/g, '').slice(0, 10)
    if (snippet.length >= 4) {
      try {
        const card = page.locator('.note-item, [data-note-id], article, section').filter({ hasText: snippet }).first()
        if (await card.count()) {
          const clickable = card.locator(
            'a[href*="/explore/"], a[href*="/discovery/item/"], .cover, .cover-container, .note-cover'
          ).first()
          if (await clickable.count()) {
            return await clickAndCaptureNoteDetail(page, clickable, post.id, detailOpenTimeoutMs)
          }
          return await clickAndCaptureNoteDetail(page, card, post.id, detailOpenTimeoutMs)
        }
      } catch {
        // ignore
      }
    }
  }

  return { opened: false, captured: null, feedFired: false, commentFired: false, metricsFired: false }
}

async function scrapeAuthorDetails(page, author, searchUrl, maxNotes = 10) {
  throwIfRiskAbort()
  if (await needsCaptcha(page)) await waitForCaptcha(page)
  const authorName = cleanAuthor(author.name)
  const profileUrl = await openAuthorProfileViaClick(page, author, searchUrl)
  const canonicalProfileUrl = stripProfileUrl(profileUrl)
  const expectedAuthorId = author.author_id || extractProfileIdFromUrl(canonicalProfileUrl) || ''
  if (!/\/user\/profile\//.test(profileUrl)) {
    console.error(`[WARN] 无法进入作者主页: ${authorName}`)
    return []
  }
  if (!(await ensureOnAuthorProfile(page, canonicalProfileUrl, expectedAuthorId))) {
    console.error(`[WARN] 作者 ${authorName} 主页上下文异常，冷却 3s 后跳过`)
    await page.waitForTimeout(2800 + Math.floor(Math.random() * 1200))
    return []
  }
  if (await needsLogin(page)) throw new LoginRequiredError()
  await page.waitForTimeout(600)

  const notes = await collectProfilePosts(page, maxNotes)
  console.error(`[INFO] 作者 ${authorName} 主页采样 ${notes.length}/${maxNotes} 篇`)
  const details = []
  for (let noteIdx = 0; noteIdx < notes.length; noteIdx++) {
    throwIfRiskAbort()
    if (await needsCaptcha(page)) await waitForCaptcha(page)
    if (!(await ensureOnAuthorProfile(page, canonicalProfileUrl, expectedAuthorId))) {
      console.error(`[WARN] 作者 ${authorName} 上下文漂移，停止当前作者后续抓取`)
      break
    }
    const note = notes[noteIdx]
    await randomDelay(authorNoteDelayMinMs, authorNoteDelayMaxMs)
    const post = {
      ...note,
      author: authorName,
      author_id: author.author_id || null,
      author_profile_url: author.author_profile_url || profileUrl,
      date: parseRelativeDate(note.date),
    }
    console.error(`[INFO] 作者 ${authorName} 笔记 ${noteIdx + 1}/${notes.length}：点击「${(post.title || post.id || '').slice(0, 20)}」`)
    let full = null
    const clickResult = await clickNoteFromProfile(page, post)
    if (await needsCaptcha(page)) await waitForCaptcha(page)
    if (clickResult.captured?.token && !xsecTokenMap.has(post.id)) {
      xsecTokenMap.set(post.id, {
        token: clickResult.captured.token,
        source: clickResult.captured.source || 'pc_user',
      })
    }

    if (clickResult.opened) {
      console.error(`[DEBUG] 笔记已打开，提取正文...`)
      await simulateDetailPageStay(page)
      full = await extractDetailFromCurrentPage(page, post)
      console.error(`[DEBUG] 正文 ${full?.content?.length || 0} 字，准备回作者主页`)
    } else {
      const tokenInfo = (
        (post.id && xsecTokenMap.get(post.id)) ||
        (post.xsec_token ? { token: post.xsec_token, source: post.xsec_source || 'pc_user' } : null) ||
        await extractTokenFromPageContext(page, post.id)
      )
      const targetUrl = buildExploreUrl(post.id, tokenInfo, post.url || '')
      if (!targetUrl || (!tokenInfo && !targetUrl.includes('xsec_token='))) {
        console.error(`[WARN] 笔记 ${post.id} 点击失败且无 token，跳过`)
        full = { ...post, detail_blocked_reason: 'click_failed_no_token' }
      } else {
        console.error(`[DEBUG] 点击失败，改用 navigateInPage 跳转`)
        const opened = await navigateInPage(page, targetUrl, navTimeoutMs)
        if (opened) {
          await simulateDetailPageStay(page)
          full = await extractDetailFromCurrentPage(page, post)
          console.error(`[DEBUG] navigateInPage 正文 ${full?.content?.length || 0} 字，返回主页`)
        } else {
          console.error(`[WARN] 笔记 ${post.id} navigateInPage 失败: ${page.url().slice(0, 60)}`)
          full = { ...post, detail_blocked_reason: `inpage_nav_failed:${page.url()}` }
        }
      }
    }
    const restored = await ensureOnAuthorProfile(page, canonicalProfileUrl, expectedAuthorId)
    if (!restored) {
      console.error(`[WARN] 作者 ${authorName} 回主页失败，停止当前作者后续抓取`)
      details.push({ ...full, viral_score: scorePost(full) })
      break
    }
    details.push({ ...full, viral_score: scorePost(full) })
  }
  return details
}

// ── 主流程 ─────────────────────────────────────────────────────────────────
const session = getValidSession(preferredAccount)
if (!session) {
  if (preferredAccount) {
    console.error(`[ERROR] 指定账号不可用: ${preferredAccount}，请检查账号是否启用、未过期且 storageState 存在`)
  } else {
    console.error('[ERROR] 无可用 session，请先运行: python scripts/account_manager.py scan --name default')
  }
  process.exit(1)
}
console.error(`[INFO] 使用账号: ${session.accountName}`)
  console.error(
    `[INFO] Anti-crawl参数: searchNav=${searchNavTimeoutMs}ms, searchResp=${searchResponseTimeoutMs}ms, ` +
    `detailOpen=${detailOpenTimeoutMs}ms, click=${clickActionTimeoutMs}ms, retries=${searchOpenRetries}, ` +
    `probeLimit=${captchaProbeLimit}, allowStaleFallback=${allowStaleFallback}`
  )

const storageStateOpt = session.storagePath
  ? session.storagePath
  : cookieStringToStorageState(session.cookieStr)

let browser
try {
  browser = await _chromium.launch(launchOptions(false))
} catch {
  browser = await _chromium.launch(launchOptionsFallback(false))
}

const runStartedAt = Date.now()
const httpRecords = []
const reqMap = new Map()
let seq = 0

// 将 fullPosts 聚合成 authors 结构，供 SIGTERM 快照和阶段四复用
function buildAuthorsFromPosts(fullPosts, selectedAuthors, authorLimit) {
  const authorMap = {}
  for (const post of fullPosts) {
    const cleanName = cleanAuthor(post.author)
    if (!cleanName) continue
    if (!authorMap[cleanName]) {
      authorMap[cleanName] = { author: cleanName, author_id: null, author_redid: null, posts: [] }
    }
    if (post.author_id && !authorMap[cleanName].author_id) authorMap[cleanName].author_id = post.author_id
    if (post.author_redid && !authorMap[cleanName].author_redid) authorMap[cleanName].author_redid = post.author_redid
    authorMap[cleanName].posts.push({ ...post, viral_score: post.viral_score || scorePost(post) })
  }
  for (const author of selectedAuthors) {
    const cleanName = cleanAuthor(author.name)
    if (!cleanName) continue
    if (!authorMap[cleanName]) {
      const seeds = author.seed_posts.map(p => ({ ...p, viral_score: p.viral_score || scorePost(p) }))
      authorMap[cleanName] = { author: cleanName, author_id: author.author_id || null, author_redid: null, posts: seeds }
    }
  }
  return Object.values(authorMap)
    .filter(a => a.posts.length > 0)
    .map(a => {
      const sorted = a.posts.sort((x, y) => y.viral_score - x.viral_score)
      return {
        name: a.author,
        xhs_id: a.author_redid || null,
        author_hex_id: a.author_id || null,
        status: '爆款账号',
        explore_data: {
          keyword,
          viral_count: a.posts.length,
          max_viral_score: sorted[0]?.viral_score || 0,
          avg_likes: Math.round(a.posts.reduce((s, p) => s + (p.likes || 0), 0) / a.posts.length),
          avg_saves: Math.round(a.posts.reduce((s, p) => s + (p.saves || 0), 0) / a.posts.length),
          top_post_title: sorted[0]?.title || '',
          top_post_url: sorted[0]?.url || '',
        }
      }
    })
    .sort((a, b) => b.explore_data.max_viral_score - a.explore_data.max_viral_score)
    .slice(0, authorLimit)
}

// 收到 SIGTERM 时输出已有的部分结果，避免超时强杀导致数据全丢
let _partialFullPosts = []
let _partialAuthors = []
process.on('SIGTERM', () => {
  console.error('[WARN] 收到 SIGTERM，输出部分采集结果后退出')
  try {
    process.stdout.write(JSON.stringify({
      partial: true,
      error: 'SIGTERM',
      keyword,
      authors: _partialAuthors,
      posts: _partialFullPosts,
    }))
  } catch {}
  process.exit(0)
})

try {
  xsecTokenMap.clear()
  const context = await browser.newContext(contextOptions(storageStateOpt))
  await context.addInitScript(NAVIGATOR_INIT_SCRIPT)

  const page = await context.newPage()

  page.on('request', async (req) => {
    const url = req.url()
    if (!isXhsApiUrl(url)) return
    if (url.includes('/api/redcaptcha/v2/getconfig')) {
      const nextCount = (globalThis.__xhsCaptchaProbeCount || 0) + 1
      globalThis.__xhsCaptchaProbeCount = nextCount
      if (nextCount >= captchaProbeLimit && !_riskAbortReason) {
        _riskAbortReason = `风控探针达到阈值(${nextCount}/${captchaProbeLimit})，已主动止损退出`
        console.error(`[WARN] ${_riskAbortReason}`)
      }
    }
    if (!httpLogPath) return
    const item = {
      seq: seq++,
      ts_ms: Date.now() - runStartedAt,
      method: req.method(),
      url,
      resource_type: req.resourceType(),
      headers: {},
      post_data: req.postData() || null,
      status: null,
      response_headers: {},
    }
    try {
      item.headers = await req.allHeaders()
    } catch {
      item.headers = {}
    }
    httpRecords.push(item)
    reqMap.set(req, item)
  })

  page.on('response', async (resp) => {
    const req = resp.request()
    const item = reqMap.get(req)
    if (item) {
      item.status = resp.status()
      try {
        item.response_headers = await resp.allHeaders()
      } catch {
        item.response_headers = {}
      }
    }

    const url = resp.url()
    if (!SEARCH_NOTES_API_RE.test(url)) return
    if (resp.status() < 200 || resp.status() >= 300) return
    try {
      const body = await resp.json()
      const items = body?.data?.items || body?.items || []
      let added = 0
      for (const row of items) {
        const noteCard = row?.note_card || row?.note || {}
        const noteId =
          row?.id?.noteId ||
          row?.note_id ||
          noteCard?.note_id ||
          row?.id
        const token =
          row?.xsec_token ||
          noteCard?.xsec_token ||
          row?.id?.xsecToken
        const source =
          row?.xsec_source ||
          noteCard?.xsec_source ||
          'pc_search'
        if (noteId && token && !xsecTokenMap.has(noteId)) {
          xsecTokenMap.set(noteId, { token, source })
          added++
        }
        // 同步存储点赞/收藏/作者数据（来自 API，比 DOM 更准确）
        if (noteId) {
          const ii = noteCard?.interact_info || {}
          const user = noteCard?.user || {}
          const cover = noteCard?.cover || {}
          const parseLikeNum = v => {
            if (!v) return 0
            const s = String(v).replace(/,/g, '')
            if (s.includes('万')) return Math.round(parseFloat(s) * 10000)
            return parseInt(s) || 0
          }
          apiNoteData.set(noteId, {
            likes:     parseLikeNum(ii.liked_count),
            saves:     parseLikeNum(ii.collected_count),
            comments:  parseLikeNum(ii.comment_count),
            title:     noteCard?.display_title || noteCard?.title || '',
            author:    user.nickname || '',
            author_id: user.user_id || user.userid || '',
            cover_url: cover.url_default || cover.url_pre || '',
          })
        }
      }
      if (added > 0) {
        console.error(`[INFO] xsec_token 收集：${xsecTokenMap.size} 条`)
      }
    } catch (e) {
      console.error(`[WARN] search/notes JSON 解析失败: ${e.message}`)
    }
  })

  const warmHits = await warmupSession(page)
  console.error(`[INFO] 会话预热命中 ${warmHits.length} 个初始化 API`)
  if (await needsLogin(page)) throw new LoginRequiredError()

  // 阶段一：列表页
  const searchUrl = SEARCH_URL + encodeURIComponent(keyword)
  const internalLimit = Math.max(searchLimit, authorLimit * 3)
  console.error(`\n[INFO] ── 阶段一：采集列表页，关键词="${keyword}"，目标 ${internalLimit} 笔记 ──`)
  const listPosts = await scrapeListPage(page, internalLimit)
  console.error(`[INFO] 阶段一完成：获取 ${listPosts.length} 笔记`)

  // 阶段二：半年内样本筛选（按点赞/收藏高值排序）
  const selectResult = selectPostsForDetail(listPosts, viralThreshold, {
    limit: Math.max(Math.min(80, internalLimit), authorLimit * 2),
    minTarget: Math.min(10, authorLimit),
  })
  const pickedPosts = selectResult.picked
  const candidateSource = pickedPosts.length > 0
    ? pickedPosts
    : (allowStaleFallback ? listPosts : [])
  const authorCandidateMap = {}
  for (const post of candidateSource) {
    const name = cleanAuthor(post.author)
    if (!name) continue
    if (!authorCandidateMap[name]) {
      authorCandidateMap[name] = {
        name,
        author_id: post.author_id || null,
        author_profile_url: post.author_profile_url || null,
        seed_posts: [],
      }
    }
    const cur = authorCandidateMap[name]
    if (!cur.author_id && post.author_id) cur.author_id = post.author_id
    if (!cur.author_profile_url && post.author_profile_url) cur.author_profile_url = post.author_profile_url
    cur.seed_posts.push({ ...post, viral_score: scorePost(post) })
  }

  // 最低互动量门槛：半年内最好一篇笔记的点赞或收藏至少达标，过滤无效低质账号
  const minPeakEngagement = parseInt(process.env.XHS_MIN_PEAK_ENGAGEMENT || '50') || 50
  const _peakOf = p => Math.max(Number(p.likes) || 0, Number(p.saves) || 0)

  const allRanked = Object.values(authorCandidateMap)
    .map(a => ({
      ...a,
      top_score:       Math.max(...a.seed_posts.map(p => p.viral_score || scorePost(p)), 0),
      peak_engagement: Math.max(...a.seed_posts.map(_peakOf), 0),
    }))
    .sort((a, b) => b.peak_engagement - a.peak_engagement || b.top_score - a.top_score)

  const qualified = allRanked.filter(a => a.peak_engagement >= minPeakEngagement)
  const selectedAuthors = (qualified.length > 0 ? qualified : allRanked)
    .slice(0, authorLimit)

  if (allRanked.length === 0) {
    console.error(
      `[WARN] 阶段二无作者候选：recent 样本为 0（共 ${listPosts.length} 条列表笔记，` +
      `可能原因：发布时间字段缺失、关键词匹配不足、或 XHS 返回空结果）`
    )
  } else if (qualified.length === 0) {
    console.error(`[WARN] 无账号达到最低互动量(${minPeakEngagement})，降级使用全部 ${allRanked.length} 个候选`)
  } else if (qualified.length < allRanked.length) {
    console.error(`[INFO] 互动量过滤：${allRanked.length} 个候选 → 保留 ${qualified.length} 个（peak≥${minPeakEngagement}）`)
  }

  console.error(
    `[INFO] 阶段二完成：作者候选 ${selectedAuthors.length} 个，详情种子 ${pickedPosts.length} 条，` +
    `策略=${selectResult.strategy}，recent=${selectResult.recentCount ?? selectResult.strictCount}，threshold=${selectResult.appliedThreshold}`
  )
  if (selectResult.strategy === 'recent_peak_insufficient') {
    console.error('[WARN] 半年内高赞/高藏样本不足，结果可能偏少')
  }
  if (pickedPosts.length === 0 && !allowStaleFallback) {
    console.error('[WARN] 半年内无可用样本，已禁用旧样本回退。可设置 XHS_ALLOW_STALE_FALLBACK=1 启用回退')
  }

  // --list-only：只输出作者候选，不抓详情
  if (listOnly) {
    const authorList = selectedAuthors.map(a => ({
      name:             a.name,
      author_id:        a.author_id,
      peak_engagement:  a.peak_engagement,
      top_score:        a.top_score,
      seed_count:       a.seed_posts?.length || 0,
      seed_posts:       (a.seed_posts || []).slice(0, 3).map(p => ({
        title:  p.title,
        likes:  p.likes,
        saves:  p.saves,
        date:   p.date,
      })),
    }))
    process.stdout.write(JSON.stringify({ authors: authorList }, null, 2))
    await browser.close()
    process.exit(0)
  }

  // 阶段三：按作者抓详情（目标：每作者 10 篇，不足则抓到现有）
  console.error(`[INFO] ── 阶段三：按作者抓详情，目标每作者 ${perAuthorNoteLimit} 篇 ──`)
  const fullPosts = []
  const seenPostIds = new Set()
  for (const author of selectedAuthors) {
    throwIfRiskAbort()
    // 作者切换间隔：模拟人工节奏（人工日志 p25=41s p50=61s），引入多段随机避免等间距
    {
      const base = 18000 + Math.random() * 22000          // 18-40 s 基础等待
      const extra = Math.random() < 0.4
        ? 15000 + Math.random() * 30000                   // 40% 概率额外停留 15-45 s（模拟阅读）
        : 0
      await randomDelay(base + extra, base + extra + 8000)
    }
    let details = []
    try {
      details = await scrapeAuthorDetails(page, author, searchUrl, perAuthorNoteLimit)
    } catch (authorErr) {
      if (authorErr instanceof CaptchaDetectedError) throw authorErr
      console.error(`[WARN] 作者 ${author.name} 抓取异常，跳过：${authorErr.message}`)
      // 重置页面到已知安全状态，避免影响后续作者
      try { await page.goto(EXPLORE_URL, { waitUntil: 'domcontentloaded', timeout: 15000 }) } catch {}
    }
    if (details.length === 0 && author.seed_posts.length > 0) {
      console.error(`[WARN] 作者 ${author.name} 主页抓取为空，回退种子详情链路`)
      for (const seed of author.seed_posts.slice(0, perAuthorNoteLimit)) {
        await randomDelay(
          Math.max(150, Math.floor(authorNoteDelayMinMs * 0.8)),
          Math.max(350, Math.floor(authorNoteDelayMaxMs * 0.9)),
        )
        let full = null
        try {
          full = await scrapeDetailPage(page, seed, searchUrl)
        } catch (seedErr) {
          if (seedErr instanceof CaptchaDetectedError) throw seedErr
        }
        if (!full) continue
        if (full?.id && seenPostIds.has(full.id)) continue
        if (full?.id) seenPostIds.add(full.id)
        fullPosts.push({ ...full, viral_score: scorePost(full) })
      }
      continue
    }
    for (const detail of details) {
      if (detail?.id && seenPostIds.has(detail.id)) continue
      if (detail?.id) seenPostIds.add(detail.id)
      fullPosts.push(detail)
    }
    // 每完成一个作者就更新部分结果快照，供 SIGTERM 时输出
    _partialFullPosts = fullPosts.slice()
    _partialAuthors = buildAuthorsFromPosts(fullPosts, selectedAuthors, authorLimit)
    console.error(`[INFO] 作者快照已更新，当前 ${_partialAuthors.length} 个作者，${_partialFullPosts.length} 篇笔记`)
  }
  console.error(`[INFO] 阶段三完成：获取 ${fullPosts.length} 笔记详情（${selectedAuthors.length} 位作者）`)

  // 阶段四：按作者聚合（复用 buildAuthorsFromPosts）
  const authors = buildAuthorsFromPosts(fullPosts, selectedAuthors, authorLimit)
  _partialAuthors = authors.slice()
  console.error(`[INFO] 阶段四完成：聚合到 ${authors.length} 个作者（目标每作者 ${perAuthorNoteLimit} 篇）`)
  markAccountUsed(session.accountName)

  const result = {
    keyword,
    search_limit: searchLimit,
    per_author_note_limit: perAuthorNoteLimit,
    selection_strategy: selectResult.strategy,
    applied_threshold: selectResult.appliedThreshold,
    authors,
    posts: fullPosts,
  }

  // 输出 JSON 到 stdout（给 Python 读取）
  process.stdout.write(JSON.stringify(result, null, 2))
  console.error(`\n[INFO] ✅ 采集完成！共 ${authors.length} 个爆款作者`)

} catch (err) {
  if (err instanceof CaptchaDetectedError) {
    console.error(`[WARN] ${err.message}`)
    process.stdout.write(JSON.stringify({
      error: 'captcha_detected',
      partial: true,
      keyword,
      message: '检测到安全验证，已立即退出并返回部分结果',
      authors: _partialAuthors,
      posts: _partialFullPosts,
    }))
  } else if (err instanceof LoginRequiredError) {
    console.error(`[ERROR] ${err.message}`)
    process.stdout.write(JSON.stringify({
      error: 'session_expired',
      message: `${err.message}。请先运行: node scripts/auth.js login --name <账号名>`,
      authors: [],
      posts: [],
    }))
  } else {
    console.error(`[ERROR] 采集异常: ${err.message}`)
    // 输出已采集到的部分数据，不因一个作者失败丢弃所有结果
    const partialAuthors = typeof authors !== 'undefined' ? authors : []
    const partialPosts = typeof fullPosts !== 'undefined' ? fullPosts : []
    console.error(`[WARN] 输出部分结果：${partialAuthors.length} 个作者，${partialPosts.length} 篇笔记`)
    process.stdout.write(JSON.stringify({
      error: err.message,
      partial: true,
      keyword,
      authors: partialAuthors,
      posts: partialPosts,
    }))
  }
} finally {
  if (httpLogPath) {
    const records = httpRecords
    const summary = {
      total_requests: records.length,
      signed_requests: records.filter(r => r.headers['x-s'] || r.headers['x-s-common']).length,
      unique_hosts: [...new Set(records.map(r => {
        try { return new URL(r.url).host } catch { return '' }
      }).filter(Boolean))],
      unique_paths: [...new Set(records.map(r => {
        try { return new URL(r.url).pathname } catch { return '' }
      }).filter(Boolean))].slice(0, 50),
      unique_user_agents: [...new Set(records.map(r => r.headers['user-agent']).filter(Boolean))],
      unique_sec_ch_ua: [...new Set(records.map(r => r.headers['sec-ch-ua']).filter(Boolean))],
    }
    const payload = {
      meta: {
        mode: 'agent',
        keyword,
        search_limit: searchLimit,
        viral_threshold: viralThreshold,
        author_limit: authorLimit,
        per_author_note_limit: perAuthorNoteLimit,
        recorded_at: new Date().toISOString(),
      },
      summary,
      requests: records,
    }
    mkdirSync(dirname(httpLogPath), { recursive: true })
    writeFileSync(httpLogPath, JSON.stringify(payload, null, 2))
    console.error(`[INFO] 已写入 HTTP 全量日志: ${httpLogPath}`)
  }
  if (keepOpen) {
    console.error('[INFO] --keep-open 已启用：浏览器将保持打开，按 Ctrl+C 结束')
    await new Promise(() => {})
  }
  // 默认保持浏览器打开 5 秒让用户看到结果，再关闭
  console.error('[INFO] 浏览器将在 5 秒后关闭...')
  await new Promise(r => setTimeout(r, 5000))
  await browser.close()
}
