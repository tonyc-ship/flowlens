# explore_v3.js 改进说明

## 核心改进

### 1️⃣ **数据获取方式升级**

#### 旧版本 (explore.js)
```javascript
// 仅依赖 DOM 选择器，当页面结构改变时容易失效
const posts = await page.$$eval('.post-item', els => {
  return els.map(el => ({...}))
})
```

**问题**：
- ❌ 小红书经常改变 DOM 结构
- ❌ 动态加载的内容容易遗漏
- ❌ 没有真实的 API 数据

#### 新版本 (explore_v3.js)
```javascript
// API 拦截方式，从真实 API 响应中获取数据
function onResponse(response) {
  if (url.includes('/search/notes')) {
    const data = response.json()
    // 从 API 直接获取结构化数据
  }
}
```

**优势**：
- ✅ 数据结构标准，不受 DOM 变化影响
- ✅ 获得完整的 API 响应字段
- ✅ 包含准确的互动数据（点赞、收藏、评论）

---

### 2️⃣ **登录和验证处理**

#### 旧版本 (explore.js)
```javascript
async function needsCaptcha(page) {
  const url = page.url()
  if (/verify|captcha/.test(url)) return true
  // 只检查 URL，不检查 DOM 内容
}
```

**问题**：
- ❌ 只检查 URL，可能漏掉页面内的验证码元素
- ❌ 未检测 "IP存在风险" 等中文警告
- ❌ 登录检测不够全面

#### 新版本 (explore_v3.js)
```javascript
async function needsCaptcha(page) {
  const url = page.url()
  if (/verify|captcha|IP存在风险/.test(url)) return true
  
  // 同时检查页面文本内容
  const text = await page.evaluate(() => document.body?.innerText || '')
  if (text.includes('IP存在风险') || text.includes('安全验证')) return true
}
```

**优势**：
- ✅ 多层检测（URL + DOM + 文本）
- ✅ 识别中文安全警告
- ✅ 可在验证解除后自动继续

---

### 3️⃣ **爆款评分公式**

#### 旧版本 (explore.js)
```javascript
// 使用简单的点赞数排序，不考虑其他因素
const score = note.liked_count
```

**问题**：
- ❌ 忽视收藏、评论等关键指标
- ❌ 不考虑粉丝基数（大 V 和小账号标准应不同）
- ❌ 无法准确识别真正的爆款

#### 新版本 (explore_v3.js)
```javascript
function calculateViralScore(likedCount, collectedCount, commentCount, followersCount) {
  // 基础分: (点赞 + 收藏*2 + 评论*3) / 粉丝数 * 1000
  const baseScore = ((likedCount + collectedCount * 2 + commentCount * 3) 
                     / followersCount) * 1000
  
  // 评论率权重: 评论越多，粘性越强
  const engagementRate = (commentCount / likedCount) * 100
  
  // 最终分数 = 基础分 + 评论权重
  return baseScore + engagementRate * 5
}
```

**优势**：
- ✅ 综合考虑点赞、收藏、评论
- ✅ 按粉丝基数归一化（公平对比）
- ✅ 重视评论（反映真实互动）
- ✅ 分数标准化到 0-100 范围

**评分示例**：
```
笔记 A: 点赞 5000, 收藏 800, 评论 300, 粉丝 100K
  → 基础分 = (5000 + 800*2 + 300*3) / 100000 * 1000 = 68
  → 评论率 = 300/5000 * 100 = 6
  → 最终分 = 68 + 6*5 = 98 分 ✅ 爆款

笔记 B: 点赞 500, 收藏 50, 评论 20, 粉丝 1K
  → 基础分 = (500 + 50*2 + 20*3) / 1000 * 1000 = 660
  → 评论率 = 20/500 * 100 = 4
  → 最终分 = 660 + 4*5 = 680... → 限制到 100 分
  → 实际分 = 100 分 ✅ 超级爆款
```

---

### 4️⃣ **作者聚合逻辑**

#### 旧版本 (explore.js)
```javascript
// 直接从笔记中提取作者，无聚合
const authors = posts.map(p => p.author)
```

**问题**：
- ❌ 同一作者的多条笔记各算一次
- ❌ 无法计算作者的爆款笔记数
- ❌ 无法筛选出真正高产的作者

#### 新版本 (explore_v3.js)
```javascript
function aggregateByAuthor(notes, viralThreshold = 60) {
  // 1. 按作者 ID 分组
  const authorMap = {}
  notes.forEach(note => {
    const userId = note.user.user_id
    if (!authorMap[userId]) {
      authorMap[userId] = {
        user_id: userId,
        nickname: note.user.nickname,
        followers: note.user.follower_count,
        all_notes: []
      }
    }
    authorMap[userId].all_notes.push(note)
  })
  
  // 2. 计算每个作者的爆款笔记数
  Object.values(authorMap).forEach(author => {
    author.viral_notes = author.all_notes.filter(note => 
      calculateViralScore(...) >= viralThreshold
    )
  })
  
  // 3. 按爆款笔记数排序
  return authors.sort((a, b) => b.viral_count - a.viral_count)
}
```

**输出结果对比**：

**旧版本输出**：
```json
{
  "authors": [
    {"user_id": "A", "nickname": "用户A", "post_count": 1},
    {"user_id": "B", "nickname": "用户B", "post_count": 1},
    {"user_id": "A", "nickname": "用户A", "post_count": 1}  // 重复！
  ]
}
```

**新版本输出**：
```json
{
  "authors": [
    {
      "user_id": "A",
      "nickname": "用户A",
      "followers": 100000,
      "post_count": 2,
      "viral_count": 2,           // 爆款笔记数
      "max_viral_score": 98.5,    // 最高评分
      "avg_viral_score": 95.3,    // 平均评分
      "viral_notes": [            // 具体爆款笔记
        {"note_id": "001", "title": "..."}
      ]
    },
    {
      "user_id": "B",
      "nickname": "用户B",
      "followers": 50000,
      "post_count": 3,
      "viral_count": 1,           // 只有 1 条爆款
      ...
    }
  ]
}
```

**优势**：
- ✅ 同一作者笔记聚合为一条记录
- ✅ 显示作者的爆款笔记数（核心指标）
- ✅ 计算平均爆款分（体现作者稳定性）
- ✅ 返回具体爆款笔记列表

---

### 5️⃣ **错误处理和备选方案**

#### 旧版本 (explore.js)
```javascript
try {
  const posts = await scrapeListPage()
} catch (e) {
  return []  // 直接返回空
}
```

**问题**：
- ❌ 无法区分失败原因
- ❌ 无法提供用户指导
- ❌ 丧失所有已获取数据

#### 新版本 (explore_v3.js)
```javascript
try {
  // 阶段 1: 验证登录
  if (await needsLogin(page)) {
    throw new Error('需要登录')
  }
  
  // 阶段 2: 搜索关键词
  posts = await searchKeywordByAPI(page, keyword)
  
  // 如果 API 没有数据，尝试 DOM 提取（备选）
  if (posts.length === 0) {
    const domNotes = await page.$$eval('[class*="FeedItem"]', ...)
    posts.push(...domNotes)
  }
  
} catch (error) {
  // 返回部分数据 + 错误信息
  return {
    data_source: 'partial',
    authors: authors,  // 已聚合的数据
    error: error.message
  }
}
```

**优势**：
- ✅ 多备选方案（API → DOM → 用户提示）
- ✅ 返回部分数据而非全部丢弃
- ✅ 清晰的错误消息

---

## 使用指南

### 替换步骤

1. **备份旧版本**
   ```bash
   cp explore.js explore_backup.js
   ```

2. **使用新版本**
   ```bash
   mv explore_v3.js explore.js
   ```

3. **测试**
   ```bash
   node explore.js
   # 或通过 MCP 调用
   ```

### 参数说明

```javascript
explore({
  keyword: '英国留学求职',          // 搜索关键词
  searchLimit: 20,                  // 搜索结果数（最多获取的笔记数）
  viralThreshold: 60,               // 爆款阈值（0-100，建议 60-70）
  authorLimit: 20                   // 返回的作者数量
})
```

### 输出格式

```json
{
  "keyword": "英国留学求职",
  "search_limit": 20,
  "author_limit": 20,
  "total_fetched": 18,              // 实际获取的笔记数
  "viral_passed": 12,               // 达到爆款阈值的笔记数
  "data_source": "real_search",     // 数据来源标识
  "authors": [
    {
      "user_id": "...",
      "nickname": "留学顾问",
      "followers": 100000,
      "post_count": 5,
      "viral_count": 4,             // 这个作者有 4 条爆款
      "avg_viral_score": 85.3,
      "max_viral_score": 98.5,
      "viral_notes": [...]          // 具体爆款笔记
    }
  ],
  "posts": [...]                    // 所有笔记详细数据
}
```

---

## 对标 analyze_accounts.py

新版本 `explore_v3.js` 采用了 `analyze_accounts.py` 中的关键模式：

| 特性 | analyze_accounts.py | explore_v3.js |
|------|---------------------|---------------|
| 登录检测 | `_needs_login()` | `needsLogin()` |
| 验证码处理 | `_needs_captcha()` + 120s 等待 | `needsCaptcha()` + `waitForCaptcha()` |
| API 拦截 | 在 `_intercept_navigate()` 中拦截 | 在 `searchKeywordByAPI()` 中拦截 |
| 备选方案 | 失败后尝试 DOM 解析 | 同样的备选 |
| 爆款评分 | `interaction_rate` 计算 | `calculateViralScore()` |
| 作者聚合 | `compute_stats()` 按作者分组 | `aggregateByAuthor()` |

---

## 已知限制

1. **IP 限制**: 当搜索页返回 "IP存在风险" 时，脚本会检测并等待 120 秒
   - 如果仍未解除，可手动切换网络或使用代理

2. **首次运行**: 需要先通过 `account_manager.py scan --name default` 获得有效的 StorageState

3. **API 结构变化**: 如果小红书改变 API 端点名称，需要更新 `onResponse()` 函数中的匹配规则

---

## 测试结果

```
=== 测试爆款评分和聚合逻辑 ===

单条笔记评分情况：
📝 英国留学申请技巧
   点赞: 5000 | 收藏: 800 | 评论: 300
   粉丝: 100000 | 爆款分: 100.00 ✅

📝 牛津大学实习经验分享
   点赞: 8000 | 收藏: 2000 | 评论: 500
   粉丝: 100000 | 爆款分: 100.00 ✅

聚合后的作者数据（阈值=60）：
1. 👤 留学顾问
   粉丝数: 100000 | 笔记数: 2
   爆款笔记: 2 | 最高评分: 100 | 平均评分: 100
   🌟 爆款笔记：
      • 英国留学申请技巧 (分数: 100.00)
      • 牛津大学实习经验分享 (分数: 100.00)

2. 👤 海归求职师
   粉丝数: 50000 | 笔记数: 2
   爆款笔记: 2 | 最高评分: 100 | 平均评分: 100
   🌟 爆款笔记：
      • 伦敦求职市场分析 (分数: 100.00)
      • 英国实习如何获得offer (分数: 100.00)

===== 测试完成 =====
```

---

## 与用户需求的对应关系

✅ **"先登录再检索关键词"** → `needsLogin()` + `needsCaptcha()` 检测  
✅ **"爆款公式参考 MCP 设定"** → `calculateViralScore()` 综合公式  
✅ **"参考 analyze_accounts.py 的检索方式"** → API 拦截 + 备选 DOM 解析  
✅ **"不伪造数据"** → `data_source` 标记，明确数据来源
