import { strict as assert } from 'assert'
import { test } from 'node:test'
import { passesHardThreshold, scorePost, filterPosts, selectPostsForDetail } from '../scripts/viral-filter.js'

test('passesHardThreshold: rejects low likes', () => {
  const post = { likes: 100, saves: 50, comments: 30 }
  assert.equal(passesHardThreshold(post), false)
})

test('passesHardThreshold: rejects low save rate', () => {
  const post = { likes: 1000, saves: 50, comments: 30 }
  assert.equal(passesHardThreshold(post), false)
})

test('passesHardThreshold: rejects low comments', () => {
  const post = { likes: 1000, saves: 200, comments: 5 }
  assert.equal(passesHardThreshold(post), false)
})

test('passesHardThreshold: passes good post', () => {
  const post = { likes: 1000, saves: 200, comments: 30 }
  assert.equal(passesHardThreshold(post), true)
})

test('scorePost: returns 0-100 range', () => {
  const post = {
    likes: 1200, saves: 340, comments: 89,
    title: '双非本科3个月拿下字节Offer？避坑必看',
    published_at: new Date(Date.now() - 15 * 86400000).toISOString().slice(0, 10)
  }
  const score = scorePost(post)
  assert.ok(score >= 0 && score <= 100, `score ${score} out of range`)
})

test('scorePost: recent post scores higher on recency', () => {
  const base = { likes: 1000, saves: 200, comments: 30, title: '测试' }
  const recent = { ...base, published_at: new Date(Date.now() - 10 * 86400000).toISOString().slice(0, 10) }
  const old = { ...base, published_at: new Date(Date.now() - 200 * 86400000).toISOString().slice(0, 10) }
  assert.ok(scorePost(recent) > scorePost(old))
})

test('scorePost: title with number scores higher', () => {
  const base = { likes: 1000, saves: 200, comments: 30, published_at: '2026-03-01' }
  const withNum = { ...base, title: '3个方法帮你搞定面试' }
  const withoutNum = { ...base, title: '帮你搞定面试的方法' }
  assert.ok(scorePost(withNum) > scorePost(withoutNum))
})

test('filterPosts: returns empty array when no posts pass', () => {
  const posts = [{ likes: 10, saves: 1, comments: 1, title: '测试', published_at: '2026-03-01' }]
  const result = filterPosts(posts, 60)
  assert.deepEqual(result, [])
})

test('filterPosts: attaches viral_score to passing posts', () => {
  const posts = [{
    likes: 1200, saves: 340, comments: 89,
    title: '3个方法必看',
    published_at: new Date(Date.now() - 10 * 86400000).toISOString().slice(0, 10)
  }]
  const result = filterPosts(posts, 30)
  assert.equal(result.length, 1)
  assert.ok(typeof result[0].viral_score === 'number')
})

test('selectPostsForDetail: uses recent_peak strategy and sorts by max(likes,saves)', () => {
  const posts = [
    { likes: 600, saves: 3000, comments: 10, title: '收藏很高', published_at: '2026-03-20' },
    { likes: 2800, saves: 900, comments: 20, title: '点赞很高', published_at: '2026-03-18' },
    { likes: 1200, saves: 260, comments: 80, title: '中位样本', published_at: '2026-03-22' },
  ]
  const result = selectPostsForDetail(posts, 40, { limit: 5, minTarget: 2 })
  assert.equal(result.strategy, 'recent_peak')
  assert.ok(result.picked.length >= 2)
  assert.equal(result.picked[0].title, '收藏很高')
})

test('selectPostsForDetail: only keeps posts within last 6 months', () => {
  const posts = [
    { likes: 400, saves: 120, comments: 10, title: '近半年样本', published_at: new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10) },
    { likes: 8000, saves: 2000, comments: 50, title: '超过半年应过滤', published_at: new Date(Date.now() - 220 * 86400000).toISOString().slice(0, 10) },
  ]
  const result = selectPostsForDetail(posts, 60, { limit: 2, minTarget: 2 })
  assert.equal(result.strategy, 'recent_peak_insufficient')
  assert.equal(result.picked.length, 1)
  assert.equal(result.picked[0].title, '近半年样本')
})

test('scorePost: handles zero likes without Infinity', () => {
  const score = scorePost({
    likes: 0,
    saves: 12,
    comments: 5,
    title: '必看？',
    published_at: '2026-03-01',
  })
  assert.ok(Number.isFinite(score))
  assert.ok(score >= 0 && score <= 100)
})

test('isWithinRecentDays via selectPostsForDetail: post with only `date` field is recognised as recent', () => {
  const recentDate = new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10)
  const posts = [
    { likes: 800, saves: 200, comments: 25, title: '只有date字段', date: recentDate },
  ]
  const result = selectPostsForDetail(posts, 0, { limit: 5, minTarget: 1 })
  assert.ok(result.recentCount > 0, `近半年应识别到1条，实际 recent=${result.recentCount}（date字段未被读取）`)
  assert.ok(result.picked.length > 0, '应选出至少1条近半年笔记')
})

test('isWithinRecentDays via selectPostsForDetail: mix of date-only and old posts filters correctly', () => {
  const recentDate = new Date(Date.now() - 20 * 86400000).toISOString().slice(0, 10)
  const oldDate = new Date(Date.now() - 300 * 86400000).toISOString().slice(0, 10)
  const posts = [
    { likes: 1000, saves: 300, comments: 30, title: '近期', date: recentDate },
    { likes: 9999, saves: 3000, comments: 200, title: '超半年', date: oldDate },
  ]
  const result = selectPostsForDetail(posts, 0, { limit: 5, minTarget: 1 })
  assert.equal(result.recentCount, 1, '应只识别1条近半年笔记')
  assert.equal(result.picked.length, 1)
  assert.equal(result.picked[0].title, '近期')
})

test('selectPostsForDetail: published_at takes priority over date when both present', () => {
  const recentDate = new Date(Date.now() - 10 * 86400000).toISOString().slice(0, 10)
  const oldDate = new Date(Date.now() - 300 * 86400000).toISOString().slice(0, 10)
  const posts = [
    { likes: 800, saves: 200, comments: 25, title: '以published_at为准', published_at: recentDate, date: oldDate },
  ]
  const result = selectPostsForDetail(posts, 0, { limit: 5, minTarget: 1 })
  assert.equal(result.recentCount, 1)
})

test('scorePost: post with only `date` field gets non-worst recency score', () => {
  const recentDate = new Date(Date.now() - 15 * 86400000).toISOString().slice(0, 10)
  const scoreWithDate = scorePost({ likes: 500, saves: 100, comments: 20, title: '测试', date: recentDate })
  const scoreNoDate = scorePost({ likes: 500, saves: 100, comments: 20, title: '测试' })
  assert.ok(scoreWithDate > scoreNoDate, '有近期 date 字段时得分应高于无时间字段')
})
