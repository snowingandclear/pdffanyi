import json
import random
import re
import time

import requests

YOUDAO_URL = "https://aidemo.youdao.com/trans"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
REQUEST_GAP = 1.5


class Translator:
    def __init__(self, api_key="", base_url="", model=""):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA})
        self._last_request = 0.0

    @staticmethod
    def _is_limited(status):
        return status in ("103", "411", "429")

    def _translate_text(self, text, src="ja", dst="zh-CHS"):
        for attempt in range(4):
            gap = REQUEST_GAP - (time.time() - self._last_request)
            if gap > 0:
                time.sleep(gap)
            try:
                resp = self.session.post(
                    YOUDAO_URL,
                    data={"q": text, "from": src, "to": dst},
                    timeout=30,
                )
                self._last_request = time.time()
                data = resp.json()
                if isinstance(data, dict) and data.get("translation"):
                    return data["translation"][0].strip()
                status = data.get("errorCode") if isinstance(data, dict) else ""
                if self._is_limited(status):
                    print(f"[translate] 限流({status}), 等待 {4 * (attempt + 1)}s...")
                    time.sleep(4 * (attempt + 1))
                    continue
                raise RuntimeError(f"有道接口异常: {str(data)[:80]}")
            except Exception as e:
                print(f"[translate] retry {attempt + 1}: {str(e)[:80]}")
                time.sleep(2 ** attempt)
        return ""

    def translate_lines(self, lines):
        if not lines:
            return []
        translated = []
        current_batch = []
        current_len = 0
        for line in lines:
            if current_len + len(line) > 800 and current_batch:
                translated.extend(self._translate_batch(current_batch))
                current_batch = []
                current_len = 0
            current_batch.append(line)
            current_len += len(line)
        if current_batch:
            translated.extend(self._translate_batch(current_batch))
        return translated

    def _translate_batch(self, lines):
        payload = "\n".join(f"{i}. {text}" for i, text in enumerate(lines))
        try:
            content = self._translate_text(payload)
        except Exception as e:
            print(f"[translate] batch failed: {str(e)[:80]}")
            return [""] * len(lines)
        return self._parse_batch(content, len(lines))

    @staticmethod
    def _parse_batch(content, expected):
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