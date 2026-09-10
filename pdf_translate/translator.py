import json
import os
import re
import time

import requests

GOOGLE_MIRRORS = [
    "https://v2g.borber.top",
    "https://translate.googleapis.com",
]
YOUDAO_URL = "https://aidemo.youdao.com/trans"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
REQUEST_GAP = 3.0
# LLM 请求间最小间隔, 秒。免费档限流时建议调大 (如 85), 可用
# 环境变量 LLM_REQUEST_GAP 覆盖。
LLM_REQUEST_GAP = float(os.environ.get("LLM_REQUEST_GAP", "1.2"))
# 限流特征串: 触发后按 10/20/40/60s 退避
RATE_LIMIT_MARKERS = ("429", "1302", "rate limit", "速率限制", "限流")
MAX_BATCH_CHARS = 400
MAX_BATCH_LINES = 12

DEFAULT_LLM_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_LLM_MODEL = "glm-4.7-flash"


class Translator:
    """翻译器类: 支持 Google/Youdao/LLM/OpenCode 四种引擎"""

    def __init__(
        self,
        api_key="",
        base_url="",
        model="",
        engine="google",
        temperature=0.2,
        max_batch_chars=None,
        max_batch_lines=None,
        fallback_engine="",
        opencode_url="",
        opencode_user="",
        opencode_pass="",
    ):
        """初始化翻译器

        Args:
            api_key: LLM API密钥
            base_url: LLM API地址
            model: LLM模型名称
            engine: 翻译引擎 (google/youdao/llm/opencode)
            temperature: LLM生成温度
            max_batch_chars: 批量翻译最大字符数
            max_batch_lines: 批量翻译最大行数
            fallback_engine: 主引擎失败后的兜底引擎
            opencode_url: opencode serve 地址
            opencode_user: opencode 用户名
            opencode_pass: opencode 密码
        """
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA})
        self._last_request = 0.0
        self.engine = engine
        self.max_batch_chars = max_batch_chars or MAX_BATCH_CHARS
        self.max_batch_lines = max_batch_lines or MAX_BATCH_LINES
        # 兜底引擎: 主引擎连续失败后自动切换 (如 llm 失败 -> opencode)
        self.fallback_engine = fallback_engine or os.environ.get(
            "LLM_FALLBACK_ENGINE", ""
        )
        self.opencode_url = (
            opencode_url or os.environ.get("OPENCODE_URL", "http://127.0.0.1:4096")
        ).rstrip("/")
        self.opencode_user = opencode_user or os.environ.get(
            "OPENCODE_USER", "opencode"
        )
        self.opencode_pass = opencode_pass or os.environ.get(
            "OPENCODE_SERVER_PASSWORD", ""
        )
        if engine == "llm":
            self.api_key = api_key or os.environ.get("LLM_API_KEY", "")
            self.base_url = base_url or os.environ.get(
                "LLM_BASE_URL", DEFAULT_LLM_BASE_URL
            )
            self.model = model or os.environ.get(
                "LLM_MODEL", DEFAULT_LLM_MODEL
            )
            self.temperature = temperature
            if max_batch_chars:
                self.max_batch_chars = max_batch_chars
            if max_batch_lines:
                self.max_batch_lines = max_batch_lines
            if not self.api_key:
                print(
                    "[translate] 警告: LLM 引擎未配置 API Key (LLM_API_KEY), "
                    "回退到 google 免费接口"
                )
                self.engine = "google"
                self.max_batch_chars = MAX_BATCH_CHARS
                self.max_batch_lines = MAX_BATCH_LINES

    @staticmethod
    def _is_limited(status):
        """判断是否被限流 (返回 True 表示需要等待重试)"""
        return status in ("103", "411", "429")

    def _translate_text(self, text, src="ja", dst="zh-CN", attempts=4):
        """翻译文本 (通用方法, 支持重试和引擎切换)

        Args:
            text: 待翻译文本
            src: 源语言代码
            dst: 目标语言代码
            attempts: 最大重试次数

        Returns:
            翻译后的文本, 失败返回空字符串
        """
        # LLM 免费档有账户级速率限制, 需要更长的退避与更多重试次数
        max_attempts = 8 if self.engine == "llm" else attempts
        engine = self.engine
        failed = 0
        for attempt in range(max_attempts):
            self._throttle()
            try:
                if engine == "google":
                    result = self._translate_google(text, src, dst)
                elif engine == "llm":
                    result = self._translate_llm(text, src, dst)
                elif engine == "opencode":
                    result = self._translate_opencode(text, src, dst)
                else:
                    result = self._translate_youdao(text)
                if result is not None:
                    return result
            except Exception as e:
                err = str(e)
                failed += 1
                limited = any(m in err for m in RATE_LIMIT_MARKERS)
                if limited and engine == "llm":
                    wait = min(10 * 2 ** min(attempt, 3), 60)
                    print(f"[translate] 限流({err[:60]}), 等待 {wait}s...")
                    time.sleep(wait)
                elif (
                    self.fallback_engine
                    and engine != self.fallback_engine
                    and failed >= 2
                ):
                    print(
                        f"[translate] {engine} 引擎连续失败 ({err[:80]}), "
                        f"切换 {self.fallback_engine} 兜底..."
                    )
                    engine = self.fallback_engine
                    failed = 0
                else:
                    print(f"[translate] retry {attempt + 1}: {err[:80]}")
                    time.sleep(2 ** attempt)
        return ""

    def _throttle(self):
        """请求限流控制: 确保两次请求间隔不低于最小间隔"""
        gap = (
            LLM_REQUEST_GAP
            if self.engine in ("llm", "opencode")
            else REQUEST_GAP
        ) - (time.time() - self._last_request)
        if gap > 0:
            time.sleep(gap)
        self._last_request = time.time()

    def _translate_google(self, text, src, dst):
        """Google 翻译: 通过免费镜像接口翻译

        Args:
            text: 待翻译文本
            src: 源语言代码 (如 'ja')
            dst: 目标语言代码 (如 'zh-CN')

        Returns:
            翻译后的文本
        """
        last_err = None
        for mirror in GOOGLE_MIRRORS:
            try:
                resp = self.session.get(
                    f"{mirror}/translate_a/single",
                    params={
                        "client": "gtx",
                        "sl": src,
                        "tl": dst,
                        "dt": "t",
                        "q": text,
                    },
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
                if data and isinstance(data[0], list):
                    return "".join(
                        part[0] for part in data[0] if part and part[0]
                    ).strip()
                raise RuntimeError(f"谷歌接口异常: {str(data)[:80]}")
            except Exception as e:
                last_err = e
                print(f"[translate] 镜像 {mirror} 失败: {str(e)[:60]}")
        raise RuntimeError(f"所有谷歌镜像失败: {str(last_err)[:60]}")

    def _translate_youdao(self, text):
        """有道翻译: 通过有道免费接口翻译"""
        resp = self.session.post(
            YOUDAO_URL,
            data={"q": text, "from": "ja", "to": "zh-CHS"},
            timeout=30,
        )
        data = resp.json()
        if isinstance(data, dict) and data.get("translation"):
            return data["translation"][0].strip()
        status = data.get("errorCode") if isinstance(data, dict) else ""
        if self._is_limited(status):
            print(f"[translate] 限流({status}), 等待 12s...")
            time.sleep(12)
            return None
        raise RuntimeError(f"有道接口异常: {str(data)[:80]}")

    @staticmethod
    def _llm_system_prompt(src, dst):
        """生成 LLM 翻译的系统提示词"""
        return (
            f"你是专业译者, 把用户消息中的{src}翻译成{dst}。"
            '用户消息的每一行都以"数字. "开头(如"0. 原文"), '
            '你必须逐行翻译, 输出时保留每一行的"数字. "前缀和原有行序, '
            "一行对应一行, 不合并、不拆分、不遗漏、不删号, "
            "不输出任何解释或额外内容, 翻译不出来的行原样保留。"
        )

    def _translate_llm(self, text, src, dst):
        """LLM 翻译: 通过 OpenAI 兼容 API 翻译"""
        from openai import OpenAI

        client = OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=60)
        resp = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self._llm_system_prompt(src, dst)},
                {"role": "user", "content": text},
            ],
            temperature=self.temperature,
        )
        content = resp.choices[0].message.content
        return (content or "").strip()

    def _translate_opencode(self, text, src, dst):
        """OpenCode 翻译: 通过本机 opencode serve 翻译"""
        """通过本机 opencode serve 翻译 (http://127.0.0.1:4096)。"""
        url = self.opencode_url
        auth = (self.opencode_user, self.opencode_pass) if self.opencode_pass else None
        timeout = 180
        # 1. 新建 session
        resp = self.session.post(
            f"{url}/session", json={}, auth=auth, timeout=timeout
        )
        resp.raise_for_status()
        sid = resp.json().get("id")
        if not sid:
            raise RuntimeError(f"opencode 创建 session 失败: {str(resp.json())[:100]}")
        try:
            # 2. 发送消息并等待回复 (system 字段带翻译指令, 模型用 opencode 默认)
            msg = self.session.post(
                f"{url}/session/{sid}/message",
                json={
                    "system": self._llm_system_prompt(src, dst),
                    "parts": [{"type": "text", "text": text}],
                },
                auth=auth,
                timeout=timeout,
            )
            msg.raise_for_status()
            data = msg.json()
            parts = data.get("parts") or []
            content = "\n".join(
                p.get("text", "")
                for p in parts
                if isinstance(p, dict) and p.get("type") == "text" and p.get("text")
            ).strip()
            if not content:
                raise RuntimeError(f"opencode 空回复: {str(data)[:120]}")
            return content
        finally:
            try:
                self.session.delete(
                    f"{url}/session/{sid}", auth=auth, timeout=10
                )
            except Exception:
                pass

    def translate_lines(self, lines):
        """批量翻译多行文本 (自动分批, 保持上下文连贯)

        Args:
            lines: 待翻译的文本列表

        Returns:
            翻译后的文本列表
        """
        if not lines:
            return []
        translated = []
        current_batch = []
        current_len = 0
        context = []  # 前几批的译文, 用于 LLM 上下文
        for line in lines:
            if (current_len + len(line) > self.max_batch_chars
                    or len(current_batch) >= self.max_batch_lines) and current_batch:
                batch_result = self._translate_batch(current_batch, context)
                translated.extend(batch_result)
                # 保留最近 3 批译文作为上下文 (每批最多 12 行)
                context.extend(batch_result)
                if len(context) > 36:  # 3 批 x 12 行
                    context = context[-36:]
                current_batch = []
                current_len = 0
            current_batch.append(line)
            current_len += len(line)
        if current_batch:
            translated.extend(self._translate_batch(current_batch, context))
        return translated

    def translate_by_sections(self, text_lines, page_height=None):
        """按章节/段落分组翻译 (根据行间距检测段落分隔)

        Args:
            text_lines: TextLine 对象列表 (包含 text, y_min, y_max 等属性)
            page_height: 页面高度 (用于计算相对间距)

        Returns:
            翻译后的文本列表 (与输入顺序对应)
        """
        if not text_lines:
            return []

        # 默认页面高度 (如果没有提供)
        if page_height is None:
            page_height = max(tl.y_max for tl in text_lines) + 100

        # 检测章节分隔: 根据行间距判断
        sections = []  # 章节列表, 每个章节是一组行
        current_section = [text_lines[0]]

        for i in range(1, len(text_lines)):
            prev = text_lines[i - 1]
            curr = text_lines[i]

            # 计算行间距 (相对于行高的倍数)
            gap = curr.y_min - prev.y_max
            prev_height = prev.y_max - prev.y_min
            curr_height = curr.y_max - curr.y_min
            avg_height = max((prev_height + curr_height) // 2, 1)

            # 判断是否为章节分隔:
            # 1. 间距超过行高的 2.5 倍
            # 2. 或者间距超过页面高度的 5%
            is_section_break = (
                gap > avg_height * 2.5 or
                gap > page_height * 0.05
            )

            if is_section_break:
                sections.append(current_section)
                current_section = [curr]
            else:
                current_section.append(curr)

        # 添加最后一个章节
        sections.append(current_section)

        # 翻译每个章节
        translated = []
        context = []  # 跨章节上下文

        for section in sections:
            # 提取章节文本
            section_texts = [tl.text for tl in section]

            # 按字符数限制拆分大章节 (避免超出API限制)
            batch_result = self._translate_section_with_context(
                section_texts, context
            )
            translated.extend(batch_result)

            # 更新上下文 (保留最近翻译的内容)
            context.extend(batch_result)
            if len(context) > 50:  # 保留最近 50 行上下文
                context = context[-50:]

        return translated

    def _translate_section_with_context(self, texts, context):
        """翻译一个章节 (带上下文支持)"""
        # 如果章节较短, 直接作为一批翻译
        total_chars = sum(len(t) for t in texts)
        if total_chars <= self.max_batch_chars and len(texts) <= self.max_batch_lines:
            return self._translate_batch(texts, context)

        # 章节较长, 按批次拆分
        translated = []
        current_batch = []
        current_len = 0

        for text in texts:
            if (current_len + len(text) > self.max_batch_chars
                    or len(current_batch) >= self.max_batch_lines) and current_batch:
                batch_result = self._translate_batch(current_batch, context)
                translated.extend(batch_result)
                context.extend(batch_result)
                if len(context) > 36:
                    context = context[-36:]
                current_batch = []
                current_len = 0
            current_batch.append(text)
            current_len += len(text)

        if current_batch:
            translated.extend(self._translate_batch(current_batch, context))

        return translated

    def _translate_batch(self, lines, context=None):
        """翻译一批文本 (带上下文支持)"""
        payload = "\n".join(f"{i}. {text}" for i, text in enumerate(lines))
        try:
            if context and self.engine in ("llm", "opencode"):
                content = self._translate_text_with_context(payload, context)
            else:
                content = self._translate_text(payload)
        except Exception as e:
            print(f"[translate] batch failed: {str(e)[:80]}")
            content = ""
        translated = self._parse_batch(content, len(lines))
        missing = [i for i, t in enumerate(translated) if not t]
        if missing:
            print(
                f"[translate] {len(missing)}/{len(lines)} 行批量失败，逐行补译..."
            )
            for i in missing:
                translated[i] = self._translate_text(lines[i], attempts=3)
        return translated

    def _translate_text_with_context(self, text, context, src="ja", dst="zh-CN"):
        """带上下文的翻译 (仅 LLM/OpenCode 引擎支持)"""
        """带上下文的翻译 (仅 LLM/opencode 引擎)"""
        if self.engine == "llm":
            return self._translate_llm_with_context(text, context, src, dst)
        elif self.engine == "opencode":
            return self._translate_opencode_with_context(text, context, src, dst)
        return self._translate_text(text, src, dst)

    def _translate_llm_with_context(self, text, context, src, dst):
        """带上下文的 LLM 翻译"""
        from openai import OpenAI

        context_text = "\n".join(f"[前文] {t}" for t in context[-12:])  # 最近 12 行上下文
        system_prompt = (
            f"你是专业译者, 把用户消息中的{src}翻译成{dst}。"
            '用户消息的每一行都以"数字. "开头(如"0. 原文"), '
            '你必须逐行翻译, 输出时保留每一行的"数字. "前缀和原有行序, '
            "一行对应一行, 不合并、不拆分、不遗漏、不删号, "
            "不输出任何解释或额外内容, 翻译不出来的行原样保留。"
            "根据前文上下文保持译文连贯性。"
        )
        user_content = f"前文译文:\n{context_text}\n\n待翻译:\n{text}" if context_text else text

        client = OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=60)
        resp = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=self.temperature,
        )
        content = resp.choices[0].message.content
        return (content or "").strip()

    def _translate_opencode_with_context(self, text, context, src, dst):
        """带上下文的 OpenCode 翻译"""
        """带上下文的 opencode 翻译"""
        url = self.opencode_url
        auth = (self.opencode_user, self.opencode_pass) if self.opencode_pass else None
        timeout = 180
        resp = self.session.post(
            f"{url}/session", json={}, auth=auth, timeout=timeout
        )
        resp.raise_for_status()
        sid = resp.json().get("id")
        if not sid:
            raise RuntimeError(f"opencode 创建 session 失败: {str(resp.json())[:100]}")
        try:
            context_text = "\n".join(f"[前文] {t}" for t in context[-12:])
            system_prompt = (
                f"你是专业译者, 把用户消息中的{src}翻译成{dst}。"
                '用户消息的每一行都以"数字. "开头(如"0. 原文"), '
                '你必须逐行翻译, 输出时保留每一行的"数字. "前缀和原有行序, '
                "一行对应一行, 不合并、不拆分、不遗漏、不删号, "
                "不输出任何解释或额外内容, 翻译不出来的行原样保留。"
                "根据前文上下文保持译文连贯性。"
            )
            user_content = f"前文译文:\n{context_text}\n\n待翻译:\n{text}" if context_text else text
            msg = self.session.post(
                f"{url}/session/{sid}/message",
                json={
                    "system": system_prompt,
                    "parts": [{"type": "text", "text": user_content}],
                },
                auth=auth,
                timeout=timeout,
            )
            msg.raise_for_status()
            data = msg.json()
            parts = data.get("parts") or []
            content = "\n".join(
                p.get("text", "")
                for p in parts
                if isinstance(p, dict) and p.get("type") == "text" and p.get("text")
            ).strip()
            if not content:
                raise RuntimeError(f"opencode 空回复: {str(data)[:120]}")
            return content
        finally:
            try:
                self.session.delete(
                    f"{url}/session/{sid}", auth=auth, timeout=10
                )
            except Exception:
                pass

    @staticmethod
    def _parse_batch(content, expected):
        """解析批量翻译结果 (支持 JSON/编号列表/纯文本格式)"""
        if not content:
            return [""] * expected
        items = {}
        try:
            data = json.loads(content)
            if isinstance(data, list):
                for i, item in enumerate(data):
                    items[i] = str(item).strip()
            elif isinstance(data, dict):
                for key, value in data.items():
                    try:
                        items[int(key)] = str(value).strip()
                    except (ValueError, TypeError):
                        for idx, v in enumerate(value):
                            items[idx] = str(v).strip()
        except json.JSONDecodeError:
            for match in re.finditer(r"^\s*(\d+)[\.:、]\s*(.+)$", content, re.MULTILINE):
                items[int(match.group(1))] = match.group(2).strip()
            if not items:
                parts = [p.strip() for p in content.splitlines() if p.strip()]
                for idx, part in enumerate(parts):
                    items[idx] = part
        return [items.get(i, "").strip() for i in range(expected)]
