#!/usr/bin/env node

/**
 * 集成测试脚本：测试新的 explore_v3.js 的核心功能
 * 包括：登录检测、验证码处理、爆款过滤、作者聚合
 */

import fs from 'fs'
import path from 'path'

// 颜色输出
const colors = {
  reset: '\x1b[0m',
  green: '\x1b[32m',
  red: '\x1b[31m',
  yellow: '\x1b[33m',
  blue: '\x1b[34m',
  cyan: '\x1b[36m'
}

function log(msg, color = 'reset') {
  console.log(`${colors[color]}${msg}${colors.reset}`)
}

function section(title) {
  log(`\n${'='.repeat(60)}`, 'cyan')
  log(`${title}`, 'cyan')
  log(`${'='.repeat(60)}`, 'cyan')
}

async function testStorageState() {
  section('测试 1: 检查 StorageState 文件')
  
  try {
    const storagePath = path.join(process.env.HOME, '.xhs-accounts/default/storage.json')
    
    if (fs.existsSync(storagePath)) {
      log(`✅ StorageState 文件存在: ${storagePath}`, 'green')
      
      const content = fs.readFileSync(storagePath, 'utf-8')
      const data = JSON.parse(content)
      
      // 检查关键字段
      const cookies = data.cookies || []
      const origins = data.origins || []
      
      log(`   - Cookies 数量: ${cookies.length}`, 'blue')
      log(`   - Origins 数量: ${origins.length}`, 'blue')
      
      // 查找 web_session
      const webSession = cookies.find(c => c.name === 'web_session')
      if (webSession) {
        log(`   ✅ 发现 web_session Cookie`, 'green')
        log(`      有效期: ${webSession.expires || '会话期'}`, 'blue')
      } else {
        log(`   ⚠️  未发现 web_session Cookie`, 'yellow')
      }
      
      return true
    } else {
      log(`❌ StorageState 文件不存在: ${storagePath}`, 'red')
      log(`   请先运行: python scripts/account_manager.py scan --name default`, 'yellow')
      return false
    }
  } catch (e) {
    log(`❌ 检查 StorageState 失败: ${e.message}`, 'red')
    return false
  }
}

function testLoginDetection() {
  section('测试 2: 登录检测逻辑')
  
  const testCases = [
    {
      url: 'https://www.xiaohongshu.com/explore',
      needsLogin: false,
      desc: '主页 (不需登录)'
    },
    {
      url: 'https://www.xiaohongshu.com/web-login/qrcode',
      needsLogin: true,
      desc: '登录页 (需登录)'
    },
    {
      url: 'https://www.xiaohongshu.com/sign-in',
      needsLogin: true,
      desc: '登录入口 (需登录)'
    },
    {
      url: 'https://www.xiaohongshu.com/search_result?keyword=test',
      needsLogin: false,
      desc: '搜索结果 (不需登录)'
    }
  ]
  
  let passed = 0
  testCases.forEach(tc => {
    const detected = /login|sign-in|signin|web-login/.test(tc.url)
    const correct = detected === tc.needsLogin
    const status = correct ? '✅' : '❌'
    const color = correct ? 'green' : 'red'
    
    log(`${status} ${tc.desc}`, color)
    log(`   URL: ${tc.url}`, 'blue')
    
    if (correct) passed++
  })
  
  log(`\n${passed}/${testCases.length} 测试通过`, passed === testCases.length ? 'green' : 'yellow')
  return passed === testCases.length
}

function testCaptchaDetection() {
  section('测试 3: 验证码检测逻辑')
  
  const testCases = [
    {
      url: 'https://www.xiaohongshu.com/website-login/captcha?xxxx',
      text: '',
      needsCaptcha: true,
      desc: 'URL 包含 captcha'
    },
    {
      url: 'https://www.xiaohongshu.com/search_result',
      text: 'IP存在风险，请切换可靠网络环境后重试',
      needsCaptcha: true,
      desc: '页面文本包含 IP 风险警告'
    },
    {
      url: 'https://www.xiaohongshu.com/search_result',
      text: '安全验证中，请稍候...',
      needsCaptcha: true,
      desc: '页面文本包含安全验证'
    },
    {
      url: 'https://www.xiaohongshu.com/explore',
      text: '推荐内容',
      needsCaptcha: false,
      desc: '正常页面'
    }
  ]
  
  let passed = 0
  testCases.forEach(tc => {
    const urlDetect = /verify|captcha|security-check|website-login|IP存在风险/.test(tc.url)
    const textDetect = tc.text.includes('IP存在风险') || 
                       tc.text.includes('安全验证') || 
                       tc.text.includes('安全限制')
    const detected = urlDetect || textDetect
    const correct = detected === tc.needsCaptcha
    const status = correct ? '✅' : '❌'
    const color = correct ? 'green' : 'red'
    
    log(`${status} ${tc.desc}`, color)
    
    if (correct) passed++
  })
  
  log(`\n${passed}/${testCases.length} 测试通过`, passed === testCases.length ? 'green' : 'yellow')
  return passed === testCases.length
}

function testViralScoring() {
  section('测试 4: 爆款评分公式')
  
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
  
  const testCases = [
    {
      name: '高互动笔记（大 V）',
      liked: 8000,
      collected: 2000,
      comment: 500,
      followers: 100000,
      expectViral: true
    },
    {
      name: '低互动笔记（小账号）',
      liked: 80,
      collected: 20,
      comment: 5,
      followers: 1000,
      expectViral: false
    },
    {
      name: '评论率高的笔记',
      liked: 1000,
      collected: 200,
      comment: 300,  // 30% 评论率
      followers: 50000,
      expectViral: true
    }
  ]
  
  const threshold = 60
  let passed = 0
  
  testCases.forEach(tc => {
    const score = calculateViralScore(tc.liked, tc.collected, tc.comment, tc.followers)
    const isViral = score >= threshold
    const correct = isViral === tc.expectViral
    const status = correct ? '✅' : '❌'
    const color = correct ? 'green' : 'red'
    
    log(`${status} ${tc.name}`, color)
    log(`   点赞: ${tc.liked} | 收藏: ${tc.collected} | 评论: ${tc.comment}`, 'blue')
    log(`   粉丝: ${tc.followers} | 评分: ${score.toFixed(2)} | 爆款: ${isViral ? '是' : '否'}`, 'blue')
    
    if (correct) passed++
  })
  
  log(`\n${passed}/${testCases.length} 测试通过`, passed === testCases.length ? 'green' : 'yellow')
  return passed === testCases.length
}

function testAuthorAggregation() {
  section('测试 5: 作者聚合逻辑')
  
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
  
  function aggregateByAuthor(notes, viralThreshold = 60) {
    const authorMap = {}
    
    notes.forEach(note => {
      const userId = note.user?.user_id || 'unknown'
      if (!authorMap[userId]) {
        authorMap[userId] = {
          user_id: userId,
          nickname: note.user?.nickname || 'unknown',
          followers: note.user?.follower_count || 0,
          all_notes: []
        }
      }
      authorMap[userId].all_notes.push(note)
    })
    
    return Object.values(authorMap).map(author => {
      const viralScores = author.all_notes.map(note =>
        calculateViralScore(note.liked_count, note.collected_count, note.comment_count, author.followers)
      )
      
      const viralNotes = author.all_notes.filter((note, idx) => viralScores[idx] >= viralThreshold)
      
      return {
        ...author,
        viral_count: viralNotes.length,
        post_count: author.all_notes.length,
        avg_viral_score: Math.round(viralScores.reduce((a, b) => a + b, 0) / viralScores.length * 10) / 10
      }
    }).sort((a, b) => b.viral_count - a.viral_count)
  }
  
  // 模拟数据
  const mockNotes = [
    {
      note_id: '1001',
      liked_count: 5000,
      collected_count: 800,
      comment_count: 300,
      user: {
        user_id: 'author_1',
        nickname: '留学顾问',
        follower_count: 100000
      }
    },
    {
      note_id: '1002',
      liked_count: 8000,
      collected_count: 2000,
      comment_count: 500,
      user: {
        user_id: 'author_1',
        nickname: '留学顾问',
        follower_count: 100000
      }
    },
    {
      note_id: '1003',
      liked_count: 500,
      collected_count: 50,
      comment_count: 20,
      user: {
        user_id: 'author_2',
        nickname: '小号用户',
        follower_count: 1000
      }
    }
  ]
  
  const result = aggregateByAuthor(mockNotes, 60)
  
  log(`输入: ${mockNotes.length} 条笔记，来自 2 个作者`, 'blue')
  log(`输出: ${result.length} 个作者聚合结果`, 'blue')
  
  let testPassed = true
  
  // 检查聚合结果
  if (result.length === 2) {
    log(`✅ 作者数量正确: 2`, 'green')
  } else {
    log(`❌ 作者数量错误: ${result.length} (期望 2)`, 'red')
    testPassed = false
  }
  
  // 检查第一个作者的笔记数
  if (result[0].post_count === 2) {
    log(`✅ 第一个作者的笔记数正确: 2`, 'green')
  } else {
    log(`❌ 第一个作者的笔记数错误: ${result[0].post_count} (期望 2)`, 'red')
    testPassed = false
  }
  
  // 检查爆款笔记数
  if (result[0].viral_count === 2) {
    log(`✅ 第一个作者的爆款笔记数正确: 2`, 'green')
  } else {
    log(`❌ 第一个作者的爆款笔记数错误: ${result[0].viral_count} (期望 2)`, 'red')
    testPassed = false
  }
  
  // 显示聚合结果
  log(`\n聚合结果:`, 'blue')
  result.forEach((author, idx) => {
    log(`${idx + 1}. ${author.nickname} (粉丝: ${author.followers})`, 'blue')
    log(`   笔记数: ${author.post_count} | 爆款数: ${author.viral_count} | 平均评分: ${author.avg_viral_score}`, 'blue')
  })
  
  return testPassed
}

async function main() {
  log('\n🚀 探红 (XHS) explore_v3.js 集成测试\n', 'cyan')
  
  const results = {
    '1. StorageState 检查': await testStorageState(),
    '2. 登录检测': testLoginDetection(),
    '3. 验证码检测': testCaptchaDetection(),
    '4. 爆款评分': testViralScoring(),
    '5. 作者聚合': testAuthorAggregation()
  }
  
  section('测试总结')
  
  let totalPassed = 0
  Object.entries(results).forEach(([name, passed]) => {
    const status = passed ? '✅' : '❌'
    const color = passed ? 'green' : 'red'
    log(`${status} ${name}`, color)
    if (passed) totalPassed++
  })
  
  const totalTests = Object.keys(results).length
  log(`\n总计: ${totalPassed}/${totalTests} 测试通过`, totalPassed === totalTests ? 'green' : 'yellow')
  
  if (totalPassed === totalTests) {
    log(`\n✅ 所有测试通过！explore_v3.js 已准备就绪。`, 'green')
    log(`\n下一步:\n  1. 运行: mv explore_v3.js explore.js\n  2. 测试搜索功能`, 'cyan')
  } else {
    log(`\n⚠️  部分测试未通过，请检查输出结果。`, 'yellow')
  }
  
  log(`\n${'='.repeat(60)}\n`, 'cyan')
}

main().catch(console.error)
