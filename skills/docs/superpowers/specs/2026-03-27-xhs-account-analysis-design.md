# XHS 账号分析报表设计文档

**日期：** 2026-03-27
**目标：** 批量爬取 19 个小红书账号的最近 10 篇笔记，分析图文风格、互动数据、受众特征，输出 Excel 报表

---

## 一、背景

运营团队管理 19 个小红书账号（留英咨询方向），需要系统了解每个账号的内容风格、流量表现和受众特征，以便制定差异化运营策略。

---

## 二、方案

在 `Auto-Redbook-Skills` 内新增 `scripts/analyze_accounts.py`，复用已有的 `account_manager.py` 获取登录 Cookie，调用 xhs 库抓取数据，调用 Claude API 分析风格，用 `openpyxl` 生成 Excel。

**xhs 库版本：** `xhs==0.2.13`（PyPI 最高可用版本，`requirements.txt` 中的 `xhs>=0.4.0` 需改为 `xhs==0.2.13`）。

---

## 三、文件结构

```
Auto-Redbook-Skills/
├── scripts/
│   └── analyze_accounts.py   # 新增：账号分析主脚本
└── output/
    └── xhs_account_analysis_YYYYMMDD.xlsx  # 运行时生成
```

---

## 四、账号清单（硬编码）

脚本内置以下 19 个账号，字段：昵称、状态：

| 昵称 | 状态 |
|------|------|
| 好运绵绵冰 | 闲置中 |
| 发呆糕手 | 闲置中 |
| Lily学姐（归国求职版） | 闲置中 |
| offer收割机学长（留子归国求职版） | 闲置中 |
| Giselle in uk | 闲置中 |
| 知识分子（无小号助理） | 闲置中 |
| 英国求职辛普森 | 闲置中 |
| 辛普森英国咨询 | 闲置中 |
| 小鹅留英咨询 | 闲置中 |
| 辛普森学长咨询 | 闲置中 |
| 叹鸭求职咨询 | 闲置中 |
| 蘑菇蘑菇（无小号助理） | 正常使用 |
| ada的留英求职 | 正常使用 |
| wikk | 闲置中 |
| offer羊羊 | 未登录 |
| 小泡芙在英国 | 正常使用 |
| 小鹅Consulting | 正常使用 |
| 利娅在英国 | 正常使用 |
| Jojo的英国生活 | 正常使用 |

---

## 五、数据流

```
1. 调用 get_next_account() 获取已登录 Cookie（来自 account_manager.py）
   ↓
2. 初始化 XhsClient（需传入 sign 函数，参考 publish_xhs.py 的 LocalPublisher.init_client() 实现）：
   from xhs import XhsClient
   from xhs.help import sign as local_sign
   cookies = parse_cookie(cookie_string)
   a1 = cookies.get('a1', '')
   def sign_func(uri, data=None, a1_param="", web_session=""):
       return local_sign(uri, data, a1=a1 or a1_param)
   client = XhsClient(cookie=cookie_string, sign=sign_func)
   ↓
3. 对每个账号昵称：
   a. 搜索用户：client.get_user_by_keyword(nickname)
      → 返回结构：result["users"][0]，从中取 user_id 字段（键名 "user_id"）
      → 若 result["users"] 为空 → 填「未找到账号」，跳过
   b. 获取用户主页：client.get_user_info(user_id)
      → 粉丝数：result["fans_count"]
      → 笔记总数：result["notes_count"]（如字段不存在则填 N/A）
   c. 获取最新笔记列表：client.get_user_notes(user_id, cursor="")
      → 取前 10 条（实际数量可能 < 10，Column E 填实际数）
      → 每条包含：note_id, title, liked_count, collected_count, comment_count, image_count
   d. 逐篇获取笔记详情：client.get_note_by_id(note_id)
      → 提取正文：result["desc"]
      → 单篇失败则跳过，继续下一篇
   e. 每获取完一篇详情后，随机延迟 1–2 秒；每个账号处理完毕后，额外延迟 3–5 秒
   f. 汇总该账号统计数据
   ↓
4. 将笔记标题 + 正文拼接，调用 Claude API 分析（返回 JSON）：
   - 图文风格描述（≤100字）
   - 内容偏好（≤30字）
   - 受众分析（≤30字）
   - 目标对象关联点（≤30字）
   - 账号权重评级（A/B/C + 一句理由，≤30字）
   → Claude 返回内容先去除 Markdown 代码块（```json ... ```），再 json.loads() 解析
   ↓
5. 创建 output/ 目录（Path("output").mkdir(exist_ok=True)）
   openpyxl 写入 Excel，保存至 output/xhs_account_analysis_YYYYMMDD.xlsx
```

---

## 六、Excel 表格结构

每个账号一行，共 19 行（+ 表头）。

| 列 | 字段名 | 类型 | 说明 |
|----|--------|------|------|
| A | 账号昵称 | 文本 | 清单硬编码 |
| B | 状态 | 文本 | 清单硬编码 |
| C | 粉丝数 | 数字 | 爬取 |
| D | 笔记总数 | 数字 | 爬取 |
| E | 分析笔记篇数 | 数字 | 实际爬取数（≤10）|
| F | 平均图片张数 | 数字（1位小数）| 计算 |
| G | 图片偏好 | 文本（≤30字）| 规则计算：均图片数==1→「单图为主」，>1→「多图为主（均X张）」，混合→「单图/多图混合」 |
| H | 平均文字字数 | 数字（整数）| 计算 |
| I | 图文风格描述 | 文本（**≤100字**）| Claude 推断 |
| J | 内容偏好 | 文本（≤30字）| Claude 推断 |
| K | 平均点赞数 | 数字（整数）| 计算 |
| L | 平均收藏数 | 数字（整数）| 计算 |
| M | 平均评论数 | 数字（整数）| 计算 |
| N | 互动率 | 百分比（1位小数）| (均点赞+均收藏)/粉丝数；粉丝数为0时填「N/A」 |
| O | 用户粘度 | 百分比（1位小数）| 均收藏/均点赞 |
| P | 受众分析 | 文本（≤30字）| Claude 推断 |
| Q | 目标对象关联点 | 文本（≤30字）| Claude 推断 |
| R | 账号权重评级 | 文本（≤30字）| Claude 综合评分 A/B/C + 理由 |

---

## 七、Claude API 分析 Prompt

对每个账号，将 10 篇笔记的「标题 + 正文」拼接后，发送以下 prompt：

```
你是一名小红书运营分析师。以下是某账号最近{n}篇笔记内容：

{拼接的标题+正文}

请分析该账号，用JSON格式回答：
{
  "图文风格描述": "不超过100字，描述视觉风格、配色偏好、排版习惯、文案语气",
  "内容偏好": "不超过30字，高频话题方向",
  "受众分析": "不超过30字，主要受众群体特征",
  "目标对象关联点": "不超过30字，内容与目标用户的痛点/需求连接",
  "账号权重评级": "A/B/C + 不超过20字理由"
}
```

---

## 八、错误处理

| 场景 | 处理方式 |
|------|---------|
| 搜索昵称无匹配结果 | 该行填「未找到账号」，跳过 |
| 账号无笔记 | 该行填「无笔记数据」，跳过 |
| 单篇笔记详情获取失败 | 跳过该篇，继续下一篇 |
| Claude API 调用失败 | 该列填「分析失败」，其余数值列正常填充 |
| Cookie 过期 | 脚本启动时校验，失效则提示运行 refresh |

---

## 九、运行方式

```bash
# 安装新增依赖（同时修正 xhs 版本）
pip install openpyxl anthropic xhs==0.2.13

# .env 中需配置：
# XHS_COOKIE=...（或使用 accounts.json 登录账号）
# ANTHROPIC_API_KEY=sk-ant-...

# 确保 accounts.json 中至少有一个有效登录账号

# 运行
python scripts/analyze_accounts.py

# 输出
# output/xhs_account_analysis_20260327.xlsx
```

---

## 十、不在本次范围内

- 笔记评论内容分析
- 历史趋势对比（多次分析结果对比）
- 图片视觉风格识别（需额外图像分析 API）
- 账号涨粉曲线分析
