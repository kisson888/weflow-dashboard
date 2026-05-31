#!/usr/bin/env python3
"""
WeFlow 微信聊天记录增量导出监控脚本
======================================

新增功能：
  - 全量备份：会话可配置 full_backup_before 日期，在该日期前全量导出一次
  - 增量备份：全量导出后自动切换到增量模式
  - 按间隔导出：支持 backup_interval_hours 避免过于频繁

命令：
  python weflow_monitor.py                    # 默认：增量导出
  python weflow_monitor.py --catchup N        # 回捞最近 N 天
  python weflow_monitor.py --sessions          # 列出所有微信会话
  python weflow_monitor.py --status            # 查看导出状态
  python weflow_monitor.py --summary [YYYY-MM-DD]  # 生成日报
"""

import json, os, re, sys, time, socket
import urllib.parse, urllib.request, urllib.error, ssl
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

CONFIG_FILE = Path(__file__).parent / "config.json"
# PyInstaller 兼容：被 importlib 从 _internal/ 加载时，config.json 在 exe 同级目录
if not CONFIG_FILE.exists():
    CONFIG_FILE = Path(__file__).parent.parent / "config.json"
# 开发模式：项目根目录
if not CONFIG_FILE.exists() and Path(__file__).parent.name != "weflow-monitor":
    for p in [Path(__file__).parent, Path(__file__).parent.parent]:
        if (p / "weflow_monitor.py").exists() and (p / "config.json").exists():
            CONFIG_FILE = p / "config.json"
            break
CHINA_TZ = timezone(timedelta(hours=8), "CST")

# 全局
config = {}
api_base_url = "http://127.0.0.1:5031"
access_token = ""
data_dir = Path(".")
report_dir = Path(".")
checkpoint_file = Path(".")

def load_config():
    global config, api_base_url, access_token, data_dir, report_dir, checkpoint_file
    if not CONFIG_FILE.exists():
        print(f"[ERROR] 配置文件不存在: {CONFIG_FILE}"); sys.exit(1)
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)
    api_base_url = config.get("api_base_url", "http://127.0.0.1:5031")
    access_token = config.get("access_token", "")
    # 路径：如果是相对路径，基于 CONFIG_FILE 所在目录解析
    cfg_base = CONFIG_FILE.parent
    raw_data = config.get("data_dir", "data")
    p_data = Path(raw_data)
    data_dir = p_data if p_data.is_absolute() else (cfg_base / raw_data)
    raw_report = config.get("report_dir", "reports")
    p_report = Path(raw_report)
    report_dir = p_report if p_report.is_absolute() else (cfg_base / raw_report)
    raw_cp = config.get("checkpoint_file", "checkpoint.json")
    p_cp = Path(raw_cp)
    checkpoint_file = p_cp if p_cp.is_absolute() else (cfg_base / raw_cp)
    data_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    load_contacts()

# ======== API ========
def api_get(path, params=None, retries=3, timeout=12):
    """带自动重试的 API GET 请求"""
    url = f"{api_base_url}{path}"
    if params:
        q = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items() if v is not None)
        url = f"{url}?{q}"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {access_token}")
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                # 校验：返回了结果但没有 messages 字段，可能 API 未就绪
                if "success" not in data and "messages" not in data:
                    if attempt < retries:
                        print(f"  [警告] API 返回异常(attempt {attempt}/{retries})，2秒后重试...")
                        time.sleep(2)
                        continue
                return data
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            print(f"  [API Error] HTTP {e.code}: {body[:200]}")
            last_err = None
            if attempt < retries and e.code in (502, 503, 504, 429):
                print(f"  [重试] {attempt}/{retries}，等待 {attempt * 2}s...")
                time.sleep(attempt * 2)
                continue
            return None
        except (urllib.error.URLError, ConnectionResetError, socket.timeout, OSError) as e:
            print(f"  [API Error] {e}")
            last_err = e
            if attempt < retries:
                print(f"  [重试] {attempt}/{retries}，等待 {attempt * 2}s...")
                time.sleep(attempt * 2)
                continue
            return None
        except Exception as e:
            print(f"  [API Error] {e}")
            return None
    return None

def api_post(path, body=None):
    url = f"{api_base_url}{path}"
    data = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {access_token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"  [API Error] HTTP {e.code}: {body[:200]}"); return None
    except Exception as e:
        print(f"  [API Error] {e}"); return None

# ======== Checkpoint ========
def load_checkpoint():
    if checkpoint_file.exists():
        with open(checkpoint_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_checkpoint(cp):
    checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
    with open(checkpoint_file, "w", encoding="utf-8") as f:
        json.dump(cp, f, ensure_ascii=False, indent=2)

# ======== 消息处理 ========
def extract_text_from_xml(content):
    if not content or not content.startswith("<?xml"):
        return content
    sys_match = re.search(r"<sysmsg[^>]*>.*?<content>(.*?)</content>", content, re.DOTALL)
    if sys_match: return f"[系统] {sys_match.group(1).strip()}"
    title_match = re.search(r"<title>(.*?)</title>", content, re.DOTALL)
    if title_match:
        title = title_match.group(1).strip()
        desc_match = re.search(r"<des>(.*?)</des>", content, re.DOTALL)
        desc = desc_match.group(1).strip()[:100] if desc_match else ""
        result = f"[链接] {title}"
        if desc: result += f" — {desc}"
        return result
    return "[XML消息]"

def simplify_message(msg):
    create_time = msg.get("createTime", 0)
    dt = datetime.fromtimestamp(create_time, tz=CHINA_TZ)
    content = msg.get("parsedContent") or msg.get("content", "")
    local_type = msg.get("localType", 0)
    TYPE_NAMES = {1: "text", 3: "image", 34: "voice", 43: "video",
                  47: "emoji", 49: "app", 10000: "system",
                  10002: "system", 21474836529: "app-article"}
    msg_type = TYPE_NAMES.get(local_type, f"type={local_type}")

    if msg_type == "system": text_summary = extract_text_from_xml(content) or "[系统消息]"
    elif content and content.startswith("<?xml"): text_summary = extract_text_from_xml(content) or "[XML消息]"
    elif not content: text_summary = f"[{msg_type}]"
    else: text_summary = content[:300]

    return {"time": dt.strftime("%Y-%m-%d %H:%M:%S"), "timestamp": create_time,
            "sender": msg.get("senderUsername", ""), "type": msg_type,
            "content": text_summary, "isSend": msg.get("isSend", 0),
            "serverId": msg.get("serverId", ""),
            "localType": local_type, "rawContent": content}

def fetch_all_messages(talker, since_ts=None, end_ts=None):
    """分批拉取消息，支持时间范围。

    分批策略：
    1. 默认 5000 条/批
    2. 如果某批失败/空结果，自动降级（2000→500→100）重试
    3. 带自动重试和结果校验
    4. 每批输出进度供前端捕获
    """
    all_msgs = []
    offset = 0
    batch_sizes = [5000, 2000, 500, 100]  # 逐级降级
    batch_size_idx = 0
    max_empty_retries = 3
    total_retries_at_current = 0
    batch_no = 1
    consecutive_empty = 0
    max_consecutive_empty = 5  # 连续空 + hasMore=true 超过此次数视为死循环

    while batch_size_idx < len(batch_sizes):
        batch_size = batch_sizes[batch_size_idx]
        params = {"talker": talker, "limit": batch_size, "offset": offset}
        if since_ts: params["start"] = since_ts
        if end_ts: params["end"] = end_ts

        print(f"  批次 {batch_no} (offset={offset}, limit={batch_size})...")
        result = api_get("/api/v1/messages", params)

        if not result:
            # API 无响应：降级批次大小再试
            print(f"  [警告] API 无响应，降级批次 {batch_size}→{batch_sizes[batch_size_idx+1] if batch_size_idx+1 < len(batch_sizes) else batch_sizes[-1]}")
            batch_size_idx += 1
            total_retries_at_current = 0
            time.sleep(1)
            continue

        if not result.get("success"):
            err = result.get("message", "未知错误")
            print(f"  [API错误] {err}")
            # API 错误也可能需要降批次
            batch_size_idx += 1
            total_retries_at_current = 0
            time.sleep(1)
            continue

        msgs = result.get("messages", [])
        has_more = result.get("hasMore", False)

        if not msgs:
            if not has_more:
                # 正常结束了（没有更多消息）
                if len(all_msgs) == 0:
                    print(f"  [提示] 该会话暂无消息")
                else:
                    print(f"    获取完毕: 共 {len(all_msgs)} 条")
                break
            # hasMore=true 但 messages=[] — API 异常状态
            consecutive_empty += 1
            if consecutive_empty >= max_consecutive_empty:
                print(f"  [放弃] API 连续 {max_consecutive_empty} 次返回空结果但 hasMore=true")
                break
            total_retries_at_current += 1
            if total_retries_at_current >= max_empty_retries:
                print(f"  [降级] 空结果超过 {max_empty_retries} 次，降级批次 {batch_size}")
                batch_size_idx += 1
                total_retries_at_current = 0
            else:
                print(f"  [重试] hasMore=true 但无数据，等待 2s... ({total_retries_at_current}/{max_empty_retries})")
                time.sleep(2)
            continue

        # 正常接收到了消息
        consecutive_empty = 0
        total_retries_at_current = 0
        all_msgs.extend(msgs)
        offset += len(msgs)
        batch_no += 1
        print(f"    已获取 {len(all_msgs)} 条...")

        if not has_more:
            # 双重校验：如果返回数小于 batch_size，说明翻到底了
            if len(msgs) < batch_size:
                print(f"    获取完毕: 共 {len(all_msgs)} 条")
                break
            # hasMore=false 但返回满了 batch_size，可能还有更多，继续尝试
            pass

        time.sleep(0.3)  # 批次间隔，避免 API 压力过大

    if not all_msgs and batch_size_idx >= len(batch_sizes):
        print(f"  [放弃] 所有批次大小均尝试失败")

    print(f"    总计获取 {len(all_msgs)} 条消息 (共 {batch_no - 1} 批次)")
    return all_msgs

def save_messages_to_files(all_msgs, display_name, session_dir, is_full_backup=False, force_overwrite=False, export_prefix="定时导出", talker=None):
    """将消息按日期分组保存（自动定时导出模式）
    定时导出：合并为一个文件（定时导出_YYYY-MM-DD_HH-MM.json）
    手动导出：按每日日期拆分
    """
    # 解析发送者名称
    if talker:
        enrich_messages_with_names(all_msgs, talker)

    # 定时导出模式：合并为一个文件
    if "定时" in export_prefix or "全量备份" in export_prefix:
        now_str = datetime.now(CHINA_TZ).strftime("%Y-%m-%d_%H-%M")
        file_path = session_dir / f"{export_prefix}_{now_str}.json"
        if file_path.exists():
            existing = json.loads(file_path.read_text("utf-8"))
            existing_ids = {m.get("serverId", "") for m in existing}
            new_only = [m for m in all_msgs if m.get("serverId", "") not in existing_ids]
            if new_only:
                existing.extend(new_only)
                existing.sort(key=lambda x: x.get("createTime", 0))
                all_msgs = existing
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(all_msgs, f, ensure_ascii=False, indent=2)
        saved = len(all_msgs)
        print(f"  [{export_prefix}] {saved} 条 → {file_path.name}")
        # 自动生成 HTML（与 JSON 同目录）
        _auto_gen_html_one(session_dir, display_name, all_msgs, f"{display_name}_全量.html")
        _log_export(session_dir, export_prefix, saved, display_name)
        return saved

    # 手动/全量导出模式：按日期拆分
    daily = {}
    for m in all_msgs:
        ts = m.get("createTime", 0)
        day = datetime.fromtimestamp(ts, tz=CHINA_TZ).strftime("%Y-%m-%d")
        daily.setdefault(day, []).append(m)

    saved = 0
    for day_key in sorted(daily.keys()):
        file_path = session_dir / f"{day_key}.json"
        day_msgs = daily[day_key]

        if file_path.exists() and not is_full_backup and not force_overwrite:
            existing = json.loads(file_path.read_text("utf-8"))
            existing_ids = {m.get("serverId", "") for m in existing}
            new_only = [m for m in day_msgs if m.get("serverId", "") not in existing_ids]
            if not new_only:
                print(f"  {day_key}: 已存在（{len(existing)} 条），跳过")
                continue
            existing.extend(new_only)
            existing.sort(key=lambda x: x.get("createTime", 0))
            day_msgs = existing

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(day_msgs, f, ensure_ascii=False, indent=2)
        saved += len(day_msgs)
        print(f"  {day_key}: {len(day_msgs)} 条 → {file_path.name}")

    # 自动生成 HTML（与 JSON 同目录）
    if saved > 0:
        _auto_gen_html_one(session_dir, display_name, all_msgs, f"{display_name}_全量.html")
        # 记录导出日志
        _log_export(session_dir, "手动导出" if "手动" in export_prefix else "定时导出", saved, display_name)

    return saved

def _log_export(session_dir, export_type, count, display_name="", since_ts=None, end_ts=None):
    """记录导出操作到 export_log.json"""
    log_file = session_dir / "export_log.json"
    log = []
    if log_file.exists():
        try: log = json.loads(log_file.read_text("utf-8"))
        except: pass
    entry = {
        "time": datetime.now(CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "type": export_type,
        "count": count
    }
    if display_name:
        entry["session"] = display_name
    if since_ts:
        entry["start"] = datetime.fromtimestamp(since_ts, tz=CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S") if since_ts else ""
    if end_ts:
        entry["end"] = datetime.fromtimestamp(end_ts, tz=CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S") if end_ts else ""
    log.append(entry)
    log_file.write_text(json.dumps(log[-100:], ensure_ascii=False, indent=2), "utf-8")

def save_manual_export(all_msgs, display_name, session_dir, export_type, talker=None, checkpoint_before=None, checkpoint_after=None, since_ts=None, end_ts=None):
    """手动导出：生成单文件 [导出类型]_日期-时间戳.json，附带同名HTML"""
    now = datetime.now(CHINA_TZ)
    timestamp = now.strftime("%Y-%m-%d_%H-%M-%S")
    file_name = f"{export_type}_{timestamp}.json"
    file_path = session_dir / file_name
    # 解析发送者名称
    if talker:
        enrich_messages_with_names(all_msgs, talker)
    export_data = {
        "export_info": {
            "type": export_type, "time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "session": display_name, "count": len(all_msgs),
            "checkpoint_before": checkpoint_before,
            "checkpoint_after": checkpoint_after,
        },
        "messages": all_msgs
    }
    file_path.write_text(json.dumps(export_data, ensure_ascii=False, indent=2, default=str), "utf-8")
    print(f"  ✅ {file_name} ({len(all_msgs)}条)")
    # 生成同名HTML
    _auto_gen_html_one(session_dir, display_name, all_msgs, file_name.replace(".json", ".html"))
    # 记录导出日志（包含时间区间，从消息中反推起止时间）
    if not since_ts and all_msgs:
        # 消息最新在前，末尾 = 最早消息
        since_ts = int(all_msgs[-1].get("createTime", 0))
    if not end_ts and all_msgs:
        end_ts = int(all_msgs[0].get("createTime", 0))
    log_since = since_ts or (checkpoint_before.get("last_timestamp") if checkpoint_before else None)
    log_end = end_ts or int(now.timestamp())
    _log_export(session_dir, export_type, len(all_msgs), display_name, log_since, log_end)
    return len(all_msgs)

def _auto_gen_html_one(session_dir, session_name, all_msgs, html_filename):
    """生成单个HTML文件，存放于JSON同目录"""
    try:
        from chat_html import build_html
        out_file = session_dir / html_filename
        # 取第一条消息的日期作为显示日期
        first_day = datetime.fromtimestamp(all_msgs[0]["createTime"], tz=CHINA_TZ).strftime("%Y-%m-%d") if all_msgs else "未知"
        build_html(session_name, first_day, all_msgs, str(out_file))
        print(f"  📄 HTML: {html_filename}")
    except Exception as e:
        print(f"  ⚠️ HTML生成略过: {e}")

_resolved_names = {}

def load_contacts():
    """从 WeFlow API 拉取通讯录 + 群成员，建立 wxid → 最佳名称 映射
    优先级：备注 > 群昵称 > 微信昵称 > displayName > wxid
    """
    global _resolved_names
    _resolved_names = {}  # 重置，保证每次加载最新
    import urllib.request
    cnt_contacts = 0
    cnt_group = 0

    # 1. 加载通讯录（好友/公众号/群）
    try:
        url = f"{api_base_url}/api/v1/contacts?limit=500"
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {access_token}")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for c in data.get("contacts", []):
            uid = c.get("username", "")
            if not uid: continue
            remark = c.get("remark", "") or ""
            display = c.get("displayName", "") or ""
            nickname = c.get("nickname", "") or ""
            best = remark or display or nickname
            if best:
                _resolved_names[uid] = best
                cnt_contacts += 1
    except Exception as e:
        print(f"  ⚠️ 加载联系人失败: {e}")

    # 2. 加载所有已监控群聊的成员信息（群昵称优先）
    sessions = config.get("sessions", [])
    for s in sessions:
        talker = s.get("talker", "")
        # 只处理群聊（@chatroom 结尾）
        if not talker.endswith("@chatroom"):
            continue
        try:
            url = f"{api_base_url}/api/v1/group-members?chatroomId={urllib.parse.quote(talker)}&includeMessageCounts=0&forceRefresh=0"
            req = urllib.request.Request(url)
            req.add_header("Authorization", f"Bearer {access_token}")
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            members = data.get("members", data.get("groupMembers", []))
            if not members:
                # 也可能是直接返回数组
                if isinstance(data, list):
                    members = data
            for m in members:
                wxid = m.get("wxid", "") or m.get("username", "")
                if not wxid:
                    continue
                # 优先级：备注 > 群昵称 > 微信昵称 > displayName
                remark = m.get("remark", "") or ""
                group_nick = m.get("groupNickname", "") or ""
                nick = m.get("nickname", "") or ""
                display = m.get("displayName", "") or ""
                best = remark or group_nick or nick or display
                if best:
                    # 覆盖已有映射（群成员信息更精确）
                    old = _resolved_names.get(wxid, "")
                    if not old or old == wxid:
                        _resolved_names[wxid] = best
                        cnt_group += 1
        except Exception as e:
            print(f"  ⚠️ 加载群成员 {talker[:20]} 失败: {e}")

    # 3. 对仍然没有名称的 wxid，用自身作为 fallback
    print(f"  📇 已加载 {cnt_contacts} 联系人 + {cnt_group} 群成员 = {len(_resolved_names)} 条")

def resolve_sender_name(wxid, message=None):
    """解析发送者名称：优先 备注 > displayName > 昵称 > rawContent提取 > 友好截断。绝不显示原始wxid"""
    if not wxid or wxid == "none": return "系统消息"
    if wxid in _resolved_names: return _resolved_names[wxid]
    # 如果是可读名称（不是wxid/gh_格式），直接使用
    if not wxid.startswith("wxid_") and not wxid.startswith("gh_") and wxid.find("@") < 0:
        if len(wxid) <= 16:
            _resolved_names[wxid] = wxid
            return wxid
    # 尝试从消息 rawContent 中提取发送者显示名
    if message:
        raw = message.get("rawContent", "") or ""
        if raw:
            parts = raw.split(":\n", 1)
            if len(parts) == 2:
                maybe_name = parts[0].strip()
                if not maybe_name.startswith("wxid_") and not maybe_name.startswith("gh_"):
                    _resolved_names[wxid] = maybe_name
                    return maybe_name
    # 兜底：显示完整wxid（群成员不在通讯录中时，这是唯一标识）
    _resolved_names[wxid] = wxid
    return wxid

def enrich_messages_with_names(all_msgs, talker):
    """批量为消息解析发送者名称"""
    for m in all_msgs:
        sender = m.get("senderUsername", "")
        display = resolve_sender_name(sender, m)
        m["_displayName"] = display
    return all_msgs

def export_session(talker, display_name, since_ts=None, catchup_days=None, is_full_backup=False, save_cp=True, export_prefix="定时导出"):
    """导出单个会话的消息
    save_cp=False 时不更新checkpoint（手动增量模式用）
    export_prefix: 导出类型标记（"定时导出"/"手动导出"/"全量导出"等）
    """
    tag = "全量备份" if is_full_backup else "回捞" if catchup_days else "增量导出"
    print(f"\n{'='*55}\n  {display_name}\n  ID: {talker}\n  [{tag}]\n{'='*55}")

    if catchup_days:
        since_dt = datetime.now(CHINA_TZ) - timedelta(days=catchup_days)
        since_ts = int(since_dt.timestamp())
        print(f"  范围: 最近 {catchup_days} 天 (自 {since_dt.strftime('%Y-%m-%d')})")
    elif since_ts:
        sd = datetime.fromtimestamp(since_ts, tz=CHINA_TZ)
        print(f"  范围: 自 {sd.strftime('%Y-%m-%d %H:%M')}")
    else:
        print(f"  范围: 全部")

    all_msgs = fetch_all_messages(talker, since_ts)
    if not all_msgs:
        # 0条时自动重试一次（可能是 WeFlow API 状态未就绪）
        print(f"  [重试] 返回0条，等待 3s 后重试...")
        import time as _tm_rs
        _tm_rs.sleep(3)
        all_msgs = fetch_all_messages(talker, since_ts)
    if not all_msgs:
        print("  结果: 无新消息"); return 0

    print(f"  共获取 {len(all_msgs)} 条消息")
    safe_name = re.sub(r'[\\/:*?"<>|]', '_', display_name)
    session_dir = data_dir / safe_name
    session_dir.mkdir(parents=True, exist_ok=True)

    saved = save_messages_to_files(all_msgs, display_name, session_dir, is_full_backup, export_prefix=export_prefix, talker=talker)
    max_ts = max(m.get("createTime", 0) for m in all_msgs)
    cp = load_checkpoint()
    entry = cp.get(talker, {})
    entry.update({"display_name": display_name, "last_timestamp": max_ts, "updated_at": int(time.time())})
    if is_full_backup:
        entry["full_backup_done"] = True
        entry["full_backup_date"] = str(date.today())
    cp[talker] = entry
    if save_cp:
        save_checkpoint(cp)
    print(f"  ✅ 新增/更新 {saved} 条")

    # 自动生成 HTML
    if saved > 0:
        generate_html_for_session(safe_name, all_msgs)

    return saved

def generate_html_for_session(session_dir_name, messages):
    """为导出的消息自动生成 HTML 可读文件"""
    try:
        from chat_html import build_html
        # 按日期分组生成
        daily = {}
        for m in messages:
            ts = m.get("createTime", 0)
            day = datetime.fromtimestamp(ts, tz=CHINA_TZ).strftime("%Y-%m-%d")
            daily.setdefault(day, []).append(m)
        for day_key, day_msgs in daily.items():
            out_file = data_dir / session_dir_name / f"chat_{session_dir_name}_{day_key}.html"
            build_html(session_dir_name, day_key, day_msgs, str(out_file))
        print(f"  📄 HTML 已生成: {len(daily)} 个文件")
    except ImportError:
        # chat_html.py 不在路径中，尝试直接导入
        import importlib.util
        spec = importlib.util.spec_from_file_location("chat_html", str(Path(__file__).parent / "chat_html.py"))
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            daily = {}
            for m in messages:
                ts = m.get("createTime", 0)
                day = datetime.fromtimestamp(ts, tz=CHINA_TZ).strftime("%Y-%m-%d")
                daily.setdefault(day, []).append(m)
            for day_key, day_msgs in daily.items():
                out_file = data_dir / session_dir_name / f"chat_{session_dir_name}_{day_key}.html"
                mod.build_html(session_dir_name, day_key, day_msgs, str(out_file))
            print(f"  📄 HTML 已生成: {len(daily)} 个文件")
    except Exception as e:
        print(f"  ⚠️ HTML 生成失败: {e}")

def handle_full_backup(session, cp):
    """处理全量备份需求：如果配置了 full_backup_before 且未完成全量备份"""
    talker = session["talker"]
    display_name = session["display_name"]
    full_before = session.get("full_backup_before")
    if not full_before:
        return 0
    # 解析日期
    try:
        before_dt = datetime.strptime(full_before, "%Y-%m-%d")
        before_ts = int(before_dt.replace(tzinfo=CHINA_TZ).timestamp())
    except:
        print(f"  ⚠️ {display_name}: full_backup_before 日期格式错误")
        return 0

    entry = cp.get(talker, {})
    if entry.get("full_backup_done"):
        return 0  # 已完成全量，跳过

    print(f"\n{'='*55}\n  [全量备份] {display_name}")
    print(f"  备份范围: {full_before} ~ 现在\n{'='*55}")

    all_msgs = fetch_all_messages(talker, since_ts=before_ts)
    if not all_msgs:
        print("  全量备份: 该时间段内无消息")
        # 仍标记完成
        cp[talker] = entry
        cp[talker]["full_backup_done"] = True
        cp[talker]["full_backup_date"] = str(date.today())
        save_checkpoint(cp)
        return 0

    print(f"  共获取 {len(all_msgs)} 条")
    safe_name = re.sub(r'[\\/:*?"<>|]', '_', display_name)
    session_dir = data_dir / safe_name
    session_dir.mkdir(parents=True, exist_ok=True)
    saved = save_messages_to_files(all_msgs, display_name, session_dir, is_full_backup=True, export_prefix="定时导出")
    max_ts = max(m.get("createTime", 0) for m in all_msgs)
    entry.update({"display_name": display_name, "last_timestamp": max_ts,
                  "updated_at": int(time.time()),
                  "full_backup_done": True, "full_backup_date": str(date.today())})
    cp[talker] = entry
    save_checkpoint(cp)
    print(f"  ✅ 全量备份完成: {saved} 条")

    # 自动生成 HTML
    if saved > 0:
        generate_html_for_session(safe_name, all_msgs)

    return saved

# ======== 日报生成 ========
def generate_summary(target_date=None, output_file=None):
    if target_date is None: target_date = date.today()
    date_str = target_date.strftime("%Y-%m-%d")
    if not output_file: output_file = report_dir / f"{date_str}_日报.md"
    lines = [f"# 微信聊天监控日报 — {date_str}", "", f"> 生成时间: {datetime.now(CHINA_TZ).strftime('%Y-%m-%d %H:%M:%S')}", ""]
    total_msgs = active_sessions = 0
    for session_dir in sorted(data_dir.iterdir()):
        if not session_dir.is_dir(): continue
        fp = session_dir / f"{date_str}.json"
        if not fp.exists(): continue
        raw = json.loads(fp.read_text("utf-8"))
        if not raw: continue
        active_sessions += 1; total_msgs += len(raw)
        display_name = session_dir.name
        for s in config.get("sessions", []):
            if re.sub(r'[\\/:*?"<>|]', '_', s.get("display_name", "")) == session_dir.name:
                display_name = s["display_name"]; break
        lines.append(f"## 📱 {display_name}\n消息总数: **{len(raw)}** 条\n")
        smap = {}
        for m in raw:
            simp = simplify_message(m); smap.setdefault(simp["sender"] or "system", []).append(simp)
        for sender, msgs in sorted(smap.items(), key=lambda x: len(x[1]), reverse=True)[:5]:
            lines.append(f"**{sender}** — {len(msgs)} 条:")
            shown = 0
            for m in msgs:
                if m["type"] == "system": continue
                if shown >= 10:
                    lines.append(f"  ... 还有 {len(msgs)-shown} 条未列出"); break
                lines.append(f"  - [{m['time']}] {m['content']}"); shown += 1
            lines.append("")
        lines.append("---\n")
    if total_msgs == 0:
        lines.append("📭 今日无聊天记录\n")
    lines.append(f"_共监控 {len([s for s in config.get('sessions',[]) if s.get('enabled',False)])} 个会话，{active_sessions} 个有今日数据_")
    
    # ---- 导出操作日志（按类型分组） ----
    today_str_full = date_str
    export_ops = []
    clean_ops = []
    for session_dir in sorted(data_dir.iterdir()):
        if not session_dir.is_dir(): continue
        elog_file = session_dir / "export_log.json"
        if not elog_file.exists(): continue
        try:
            elog = json.loads(elog_file.read_text("utf-8"))
            for e in elog:
                if e.get("time", "").startswith(today_str_full):
                    session_name = e.get("session", session_dir.name)
                    op_type = e.get("type", "")
                    start_str = e.get("start", "")
                    end_str = e.get("end", "")
                    range_str = f" 区间:{start_str}~{end_str}" if start_str and end_str else ""
                    line = f"- **{session_name}** `{op_type}` {e.get('count',0)}条 ({e.get('time','')}{range_str})"
                    if "清空" in op_type:
                        clean_ops.append(line)
                    else:
                        export_ops.append(line)
        except:
            pass
    # 从中心操作日志读取清空等跨会话操作
    try:
        ops_file = data_dir / "_operations_log.json"
        if ops_file.exists():
            ops = json.loads(ops_file.read_text("utf-8"))
            for e in ops:
                if e.get("time", "").startswith(today_str_full):
                    sn = e.get("session", "")
                    op_type = e.get("type", "")
                    line = f"- **{sn}** `{op_type}` ({e.get('time','')})"
                    clean_ops.append(line)
    except:
        pass
    if export_ops:
        lines.append("\n## 📊 导出操作\n")
        lines.extend(sorted(export_ops))
        lines.append("")
    if clean_ops:
        lines.append("\n## 🗑️ 删除操作\n")
        lines.extend(sorted(clean_ops))
        lines.append("")
    
    summary = "\n".join(lines)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f: f.write(summary)
    print(f"  ✅ 日报已生成: {output_file}")
    print(f"  📊 会话: {active_sessions}/{len([s for s in config.get('sessions',[]) if s.get('enabled',False)])} 活跃 | 📝 消息: {total_msgs} 条")
    return summary

# ======== 交互命令 ========
def cmd_range_export(talker, display_name, start_date, end_date):
    """按指定日期范围导出（手动模式，单文件）"""
    from datetime import datetime as _dt
    sd = _dt.strptime(start_date, "%Y-%m-%d").replace(tzinfo=CHINA_TZ)
    ed = _dt.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59, tzinfo=CHINA_TZ)
    start_ts = int(sd.timestamp()); end_ts = int(ed.timestamp())
    print(f"\n{'='*55}\n  [区间导出] {display_name}\n  范围: {start_date} ~ {end_date}\n{'='*55}")
    all_msgs = fetch_all_messages(talker, since_ts=start_ts, end_ts=end_ts)
    if not all_msgs:
        print("  结果: 该范围无消息"); return 0
    print(f"  共获取 {len(all_msgs)} 条")
    safe_name = re.sub(r'[\\/:*?"<>|]', '_', display_name)
    session_dir = data_dir / safe_name
    session_dir.mkdir(parents=True, exist_ok=True)
    saved = save_manual_export(all_msgs, display_name, session_dir, "区间导出")
    return saved

def do_manual_export(talker, display_name, export_type, since_ts=None, end_ts=None, cp_before=None, cp_after=None):
    """针对单个会话执行手动导出（0条时自动重试一次），返回 (条数, 最后消息时间戳)"""
    safe_name = re.sub(r'[\\/:*?"<>|]', '_', display_name)
    session_dir = data_dir / safe_name
    session_dir.mkdir(parents=True, exist_ok=True)
    msgs = fetch_all_messages(talker, since_ts=since_ts, end_ts=end_ts)
    # 0条时自动重试（可能是 WeFlow API 状态未就绪）
    if not msgs:
        print(f"  [重试] {display_name} 返回0条，等待 3s 后重试...")
        import time as _tm_r
        _tm_r.sleep(3)
        msgs = fetch_all_messages(talker, since_ts=since_ts, end_ts=end_ts)
    if not msgs: return (0, 0)
    # 取最后一条消息的 createTime 作为实际最后消息时间
    last_msg_ts = int(msgs[-1].get("createTime", 0))
    print(f"  [{export_type}] {display_name}: {len(msgs)}条 (最后消息: {datetime.fromtimestamp(last_msg_ts, tz=CHINA_TZ).strftime('%Y-%m-%d %H:%M') if last_msg_ts else '无'})")
    count = save_manual_export(msgs, display_name, session_dir, export_type, talker, cp_before, cp_after, since_ts, end_ts)
    return (count, last_msg_ts)

def do_full_export_for_sessions(sessions, use_session_cp=False):
    """对指定会话列表执行全量导出"""
    cp = load_checkpoint()
    today_dt = datetime.now(CHINA_TZ).replace(hour=0, minute=0, second=0)
    today_ts = int(today_dt.timestamp())
    today_str = str(date.today())
    total = 0
    for s in sessions:
        talker, name = s["talker"], s["display_name"]
        entry = cp.get(talker, {})
        cp_before = {"last_timestamp": entry.get("last_timestamp"), "updated_at": entry.get("updated_at")} if entry else None
        cp_after = {"last_timestamp": today_ts, "updated_at": int(time.time())}
        count, last_ts = do_manual_export(talker, name, "全量导出", cp_before=cp_before, cp_after=cp_after)
        total += count
        entry.update({"display_name": name, "last_timestamp": last_ts if last_ts else today_ts,
                      "updated_at": int(time.time()),
                      "full_backup_done": True, "full_backup_date": today_str})
        cp[talker] = entry
    save_checkpoint(cp)
    if total > 0: generate_summary(date.today())
    return total

def do_incr_export_for_sessions(sessions):
    """对指定会话列表执行手动增量导出，并更新checkpoint为今天（调整定时节奏）"""
    cp = load_checkpoint()
    today_dt = datetime.now(CHINA_TZ).replace(hour=0, minute=0, second=0)
    today_ts = int(today_dt.timestamp())
    today_str = str(date.today())
    total = 0
    for s in sessions:
        talker, name = s["talker"], s["display_name"]
        entry = cp.get(talker, {})
        since_ts = entry.get("last_timestamp")
        cp_before = {"last_timestamp": since_ts, "updated_at": entry.get("updated_at")} if entry else None
        cp_after = {"last_timestamp": today_ts, "updated_at": int(time.time())}
        count, last_ts = do_manual_export(talker, name, "手动导出", since_ts=since_ts, cp_before=cp_before, cp_after=cp_after)
        if count > 0:
            generate_html_for_session(re.sub(r'[\\/:*?"<>|]', '_', name), None)
        total += count
        # 更新checkpoint为今天（用实际最后消息时间）
        entry = cp.get(talker, {})
        entry.update({"display_name": name, "last_timestamp": last_ts if last_ts else today_ts,
                      "updated_at": int(time.time()),
                      "full_backup_done": True, "full_backup_date": today_str})
        cp[talker] = entry
    save_checkpoint(cp)
    if total > 0:
        generate_summary(date.today())
    return total

def cmd_full_export():
    """全量导出所有会话（今天之前），重置checkpoint为今天"""
    cp = load_checkpoint()
    sessions = config.get("sessions", [])
    active = [s for s in sessions if s.get("enabled", True)]
    total = 0
    today_dt = datetime.now(CHINA_TZ).replace(hour=0, minute=0, second=0)
    today_ts = int(today_dt.timestamp())
    for session in active:
        talker, name = session["talker"], session["display_name"]
        print(f"\n{'='*55}\n  [全量] {name}\n{'='*55}")
        # 获取所有消息（不限时间范围）
        msgs = fetch_all_messages(talker)
        if not msgs:
            print("  无历史消息")
            continue
        print(f"  共获取 {len(msgs)} 条")
        safe_name = re.sub(r'[\\/:*?"<>|]', '_', name)
        session_dir = data_dir / safe_name
        session_dir.mkdir(parents=True, exist_ok=True)
        saved = save_manual_export(msgs, name, session_dir, "全量导出")
        total += saved
        # 更新 checkpoint 为今天
        entry = cp.get(talker, {})
        entry.update({"display_name": name, "last_timestamp": today_ts,
                      "updated_at": int(time.time()),
                      "full_backup_done": True, "full_backup_date": str(date.today())})
        cp[talker] = entry
        if saved > 0:
            generate_html_for_session(safe_name, msgs)
    save_checkpoint(cp)
    if total > 0:
        generate_summary(date.today())
    print(f"\n{'='*55}\n  ✅ 全量导出完成: {total} 条\n{'='*55}")
    return total

def cmd_manual_incr():
    """手动增量导出（基于checkpoint，更新checkpoint为今天）"""
    cp = load_checkpoint()
    today_dt = datetime.now(CHINA_TZ).replace(hour=0, minute=0, second=0)
    today_ts = int(today_dt.timestamp())
    today_str = str(date.today())
    sessions = config.get("sessions", [])
    active = [s for s in sessions if s.get("enabled", True)]
    total = 0
    results = []
    for session in active:
        talker, name = session["talker"], session["display_name"]
        since_ts = cp.get(talker, {}).get("last_timestamp")
        since_str = datetime.fromtimestamp(since_ts, tz=CHINA_TZ).strftime("%Y-%m-%d %H:%M") if since_ts else "无"
        results.append({"name": name, "since": since_str})
        # 直接拉取消息，用 save_manual_export 生成单文件
        if since_ts:
            msgs = fetch_all_messages(talker, since_ts=since_ts)
        else:
            msgs = fetch_all_messages(talker)  # 无checkpoint则全量
        if msgs:
            safe_name = re.sub(r'[\\/:*?"<>|]', '_', name)
            session_dir = data_dir / safe_name
            session_dir.mkdir(parents=True, exist_ok=True)
            count = save_manual_export(msgs, name, session_dir, "手动导出")
            total += count
            # 更新checkpoint
            entry = cp.get(talker, {})
            entry.update({"display_name": name, "last_timestamp": today_ts,
                          "updated_at": int(time.time()),
                          "full_backup_done": True, "full_backup_date": today_str})
            cp[talker] = entry
    save_checkpoint(cp)
    print(f"\n✅ 手动增量完成: {total} 条")
    # checkpoint 已更新为今天
    for r in results:
        print(f"  {r['name']}: checkpoint={r['since']}")
    return total

def cmd_list_sessions():
    print("\n正在获取微信会话列表...")
    result = api_get("/api/v1/sessions", {"limit": 100})
    if not result or not result.get("success"): print("❌ 获取失败！"); return
    sessions = result.get("sessions", [])
    print(f"\n共 {len(sessions)} 个会话\n")
    print(f"{'名称':<28} {'类型':<10} {'最后活跃':<20} {'未读':<5}"); print("-"*63)
    for s in sessions:
        name = (s.get("displayName","") or "")[:26]
        stype = s.get("sessionType","?")[:8]
        ts = s.get("lastTimestamp",0)
        last_time = datetime.fromtimestamp(ts, tz=CHINA_TZ).strftime("%m-%d %H:%M") if ts else "-"
        unread = s.get("unreadCount",0)
        print(f"{name:<28} {stype:<10} {last_time:<20} {unread:<5}")

def cmd_status():
    cp = load_checkpoint()
    if not cp: print("\n📊 尚未执行过导出"); return
    print(f"\n📊 导出状态 — {len(cp)} 个会话\n")
    print(f"{'会话':<22} {'最后消息':<22} {'同步':<14}")
    print("-"*58)
    now = time.time()
    for talker, info in cp.items():
        name = info.get("display_name", talker)[:20]
        ts = info.get("last_timestamp", 0)
        lt = datetime.fromtimestamp(ts, tz=CHINA_TZ).strftime("%Y-%m-%d %H:%M") if ts else "-"
        h = (now - info.get("updated_at",0)) / 3600
        st = "✅ 今日同步" if h < 24 else f"⚠️ {int(h)}h前"
        fb = " 📦全量" if info.get("full_backup_done") else ""
        print(f"{name:<22} {lt:<22} {st:<14}{fb}")
    print(f"\n📁 数据文件:")
    tf = tm = 0
    for d in sorted(data_dir.iterdir()):
        if d.is_dir():
            files = list(d.glob("*.json"))
            cnt = sum(len(json.loads(f.read_text("utf-8"))) for f in files if f.stat().st_size)
            tf += len(files); tm += cnt
            print(f"  {d.name}: {len(files)} 个文件, {cnt} 条")
    print(f"\n  合计: {tf} 个文件, {tm} 条消息")

def cmd_run(catchup_days=None):
    print(f"{'='*55}\n  WeFlow 微信监控 — 导出执行\n  时间: {datetime.now(CHINA_TZ).strftime('%Y-%m-%d %H:%M:%S')}\n{'='*55}")
    health = api_get("/health")
    if not health or health.get("status") != "ok":
        print("\n❌ WeFlow 未运行或 API 不可达！"); return 0
    print("✅ WeFlow API 连接正常\n")
    cp = load_checkpoint()
    sessions = config.get("sessions", [])
    active = [s for s in sessions if s.get("enabled", True)]
    if not active: print("⚠️ 没有启用的监控会话"); return 0
    print(f"待处理会话: {len(active)} 个\n")
    total = 0
    for session in active:
        talker, name = session["talker"], session["display_name"]
        # 1) 检查是否需要全量备份
        if not catchup_days:
            count = handle_full_backup(session, cp)
            total += count
            cp = load_checkpoint()  # 重新加载（可能被 handle_full_backup 更新）
        # 2) 增量导出
        if catchup_days:
            count = export_session(talker, name, catchup_days=catchup_days)
        else:
            since_ts = cp.get(talker, {}).get("last_timestamp")
            count = export_session(talker, name, since_ts=since_ts)
        total += count
    print(f"\n{'='*55}\n  执行完毕！共获取 {total} 条新消息\n{'='*55}")
    if total > 0:
        print(f"\n📝 生成日报...")
        generate_summary(date.today())
    return total

def main():
    load_config()
    if len(sys.argv) < 2: cmd_run(); return
    cmd = sys.argv[1]
    if cmd == "--sessions": cmd_list_sessions()
    elif cmd == "--status": cmd_status()
    elif cmd == "--catchup":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 7
        cmd_run(catchup_days=days)
    elif cmd == "--full":
        cmd_full_export()
    elif cmd == "--manual-incr":
        cmd_manual_incr()
    elif cmd == "--range":
        # --range <talker> <display_name> <start> <end>
        if len(sys.argv) < 6:
            print("用法: --range <talker> <display_name> <start_date> <end_date>")
        else:
            cmd_range_export(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
    elif cmd == "--summary":
        target = datetime.strptime(sys.argv[2],"%Y-%m-%d").date() if len(sys.argv) > 2 else date.today()
        generate_summary(target)
    elif cmd == "--ping":
        """测试 WeFlow API 连通性"""
        load_config()
        result = api_get("/api/v1/messages", {"talker": "", "limit": 1}, retries=1, timeout=5)
        if result and result.get("success", False) is not False:
            print("PONG", flush=True)
            sys.exit(0)
        print(f"无法连接 WeFlow API: {api_base_url}", flush=True)
        sys.exit(1)
    elif cmd == "--help": print(__doc__)
    else: print(f"未知命令: {cmd}")

if __name__ == "__main__":
    main()
