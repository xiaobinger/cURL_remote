"""AI 脚本生成：OpenAI 兼容协议（通义 / DeepSeek / Kimi / 自定义中转站通用）

配置存于 ~/.cURL/ai_config.json；后端内置专属 system prompt（pm API 子集），
仅输出可在前端沙箱直接运行的 JS 代码。
"""
import json
import logging
import os
import urllib.error
import urllib.request

from .config import CONFIG_DIR

logger = logging.getLogger("curlgen.ai")

AI_CONFIG_FILE = os.path.join(CONFIG_DIR, "ai_config.json")

DEFAULT_CONFIG = {"base_url": "", "api_key": "", "model": ""}

SYSTEM_PROMPT = """\
你是一个 curl 调试工具里的脚本生成 agent，专门为请求编写前置脚本（pre-request）和后置脚本（post-response）。
运行环境是浏览器内 JS 沙箱，只支持以下 pm API（Postman 风格的子集），禁止使用任何其他 API：

前置脚本可用：
- pm.request.method                    请求方法（字符串，只读）
- pm.request.url                       请求 URL（字符串，可赋值修改）
- pm.request.body                      请求体（字符串，可赋值修改）
- pm.request.headers.get(key) / set(key, value) / remove(key)
- pm.request.params.get(key) / set(key, value) / remove(key)
- pm.variables.get(key) / set(key, value)      会话级变量
- pm.environment.get(key)              当前环境的变量（只读）
- console.log(...)                     日志会显示在响应区

后置脚本额外可用：
- pm.response.code                     HTTP 状态码
- pm.response.time                     耗时（秒）
- pm.response.text()                   响应文本
- pm.response.json()                   解析后的 JSON 对象

硬性规则：
1. 只输出纯 JavaScript 代码，不要 markdown 代码块、不要任何解释文字
2. 同步代码，禁止 async/await、fetch、require、import、setTimeout
3. 禁止访问 window、document、全局对象
4. 代码要短小直接，做完该做的事就好
"""

_USER_TPL = """\
请为以下请求编写{kind_label}脚本。

当前请求：
- 方法: {method}
- URL: {url}
- Headers: {headers}
- Query Params: {params}
- Body 类型: {body_type}
- Body: {body}

需求：{prompt}
"""


def load_ai_config():
    if os.path.exists(AI_CONFIG_FILE):
        try:
            with open(AI_CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            return {**DEFAULT_CONFIG, **{k: v for k, v in cfg.items() if k in DEFAULT_CONFIG}}
        except Exception as e:
            logger.warning("AI 配置读取失败: %s", e)
    return dict(DEFAULT_CONFIG)


def save_ai_config(data):
    cfg = {k: str(data.get(k, "")).strip() for k in DEFAULT_CONFIG}
    with open(AI_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    logger.info("已保存 AI 配置: base_url=%s model=%s", cfg["base_url"], cfg["model"])
    return cfg


def _strip_code_fence(text):
    t = (text or "").strip()
    if t.startswith("```"):
        lines = t.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    return t


def generate_script(cfg, kind, prompt, context):
    """调 OpenAI 兼容接口生成脚本，返回脚本字符串；失败抛 ValueError"""
    base = (cfg.get("base_url") or "").rstrip("/")
    key = cfg.get("api_key") or ""
    model = cfg.get("model") or ""
    if not base or not key or not model:
        raise ValueError("请先在 🤖 AI 设置中配置接口地址、API Key 和模型")

    kind_label = "前置" if kind == "pre" else "后置"
    kv = lambda rows: ", ".join(f"{r[0]}={r[1]}" for r in rows if r and r[0]) or "（无）"
    user_msg = _USER_TPL.format(
        kind_label=kind_label,
        method=context.get("method", "GET"),
        url=context.get("url", ""),
        headers=kv(context.get("headers", [])),
        params=kv(context.get("params", [])),
        body_type=context.get("body_type", "None"),
        body=(context.get("body") or "")[:2000] or "（无）",
        prompt=prompt,
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.2,
    }
    req = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise ValueError(f"模型接口返回 {e.code}: {detail}")
    except Exception as e:
        raise ValueError(f"模型接口调用失败: {e}")

    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise ValueError("模型返回格式异常: " + json.dumps(data, ensure_ascii=False)[:300])
    script = _strip_code_fence(text)
    if not script:
        raise ValueError("模型返回了空内容")
    logger.info("AI 生成%s脚本成功: model=%s, %d 字符", kind_label, model, len(script))
    return script
