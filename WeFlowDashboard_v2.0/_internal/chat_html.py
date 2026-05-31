#!/usr/bin/env python3
"""
WeFlow 聊天记录 HTML 解析器
============================
将原始 JSON 消息转换为可读的 HTML 格式，包含：
  - 消息类型图标 (文本/图片/语音/视频/链接/系统)
  - 语音消息标记
  - 美观的时间线排版
  - 会话统计信息

用法：
  python chat_html.py <session_name> <date>     # 输出到 stdout
  python chat_html.py <session_name> <date> -o   # 保存到 reports/
"""

import json, re, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

CHINA_TZ = timezone(timedelta(hours=8), "CST")
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
OUT_DIR = BASE_DIR / "reports"

# 消息类型 → 图标/颜色
TYPE_STYLES = {
    1:       ("💬", "#1a73e8", "文本"),
    3:       ("🖼️", "#e67e22", "图片"),
    34:      ("🎤", "#9b59b6", "语音"),
    43:      ("🎬", "#e74c3c", "视频"),
    47:      ("😊", "#f39c12", "表情"),
    49:      ("📎", "#2ecc71", "应用"),
    10000:   ("🔔", "#95a5a6", "系统"),
    10002:   ("🔔", "#95a5a6", "系统"),
    21474836529: ("📰", "#1abc9c", "文章"),
}


def extract_text_from_xml(content):
    """从 XML 提取可读文本"""
    if not content or not content.startswith("<?xml"):
        return content, "text"
    sys_match = re.search(r"<sysmsg[^>]*>.*?<content>(.*?)</content>", content, re.DOTALL)
    if sys_match: return sys_match.group(1).strip(), "system"
    title_match = re.search(r"<title>(.*?)</title>", content, re.DOTALL)
    if title_match:
        title = title_match.group(1).strip()
        desc_match = re.search(r"<des>(.*?)</des>", content, re.DOTALL)
        desc = desc_match.group(1).strip() if desc_match else ""
        url_match = re.search(r"<url>(.*?)</url>", content, re.DOTALL)
        url = url_match.group(1).strip() if url_match else ""
        result = f"<strong>{title}</strong>"
        if desc: result += f"<br><span style='color:#666'>{desc[:200]}</span>"
        if url: result += f"<br><a href='{url}' target='_blank' style='color:#1a73e8;font-size:12px'>查看原文 →</a>"
        return result, "link"
    return "[复杂消息]", "unknown"


def parse_message(msg, prev_date=None):
    """解析单条消息为 HTML 片段。prev_date: 上一条消息的日期字符串"""
    ts = msg.get("createTime", 0)
    dt = datetime.fromtimestamp(ts, tz=CHINA_TZ)
    time_str = dt.strftime("%m-%d %H:%M:%S")
    date_str = dt.strftime("%Y-%m-%d")  # 用于日期分割线
    local_type = msg.get("localType", 0)
    # 使用 _displayName（后端已解析）或回退 senderUsername
    sender = msg.get("_displayName") or msg.get("senderUsername", "系统消息")
    content = msg.get("parsedContent") or msg.get("content", "")
    is_send = msg.get("isSend", 0)
    media_file = msg.get("mediaFileName", "")

    # 消息类型
    icon, color, label = TYPE_STYLES.get(local_type, ("📄", "#7f8c8d", "未知"))

    # 内容解析
    if local_type == 10000 or local_type == 10002:  # 系统消息
        display_text, subtype = extract_text_from_xml(content)
        html_content = f'<span style="color:{color}">{display_text}</span>'
    elif content and content.startswith("<?xml"):
        display_text, subtype = extract_text_from_xml(content)
        if subtype == "link":
            html_content = f'<div class="link-card">{display_text}</div>'
        else:
            html_content = f'<span style="color:{color}">{display_text}</span>'
    elif local_type == 34:  # 语音
        html_content = f'<span style="color:{color}">🎤 语音消息</span>'
    elif local_type == 3:  # 图片
        html_content = f'<span style="color:{color}">🖼️ [图片]</span>'
    elif local_type == 43:  # 视频
        html_content = f'<span style="color:{color}">🎬 [视频]</span>'
    elif local_type == 47:
        html_content = f'<span style="color:{color}">😊 [表情]</span>'
    elif not content:
        html_content = f'<span style="color:#999">[{label}]</span>'
    else:
        # 文本消息
        html_content = f'<span>{content}</span>'

    # 媒体标记
    if media_file:
        if "voice" in media_file.lower():
            html_content += ' <span style="background:#f0e6ff;color:#7c3aed;font-size:11px;padding:1px 6px;border-radius:4px">🎤 有语音</span>'
        elif "image" in media_file.lower():
            html_content += ' <span style="background:#fef3c7;color:#92400e;font-size:11px;padding:1px 6px;border-radius:4px">🖼️ 有图片</span>'
        elif "video" in media_file.lower():
            html_content += ' <span style="background:#fee2e2;color:#991b1b;font-size:11px;padding:1px 6px;border-radius:4px">🎬 有视频</span>'

    align = "right" if is_send else "left"
    bg = "#dbeafe" if is_send else "#f3f4f6"
    # 日期分割线：如果日期变了就插入
    divider = ""
    if prev_date and prev_date != date_str:
        divider = f'<div class="day-divider"><span style="background:#f0f2f5;padding:0 12px">{date_str}</span></div>'
    sender_display = sender
    return f"""
    {divider}
    <div class="msg-row msg-{align}">
      <div class="msg-time">{time_str} · <span class="msg-sender">{sender_display}</span></div>
      <div class="msg-bubble" style="background:{bg};text-align:{align}">
        <div class="msg-icon" style="color:{color}">{icon}</div>
        <div class="msg-text">{html_content}</div>
      </div>
    </div>
    """


def build_html(session_name, date_str, messages, output_path=None):
    """生成完整 HTML 聊天记录页面"""
    # 统计
    total = len(messages)
    types_count = {}
    sender_count = {}
    for m in messages:
        lt = m.get("localType", 0)
        types_count[lt] = types_count.get(lt, 0) + 1
        s = m.get("senderUsername", "system")
        sender_count[s] = sender_count.get(s, 0) + 1

    top_senders = sorted(sender_count.items(), key=lambda x: x[1], reverse=True)[:5]
    type_summary = " · ".join(
        f"{TYPE_STYLES.get(t, ('','','未知'))[0]} {TYPE_STYLES.get(t, ('','',str(t)))[2]} {c}条"
        for t, c in sorted(types_count.items(), key=lambda x: x[1], reverse=True)
    )

    # 消息列表（带日期分割线）
    msg_html = ""
    prev_date = None
    for m in messages:
        msg_html += parse_message(m, prev_date)
        ts = m.get("createTime", 0)
        prev_date = datetime.fromtimestamp(ts, tz=CHINA_TZ).strftime("%Y-%m-%d")

    page = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>聊天记录 — {session_name} {date_str}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ background: #f0f2f5; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; color: #1a1a2e; }}
  .header {{ background: linear-gradient(135deg, #1a73e8, #0d47a1); color: white; padding: 32px 24px; }}
  .header h1 {{ font-size: 22px; font-weight: 600; margin-bottom: 4px; }}
  .header .meta {{ font-size: 13px; opacity: .8; }}
  .stats {{ display: flex; gap: 16px; padding: 16px 24px; background: white; border-bottom: 1px solid #e5e7eb; flex-wrap: wrap; }}
  .stat-item {{ text-align: center; min-width: 70px; }}
  .stat-num {{ font-size: 20px; font-weight: 700; color: #1a73e8; }}
  .stat-label {{ font-size: 11px; color: #6b7280; margin-top: 2px; }}
  .type-summary {{ padding: 12px 24px; background: white; font-size: 13px; color: #4b5563; border-bottom: 1px solid #e5e7eb; }}
  .chat-container {{ max-width: 720px; margin: 0 auto; padding: 16px; }}
  .msg-row {{ display: flex; flex-direction: column; margin-bottom: 12px; }}
  .msg-left {{ align-items: flex-start; }}
  .msg-right {{ align-items: flex-end; }}
  .msg-time {{ font-size: 11px; color: #9ca3af; margin: 0 4px 4px; }}
  .msg-left .msg-time {{ text-align: left; }}
  .msg-right .msg-time {{ text-align: right; }}
  .msg-bubble {{ max-width: 85%; padding: 10px 14px; border-radius: 12px; display: inline-flex; align-items: flex-start; gap: 8px; font-size: 14px; line-height: 1.5; }}
  .msg-left .msg-bubble {{ border-bottom-left-radius: 4px; }}
  .msg-right .msg-bubble {{ border-bottom-right-radius: 4px; }}
  .msg-icon {{ font-size: 16px; flex-shrink: 0; margin-top: 1px; }}
  .msg-sender {{ font-size: 11px; color: #6b7280; }}
  .msg-text a {{ color: #1a73e8; text-decoration: none; }}
  .msg-text a:hover {{ text-decoration: underline; }}
  .link-card {{ background: white; border: 1px solid #e5e7eb; border-radius: 8px; padding: 8px 12px; font-size: 13px; max-width: 350px; }}
  .day-divider {{ text-align: center; color: #9ca3af; font-size: 12px; margin: 24px 0; position: relative; }}
  .day-divider::before, .day-divider::after {{ content: ''; position: absolute; top: 50%; width: 30%; height: 1px; background: #e5e7eb; }}
  .day-divider::before {{ left: 0; }}
  .day-divider::after {{ right: 0; }}
  .footer {{ text-align: center; padding: 24px; color: #9ca3af; font-size: 12px; }}
</style>
</head>
<body>
  <div class="header">
    <h1>💬 {session_name}</h1>
    <div class="meta">📅 {date_str} · 共 {total} 条消息 · 生成于 {datetime.now(CHINA_TZ).strftime('%Y-%m-%d %H:%M')}</div>
  </div>

  <div class="stats">
    <div class="stat-item"><div class="stat-num">{total}</div><div class="stat-label">总消息</div></div>
    <div class="stat-item"><div class="stat-num">{len(types_count)}</div><div class="stat-label">消息类型</div></div>
    <div class="stat-item"><div class="stat-num">{len(sender_count)}</div><div class="stat-label">发言者</div></div>
    """
    for sender, cnt in top_senders:
        short = sender[-8:] if len(sender) > 10 else sender
        page += f'<div class="stat-item"><div class="stat-num" style="font-size:14px">{cnt}</div><div class="stat-label" style="font-size:10px">{short}</div></div>'

    page += f"""</div>

  <div class="type-summary">📊 {type_summary}</div>

  <div class="chat-container">
    {msg_html}
  </div>

  <div class="footer">
    由 WeFlow 监控系统自动生成 · 数据本地存储 · {date_str}
  </div>
</body>
</html>
"""
    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(page, encoding="utf-8")
        print(f"✅ HTML 已保存: {output_path}")
    else:
        print(page)

    return page


def main():
    if len(sys.argv) < 3:
        print("用法: python chat_html.py <session_name> <date> [-o]")
        print("示例: python chat_html.py 小虾秘 2026-05-15")
        print("      python chat_html.py 小虾秘 2026-05-15 -o")
        sys.exit(1)

    session_name = sys.argv[1]
    date_str = sys.argv[2]
    save_file = len(sys.argv) > 3 and sys.argv[3] == "-o"

    # 查找数据文件
    session_dir = DATA_DIR / session_name
    if not session_dir.exists():
        # 尝试模糊匹配
        candidates = [d for d in DATA_DIR.iterdir() if d.is_dir() and session_name in d.name]
        if candidates:
            session_dir = candidates[0]
        else:
            print(f"❌ 找不到会话目录: {session_name}")
            sys.exit(1)

    data_file = session_dir / f"{date_str}.json"
    if not data_file.exists():
        print(f"❌ 找不到数据文件: {data_file}")
        # 列出可用日期
        files = sorted(session_dir.glob("*.json"))
        if files:
            print(f"   可用日期: {', '.join(f.stem for f in files[:10])}")
        sys.exit(1)

    msgs = json.loads(data_file.read_text("utf-8"))
    safe_name = session_dir.name

    if save_file:
        output = OUT_DIR / f"chat_{safe_name}_{date_str}.html"
    else:
        output = None

    build_html(safe_name, date_str, msgs, output)


if __name__ == "__main__":
    main()
