#!/usr/bin/env python3
"""
Minimal Feishu IM client helpers shared by travel automation scripts.
"""
from __future__ import annotations

import json
import os
import warnings
from typing import Any

import requests
from urllib3.exceptions import NotOpenSSLWarning

from runtime_config import load_project_env, require_env


warnings.filterwarnings("ignore", category=NotOpenSSLWarning)

load_project_env()

APP_ID = require_env("FEISHU_APP_ID")
APP_SECRET = require_env("FEISHU_APP_SECRET")
USER_OPEN_ID = require_env("FEISHU_USER_OPEN_ID")

BASE_HEADERS = {"Content-Type": "application/json"}


def get_tenant_token() -> str:
    resp = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": APP_ID, "app_secret": APP_SECRET},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取 token 失败: {data.get('msg')}")
    return data["tenant_access_token"]


def _auth_headers(token: str) -> dict[str, str]:
    headers = dict(BASE_HEADERS)
    headers["Authorization"] = f"Bearer {token}"
    return headers


def send_text_message(token: str, receive_id: str, content: str, receive_id_type: str = "open_id") -> dict[str, Any]:
    payload = {
        "receive_id": receive_id,
        "msg_type": "text",
        "content": json.dumps({"text": content}),
    }
    resp = requests.post(
        f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type}",
        headers=_auth_headers(token),
        json=payload,
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"发送消息失败: {data.get('msg')}")
    return data


def reply_text_message(token: str, message_id: str, content: str) -> dict[str, Any]:
    payload = {
        "msg_type": "text",
        "content": json.dumps({"text": content}),
    }
    resp = requests.post(
        f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reply",
        headers=_auth_headers(token),
        json=payload,
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"回复消息失败: {data.get('msg')}")
    return data


def list_chat_messages(
    token: str,
    chat_id: str,
    *,
    start_time: int | None = None,
    end_time: int | None = None,
    sort_type: str = "ByCreateTimeAsc",
    page_size: int = 50,
    page_token: str | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "container_id_type": "chat",
        "container_id": chat_id,
        "sort_type": sort_type,
        "page_size": page_size,
    }
    if start_time is not None:
        params["start_time"] = start_time
    if end_time is not None:
        params["end_time"] = end_time
    if page_token:
        params["page_token"] = page_token

    resp = requests.get(
        "https://open.feishu.cn/open-apis/im/v1/messages",
        headers=_auth_headers(token),
        params=params,
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取会话历史消息失败: {data.get('msg')}")
    return data


def parse_text_content(message: dict[str, Any]) -> str | None:
    body = message.get("body", {})
    raw_content = body.get("content")
    if not raw_content:
        return None
    try:
        parsed = json.loads(raw_content)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        return parsed.get("text")
    return None
