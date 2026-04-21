import { chromium } from 'playwright'
import { getValidSession, getSessionError, markAccountUsed, cookieStringToStorageState } from './auth.js'
import { scorePost, selectPostsForDetail } from './viral-filter.js'
import {
  NAVIGATOR_INIT_SCRIPT,
  launchOptions,
  launchOptionsFallback,
  contextOptions,
  warmupSession,
} from './browser-fingerprint.js'

const SEARCH_URL = 'https://www.xiaohongshu.com/search_result?keyword='
const SEARCH_NOTES_API_RE = /\/api\/sns\/web\/v1\/search\/notes\b/
const LOGIN_SELECTORS = [
  '.login-container',
  '.qrcode-img',
  '[class*="LoginModal"]',
  '[class*="login-modal"]',
  '.reds-login',
  '#login-page',
]

class LoginRequiredError extends Error {
  constructor(message = '检测到未登录状态，需要重新扫码登录') {
    super(message)
    this.name = 'LoginRequiredError'
  }
}

// 将页面相对时间转为 YYYY-MM-DD，基准为当天
// "X天前"精确转换，"X个月前"按 30天/月估算，"刚刚"/"X小时前"返回今天
export function parseRelativeDate(text) {
  if (!text) return new Date().toISOString().slice(0, 10)
  const now = Date.now()
  const m = text.match(/(\d+)\s*(天|周|个月|月|年)前/)
  if (!m) return new Date(now).toISOString().slice(0, 10)
  const [, num, unit] = m
  const n = parseInt(num)
  const msMap = { 天: 86400000, 周: 604800000, 个月: 2592000000, 月: 2592000000, 年: 31536000000 }
  return new Date(now - n * (msMap[unit] || 86400000)).toISOString().slice(0, 10)
}

function randomDelay(minMs = 2000, maxMs = 5000) {
  return new Promise(r => setTimeout(r, minMs + Math.random() * (maxMs - minMs)))
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
    console.error(`[ERROR] needsLogin 异常: ${msg}`)
  }
  return false
}

async function needsCaptcha(page) {
  try {
    const url = page.url()
    console.error(`[DEBUG] 当前 URL: ${url}`)
    
    // 检查 URL 是否包含验证相关路径
    if (/verify|captcha|security-check/.test(url)) {
      console.error(`[WARN] URL 包含验证特征: ${url.slice(0, 80)}`)
      return true
    }
    
    const found = await page.$('[class*="captcha"],[class*="verify"],[class*="security"],[id*="captcha"],[id*="verify"]')
    if (found) {
      console.error(`[WARN] 页面中找到验证码元素`)
      return true
    }
    
    const text = await page.evaluate(() => document.body?.innerText || '')
    if (text.includes('安全验证') && text.includes('刷新')) {
      console.error(`[WARN] 页面包含'安全验证'文本`)
      return true
    }
  } catch (e) {
    console.error(`[ERROR] needsCaptcha 异常: ${e.message}`)
  }
  return false
}

async function waitForCaptcha(page) {
  console.error('⚠️  检测到安全验证，等待 120 秒...')
  for (let remaining = 120; remaining > 0; remaining -= 10) {
    console.error(`⏳ 还剩 ${remaining} 秒...`)
    await page.waitForTimeout(10000)
    if (await needsLogin(page)) {
      throw new LoginRequiredError()
    }
    if (!await needsCaptcha(page)) {
      console.error('✅ 安全验证已解除')
      return
    }
  }
  console.error('⚠️  120 秒已过，继续执行')
}

async function scrapeListPage(page, keyword, limit, xsecTokenMap) {
  const url = SEARCH_URL + encodeURIComponent(keyword)
  console.error(`[INFO] 打开搜索页面: ${url}`)
  
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 })
  console.error(`[INFO] 页面加载完成，URL: ${page.url()}`)
  if (await needsLogin(page)) {
    throw new LoginRequiredError()
  }
  
  await page.waitForTimeout(2000)
  
  // 检查验证码
  let needsWait = await needsCaptcha(page)
  if (needsWait) {
    console.error(`[WARN] 第一次检查发现验证码，执行等待流程...`)
    await waitForCaptcha(page)
  }
  
  await page.waitForTimeout(3000)
  if (await needsLogin(page)) {
    throw new LoginRequiredError()
  }
  
  console.error(`[DEBUG] 开始采集列表，当前 URL: ${page.url()}`)

  const posts = []
  let attempts = 0
  while (posts.length < limit && attempts < 10) {
    if (await needsLogin(page)) {
      throw new LoginRequiredError()
    }
    console.error(`[DEBUG] 采集轮次 ${attempts + 1}/10，当前已获取 ${posts.length}/${limit} 笔记`)
    
    try {
      const items = await page.$$eval(
        '[data-v-a264d] .note-item, .search-feed-item, .note-list-item',
        els => {
          console.log(`[DEBUG] 找到 ${els.length} 个列表项`)
          return els.map(el => {
            const a = el.querySelector('a[href*="/explore/"]') || el.querySelector('a')
            const href = a?.href || ''
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
            return {
              id: el.getAttribute('data-id') || parsed?.pathname?.match(/\/explore\/([a-z0-9]+)/)?.[1],
              title: el.querySelector('.title, .note-title, h3')?.textContent?.trim(),
              likes: parseInt(el.querySelector('.like-count, .count')?.textContent?.replace(/[^0-9]/g, '')) || 0,
              saves: parseInt(el.querySelector('.collect-count, .save-count')?.textContent?.replace(/[^0-9]/g, '')) || 0,
              comments: parseInt(el.querySelector('.comment-count')?.textContent?.replace(/[^0-9]/g, '')) || 0,
              url: href,
              xsec_token: xsecToken,
              xsec_source: xsecSource,
              cover_image_url: el.querySelector('img')?.src,
            }
          })
        }
      )
      
      console.error(`[INFO] 本轮获取 ${items.length} 个项目`)
      
      for (const item of items) {
        if (item.id && !posts.find(p => p.id === item.id)) {
          if (item.xsec_token) {
            xsecTokenMap.set(item.id, {
              token: item.xsec_token,
              source: item.xsec_source || 'pc_search',
            })
          }
          posts.push(item)
        }
      }
    } catch (err) {
      console.error(`[ERROR] 采集失败: ${err.message}`)
    }
    
    if (posts.length < limit) {
      console.error(`[DEBUG] 滚动页面...`)
      await page.evaluate(() => window.scrollBy(0, 800))
      await page.waitForTimeout(1500)
    }
    attempts++
  }
  
  console.error(`[INFO] 列表采集完成，共获取 ${posts.length} 笔记`)
  return posts.slice(0, limit)
}

async function scrapeDetailPage(page, post, searchUrl, xsecTokenMap) {
  if (!post.id && !post.url) return post
  const tokenInfo = (
    (post.id && xsecTokenMap.get(post.id)) ||
    (post.xsec_token ? { token: post.xsec_token, source: post.xsec_source || 'pc_search' } : null)
  )
  const fallbackUrl = post.url || `https://www.xiaohongshu.com/explore/${post.id}`
  const targetUrl = tokenInfo
    ? `https://www.xiaohongshu.com/explore/${post.id}` +
      `?xsec_token=${encodeURIComponent(tokenInfo.token)}` +
      `&xsec_source=${encodeURIComponent(tokenInfo.source || 'pc_search')}`
    : fallbackUrl
  if (!tokenInfo && post.id) {
    console.error(`[WARN] ${post.id} 无 xsec_token，使用裸 URL（降级）`)
  }
  await page.goto(targetUrl, {
    waitUntil: 'domcontentloaded',
    timeout: 30000,
    referer: searchUrl,
  })
  const landedUrl = page.url()
  if (!/\/explore\/[a-z0-9]+/.test(landedUrl)) {
    return { ...post, detail_blocked_reason: `redirected:${landedUrl}` }
  }
  if (await needsLogin(page)) {
    throw new LoginRequiredError()
  }
  if (await needsCaptcha(page)) await waitForCaptcha(page)
  await page.waitForTimeout(2000)

  const detail = await page.evaluate(() => {
    const content = document.querySelector('#detail-desc, .note-text, .desc')?.innerText?.trim()
    const dateText = document.querySelector('.date, .publish-date, time')?.textContent?.trim()
    const authorEl = document.querySelector('.username, .author-name, .user-name')
    const author = authorEl?.textContent?.trim()
    const authorLink = document.querySelector('a[href*="/user/profile/"]')
    const author_id = authorLink?.href?.match(/\/user\/profile\/([a-f0-9]+)/)?.[1] || ''
    const coverImg = document.querySelector('.slide-image img, .note-image img')?.src
    return { content, dateText, author, author_id, coverImg }
  })

  return {
    ...post,
    content: detail.content || '',
    published_at: parseRelativeDate(detail.dateText),
    author: detail.author || '',
    author_id: detail.author_id || '',
    cover_image_url: detail.coverImg || post.cover_image_url || '',
  }
}

export async function explore({ keyword, searchLimit = 20, viralThreshold = 60, authorLimit = 20, accountName = '' }) {
  console.error(`[INFO] 开始采集: keyword=${keyword}, searchLimit=${searchLimit}, viralThreshold=${viralThreshold}, authorLimit=${authorLimit}`)
  
  const session = getValidSession(accountName)
  if (!session) {
    console.error(accountName ? `[ERROR] 指定账号不可用: ${accountName}` : `[ERROR] 无可用 session`)
    return getSessionError()
  }
  
  console.error(`[INFO] 使用账号: ${session.accountName}`)

  // 支持两种 session：storageState 文件路径 或 env cookie 字符串（回退）
  const storageStateOpt = session.storagePath
    ? session.storagePath
    : cookieStringToStorageState(session.cookieStr)

  const headless = process.env.HEADLESS !== 'false'
  let browser
  try {
    browser = await chromium.launch(launchOptions(headless))
  } catch {
    browser = await chromium.launch(launchOptionsFallback(headless))
  }

  try {
    const context = await browser.newContext(contextOptions(storageStateOpt))
    await context.addInitScript(NAVIGATOR_INIT_SCRIPT)

    const page = await context.newPage()
    const xsecTokenMap = new Map()
    page.on('response', async (resp) => {
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
    if (await needsLogin(page)) {
      throw new LoginRequiredError()
    }

    // 阶段一：列表页抓取，为确保能凑够 authorLimit 个不同作者，多抓一些笔记
    const searchUrl = SEARCH_URL + encodeURIComponent(keyword)
    const internalLimit = Math.max(searchLimit, authorLimit * 3)
    console.error(`[INFO] 阶段一：抓取列表页，目标 ${internalLimit} 笔记`)
    
    const listPosts = await scrapeListPage(page, keyword, internalLimit, xsecTokenMap)
    console.error(`[INFO] 阶段一完成：获取 ${listPosts.length} 笔记`)

    // 阶段二：半年内样本筛选（按点赞/收藏高值排序）
    const selectResult = selectPostsForDetail(listPosts, viralThreshold, {
      limit: Math.min(20, internalLimit),
      minTarget: Math.min(10, authorLimit),
    })
    const pickedPosts = selectResult.picked
    console.error(
      `[INFO] 阶段二完成：详情候选 ${pickedPosts.length} 条，策略=${selectResult.strategy}，` +
      `recent=${selectResult.recentCount ?? selectResult.strictCount}，threshold=${selectResult.appliedThreshold}`
    )
    if (selectResult.strategy === 'recent_peak_insufficient') {
      console.error('[WARN] 半年内高赞/高藏样本不足，结果可能偏少')
    }

    // 阶段三：串行详情抓取（随机延迟防反爬），并用详情数据更新 viral_score
    console.error(`[INFO] 阶段三：抓取详情页...`)
    const fullPosts = []
    for (const post of pickedPosts) {
      await randomDelay()
      const full = await scrapeDetailPage(page, post, searchUrl, xsecTokenMap)
      fullPosts.push({ ...full, viral_score: scorePost(full) })
    }
    console.error(`[INFO] 阶段三完成：获取 ${fullPosts.length} 笔记详情`)

    // 阶段四：按作者聚合（优先 author_id，回退作者名），避免同作者重复和 ID 缺失导致的丢失
    const fullPostByUrl = {}
    for (const p of fullPosts) {
      if (p.url) fullPostByUrl[p.url] = p
    }

    const cleanAuthor = (name = '') => (
      name
        .replace(/\d+小时前$/, '')
        .replace(/\d+天前$/, '')
        .replace(/\d+个月前$/, '')
        .replace(/\d{2}-\d{2}$/, '')
        .replace(/\d{4}-\d{2}-\d{2}$/, '')
        .trim()
    )

    const authorMap = {}
    for (const post of listPosts) {
      const enriched = fullPostByUrl[post.url] || post
      const authorName = cleanAuthor(enriched.author || post.author || '')
      const authorId = enriched.author_id || ''
      const key = authorId ? `id:${authorId}` : (authorName ? `name:${authorName}` : '')
      if (!key) continue
      if (!authorMap[key]) {
        authorMap[key] = {
          author: authorName || enriched.author || post.author || '',
          author_id: authorId,
          posts: [],
        }
      }
      authorMap[key].posts.push({
        ...enriched,
        viral_score: typeof enriched.viral_score === 'number' ? enriched.viral_score : scorePost(enriched),
      })
    }

    const authors = Object.values(authorMap)
      .map(a => {
        const sorted = [...a.posts].sort((x, y) => y.viral_score - x.viral_score)
        return {
          author: a.author,
          author_id: a.author_id,
          viral_count: a.posts.length,
          max_viral_score: sorted[0]?.viral_score || 0,
          top_post_title: sorted[0]?.title || '',
          top_post_url: sorted[0]?.url || '',
        }
      })
      .sort((a, b) => b.max_viral_score - a.max_viral_score)
      .slice(0, authorLimit)

    console.error(`[INFO] 阶段四完成：聚合到 ${authors.length} 个作者`)

    markAccountUsed(session.accountName)
    
    const result = {
      keyword,
      search_limit: searchLimit,
      author_limit: authorLimit,
      total_fetched: listPosts.length,
      viral_passed: fullPosts.length,
      selection_strategy: selectResult.strategy,
      applied_threshold: selectResult.appliedThreshold,
      authors,
      posts: fullPosts
    }
    
    console.error(`[INFO] 采集完成！`)
    return result
  } catch (err) {
    if (err instanceof LoginRequiredError) {
      const sessionError = getSessionError()
      return {
        ...sessionError,
        error: 'session_expired',
        message: `${err.message}。${sessionError.message}`,
      }
    }
    console.error(`[ERROR] 采集异常: ${err.message}`)
    return { error: 'network_error', message: err.message }
  } finally {
    await browser.close()
  }
}
