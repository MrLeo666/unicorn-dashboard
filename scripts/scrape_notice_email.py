#!/usr/bin/env python3
"""
scrape_notice_email.py
======================
拉取 leo.lai@lqpacific.com 邮箱中最新的一封 Notice Weekly Update 邮件
（发件人 notifications@notice.co，subject "Notice Weekly Update MM/DD/YY"），
解析 plaintext body 里的 "My watchlist" 区块，写入 data/notice_secondary.json。

依赖
----
- google-api-python-client, google-auth, google-auth-oauthlib, google-auth-httplib2

GitHub Action 环境变量（Secrets 配置）
---------------------------------------
- GMAIL_CLIENT_ID         OAuth client ID
- GMAIL_CLIENT_SECRET     OAuth client secret
- GMAIL_REFRESH_TOKEN     用户授权后获得的 refresh_token
- GMAIL_ACCOUNT           可选；默认 'me'（即 token 对应的账号）

本地一次性获取 refresh_token
---------------------------
1. Google Cloud Console 创建 OAuth Client（Desktop App 类型）
2. 下载 credentials.json
3. 本地跑（仅一次）：
     pip install google-auth-oauthlib
     python -c "
       from google_auth_oauthlib.flow import InstalledAppFlow
       flow = InstalledAppFlow.from_client_secrets_file(
         'credentials.json', ['https://www.googleapis.com/auth/gmail.readonly'])
       creds = flow.run_local_server(port=0)
       print('refresh_token:', creds.refresh_token)"
4. 把 refresh_token 存到 GitHub Secrets

输出
----
data/notice_secondary.json — 结构见 _schema_hint
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# Google API SDK
try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
except ImportError:
    print("ERR: missing google-api-python-client. Install: pip install google-api-python-client google-auth google-auth-oauthlib", file=sys.stderr)
    sys.exit(1)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
NOTICE_FILE = DATA / "notice_secondary.json"

# 公司名规整：Notice → unicorns.json 标准名
COMPANY_ALIASES = {
    "Bytedance (TikTok)": "ByteDance",
    "OpenAI (ChatGPT)": "OpenAI",
    "Anduril": "Anduril Industries",
    "Cognition": "Cognition AI",
}


def get_gmail_service():
    """Returns an authenticated Gmail API service."""
    cid = os.environ.get("GMAIL_CLIENT_ID")
    csec = os.environ.get("GMAIL_CLIENT_SECRET")
    rtok = os.environ.get("GMAIL_REFRESH_TOKEN")
    if not (cid and csec and rtok):
        print("ERR: missing GMAIL_CLIENT_ID/GMAIL_CLIENT_SECRET/GMAIL_REFRESH_TOKEN env vars.", file=sys.stderr)
        sys.exit(2)
    creds = Credentials(
        token=None,
        refresh_token=rtok,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=cid,
        client_secret=csec,
        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
    )
    creds.refresh(Request())
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def find_latest_weekly_update(service, user_id: str = "me"):
    """Find the most recent Notice Weekly Update email; return (msg_id, internal_date)."""
    query = 'from:notifications@notice.co subject:"Weekly Update"'
    res = service.users().messages().list(userId=user_id, q=query, maxResults=5).execute()
    msgs = res.get("messages", [])
    if not msgs:
        print("WARN: no Notice Weekly Update found.")
        return None
    # First result = most recent
    return msgs[0]["id"]


def get_message_plaintext(service, msg_id: str, user_id: str = "me") -> str:
    """Fetch the plaintext body of a Gmail message."""
    msg = service.users().messages().get(userId=user_id, id=msg_id, format="full").execute()
    payload = msg["payload"]

    def find_plain(part):
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            return part["body"]["data"]
        for sub in part.get("parts", []) or []:
            out = find_plain(sub)
            if out:
                return out
        return None

    data = find_plain(payload)
    if not data:
        return ""
    return base64.urlsafe_b64decode(data + "===").decode("utf-8", errors="replace")


def parse_watchlist(plaintext: str) -> dict:
    """Parse the 'My watchlist' block.
    Returns dict { company: {pps, pps_change, valuation_b, new_indications, date} }."""
    # 删 URL（避免干扰正则）
    clean = re.sub(r"\(\s*https://[^\)]+\)", "", plaintext)
    clean = re.sub(r"https://\S+", "", clean)
    # 删零宽字符
    clean = re.sub(r"[ ͏؜ᅟᅠ឴឵᠎​-‏‪-‮⁠-⁯﻿]+", " ", clean)

    # 切到 "My watchlist" 段
    wl_match = re.search(r"My watchlist\b(.+?)(?:Full watchlist|Most active)", clean, re.DOTALL)
    if not wl_match:
        return {}
    block = wl_match.group(1)

    # 提取日期（Market summary MM/DD - MM/DD 头部，但 subject 里有 MM/DD/YY，更可靠）
    # 这里只解析 watchlist 内的公司
    records = {}
    # 每个公司一段，格式（去链接后大致）：
    #   公司名 (N new indications)
    #    $PPS  (+CHANGE)    $VALB         View
    pattern = re.compile(
        r"([A-Za-z][\w&./\- ()]+?)\s*\((\d+)\s+new\s+indications?\)\s*"
        r"\$([\d,]+\.?\d*)\s*\(([+\-]?[\d.]+)\)\s+"
        r"\$([\d,]+\.?\d*)([BMT]?)\s+View",
        re.IGNORECASE,
    )
    for m in pattern.finditer(block):
        raw_name = m.group(1).strip()
        indications = int(m.group(2))
        pps = float(m.group(3).replace(",", ""))
        change = float(m.group(4))
        val = float(m.group(5).replace(",", ""))
        unit = m.group(6).upper()
        if unit == "M":
            val /= 1000
        elif unit == "T":
            val *= 1000

        name = COMPANY_ALIASES.get(raw_name, raw_name)
        records[name] = {
            "pps": round(pps, 4),
            "pps_change": round(change, 4),
            "valuation_b": round(val, 4),
            "new_indications": indications,
        }
    return records


def parse_most_active(plaintext: str) -> list:
    """Parse the 'Most active' block — return [{company, indications}]."""
    clean = re.sub(r"\(\s*https://[^\)]+\)", "", plaintext)
    clean = re.sub(r"https://\S+", "", clean)
    m = re.search(r"Most active\b(.+?)(?:See more|Notable financings|$)", clean, re.DOTALL)
    if not m:
        return []
    block = m.group(1)
    out = []
    for mm in re.finditer(r"([A-Za-z][\w&./\- ()]+?)\s*\((\d+)\s+new\s+indications?\)", block):
        raw = mm.group(1).strip()
        out.append({
            "company": COMPANY_ALIASES.get(raw, raw),
            "indications": int(mm.group(2)),
        })
    return out


def merge_history(existing: dict, new_records: dict, date_str: str) -> dict:
    """Merge: existing records keep history; latest fields are replaced with new."""
    merged = {}
    all_companies = set(existing.keys()) | set(new_records.keys())
    for co in all_companies:
        old = existing.get(co, {}) or {}
        old_history = list(old.get("history") or [])
        new_lat = new_records.get(co)
        if new_lat:
            # 把上一次的"latest"快照推进 history（如果日期不同）
            if old.get("date") and old.get("date") != date_str:
                snap = {k: old.get(k) for k in ["pps", "pps_change", "valuation_b", "new_indications", "date"]}
                if snap.get("date"):
                    old_history.insert(0, snap)
                    old_history = old_history[:52]  # 保留一年
            merged[co] = {
                **new_lat,
                "date": date_str,
                "history": old_history,
            }
        else:
            # 这周没出现的公司 — 保留旧 latest 不动
            merged[co] = old
    return merged


def main():
    service = get_gmail_service()
    msg_id = find_latest_weekly_update(service)
    if not msg_id:
        print("No Notice Weekly Update email found; skipping update.")
        return

    msg = service.users().messages().get(userId="me", id=msg_id, format="metadata",
                                          metadataHeaders=["Subject", "Date"]).execute()
    headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
    subject = headers.get("Subject", "")
    # 从 subject 提取日期 MM/DD/YY
    sub_m = re.search(r"(\d{2})/(\d{2})/(\d{2})", subject)
    if sub_m:
        mm, dd, yy = sub_m.groups()
        yyyy = "20" + yy
        date_str = f"{yyyy}-{mm}-{dd}"
    else:
        # fallback to internal Gmail date
        date_str = datetime.utcfromtimestamp(int(msg["internalDate"]) / 1000).strftime("%Y-%m-%d")

    plaintext = get_message_plaintext(service, msg_id)
    records = parse_watchlist(plaintext)
    most_active = parse_most_active(plaintext)

    if not records:
        print(f"WARN: parsed 0 companies from Weekly Update {date_str}. Subject: {subject}")
        # 仍 exit 0 以便 cron 不报失败
        return

    # 读现有 file，merge history
    existing = {}
    if NOTICE_FILE.exists():
        try:
            existing = json.loads(NOTICE_FILE.read_text())
        except Exception:
            existing = {}

    merged_records = merge_history(existing.get("records", {}), records, date_str)

    out = {
        "as_of": date_str,
        "period": existing.get("period", ""),
        "source": "Notice Weekly Update email (notifications@notice.co)",
        "extracted_at": datetime.utcnow().isoformat() + "Z",
        "extraction_method": "scripts/scrape_notice_email.py via GitHub Action",
        "notes": "每周一封 Weekly Update 邮件覆盖 watchlist 全集。公司名已规整。",
        "records": merged_records,
        "most_active": most_active,
        "_schema_hint": existing.get("_schema_hint", {}),
    }
    NOTICE_FILE.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(f"OK: wrote {NOTICE_FILE.name} — {len(records)} companies in latest week ({date_str}).")
    for co, r in records.items():
        chg = r["pps_change"]
        print(f"  {co:24s} ${r['pps']:8.2f} ({chg:+.2f})  ${r['valuation_b']:7.2f}B  · {r['new_indications']} indications")


if __name__ == "__main__":
    main()
