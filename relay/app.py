#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Alertmanager → 飞书群机器人 告警中继。

只用标准库，无第三方依赖。职责：
1. 接收 Alertmanager 的 webhook POST；
2. 按 (fingerprint, status) 做时间窗去重，避免同一告警在 repeat_interval 内反复推送；
3. 组装飞书「消息卡片」并发送；未配置 webhook 时只打印日志，方便本地调试。

环境变量：
    FEISHU_WEBHOOK   飞书群机器人地址，留空则不外发
    DEDUP_WINDOW     去重窗口秒数，默认 300
    PORT             监听端口，默认 8088
"""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WEBHOOK = os.environ.get("FEISHU_WEBHOOK", "").strip()
DEDUP_WINDOW = int(os.environ.get("DEDUP_WINDOW", "300"))
PORT = int(os.environ.get("PORT", "8088"))

# fingerprint -> 最近一次发送时间
_seen = {}
_lock = threading.Lock()

SEVERITY_TEXT = {
    "critical": ("严重", "red"),
    "warning": ("警告", "orange"),
    "info": ("提示", "blue"),
}


def dedup(fingerprint):
    """窗口内已发送过则返回 False。"""
    now = time.time()
    with _lock:
        # 顺手清理过期条目，避免长时间运行后字典无限增长
        expired = [k for k, ts in _seen.items() if now - ts > DEDUP_WINDOW]
        for k in expired:
            _seen.pop(k, None)
        if fingerprint in _seen:
            return False
        _seen[fingerprint] = now
        return True


def build_card(payload):
    alerts = payload.get("alerts") or []
    if not alerts:
        return None

    firing = [a for a in alerts if a.get("status") == "firing"]
    resolved = [a for a in alerts if a.get("status") == "resolved"]
    status = "firing" if firing else "resolved"

    lines = []
    for a in alerts[:8]:
        labels = a.get("labels", {})
        ann = a.get("annotations", {})
        name = labels.get("alertname", "unknown")
        sev = labels.get("severity", "info")
        sev_cn, _ = SEVERITY_TEXT.get(sev, ("提示", "blue"))
        summary = ann.get("summary", "")
        desc = ann.get("description", "")
        head = "🔥" if a.get("status") == "firing" else "✅"
        lines.append(f"{head} **[{sev_cn}] {name}**")
        if summary:
            lines.append(summary)
        if desc:
            lines.append(f"<font color='grey'>{desc}</font>")
        lines.append("")

    title = f"告警通知 · {len(firing)} 触发 / {len(resolved)} 恢复"
    color = "red" if firing else "green"

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": color,
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}},
                {
                    "tag": "note",
                    "elements": [
                        {
                            "tag": "plain_text",
                            "content": f"状态: {status} · 接收时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
                        }
                    ],
                },
            ],
        },
    }


def send(card):
    if not WEBHOOK:
        print("[relay] 未配置 FEISHU_WEBHOOK，仅打印：", flush=True)
        print(json.dumps(card, ensure_ascii=False)[:800], flush=True)
        return True

    body = json.dumps(card, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        WEBHOOK,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            text = resp.read().decode("utf-8", "replace")
        # 飞书成功返回 {"code":0,...}
        try:
            code = json.loads(text).get("code")
        except json.JSONDecodeError:
            code = None
        if code not in (0, None):
            print(f"[relay] 飞书返回异常: {text}", flush=True)
            return False
        print("[relay] 发送成功", flush=True)
        return True
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"[relay] 发送失败: {exc}", flush=True)
        return False


class Handler(BaseHTTPRequestHandler):
    server_version = "alert-relay/1.0"

    def log_message(self, fmt, *args):  # 精简访问日志
        sys.stderr.write("[http] %s\n" % (fmt % args))

    def _json(self, code, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/healthz":
            self._json(200, {"status": "ok", "dedup_window": DEDUP_WINDOW})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/webhook":
            self._json(404, {"error": "not found"})
            return

        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json(400, {"error": "invalid json"})
            return

        alerts = payload.get("alerts") or []
        if alerts:
            fresh = []
            for alert in alerts:
                fp = alert.get("fingerprint") or str(alert.get("labels"))
                key = f"{fp}:{alert.get('status')}"
                if dedup(key):
                    fresh.append(alert)
                else:
                    print(f"[relay] 去重命中: {key}", flush=True)
            if not fresh:
                self._json(200, {"status": "deduped"})
                return
            payload = dict(payload)
            payload["alerts"] = fresh

        card = build_card(payload)
        if card is None:
            self._json(200, {"status": "ignored"})
            return

        ok = send(card)
        self._json(200 if ok else 502, {"status": "sent" if ok else "failed"})


def main():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[relay] listening on :{PORT}, dedup_window={DEDUP_WINDOW}s", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
