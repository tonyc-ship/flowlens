const DEFAULTS = {
  minLikes: parseInt(process.env.VIRAL_MIN_LIKES) || 500,
  minSaveRate: parseFloat(process.env.VIRAL_MIN_SAVE_RATE) || 0.15,
  minComments: parseInt(process.env.VIRAL_MIN_COMMENTS) || 20,
}
const RECENT_DAYS = parseInt(process.env.VIRAL_RECENT_DAYS || '183') || 183

function toNumber(value) {
  const n = Number(value)
  return Number.isFinite(n) ? n : 0
}

function normalizePost(post) {
  return {
    ...post,
    likes: Math.max(0, toNumber(post.likes)),
    saves: Math.max(0, toNumber(post.saves)),
    comments: Math.max(0, toNumber(post.comments)),
  }
}

function parsePublishedAtMs(value) {
  if (!value) return NaN
  const ms = new Date(value).getTime()
  return Number.isFinite(ms) ? ms : NaN
}

function isWithinRecentDays(post, days = RECENT_DAYS) {
  const ts = parsePublishedAtMs(post.published_at ?? post.date)
  if (!Number.isFinite(ts)) return false
  return (Date.now() - ts) <= days * 86400000
}

function peakLikesOrSaves(post) {
  return Math.max(toNumber(post.likes), toNumber(post.saves))
}

export function passesHardThreshold(post, config = {}) {
  const { minLikes, minSaveRate, minComments } = { ...DEFAULTS, ...config }
  const p = normalizePost(post)
  if (p.likes < minLikes) return false
  if ((p.saves / Math.max(p.likes, 1)) < minSaveRate) return false
  if (p.comments < minComments) return false
  return true
}

export function scorePost(post) {
  const p = normalizePost(post)
  const reachScore = Math.min(
    Math.log10(Math.max(1, p.likes + p.saves * 2)) / Math.log10(50000) * 25, 25
  )
  const engagementScore = Math.min((p.comments / Math.max(p.likes, 1)) * 500, 25)

  const title = p.title || ''
  let titleScore = 0
  if (/\d/.test(title)) titleScore += 10
  if (/后悔|千万|一定|必看|避坑/.test(title)) titleScore += 8
  if (/[？?]/.test(title)) titleScore += 7
  titleScore = Math.min(titleScore, 25)

  const publishedAt = new Date(p.published_at ?? p.date ?? '').getTime()
  const daysAgo = Number.isFinite(publishedAt)
    ? Math.floor((Date.now() - publishedAt) / 86400000)
    : 999
  const recencyScore = daysAgo <= 30 ? 25 : daysAgo <= 90 ? 15 : daysAgo <= 180 ? 8 : 3

  return Math.round(reachScore + engagementScore + titleScore + recencyScore)
}

export function rankPosts(posts) {
  return posts
    .map(normalizePost)
    .map(p => ({ ...p, viral_score: scorePost(p) }))
    .sort((a, b) => b.viral_score - a.viral_score)
}

export function filterPosts(posts, threshold = 60, config = {}) {
  return rankPosts(posts)
    .filter(p => passesHardThreshold(p, config))
    .filter(p => p.viral_score >= threshold)
}

export function selectPostsForDetail(posts, threshold = 60, options = {}) {
  const {
    limit = 20,
    minTarget = Math.min(8, limit),
    recentDays = RECENT_DAYS,
  } = options

  const ranked = rankPosts(posts)
  if (ranked.length === 0) {
    return {
      picked: [],
      strategy: 'empty',
      strictCount: 0,
      appliedThreshold: threshold,
    }
  }

  const recent = ranked.filter(p => isWithinRecentDays(p, recentDays))
  const ordered = recent
    .slice()
    .sort((a, b) => {
      const byPeak = peakLikesOrSaves(b) - peakLikesOrSaves(a)
      if (byPeak !== 0) return byPeak
      const byLikes = toNumber(b.likes) - toNumber(a.likes)
      if (byLikes !== 0) return byLikes
      const bySaves = toNumber(b.saves) - toNumber(a.saves)
      if (bySaves !== 0) return bySaves
      return (b.viral_score || 0) - (a.viral_score || 0)
    })

  return {
    picked: ordered.slice(0, limit),
    strategy: ordered.length >= minTarget ? 'recent_peak' : 'recent_peak_insufficient',
    strictCount: recent.length,
    recentCount: recent.length,
    appliedThreshold: threshold,
  }
}
