#!/usr/bin/env python3
"""send_feishu.py — upload video to Feishu and send as media + text"""
import json, os, sys, re, urllib.request, urllib.error

ENV_PATH = os.path.expanduser("~/.hermes/.env")
CHAT_ID = "REDACTED"
BASE = "https://open.feishu.cn/open-apis"


def load_env():
    env = {}
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k] = v.strip().strip('"').strip("'")
    return env


def api(method, path, t, body=None):
    """Call Feishu API, return parsed JSON."""
    url = BASE + path
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json; charset=utf-8")
    req.add_header("Authorization", "Bearer " + t)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())


def upload_file(t, video_path):
    """Upload file via multipart to Feishu."""
    boundary = "----FeishuUpload"
    fname = os.path.basename(video_path)
    with open(video_path, "rb") as f:
        file_data = f.read()

    body = (
        f"--{boundary}\r\n"
        f"Content-Disposition: form-data; name=\"file_type\"\r\n\r\n"
        f"mp4\r\n"
        f"--{boundary}\r\n"
        f"Content-Disposition: form-data; name=\"file_name\"\r\n\r\n"
        f"{fname}\r\n"
        f"--{boundary}\r\n"
        f"Content-Disposition: form-data; name=\"file\"; filename=\"{fname}\"\r\n"
        f"Content-Type: video/mp4\r\n\r\n"
    ).encode() + file_data + f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(BASE + "/im/v1/files", data=body, method="POST")
    req.add_header("Authorization", "Bearer " + t)
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    with urllib.request.urlopen(req) as resp:
        r = json.loads(resp.read())
    if r["code"] != 0:
        print(f"Upload failed: {r}", file=sys.stderr)
        sys.exit(1)
    return r["data"]["file_key"]


def send_msg(t, msg_type, content_dict):
    """Send message to Feishu chat."""
    body = {
        "receive_id": CHAT_ID,
        "msg_type": msg_type,
        "content": json.dumps(content_dict)
    }
    r = api("POST", f"/im/v1/messages?receive_id_type=chat_id", t, body)
    if r["code"] != 0:
        print(f"Send {msg_type} failed: {r}", file=sys.stderr)
        sys.exit(1)
    print(f"{msg_type} sent: {r['data']['message_id']}")


def extract_date(fname):
    m = re.match(r'(\d{4}-\d{2}-\d{2})', fname)
    return m.group(1) if m else None


def build_caption(session, date_str):
    from datetime import datetime, date
    if date_str:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
    else:
        dt = date.today()
    label = "午盘" if session == "morning" else "收盘"
    return (
        f"{dt.month}月{dt.day}日{label}市场数据统计\n\n"
        f"公开市场数据整理，仅供信息参考。\n\n"
        f"⚠️ 数据来源东方财富，不构成任何投资建议。市场有风险，投资需谨慎。"
    )


def main():
    if len(sys.argv) < 2:
        print("Usage: send_feishu.py <video_path> [morning|afternoon]", file=sys.stderr)
        sys.exit(1)

    video_path = sys.argv[1]
    session = sys.argv[2] if len(sys.argv) > 2 else "morning"
    if not os.path.exists(video_path):
        print(f"Video not found: {video_path}", file=sys.stderr)
        sys.exit(1)

    fname = os.path.basename(video_path)
    date_str = extract_date(fname)
    env = load_env()

    # Get tenant access token
    r = api("POST", "/auth/v3/tenant_access_token/internal", "", {
        "app_id": env["FEISHU_APP_ID"],
        "app_secret": env["FEISHU_APP_SECRET"]
    })
    t = r["tenant_access_token"]

    size_mb = os.path.getsize(video_path) / 1024 / 1024
    print(f"Uploading {fname} ({size_mb:.1f}MB)...")
    file_key = upload_file(t, video_path)

    send_msg(t, "media", {"file_key": file_key})
    caption = build_caption(session, date_str)
    send_msg(t, "text", {"text": caption})
    print("Done.")


if __name__ == "__main__":
    main()
