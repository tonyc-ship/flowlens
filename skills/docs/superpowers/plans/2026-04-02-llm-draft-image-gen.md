# LLM 文案生成 + Wanx 图片生成 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 LLM 基于分析风格参数创作文案（替代当前 Python 字符串填充），文案确认后调阿里云 Wanx API 生成配图，全程在同一 AI 对话框内完成文案和图片调整。

**Architecture:** `visual_discovery.py` 单文件修改：提取 `_read_env` 为模块级函数，新增 `_load_model_config` / `_generate_draft_llm` / `_build_image_prompt` / `_submit_wanx_task` / `_poll_wanx_task` / `_detect_intent` 六个函数，扩展三个 HTTP 端点，更新前端 HTML/JS。

**Tech Stack:** Python stdlib (`urllib.request`, `json`, `re`, `pathlib`) · DashScope Chat Completions API · DashScope Wanx text2image API · pytest + monkeypatch

---

## File Structure

| 文件 | 变更 |
|------|------|
| `scripts/visual_discovery.py` | 修改（唯一改动文件） |
| `tests/test_visual_discovery.py` | 修改（追加测试） |

---

### Task 1: 提取 `_read_env` 为模块级函数，新增 `_load_model_config`

**Files:**
- Modify: `scripts/visual_discovery.py:452-475`（`_load_llm_key` 区域）
- Modify: `tests/test_visual_discovery.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_visual_discovery.py` 末尾追加：

```python
# ── _load_model_config ────────────────────────────────────────────────────────

def test_load_model_config_defaults(monkeypatch, tmp_path):
    monkeypatch.delenv("DASHSCOPE_TEXT_MODEL",  raising=False)
    monkeypatch.delenv("DASHSCOPE_IMAGE_MODEL", raising=False)
    monkeypatch.delenv("DASHSCOPE_IMAGE_SIZE",  raising=False)
    monkeypatch.setattr(visual_discovery, "BASE_DIR", tmp_path)
    cfg = visual_discovery._load_model_config()
    assert cfg["text_model"]  == "qwen-plus"
    assert cfg["image_model"] == "wanx2.1-t2i-turbo"
    assert cfg["image_size"]  == "1024*1024"


def test_load_model_config_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHSCOPE_TEXT_MODEL",  "qwen-max")
    monkeypatch.setenv("DASHSCOPE_IMAGE_MODEL", "wanx2.1-t2i-plus")
    monkeypatch.setenv("DASHSCOPE_IMAGE_SIZE",  "768*768")
    monkeypatch.setattr(visual_discovery, "BASE_DIR", tmp_path)
    cfg = visual_discovery._load_model_config()
    assert cfg["text_model"]  == "qwen-max"
    assert cfg["image_model"] == "wanx2.1-t2i-plus"
    assert cfg["image_size"]  == "768*768"


def test_load_model_config_reads_from_dotenv(monkeypatch, tmp_path):
    monkeypatch.delenv("DASHSCOPE_TEXT_MODEL",  raising=False)
    monkeypatch.delenv("DASHSCOPE_IMAGE_MODEL", raising=False)
    monkeypatch.delenv("DASHSCOPE_IMAGE_SIZE",  raising=False)
    (tmp_path / ".env").write_text('DASHSCOPE_IMAGE_MODEL=wanx-custom\n')
    monkeypatch.setattr(visual_discovery, "BASE_DIR", tmp_path)
    cfg = visual_discovery._load_model_config()
    assert cfg["image_model"] == "wanx-custom"
    assert cfg["text_model"]  == "qwen-plus"   # default
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /Users/chenqinghua/Desktop/Auto-Redbook-Skills
python -m pytest tests/test_visual_discovery.py::test_load_model_config_defaults -v
```

期望：`FAILED` — `AttributeError: module 'visual_discovery' has no attribute '_load_model_config'`

- [ ] **Step 3: 实现**

在 `scripts/visual_discovery.py` 中，定位 `_load_llm_key` 函数（约第 452 行）。

**3a. 在 `_load_llm_key` 之前（第 451 行 `# ── OpenAI Chat` 注释后）插入模块级 `_read_env`：**

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
```

**3b. 紧接着插入 `_load_model_config`：**

```python
def _load_model_config() -> dict:
    """读取模型配置，全部有默认值，不写死业务逻辑"""
    return {
        "text_model":  _read_env("DASHSCOPE_TEXT_MODEL")  or "qwen-plus",
        "image_model": _read_env("DASHSCOPE_IMAGE_MODEL") or "wanx2.1-t2i-turbo",
        "image_size":  _read_env("DASHSCOPE_IMAGE_SIZE")  or "1024*1024",
    }
```

**3c. 修改 `_load_llm_key`，删除内部嵌套的 `_read_env` 定义，改调模块级版本：**

将原来的：
```python
def _load_llm_key() -> tuple:
    """返回 (api_key, base_url, model)，优先 OpenAI，回退 DashScope"""
    def _read_env(name):
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

    openai_key = _read_env("OPENAI_API_KEY")
```

替换为：
```python
def _load_llm_key() -> tuple:
    """返回 (api_key, base_url, model)，优先 OpenAI，回退 DashScope"""
    openai_key = _read_env("OPENAI_API_KEY")
```

（其余内容 `if openai_key: ... dashscope_key ... return "", "", ""` 保持不变）

- [ ] **Step 4: 运行测试确认通过**

```bash
python -m pytest tests/test_visual_discovery.py -v
```

期望：所有测试 PASS（包含原有的 `_load_llm_key` 和 `chat_with_gpt` 测试）

- [ ] **Step 5: Commit**

```bash
git add scripts/visual_discovery.py tests/test_visual_discovery.py
git commit -m "refactor: extract _read_env to module level, add _load_model_config"
```

---

### Task 2: 新增 `_generate_draft_llm`，重构文案生成循环

**Files:**
- Modify: `scripts/visual_discovery.py:250-327`（draft 生成区域）
- Modify: `tests/test_visual_discovery.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_visual_discovery.py` 末尾追加：

```python
# ── _generate_draft_llm ───────────────────────────────────────────────────────

def test_generate_draft_llm_returns_content(monkeypatch, tmp_path):
    monkeypatch.setattr(visual_discovery, "BASE_DIR", tmp_path)

    import urllib.request

    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self):
            import json
            return json.dumps({
                "choices": [{"message": {"content": "# 测试标题\n\n正文内容\n\n#标签"}}]
            }).encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: FakeResp())

    style_params = {
        "avg_words": 300, "style_desc": "口语化", "emotion": "积极",
        "structure": "痛点 | 解法", "hook_examples": "你知道吗",
        "cta_examples": "收藏", "image_style": "单图大字"
    }
    result = visual_discovery._generate_draft_llm(
        "美国ai求职", style_params, "sk-fake",
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "qwen-plus"
    )
    assert "测试标题" in result
    assert "正文内容" in result


def test_generate_draft_llm_prompt_contains_topic(monkeypatch, tmp_path):
    monkeypatch.setattr(visual_discovery, "BASE_DIR", tmp_path)
    import urllib.request
    captured = {}

    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self):
            import json
            return json.dumps({"choices": [{"message": {"content": "# title\n\nbody"}}]}).encode()

    def fake_urlopen(req, timeout=None):
        import json
        captured["body"] = json.loads(req.data)
        return FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    style_params = {
        "avg_words": 200, "style_desc": "", "emotion": "轻松",
        "structure": "A | B", "hook_examples": "", "cta_examples": "", "image_style": ""
    }
    visual_discovery._generate_draft_llm(
        "python副业", style_params, "sk-fake", "https://fake.url/v1/chat/completions", "qwen-plus"
    )
    user_content = captured["body"]["messages"][1]["content"]
    assert "python副业" in user_content
    assert "200" in user_content   # avg_words in prompt
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python -m pytest tests/test_visual_discovery.py::test_generate_draft_llm_returns_content -v
```

期望：`FAILED` — `AttributeError: module 'visual_discovery' has no attribute '_generate_draft_llm'`

- [ ] **Step 3: 在 `_load_model_config` 之后插入 `_generate_draft_llm`**

```python
def _generate_draft_llm(topic: str, style_params: dict,
                        key: str, base_url: str, model: str) -> str:
    """调用 LLM，以分析数据为风格参考，创作小红书文案。返回 markdown 字符串。"""
    import urllib.request
    system = "你是专业的小红书内容创作者，擅长依据风格参考创作有感染力的原创笔记。"
    user = f"""请围绕主题「{topic}」创作一篇小红书笔记。

【写作风格参考（来自同类爆款账号分析，仅作风格指引，不要照抄）】
- 目标字数：约 {style_params['avg_words']} 字
- 语言风格：{style_params['style_desc'] or '口语化、亲切自然'}
- 情绪基调：{style_params['emotion'] or '积极正向'}
- 内容结构：{style_params['structure'] or '问题-解法-行动'}
- Hook 风格示例：{style_params['hook_examples'] or '无'}
- CTA 风格示例：{style_params['cta_examples'] or '无'}

【图片风格（供配图参考）】
{style_params['image_style'] or '单图大字信息流'}

【输出格式（严格遵守）】
用 Markdown 输出，包含：
1. 一级标题（笔记标题）
2. 正文（分段，可用 emoji，符合字数要求）
3. 话题标签（# 开头，3~5个，贴合主题）

只输出 Markdown 内容，不要任何额外说明。"""

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
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"].strip()
```

- [ ] **Step 4: 重构 `run_pipeline` 中的文案生成循环（约第 242–327 行）**

定位 `# ── Step 4: 生成文案` 注释，将整个 draft 生成块替换为：

```python
        # ── Step 4: 生成文案 ──────────────────────────────────────────────────
        state["step"] = 4
        add_log(f"开始生成文案（{len(state['accounts'])} 账号 × {copies} 篇）...", "info")

        DRAFT_DIR.mkdir(parents=True, exist_ok=True)
        draft_paths = []
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")

        llm_key, llm_url, _ = _load_llm_key()
        llm_cfg = _load_model_config()

        for acc in state["accounts"]:
            name = acc.get("账号昵称", "未知账号")

            # 从分析结果读取风格参数
            avg_words  = int(acc.get("平均文字字数") or 300)
            style_desc = str(acc.get("图文风格描述") or "")
            image_style = str(acc.get("图片风格") or "单图大字信息流")
            emotion    = str(acc.get("情绪标签") or "口语化")
            hooks_raw  = str(acc.get("Hook模板", "") or "")
            hook_examples = " | ".join(
                h.strip() for h in re.split(r"[|｜]", hooks_raw) if h.strip()
            )[:100]
            ctas_raw   = str(acc.get("CTA模板", "") or "")
            cta_examples = " | ".join(
                c.strip() for c in re.split(r"[|｜]", ctas_raw) if c.strip()
            )[:100]
            structure  = str(acc.get("内容结构", "痛点引入 | 解决方案 | 行动号召") or "")

            style_params = {
                "avg_words":    avg_words,
                "style_desc":   style_desc,
                "image_style":  image_style,
                "emotion":      emotion,
                "structure":    structure,
                "hook_examples": hook_examples,
                "cta_examples":  cta_examples,
            }

            for i in range(copies):
                if llm_key:
                    try:
                        draft_md = _generate_draft_llm(
                            keyword, style_params, llm_key, llm_url, llm_cfg["text_model"]
                        )
                    except Exception as e:
                        add_log(f"  [{name}] LLM 生成失败: {e}", "warn")
                        draft_md = f"# {keyword} — 待完善\n\n（LLM 生成失败，请手动编写）\n"
                else:
                    draft_md = f"# {keyword} — 待完善\n\n（未配置 API Key，请在 .env 中配置 DASHSCOPE_API_KEY）\n"

                # 在文案头部追加元数据（供后续图片生成读取）
                metadata = (
                    f"**账号参考**：{name}\n"
                    f"**主题**：{keyword}\n"
                    f"**图片风格**：{image_style}\n"
                    f"**情绪**：{emotion}\n"
                    f"**参考字数**：{avg_words}字\n\n---\n\n"
                )
                copy_text = metadata + draft_md

                safe_name = re.sub(r'[\\/:*?"<>|]', "_", name)
                fpath = DRAFT_DIR / f"{safe_name}_文案{i+1}_{ts}.md"
                fpath.write_text(copy_text, encoding="utf-8")
                draft_paths.append(fpath)

                # 从 LLM 输出的 markdown 提取标题用于日志
                title_match = re.search(r"^#\s+(.+)", draft_md, re.MULTILINE)
                title_log = title_match.group(1)[:30] if title_match else keyword
                add_log(f"  [{name}] 篇{i+1}：{title_log}", "ok")

        state["drafts"] = [str(p) for p in draft_paths]
        add_log(f"文案生成完成，共 {len(draft_paths)} 篇", "ok")
```

- [ ] **Step 5: 运行测试确认通过**

```bash
python -m pytest tests/test_visual_discovery.py -v
```

期望：全部 PASS

- [ ] **Step 6: Commit**

```bash
git add scripts/visual_discovery.py tests/test_visual_discovery.py
git commit -m "feat: replace string-padding draft gen with LLM creative generation"
```

---

### Task 3: 修复 `chat_with_gpt` 硬编码领域

**Files:**
- Modify: `scripts/visual_discovery.py:478-529`

- [ ] **Step 1: 定位并替换系统 prompt**

找到 `chat_with_gpt` 函数中的 `system_prompt` 变量（约第 485 行），将：

```python
    system_prompt = """你是一位专业的小红书内容运营专家，擅长英国留学求职领域的内容创作。
用户会提供一篇小红书文案草稿，你需要根据用户的修改建议对文案进行优化。

规则：
1. 保持小红书风格：口语化、emoji点缀、分点清晰
2. 保留原文案的核心信息和结构
3. 回复时先给出修改建议说明（1-3句话），再给出完整修改后的文案（用 ```markdown ... ``` 包裹）
4. 如果用户只是提问而非要求修改，正常回答即可，不需要输出完整文案"""
```

替换为：

```python
    system_prompt = """你是一位专业的小红书内容运营专家，擅长根据用户需求对文案进行创意优化。
用户会提供一篇小红书文案草稿，你需要根据用户的修改建议对文案进行优化。

规则：
1. 保持小红书风格：口语化、emoji 点缀、分点清晰
2. 保留原文案的核心信息和主题方向
3. 回复时先给出修改建议说明（1-3句话），再给出完整修改后的文案（用 ```markdown ... ``` 包裹）
4. 如果用户只是提问而非要求修改，正常回答即可，不需要输出完整文案"""
```

- [ ] **Step 2: 运行已有测试确认不回退**

```bash
python -m pytest tests/test_visual_discovery.py -v
```

期望：全部 PASS

- [ ] **Step 3: Commit**

```bash
git add scripts/visual_discovery.py
git commit -m "fix: remove hardcoded domain from chat_with_gpt system prompt"
```

---

### Task 4: 新增 Wanx 图片生成函数

**Files:**
- Modify: `scripts/visual_discovery.py`（在 `_generate_draft_llm` 之后插入）
- Modify: `tests/test_visual_discovery.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_visual_discovery.py` 末尾追加：

```python
# ── Wanx image generation ─────────────────────────────────────────────────────

def test_build_image_prompt_returns_string(monkeypatch, tmp_path):
    monkeypatch.setattr(visual_discovery, "BASE_DIR", tmp_path)
    import urllib.request, json as _json

    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self):
            return _json.dumps({"choices": [{"message": {"content": "简洁白底信息图，大字"}}]}).encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: FakeResp())
    result = visual_discovery._build_image_prompt(
        "# 求职\n正文", "单图大字信息流", "美国ai求职",
        "sk-fake", "https://fake.url/v1/chat/completions", "qwen-plus"
    )
    assert isinstance(result, str)
    assert len(result) > 0


def test_submit_wanx_task_returns_task_id(monkeypatch, tmp_path):
    monkeypatch.setattr(visual_discovery, "BASE_DIR", tmp_path)
    import urllib.request, json as _json

    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self):
            return _json.dumps({"output": {"task_id": "task-abc123", "task_status": "PENDING"}}).encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: FakeResp())
    task_id = visual_discovery._submit_wanx_task(
        "一张白色背景的大字海报", "sk-fake", "wanx2.1-t2i-turbo", "1024*1024"
    )
    assert task_id == "task-abc123"


def test_poll_wanx_task_succeeded(monkeypatch, tmp_path):
    monkeypatch.setattr(visual_discovery, "BASE_DIR", tmp_path)
    import urllib.request, json as _json

    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self):
            return _json.dumps({
                "output": {
                    "task_status": "SUCCEEDED",
                    "results": [{"url": "https://example.com/img.jpg"}]
                }
            }).encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: FakeResp())
    result = visual_discovery._poll_wanx_task("task-abc123", "sk-fake")
    assert result["status"] == "SUCCEEDED"
    assert result["url"] == "https://example.com/img.jpg"


def test_poll_wanx_task_pending(monkeypatch, tmp_path):
    monkeypatch.setattr(visual_discovery, "BASE_DIR", tmp_path)
    import urllib.request, json as _json

    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self):
            return _json.dumps({"output": {"task_status": "PENDING"}}).encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: FakeResp())
    result = visual_discovery._poll_wanx_task("task-xyz", "sk-fake")
    assert result["status"] == "PENDING"
    assert result["url"] is None
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python -m pytest tests/test_visual_discovery.py::test_build_image_prompt_returns_string -v
```

期望：`FAILED` — `AttributeError: ... has no attribute '_build_image_prompt'`

- [ ] **Step 3: 在 `_generate_draft_llm` 之后插入三个函数**

```python
def _build_image_prompt(draft_md: str, image_style: str, topic: str,
                        key: str, base_url: str, model: str) -> str:
    """用 LLM 将文案风格转换为 Wanx 图片描述（≤40字，无版权词）"""
    import urllib.request
    user = f"""根据以下小红书文案和图片风格，生成一段适合 AI 图片生成的中文图片描述（40字以内，不含人名/版权词）。

文案主题：{topic}
图片风格：{image_style or '简洁信息流'}
文案摘要：{draft_md[:200]}

只输出图片描述，不要任何解释。"""
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": user}],
        "temperature": 0.7,
        "max_tokens": 100,
    }).encode()
    req = urllib.request.Request(
        base_url, data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"].strip()


def _submit_wanx_task(prompt: str, api_key: str, model: str, size: str) -> str:
    """提交 Wanx 异步图片生成任务，返回 task_id"""
    import urllib.request
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
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read())
    return data["output"]["task_id"]


def _poll_wanx_task(task_id: str, api_key: str) -> dict:
    """查询 Wanx 任务状态。返回 {"status": str, "url": str|None}"""
    import urllib.request
    url = f"https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {api_key}"}, method="GET"
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    status = data["output"]["task_status"]
    img_url = None
    if status == "SUCCEEDED":
        img_url = data["output"]["results"][0]["url"]
    return {"status": status, "url": img_url}
```

- [ ] **Step 4: 运行测试确认通过**

```bash
python -m pytest tests/test_visual_discovery.py -v
```

期望：全部 PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/visual_discovery.py tests/test_visual_discovery.py
git commit -m "feat: add _build_image_prompt, _submit_wanx_task, _poll_wanx_task"
```

---

### Task 5: 意图检测 + 扩展 `/api/chat`

**Files:**
- Modify: `scripts/visual_discovery.py`（`_poll_wanx_task` 之后 + `do_POST` 中的 `/api/chat`）
- Modify: `tests/test_visual_discovery.py`

- [ ] **Step 1: 写失败测试**

```python
# ── _detect_intent ────────────────────────────────────────────────────────────

def test_detect_intent_image_keywords():
    for msg in ["把图片改成暖色调", "换一张图", "重新生成图片", "图像风格调整", "配图太暗了"]:
        assert visual_discovery._detect_intent(msg) == "image", f"应识别为 image: {msg}"


def test_detect_intent_text_keywords():
    for msg in ["把语气改得更亲切", "加强紧迫感", "缩短文案", "重写开头", "帮我润色"]:
        assert visual_discovery._detect_intent(msg) == "text", f"应识别为 text: {msg}"
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python -m pytest tests/test_visual_discovery.py::test_detect_intent_image_keywords -v
```

期望：`FAILED` — `AttributeError: ... has no attribute '_detect_intent'`

- [ ] **Step 3: 在 `_poll_wanx_task` 之后插入 `_detect_intent`**

```python
def _detect_intent(message: str) -> str:
    """判断用户意图：'image'（图片调整）or 'text'（文案修改）"""
    image_keywords = ["图片", "配图", "换图", "图像", "重新生成图", "图的", "颜色", "构图", "背景", "画面"]
    return "image" if any(k in message for k in image_keywords) else "text"
```

- [ ] **Step 4: 修改 `do_POST` 中的 `/api/chat` 处理逻辑**

找到（约第 589 行）：

```python
        if path == "/api/chat":
            draft_content = body.get("draft_content", "")
            history = body.get("history", [])
            message = body.get("message", "")
            if not message:
                self._json({"error": "message 不能为空"})
                return
            result = chat_with_gpt(draft_content, history, message)
            self._json(result)
```

替换为：

```python
        if path == "/api/chat":
            draft_content = body.get("draft_content", "")
            history       = body.get("history", [])
            message       = body.get("message", "")
            draft_path    = body.get("draft_path", "")
            image_style   = body.get("image_style", "")
            topic         = body.get("topic", "")
            if not message:
                self._json({"error": "message 不能为空"})
                return

            intent = _detect_intent(message)

            if intent == "image":
                key, base_url, _ = _load_llm_key()
                cfg = _load_model_config()
                if not key:
                    self._json({"error": "未配置 DASHSCOPE_API_KEY", "intent": "image"})
                    return
                draft_md = ""
                if draft_path:
                    try:
                        draft_md = Path(draft_path).read_text(encoding="utf-8")
                    except Exception:
                        pass
                combined_style = f"{image_style}。用户调整要求：{message}"
                try:
                    img_prompt = _build_image_prompt(draft_md, combined_style, topic, key, base_url, cfg["text_model"])
                    task_id    = _submit_wanx_task(img_prompt, key, cfg["image_model"], cfg["image_size"])
                    self._json({"reply": f"正在重新生成图片，描述词：{img_prompt}", "task_id": task_id, "intent": "image"})
                except Exception as e:
                    self._json({"error": str(e), "intent": "image"})
            else:
                result = chat_with_gpt(draft_content, history, message)
                result["intent"] = "text"
                self._json(result)
```

- [ ] **Step 5: 运行测试确认通过**

```bash
python -m pytest tests/test_visual_discovery.py -v
```

期望：全部 PASS

- [ ] **Step 6: Commit**

```bash
git add scripts/visual_discovery.py tests/test_visual_discovery.py
git commit -m "feat: add _detect_intent, extend /api/chat to handle image regeneration"
```

---

### Task 6: 新增 HTTP 端点（`/api/generate_image`、`/api/image_task/<id>`、静态图片）

**Files:**
- Modify: `scripts/visual_discovery.py`（`do_GET` 和 `do_POST`）

- [ ] **Step 1: 在 `do_GET` 中添加图片任务查询和静态图片服务**

找到 `do_GET` 中的 `else:` 分支（约第 580 行）：

```python
        else:
            self.send_response(404); self.end_headers()
```

替换为：

```python
        elif path.startswith("/api/image_task/"):
            task_id = path.split("/api/image_task/", 1)[1]
            key, _, _ = _load_llm_key()
            if not key:
                self._json({"error": "未配置 API Key"})
                return
            try:
                result = _poll_wanx_task(task_id, key)
                if result["status"] == "SUCCEEDED" and result["url"]:
                    img_dir = BASE_DIR / "output" / "images"
                    img_dir.mkdir(parents=True, exist_ok=True)
                    img_path = img_dir / f"{task_id}.jpg"
                    if not img_path.exists():
                        import urllib.request as _ur
                        _ur.urlretrieve(result["url"], str(img_path))
                    result["local_path"] = f"/output/images/{task_id}.jpg"
                self._json(result)
            except Exception as e:
                self._json({"error": str(e)})
        elif path.startswith("/output/images/"):
            img_path = BASE_DIR / path.lstrip("/")
            if img_path.exists():
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.end_headers()
                self.wfile.write(img_path.read_bytes())
            else:
                self.send_response(404); self.end_headers()
        else:
            self.send_response(404); self.end_headers()
```

- [ ] **Step 2: 在 `do_POST` 中添加 `/api/generate_image`**

找到 `do_POST` 中的 `else:` 分支（约第 627 行）：

```python
        else:
            self.send_response(404); self.end_headers()
```

替换为：

```python
        elif path == "/api/generate_image":
            draft_path  = body.get("draft_path", "")
            image_style = body.get("image_style", "")
            topic       = body.get("topic", "")
            key, base_url, _ = _load_llm_key()
            cfg = _load_model_config()
            if not key:
                self._json({"error": "未配置 DASHSCOPE_API_KEY"})
                return
            draft_md = ""
            if draft_path:
                try:
                    draft_md = Path(draft_path).read_text(encoding="utf-8")
                except Exception:
                    pass
            try:
                img_prompt = _build_image_prompt(draft_md, image_style, topic, key, base_url, cfg["text_model"])
                task_id    = _submit_wanx_task(img_prompt, key, cfg["image_model"], cfg["image_size"])
                self._json({"task_id": task_id, "prompt": img_prompt})
            except Exception as e:
                self._json({"error": str(e)})
        else:
            self.send_response(404); self.end_headers()
```

- [ ] **Step 3: 手动验证服务器能启动**

```bash
kill $(lsof -ti:8888) 2>/dev/null; sleep 1
python scripts/visual_discovery.py &
sleep 2
curl -s http://localhost:8888/api/state | python3 -m json.tool | head -5
```

期望：返回合法 JSON，包含 `"status"` 字段

- [ ] **Step 4: Commit**

```bash
git add scripts/visual_discovery.py
git commit -m "feat: add /api/generate_image, /api/image_task/<id>, static image serving"
```

---

### Task 7: 前端 UI — 生成图片按钮 + 图片预览 + 联动 sendChat

**Files:**
- Modify: `scripts/visual_discovery.py`（HTML/JS 字符串，约第 886–1210 行）

- [ ] **Step 1: 在文案预览 `<textarea>` 下方插入图片区域**

找到（约第 899–901 行）：

```html
        <div style="display:flex;gap:8px;margin-top:8px">
          <button class="btn" style="flex:1;padding:8px;font-size:.85rem;background:linear-gradient(135deg,#34c759,#28a745)" onclick="applyUpdated()" id="applyBtn" disabled>✅ 应用修改并保存</button>
        </div>
      </div>
```

替换为：

```html
        <div style="display:flex;gap:8px;margin-top:8px">
          <button class="btn" style="flex:1;padding:8px;font-size:.85rem;background:linear-gradient(135deg,#34c759,#28a745)" onclick="applyUpdated()" id="applyBtn" disabled>✅ 应用修改并保存</button>
          <button class="btn" style="flex:1;padding:8px;font-size:.85rem;background:linear-gradient(135deg,#ff6b6b,#ee5a24)" onclick="generateImage()" id="genImgBtn" disabled>🎨 生成配图</button>
        </div>
        <div style="margin-top:6px;font-size:.75rem;color:#aaa" id="imgStatus"></div>
        <div id="imgPreview" style="display:none;margin-top:10px">
          <img id="genImgEl" style="width:100%;border-radius:10px;border:1.5px solid #eee;display:block" />
        </div>
      </div>
```

- [ ] **Step 2: 修改 `loadDraft()` 函数，提取元数据并启用图片按钮**

找到（约第 1107 行）：

```js
function loadDraft() {
  const file = document.getElementById('draftSel').value;
  if (!file) return;
  fetch(`/api/draft?file=${encodeURIComponent(file)}`).then(r => r.json()).then(d => {
    if (d.error) { alert(d.error); return; }
    currentDraftFile = file;
    currentDraftContent = d.content;
    document.getElementById('draftPreview').value = d.content;
    chatHistory = [];
    pendingUpdatedDraft = null;
    document.getElementById('applyBtn').disabled = true;
    document.getElementById('chatHistory').innerHTML =
      '<div style="text-align:center;color:#bbb;font-size:.82rem;padding-top:40px">文案已加载，可以开始对话修改</div>';
  });
}
```

替换为：

```js
function loadDraft() {
  const file = document.getElementById('draftSel').value;
  if (!file) return;
  fetch(`/api/draft?file=${encodeURIComponent(file)}`).then(r => r.json()).then(d => {
    if (d.error) { alert(d.error); return; }
    currentDraftFile = file;
    currentDraftContent = d.content;
    document.getElementById('draftPreview').value = d.content;
    chatHistory = [];
    pendingUpdatedDraft = null;
    document.getElementById('applyBtn').disabled = true;
    document.getElementById('genImgBtn').disabled = false;
    document.getElementById('imgStatus').textContent = '';
    document.getElementById('imgPreview').style.display = 'none';
    document.getElementById('chatHistory').innerHTML =
      '<div style="text-align:center;color:#bbb;font-size:.82rem;padding-top:40px">文案已加载，可以开始对话修改，或点击「生成配图」</div>';
    // 从元数据提取图片风格和主题
    const imgStyleMatch = d.content.match(/\*\*图片风格\*\*[：:]\s*(.+)/);
    const topicMatch    = d.content.match(/\*\*主题\*\*[：:]\s*(.+)/);
    currentImageStyle = imgStyleMatch ? imgStyleMatch[1].trim() : '';
    currentTopic      = topicMatch    ? topicMatch[1].trim()    : '';
  });
}
```

- [ ] **Step 3: 在 `let chatHistory = []` 附近的 JS 变量声明处追加新变量**

找到（约第 1086–1089 行）：

```js
let chatHistory = [];
let currentDraftFile = '';
let currentDraftContent = '';
let pendingUpdatedDraft = null;
```

替换为：

```js
let chatHistory = [];
let currentDraftFile = '';
let currentDraftContent = '';
let pendingUpdatedDraft = null;
let currentImageStyle = '';
let currentTopic = '';
```

- [ ] **Step 4: 在 `applyUpdated` 函数之后插入图片生成 JS 函数**

找到（约第 1205 行）`// 初始加载文案列表` 注释之前，插入：

```js
async function generateImage() {
  const draftPath = document.getElementById('draftSel').value;
  if (!draftPath) return;
  document.getElementById('imgStatus').textContent = '生成中...';
  document.getElementById('genImgBtn').disabled = true;
  document.getElementById('imgPreview').style.display = 'none';
  try {
    const res = await fetch('/api/generate_image', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({draft_path: draftPath, image_style: currentImageStyle, topic: currentTopic})
    }).then(r => r.json());
    if (res.error) {
      document.getElementById('imgStatus').textContent = '❌ ' + res.error;
      document.getElementById('genImgBtn').disabled = false;
      return;
    }
    document.getElementById('imgStatus').textContent = `描述词：${res.prompt}`;
    pollImageTask(res.task_id);
  } catch(e) {
    document.getElementById('imgStatus').textContent = '❌ 请求失败: ' + e.message;
    document.getElementById('genImgBtn').disabled = false;
  }
}

function pollImageTask(taskId) {
  let count = 0;
  const timer = setInterval(async () => {
    count++;
    if (count > 20) {
      clearInterval(timer);
      document.getElementById('imgStatus').textContent = '⚠️ 超时（60s），请重试';
      document.getElementById('genImgBtn').disabled = false;
      return;
    }
    try {
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
    } catch(e) {
      clearInterval(timer);
      document.getElementById('imgStatus').textContent = '❌ 轮询失败: ' + e.message;
      document.getElementById('genImgBtn').disabled = false;
    }
  }, 3000);
}
```

- [ ] **Step 5: 修改 `sendChat()` 请求体，加入 `draft_path`、`image_style`、`topic`，并处理 `intent === 'image'` 响应**

找到 `sendChat()` 中的 `fetch('/api/chat', ...)` 调用：

```js
  fetch('/api/chat', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      draft_content: currentDraftContent,
      history: chatHistory,
      message: msg
    })
  }).then(r => r.json()).then(d => {
    document.getElementById('chatBtn').disabled = false;
    document.getElementById('chatBtn').textContent = '发送';
    if (d.error) {
      appendChatMsg('error', '❌ ' + d.error);
      return;
    }
    chatHistory.push({role:'user', content: msg});
    chatHistory.push({role:'assistant', content: d.reply});
    appendChatMsg('assistant', d.reply);
    if (d.updated_draft) {
      pendingUpdatedDraft = d.updated_draft;
      document.getElementById('applyBtn').disabled = false;
      document.getElementById('draftPreview').value = d.updated_draft;
    }
  })
```

替换为：

```js
  fetch('/api/chat', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      draft_content: currentDraftContent,
      draft_path:    currentDraftFile,
      image_style:   currentImageStyle,
      topic:         currentTopic,
      history:       chatHistory,
      message:       msg
    })
  }).then(r => r.json()).then(d => {
    document.getElementById('chatBtn').disabled = false;
    document.getElementById('chatBtn').textContent = '发送';
    if (d.error) {
      appendChatMsg('error', '❌ ' + d.error);
      return;
    }
    chatHistory.push({role:'user', content: msg});
    chatHistory.push({role:'assistant', content: d.reply});
    appendChatMsg('assistant', d.reply);
    if (d.intent === 'image' && d.task_id) {
      // 图片重生成：直接开始轮询
      document.getElementById('imgStatus').textContent = `描述词已更新，生成中...`;
      document.getElementById('imgPreview').style.display = 'none';
      document.getElementById('genImgBtn').disabled = true;
      pollImageTask(d.task_id);
    } else if (d.updated_draft) {
      pendingUpdatedDraft = d.updated_draft;
      document.getElementById('applyBtn').disabled = false;
      document.getElementById('draftPreview').value = d.updated_draft;
    }
  })
```

- [ ] **Step 6: 重启服务器并手动验证 UI**

```bash
kill $(lsof -ti:8888) 2>/dev/null; sleep 1
python scripts/visual_discovery.py &
sleep 2
open http://localhost:8888
```

验证步骤：
1. 选择一篇已有文案 → 确认「生成配图」按钮变为可点击（不再灰显）
2. 确认 `currentImageStyle` 和 `currentTopic` 从文案元数据中正确读取（在浏览器控制台输入 `currentImageStyle` 查看）
3. 在聊天框输入"帮我把语气改得更轻松" → 确认走文案修改路径（没有触发图片生成）
4. 在聊天框输入"图片太暗了，改成清新白色" → 确认 `imgStatus` 显示"描述词已更新，生成中..."

- [ ] **Step 7: Commit**

```bash
git add scripts/visual_discovery.py
git commit -m "feat: add image gen button, preview, and unified AI chat for text+image"
```

---

## 自检：Spec 覆盖确认

| Spec 要求 | 对应 Task |
|-----------|-----------|
| `_read_env` 提取为模块级 | Task 1 |
| `_load_model_config` 从 env 读取，有默认值 | Task 1 |
| `_generate_draft_llm` 替代字符串填充 | Task 2 |
| 删除 `CONTENT_TOPICS` 硬编码 | Task 2 |
| `chat_with_gpt` 去掉硬编码领域 | Task 3 |
| `_build_image_prompt` | Task 4 |
| `_submit_wanx_task` | Task 4 |
| `_poll_wanx_task` | Task 4 |
| `_detect_intent` | Task 5 |
| `/api/chat` 意图分流 | Task 5 |
| `/api/generate_image` POST | Task 6 |
| `/api/image_task/<id>` GET + 下载保存 | Task 6 |
| `/output/images/` 静态服务 | Task 6 |
| 生成图片按钮 + 图片预览 | Task 7 |
| `loadDraft` 提取元数据 + 启用按钮 | Task 7 |
| `sendChat` 传入 image_style/topic，处理 image intent | Task 7 |
