#!/usr/bin/env node

/**
 * 测试脚本：测试新的 explore_v3.js 中的爆款评分和聚合逻辑
 */

// Mock 爆款评分
function calculateViralScore(likedCount, collectedCount, commentCount, followersCount = 10000) {
  if (followersCount === 0) followersCount = 10000
  
  // 维度 1: 绝对互动数评分（最多 60 分）
  const absoluteScore = Math.min(
    (likedCount / 1000) * 20 +
    (collectedCount / 200) * 20 +
    (commentCount / 100) * 20,
    60
  )
  
  // 维度 2: 相对互动率评分（最多 40 分）
  const interactionRate = ((likedCount + collectedCount * 0.5) / Math.max(followersCount, 1)) * 100
  const relativeScore = Math.min(
    interactionRate > 10 ? 40 :
    interactionRate > 5 ? 30 :
    interactionRate > 1 ? 20 :
    interactionRate > 0.1 ? 10 : 0
  )
  
  // 评论权重加分
  const commentBonus = likedCount > 0 ? 
    Math.min((commentCount / likedCount) * 100 * 0.5, 10) : 0
  
  const viralScore = absoluteScore + relativeScore + commentBonus
  return Math.min(Math.max(Math.round(viralScore * 10) / 10, 0), 100)
}

// Mock 数据
const mockNotes = [
  {
    note_id: '1001',
    title: '海外求职申请技巧',
    liked_count: 5000,
    collected_count: 800,
    comment_count: 300,
    user: {
      user_id: 'author_1',
      nickname: '留学顾问',
      follower_count: 100000,
      note_count: 150
    }
  },
  {
    note_id: '1002',
    title: '牛津大学实习经验分享',
    liked_count: 8000,
    collected_count: 2000,
    comment_count: 500,
    user: {
      user_id: 'author_1',
      nickname: '留学顾问',
      follower_count: 100000,
      note_count: 150
    }
  },
  {
    note_id: '1003',
    title: '伦敦求职市场分析',
    liked_count: 3000,
    collected_count: 400,
    comment_count: 150,
    user: {
      user_id: 'author_2',
      nickname: '海归求职师',
      follower_count: 50000,
      note_count: 80
    }
  },
  {
    note_id: '1004',
    title: '英国实习如何获得offer',
    liked_count: 12000,
    collected_count: 3000,
    comment_count: 800,
    user: {
      user_id: 'author_2',
      nickname: '海归求职师',
      follower_count: 50000,
      note_count: 80
    }
  }
]

// 聚合逻辑
function aggregateByAuthor(notes, viralThreshold = 60) {
  const authorMap = {}
  
  notes.forEach(note => {
    const userId = note.user?.user_id || 'unknown'
    const nickname = note.user?.nickname || 'unknown'
    
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
  
  const authors = Object.values(authorMap).map(author => {
    const viralScores = author.all_notes.map(note => 
      calculateViralScore(
        note.liked_count,
        note.collected_count,
        note.comment_count,
        author.followers || 10000
      )
    )
    
    author.viral_notes = author.all_notes.filter((note, idx) => viralScores[idx] >= viralThreshold)
    
    const avgViralScore = viralScores.length > 0 
      ? viralScores.reduce((a, b) => a + b, 0) / viralScores.length 
      : 0
    
    return {
      ...author,
      viral_count: author.viral_notes.length,
      max_viral_score: Math.round(Math.max(...viralScores, 0) * 10) / 10,
      avg_viral_score: Math.round(avgViralScore * 10) / 10,
      post_count: author.all_notes.length
    }
  })
  
  return authors.sort((a, b) => b.viral_count - a.viral_count)
}

// 运行测试
console.log('=== 测试爆款评分和聚合逻辑 ===\n')

// 显示每条笔记的爆款评分
console.log('单条笔记评分情况：')
console.log('---')

mockNotes.forEach(note => {
  const score = calculateViralScore(
    note.liked_count,
    note.collected_count,
    note.comment_count,
    note.user.follower_count
  )
  console.log(`📝 ${note.title}`)
  console.log(`   点赞: ${note.liked_count} | 收藏: ${note.collected_count} | 评论: ${note.comment_count}`)
  console.log(`   粉丝: ${note.user.follower_count} | 爆款分: ${score.toFixed(2)}`)
  console.log('')
})

// 聚合结果
console.log('---\n')
console.log('聚合后的作者数据（阈值=60）：')
console.log('---')

const authors = aggregateByAuthor(mockNotes, 60)
authors.forEach((author, idx) => {
  console.log(`${idx + 1}. 👤 ${author.nickname}`)
  console.log(`   作者 ID: ${author.user_id}`)
  console.log(`   粉丝数: ${author.followers} | 笔记数: ${author.post_count}`)
  console.log(`   爆款笔记: ${author.viral_count} | 最高评分: ${author.max_viral_score} | 平均评分: ${author.avg_viral_score}`)
  
  if (author.viral_notes.length > 0) {
    console.log(`   🌟 爆款笔记：`)
    author.viral_notes.forEach(note => {
      const score = calculateViralScore(
        note.liked_count,
        note.collected_count,
        note.comment_count,
        author.followers
      )
      console.log(`      • ${note.title} (分数: ${score.toFixed(2)})`)
    })
  }
  console.log('')
})

console.log('===== 测试完成 =====')
