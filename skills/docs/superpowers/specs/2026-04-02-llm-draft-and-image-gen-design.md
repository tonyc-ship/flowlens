# LLM 文案生成 + 阿里云图片生成 设计文档

## Goal

重构文案生成流程：用 LLM 基于分析风格参数创作文案（替代当前 Python 字符串填充），并在文案确认后调阿里云 Wanx API 生成配图，全程在同一 AI 对话框内完成文案和图片的调整。

## Architecture

```
分析 Excel → 提取风格参数
                ↓
         _generate_draft_llm()   ← qwen-plus（可配置）
                ↓
         保存 .md 文件（含 image_style 元数据）
                ↓
       用户在 UI 查看 / AI 对话调整文案
                ↓
       点击"生成图片"按钮
                ↓
    _build_image_prompt() via LLM  ← 根据文案+图片风格生成 Wanx prompt
                ↓
    submit_wanx_task() → task_id
                ↓
    前端轮询 /api/image_task/{id} → 完成后下载保存到草稿目录
                ↓
       图片预览显示在文案左侧下方
                ↓
       同一 AI 对话框输入（文案调整 or 图片重生成）
       → /api/chat 判断意图，分别处理
```

## Tech Stack

- 文案生成：DashScope Chat Completions（qwen-plus 默认，可覆盖）
- 图片生成：DashScope Wanx（`wanx2.1-t2i-turbo` 默认，可覆盖）
- 图片轮询：前端 `setInterval` 每 3s 查一次，最多等 60s
- 模型配置：读取 `.env` 中 `DASHSCOPE_TEXT_MODEL` / `DASHSCOPE_IMAGE_MODEL` / `DASHSCOPE_IMAGE_SIZE`，缺省值内置，不写死

---

## 文件变更清单

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `scripts/visual_discovery.py` | 修改 | 重构文案生成逻辑、新增图片生成 API、扩展 chat 意图判断 |

只改这一个文件，不新增文件。

---

## 详细设计

### 1. 提取 `_read_env` 为模块级函数 + 新函数 `_load_model_config`

当前 `_read_env` 是 `_load_llm_key` 内部的嵌套函数，需先将其提取为模块级，供多处复用：

```python
def _read_env(name: str) -> str:
    """按优先级读取环境变量：系统环境 → BASE_DIR/.env → ~/.baoyu-skills/.env"""
    val = os.environ.get(name, "")
    if val:
        return val
    for env_path in [BASE_DIR / ".env", Path.home() / ".baoyu-skills" / ".env"]:
        try:
            for line in env_path.read_text().splitlines():
                if line.startswith(name + "="):
                    return line.split("=", 1)[1].strip().strip('"')
        except Exception:
            pass
    return ""

def _load_model_config() -> dict:
    """读取模型配置，全部有默认值，不写死业务逻辑"""
    return {
        "text_model":  _read_env("DASHSCOPE_TEXT_MODEL")  or "qwen-plus",
        "image_model": _read_env("DASHSCOPE_IMAGE_MODEL") or "wanx2.1-t2i-turbo",
        "image_size":  _read_env("DASHSCOPE_IMAGE_SIZE")  or "1024*1024",
    }
```

`_load_llm_key` 内部的嵌套 `_read_env` 定义删除，改为调用模块级版本。

---

### 2. 文案生成重构（替换 `generate_drafts` 中的字符串填充部分）

**新函数 `_generate_draft_llm(topic, style_params, key, base_url, text_model)`**

```python
def _generate_draft_llm(topic: str, style_params: dict, key: str, base_url: str, model: str) -> str:
    """调用 LLM，以分析数据为风格参考，创作小红书文案。返回 markdown 字符串。"""
    system = "你是专业的小红书内容创作者，擅长依据风格参考创作有感染力的原创笔记。"
    user = f"""请围绕主题「{topic}」创作一篇小红书笔记。

【写作风格参考（来自同类爆款账号分析，仅作风格指引，不要照抄）】
- 目标字数：约 {style_params['avg_words']} 字
- 语言风格：{style_params['style_desc'] or '口语化、亲切自然'}
- 情绪基调：{style_params['emotion'] or '积极正向'}
- 内容结构：{style_params['structure'] or '问题-解法-行动'}
- Hook 风格示例：{style_params['hook_examples'] or '无'}
- CTA 风格示例：{style_params['cta_examples'] or '无'}

【图片风格（用于配图参考）】
{style_params['image_style'] or '单图大字信息流'}

【输出格式（严格遵守）】
用 Markdown 输出，包含：
1. 一级标题（笔记标题）
2. 正文（分段，可用 emoji，符合字数要求）
3. 话题标签（# 开头，3~5个）

不要输出任何额外说明，直接输出 Markdown 内容。"""

    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        "temperature": 0.85,
        "max_tokens": 2000,
    }).encode()
    req = urllib.request.Request(
        base_url, data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"].strip()
```

**修改 `generate_drafts` 中的循环体**

当前的字符串填充逻辑（`body_parts` + `while len(filler) < per_sec_chars`）整块替换为：

```python
key, base_url, _ = _load_llm_key()
cfg = _load_model_config()
if key:
    draft_md = _generate_draft_llm(topic, style_params, key, base_url, cfg["text_model"])
else:
    draft_md = f"# {title}\n\n（未配置 API Key，无法生成文案）\n"
```

`style_params` 字典从已有的 `avg_words`, `style_desc`, `emotion`, `structure`, `hook_examples`, `cta_examples`, `image_style` 字段组装，这些字段已经在现有代码中读取。

---

### 3. 图片生成（新函数组）

**`_build_image_prompt(draft_md, image_style, topic, key, base_url, model)`**

用 LLM 将文案风格转换为 Wanx 能理解的图片描述：

```python
def _build_image_prompt(draft_md: str, image_style: str, topic: str,
                        key: str, base_url: str, model: str) -> str:
    user = f"""根据以下小红书文案和图片风格，生成一段适合 AI 图片生成的中文图片描述（40字以内，不含人名/版权词）。

文案主题：{topic}
图片风格：{image_style}
文案摘要：{draft_md[:200]}

只输出图片描述，不要解释。"""
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": user}],
        "temperature": 0.7, "max_tokens": 100,
    }).encode()
    req = urllib.request.Request(
        base_url, data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"].strip()
```

**`_submit_wanx_task(prompt, api_key, image_model, image_size)`**

```python
def _submit_wanx_task(prompt: str, api_key: str, model: str, size: str) -> str:
    """提交 Wanx 异步任务，返回 task_id"""
    url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis"
    payload = json.dumps({
        "model": model,
        "input": {"prompt": prompt},
        "parameters": {"size": size, "n": 1},
    }).encode()
    req = urllib.request.Request(
        url, data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "X-DashScope-Async": "enable",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    return data["output"]["task_id"]
```

**`_poll_wanx_task(task_id, api_key)`**

```python
def _poll_wanx_task(task_id: str, api_key: str) -> dict:
    """查询 Wanx 任务状态。返回 {"status": "SUCCEEDED"|"FAILED"|"PENDING", "url": str|None}"""
    url = f"https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {api_key}"}, method="GET"
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    status = data["output"]["task_status"]
    img_url = None
    if status == "SUCCEEDED":
        img_url = data["output"]["results"][0]["url"]
    return {"status": status, "url": img_url}
```

---

### 4. 新增 API 端点（`do_POST` / `do_GET` 中添加）

**POST `/api/generate_image`**

```python
# body: { "draft_path": "...", "image_style": "...", "topic": "..." }
draft_path = body.get("draft_path", "")
image_style = body.get("image_style", "")
topic       = body.get("topic", "")
draft_md    = Path(draft_path).read_text(encoding="utf-8") if draft_path else ""

key, base_url, _ = _load_llm_key()
cfg = _load_model_config()
if not key:
    self._json({"error": "未配置 DASHSCOPE_API_KEY"}); return

img_prompt = _build_image_prompt(draft_md, image_style, topic, key, base_url, cfg["text_model"])
task_id    = _submit_wanx_task(img_prompt, key, cfg["image_model"], cfg["image_size"])
self._json({"task_id": task_id, "prompt": img_prompt})
```

**GET `/api/image_task/<task_id>`**

```python
# 前端轮询，完成后下载图片保存到草稿同目录
task_id = path.split("/api/image_task/", 1)[1]
key, _, _ = _load_llm_key()
result = _poll_wanx_task(task_id, key)

if result["status"] == "SUCCEEDED" and result["url"]:
    # 下载并保存
    img_path = BASE_DIR / "output" / "images" / f"{task_id}.jpg"
    img_path.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(result["url"], str(img_path))
    result["local_path"] = f"/output/images/{task_id}.jpg"

self._json(result)
```

---

### 5. 扩展 `/api/chat`（意图判断）

在现有 `chat_with_gpt` 中，系统 prompt 去掉硬编码领域，增加意图识别：

**新函数 `_detect_intent(message)`** — 判断用户是要调文案还是调图片：

```python
def _detect_intent(message: str) -> str:
    """简单关键词判断：返回 'image' 或 'text'"""
    image_keywords = ["图片", "配图", "图", "换图", "重新生成图", "图像", "风格", "颜色", "构图"]
    return "image" if any(k in message for k in image_keywords) else "text"
```

**修改 `/api/chat` 处理逻辑**：

```python
intent = _detect_intent(message)
if intent == "image":
    # 图片重新生成：用 message 作为新的风格指令
    draft_path  = body.get("draft_path", "")
    image_style = body.get("image_style", "")
    topic       = body.get("topic", "")
    draft_md    = Path(draft_path).read_text(encoding="utf-8") if draft_path else ""
    key, base_url, _ = _load_llm_key()
    cfg = _load_model_config()
    # 把用户指令追加到图片风格描述里，让 LLM 重新生成 prompt
    combined_style = f"{image_style}。用户调整要求：{message}"
    img_prompt = _build_image_prompt(draft_md, combined_style, topic, key, base_url, cfg["text_model"])
    task_id    = _submit_wanx_task(img_prompt, key, cfg["image_model"], cfg["image_size"])
    self._json({"reply": f"正在重新生成图片，描述词：{img_prompt}", "task_id": task_id, "intent": "image"})
else:
    # 原有文案修改逻辑（调 chat_with_gpt）
    result = chat_with_gpt(draft_content, history, message)
    result["intent"] = "text"
    self._json(result)
```

---

### 6. 前端 UI 变更（`visual_discovery.py` 中的 HTML/JS 字符串）

**在文案预览 `<textarea>` 下方新增图片区域：**

```html
<!-- 生成图片按钮 + 图片预览（文案确认后显示） -->
<div style="margin-top:8px;display:flex;gap:8px;align-items:center">
  <button class="btn" id="genImgBtn" style="flex:1;padding:8px;font-size:.85rem;background:linear-gradient(135deg,#ff6b6b,#ee5a24)" onclick="generateImage()" disabled>🎨 生成配图</button>
  <span id="imgStatus" style="font-size:.78rem;color:#aaa"></span>
</div>
<div id="imgPreview" style="margin-top:10px;display:none">
  <img id="genImgEl" style="width:100%;border-radius:10px;border:1.5px solid #eee" />
</div>
```

**JS 函数（新增）：**

```js
let currentImageStyle = '';
let currentTopic = '';

async function generateImage() {
  const draftPath = document.getElementById('draftSel').value;
  if (!draftPath) return;
  document.getElementById('imgStatus').textContent = '生成中...';
  document.getElementById('genImgBtn').disabled = true;

  const res = await fetch('/api/generate_image', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ draft_path: draftPath, image_style: currentImageStyle, topic: currentTopic })
  }).then(r => r.json());

  if (res.error) { document.getElementById('imgStatus').textContent = res.error; return; }
  pollImageTask(res.task_id);
}

function pollImageTask(taskId) {
  const maxPolls = 20; let count = 0;
  const timer = setInterval(async () => {
    count++;
    if (count > maxPolls) { clearInterval(timer); document.getElementById('imgStatus').textContent = '超时'; return; }
    const res = await fetch(`/api/image_task/${taskId}`).then(r => r.json());
    if (res.status === 'SUCCEEDED') {
      clearInterval(timer);
      document.getElementById('genImgEl').src = res.local_path + '?t=' + Date.now();
      document.getElementById('imgPreview').style.display = 'block';
      document.getElementById('imgStatus').textContent = '✅ 生成完成';
      document.getElementById('genImgBtn').disabled = false;
    } else if (res.status === 'FAILED') {
      clearInterval(timer);
      document.getElementById('imgStatus').textContent = '❌ 生成失败';
      document.getElementById('genImgBtn').disabled = false;
    } else {
      document.getElementById('imgStatus').textContent = `生成中... (${count * 3}s)`;
    }
  }, 3000);
}
```

**修改 `loadDraft()` 函数**，在加载文案时从 metadata 读取 `image_style` 和 `topic`，并启用生成图片按钮：

```js
// 从 markdown metadata 提取 image_style / topic
const imgStyleMatch = text.match(/\*\*图片风格\*\*[：:]\s*(.+)/);
const topicMatch = text.match(/\*\*主题\*\*[：:]\s*(.+)/);
currentImageStyle = imgStyleMatch ? imgStyleMatch[1].trim() : '';
currentTopic = topicMatch ? topicMatch[1].trim() : '';
document.getElementById('genImgBtn').disabled = false;
```

**修改 `sendChat()` 函数**，处理 `intent === 'image'` 的响应（发起轮询）：

```js
if (data.intent === 'image' && data.task_id) {
  appendChat('assistant', data.reply);
  pollImageTask(data.task_id);
}
```

**修改 `sendChat()` 请求体**，加入 `image_style` 和 `topic`：

```js
body: JSON.stringify({
  draft_content: document.getElementById('draftPreview').value,
  draft_path: document.getElementById('draftSel').value,
  image_style: currentImageStyle,
  topic: currentTopic,
  history: chatHistory,
  message: msg
})
```

---

## 静态文件服务扩展

现有 `do_GET` 需支持 `/output/images/` 路径的本地图片文件访问：

```python
elif path.startswith("/output/images/"):
    img_path = BASE_DIR / path.lstrip("/")
    if img_path.exists():
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.end_headers()
        self.wfile.write(img_path.read_bytes())
    else:
        self.send_response(404); self.end_headers()
    return
```

---

## 不做的事

- 不新增文件
- 不改 `analyze_accounts.py` 或 `publish_manager.py`
- 不对接除 DashScope 以外的图片 API
- 不做图片历史记录管理
- 不做图片上传到小红书（仍由现有 publish 流程处理）
