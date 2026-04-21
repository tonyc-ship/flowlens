import { chromium } from 'playwright'
import { getValidSession, getSessionError, markAccountUsed } from './auth.js'
import {
  NAVIGATOR_INIT_SCRIPT,
  launchOptions,
  launchOptionsFallback,
  contextOptions,
  warmupSession,
} from './browser-fingerprint.js'

const SEARCH_URL = 'https://www.xiaohongshu.com/search_result?keyword='

/**
 * 解析相对时间到 YYYY-MM-DD 格式
 */
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

/**
 * 爆款评分计算公式
 * 参考 analyze_accounts.py 的互动率逻辑
 * 
 * 评分基于两个维度：
 * 1. 绝对互动数（点赞/收藏/评论）- 反映笔记本身的受欢迎程度
 * 2. 相对互动率（互动/粉丝） - 反映作者的转化效率
 */
function calculateViralScore(likedCount, collectedCount, commentCount, followersCount = 10000) {
  if (followersCount === 0) followersCount = 10000
  
  // 维度 1: 绝对互动数评分（最多 60 分）
  // 爆款阈值：点赞 > 1000，收藏 > 200，评论 > 100
  const absoluteScore = Math.min(
    (likedCount / 1000) * 20 +
    (collectedCount / 200) * 20 +
    (commentCount / 100) * 20,
    60
  )
  
  // 维度 2: 相对互动率评分（最多 40 分）
  // 高互动率 = 高粘性，反映真实用户兴趣
  const interactionRate = ((likedCount + collectedCount * 0.5) / Math.max(followersCount, 1)) * 100
  const relativeScore = Math.min(
    interactionRate > 10 ? 40 :
    interactionRate > 5 ? 30 :
    interactionRate > 1 ? 20 :
    interactionRate > 0.1 ? 10 : 0
  )
  
  // 评论权重加分（反映高粘性）
  const commentBonus = likedCount > 0 ? 
    Math.min((commentCount / likedCount) * 100 * 0.5, 10) : 0
  
  // 最终分数 = 绝对分 + 相对分 + 评论加分
  const viralScore = absoluteScore + relativeScore + commentBonus
  
  return Math.min(Math.max(Math.round(viralScore * 10) / 10, 0), 100)
}

/**
 * 检测当前页面是否需要登录
 */
async function needsLogin(page) {
  try {
    const url = page.url()
    if (/login|sign-in|signin/.test(url)) {
      console.error(`[WARN] URL 指示需要登录: ${url}`)
      return true
    }
    
    // 检查页面中是否有登录元素
    const loginEl = await page.$('.login-container, .qrcode-img, [class*="LoginModal"]')
    if (loginEl) {
      console.error(`[WARN] 页面中检测到登录元素`)
      return true
    }
  } catch (e) {
    console.error(`[ERROR] needsLogin 异常: ${e.message}`)
  }
  return false
}

/**
 * 检测当前页面是否触发了安全验证
 */
async function needsCaptcha(page) {
  try {
    const url = page.url()
    if (/verify|captcha|security-check|website-login|IP存在风险/.test(url)) {
      console.error(`[WARN] URL 指示需要验证: ${url}`)
      return true
    }
    
    const text = await page.evaluate(() => document.body?.innerText || '')
    if (text.includes('IP存在风险') || text.includes('安全限制') || text.includes('安全验证')) {
      console.error(`[WARN] 页面显示安全验证`)
      return true
    }
  } catch (e) {
    console.error(`[ERROR] needsCaptcha 异常: ${e.message}`)
  }
  return false
}

/**
 * 等待安全验证解除（120秒超时）
 */
async function waitForCaptcha(page) {
  console.error('⚠️  检测到安全验证，等待 120 秒...')
  for (let remaining = 120; remaining > 0; remaining -= 10) {
    console.error(`⏳ 还剩 ${remaining} 秒...`)
    await page.waitForTimeout(10000)
    if (!await needsCaptcha(page)) {
      console.error('✅ 安全验证已解除')
      return true
    }
  }
  console.error('⚠️  120 秒已过，验证可能未解除')
  return false
}

/**
 * 通过 API 拦截方式搜索关键词并获取笔记数据
 * 参考 analyze_accounts.py 中的 XhsFetcher.search_user_id() 的 API 拦截方式
 */
async function searchKeywordByAPI(page, keyword, limit = 20) {
  // 收集 API 响应 Promise，导航结束后统一 await
  const responsePromises = []

  function onResponse(response) {
    const url = response.url()
    const status = response.status()
    if ((url.includes('/api/sns/web/v1/search/notes') || url.includes('/search/notes')) && status >= 200 && status < 300) {
      console.error(`[DEBUG] 捕获搜索 API: ${url.slice(-80)} status=${status}`)
      responsePromises.push(response.json().catch(() => null))
    }
  }

  page.on('response', onResponse)

  try {
    const searchUrl = SEARCH_URL + encodeURIComponent(keyword)
    console.error(`[INFO] 导航到搜索页: ${searchUrl}`)

    await page.goto(searchUrl, { waitUntil: 'domcontentloaded', timeout: 30000 })
    console.error(`[INFO] 页面加载完成，URL: ${page.url()}`)

    // 有了第一个搜索 API 响应就继续，最多等 8s（替代 networkidle 15s）
    await Promise.race([
      new Promise(resolve => {
        const check = setInterval(() => {
          if (responsePromises.length > 0) { clearInterval(check); resolve() }
        }, 200)
        setTimeout(() => { clearInterval(check); resolve() }, 8000)
      })
    ])
    // 再等 1s 收尾可能的追加响应
    await page.waitForTimeout(1000)

    // 检测验证码
    if (await needsCaptcha(page)) {
      console.error(`[WARN] 搜索页触发验证码`)
      await waitForCaptcha(page)
    }

  } finally {
    page.removeListener('response', onResponse)
  }

  // 等待所有拦截到的 API 响应完成
  const responses = await Promise.all(responsePromises)
  console.error(`[DEBUG] 拦截到 ${responses.length} 个 API 响应`)

  const results = []
  for (const data of responses) {
    if (!data) continue
    const items = data?.data?.items || data?.data?.notes || []
    console.error(`[DEBUG] API 响应包含 ${items.length} 条笔记`)
    items.forEach(item => {
      try {
        // note ID 在顶层 item.id，详情在 item.note_card
        const noteId = item.id || item.note_card?.note_id || ''
        const note = item.note_card || item
        const interactInfo = note.interact_info || {}
        if (noteId && note.display_title !== undefined) {
          results.push({
            note_id: noteId,
            xsec_token: item.xsec_token || '',
            title: note.display_title || note.title || '',
            desc: note.desc || '',
            liked_count: parseInt(interactInfo.liked_count || 0),
            collected_count: parseInt(interactInfo.collected_count || 0),
            comment_count: parseInt(interactInfo.comment_count || 0),
            image_count: (note.image_list && note.image_list.length) || 1,
            user: note.user || {},
            post_date: note.create_time || interactInfo.create_time || new Date().toISOString()
          })
        }
      } catch (e) { /* 忽略单项错误 */ }
    })
  }

  console.error(`[INFO] 总获取笔记: ${results.length}`)
  return results.slice(0, limit)
}

/**
 * 从搜索关键词提取检索 token。
 * 英文：按单词分割（2字母以上）
 * 中文：对每段连续汉字做 2-char bigram 滑动窗口，
 *       同时保留原始词组用于完整命中加分
 */
function extractKeywordTokens(keyword) {
  const tokens = []
  // 英文单词（2字母以上）
  const enMatches = keyword.match(/[a-zA-Z]{2,}/g) || []
  enMatches.forEach(w => tokens.push(w.toLowerCase()))
  // 中文：对每段连续汉字生成 bigram（2字滑动窗口）
  const zhChunks = keyword.match(/[\u4e00-\u9fa5]+/g) || []
  for (const chunk of zhChunks) {
    if (chunk.length >= 2) {
      // 完整词组也加入（用于完整命中判断）
      tokens.push(chunk)
      // 2-char bigrams
      for (let i = 0; i < chunk.length - 1; i++) {
        tokens.push(chunk.slice(i, i + 2))
      }
    } else {
      tokens.push(chunk)  // 单字直接加
    }
  }
  return [...new Set(tokens)]
}

/**
 * 计算笔记与关键词的相关性得分（0~1）
 *
 * 评分逻辑：
 * 1. 完整关键词命中 → 1.0
 * 2. 否则用 bigram 命中率计算：
 *    - 英文 token + 中文 bigram 各占比例
 *    - 要求多个语义单元至少有一半命中
 */
function relevanceScore(note, keyword, tokens) {
  const text = ((note.title || '') + ' ' + (note.desc || '')).toLowerCase()
  // 完整关键词直接命中
  if (text.includes(keyword.toLowerCase())) return 1.0
  // 提取有效的评分 token：英文单词 + 2-char bigram（排除完整词组避免重复计权）
  const scoringTokens = tokens.filter(t => {
    if (t.match(/^[a-z]+$/)) return t.length >= 2  // 英文单词
    return t.length === 2  // 只用中文 bigram 计分，不用完整词组
  })
  if (scoringTokens.length === 0) return 1.0
  const hits = scoringTokens.filter(t => text.includes(t)).length
  return hits / scoringTokens.length
}

/**
 * 过滤不相关笔记
 * minScore 依 bigram 数量动态调整：token 越多阈值越低，避免误杀
 */
function filterByRelevance(notes, keyword) {
  const tokens = extractKeywordTokens(keyword)
  const scoringTokens = tokens.filter(t => {
    if (t.match(/^[a-z]+$/)) return t.length >= 2
    return t.length === 2
  })
  // 关键词太短无法有效拆分，跳过过滤
  if (scoringTokens.length === 0) return notes

  // bigram 越多容忍度越高（长词组不可能所有 bigram 全命中）
  // 2 tokens → 0.5，3~4 → 0.4，5+ → 0.33
  const minScore = scoringTokens.length <= 2 ? 0.5
    : scoringTokens.length <= 4 ? 0.4
    : 0.33

  const filtered = notes.filter(note => {
    const score = relevanceScore(note, keyword, tokens)
    if (score < minScore) {
      console.error(`[FILTER] 低相关度(${score.toFixed(2)}<${minScore}) 跳过: 「${(note.title || '').slice(0, 20)}」`)
      return false
    }
    return true
  })
  console.error(`[FILTER] 相关性过滤: ${notes.length} → ${filtered.length} 条 (keyword="${keyword}", minScore=${minScore})`)
  return filtered
}

/**
 * 聚合笔记到作者维度，计算爆款指标
 */
function aggregateByAuthor(notes, viralThreshold = 60) {
  const authorMap = {}
  
  // 先按作者分组
  notes.forEach(note => {
    const userId = note.user?.user_id || note.author_id || 'unknown'
    const nickname = note.user?.nickname || note.author_name || 'unknown'
    
    if (!authorMap[userId]) {
      authorMap[userId] = {
        user_id: userId,
        nickname: nickname,
        avatar: note.user?.avatar || '',
        desc: note.user?.desc || '',
        followers: note.user?.follower_count || 0,
        following: note.user?.following_count || 0,
        notes_count: note.user?.note_count || 0,
        viral_notes: [],
        all_notes: []
      }
    }
    
    authorMap[userId].all_notes.push(note)
  })
  
  // 计算每个作者的爆款指标
  const authors = Object.values(authorMap).map(author => {
    // 计算每条笔记的爆款分数
    const viralScores = author.all_notes.map(note => 
      calculateViralScore(
        note.liked_count,
        note.collected_count,
        note.comment_count,
        author.followers || 10000
      )
    )
    
    // 过滤出爆款笔记（>= 阈值）
    author.viral_notes = author.all_notes.filter((note, idx) => viralScores[idx] >= viralThreshold)
    
    // 计算平均爆款分
    const avgViralScore = viralScores.length > 0 
      ? viralScores.reduce((a, b) => a + b, 0) / viralScores.length 
      : 0
    
    return {
      ...author,
      viral_count: author.viral_notes.length,
      max_viral_score: Math.max(...viralScores, 0),
      avg_viral_score: Math.round(avgViralScore * 10) / 10,
      post_count: author.all_notes.length
    }
  })
  
  // 按爆款数排序
  return authors.sort((a, b) => b.viral_count - a.viral_count)
}

/**
 * 主 explore 函数
 */
export async function explore({ keyword, searchLimit = 20, viralThreshold = 60, authorLimit = 20 }) {
  console.error(`[INFO] 开始采集: keyword=${keyword}, searchLimit=${searchLimit}, viralThreshold=${viralThreshold}, authorLimit=${authorLimit}`)
  
  // 获取有效的 session
  const session = await getValidSession()
  if (!session) {
    console.error(`[ERROR] 无法获取有效账号`)
    return {
      keyword,
      search_limit: searchLimit,
      author_limit: authorLimit,
      total_fetched: 0,
      viral_passed: 0,
      authors: [],
      posts: [],
      data_source: 'error'
    }
  }
  
  console.error(`[INFO] 使用账号: ${session.accountName}`)
  
  // 启动浏览器（带指纹伪装配置）
  const headless = process.env.HEADLESS !== 'false'
  let browser
  try {
    browser = await chromium.launch(launchOptions(headless))
  } catch {
    browser = await chromium.launch(launchOptionsFallback(headless))
  }
  const context = await browser.newContext(contextOptions(session.storagePath))
  // 注入 navigator / WebGL / Canvas 指纹伪装（每个新页面都会执行）
  await context.addInitScript(NAVIGATOR_INIT_SCRIPT)
  const page = await context.newPage()
  
  let posts = []
  let authors = []
  
  try {
    // 阶段 1: 预热 Session + 验证登录状态
    // warmupSession 访问首页→探索页，触发 XHS JS 初始化 Cookie 链（acw_tc/loadts/ets）
    // 并让浏览器完成 x-s-common 参数的首次生成，行为更接近真人打开浏览器
    console.error(`[INFO] 阶段一: 预热 Session + 验证登录状态...`)
    const warmupHits = await warmupSession(page)
    console.error(`[INFO] 预热命中 API: ${warmupHits.join(', ') || '(none)'}`)

    if (await needsLogin(page)) {
      console.error(`[ERROR] 需要登录，请先运行账号扫码登录`)
      throw new Error('需要登录')
    }
    console.error(`[INFO] ✅ 已登录`)
    
    // 阶段 2: 搜索关键词并通过 API 拦截获取数据
    console.error(`[INFO] 阶段二: 搜索关键词「${keyword}」...`)
    posts = await searchKeywordByAPI(page, keyword, searchLimit)
    console.error(`[INFO] 获取笔记数: ${posts.length}`)
    
    if (posts.length === 0) {
      console.error(`[WARN] 未获取到任何笔记`)
      return {
        keyword,
        search_limit: searchLimit,
        author_limit: authorLimit,
        total_fetched: 0,
        viral_passed: 0,
        authors: [],
        posts: [],
        data_source: 'empty'
      }
    }
    
    // 阶段 3: 相关性过滤 + 爆款过滤和作者聚合
    console.error(`[INFO] 阶段三: 相关性过滤 + 爆款过滤（阈值=${viralThreshold}）...`)
    const relevantPosts = filterByRelevance(posts, keyword)
    authors = aggregateByAuthor(relevantPosts, viralThreshold)
    
    const viralPassed = authors.reduce((sum, a) => sum + a.viral_count, 0)
    console.error(`[INFO] 爆款笔记数: ${viralPassed}`)

    // 返回结果
    return {
      keyword,
      search_limit: searchLimit,
      author_limit: authorLimit,
      total_fetched: relevantPosts.length,
      raw_fetched: posts.length,
      viral_passed: viralPassed,
      authors: authors.slice(0, authorLimit),
      posts: relevantPosts,
      data_source: 'real_search'
    }
    
  } catch (error) {
    console.error(`[ERROR] explore 流程异常: ${error.message}`)
    console.error(error.stack)
    
    // 如果是验证或登录错误，返回空结果但标记数据源
    return {
      keyword,
      search_limit: searchLimit,
      author_limit: authorLimit,
      total_fetched: posts.length,
      viral_passed: authors.reduce((sum, a) => sum + a.viral_count, 0),
      authors: authors,
      posts: posts,
      data_source: 'error',
      error: error.message
    }
    
  } finally {
    await browser.close()
  }
}

// 如果直接运行此文件
if (import.meta.url === `file://${process.argv[1]}`) {
  (async () => {
    const result = await explore({
      keyword: '海外求职',
      searchLimit: 20,
      viralThreshold: 60,
      authorLimit: 5
    })
    console.log(JSON.stringify(result, null, 2))
  })()
}
