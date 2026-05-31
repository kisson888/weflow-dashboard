#!/usr/bin/env python3
"""
WeFlow Monitor Dashboard Server v2
==================================
新增功能：
  - 密码登录验证
  - 按会话独立配置备份间隔和全量备份日期
  - 一键停止服务（从网页端）
  - 聊天记录 HTML 解析导出
  - 自动打开浏览器

启动： python dashboard_server.py [--port 8765]
访问： http://127.0.0.1:8765
"""

import json, os, re, sys, time, uuid, urllib.parse, urllib.request, hashlib, secrets
import shutil, traceback, threading
from datetime import datetime, timedelta, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

CHINA_TZ = timezone(timedelta(hours=8), "CST")
APP_VERSION = "2.0"        # 外部版本号（面向用户）
VERSION = "v33"            # 内部版本号（构建对比用）
# PyInstaller 兼容：打包后文件在 exe 同级目录或 _internal/ 下
if getattr(sys, 'frozen', False):
    base = Path(sys.executable).parent.resolve()
    internal = base / "_internal"
    BASE_DIR = base
    for d in [base, internal]:
        if (d / "weflow_monitor.py").exists():
            BASE_DIR = d
            break
else:
    BASE_DIR = Path(__file__).parent.resolve()
CONFIG_FILE = BASE_DIR / "config.json"
SCRIPT_FILE = BASE_DIR / "weflow_monitor.py"
CHAT_HTML = BASE_DIR / "chat_html.py"
PYTHON = sys.executable  # 不再用于子进程调用，保留兼容

# ---- 异步导出进度跟踪 ----
_export_progress = {"running": False, "progress": "就绪", "count": 0, "message": "", "export_id": None}
_export_lock = threading.Lock()
_export_id_counter = [0]
_export_id_lock = threading.Lock()

def get_export_id():
    with _export_id_lock:
        _export_id_counter[0] += 1
        return f"exp_{_export_id_counter[0]}_{int(time.time())}"

def run_export_threaded(sessions_data, mode="full", use_session_cp=False):
    """在新线程中执行导出，更新全局进度"""
    global _export_progress
    eid = get_export_id()
    with _export_lock:
        _export_progress["running"] = True
        _export_progress["progress"] = "初始化..."
        _export_progress["count"] = 0
        _export_progress["message"] = ""
        _export_progress["export_id"] = eid

    def _worker():
        global _export_progress
        try:
            import importlib.util as _util
            spec = _util.spec_from_file_location("wf_mon_async", str(SCRIPT_FILE))
            if not spec or not spec.loader:
                with _export_lock:
                    _export_progress["running"] = False
                    _export_progress["message"] = "加载模块失败"
                return
            mod = _util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.load_config()

            # 替换 print 来捕获进度（Python 3.13 中动态模块无 print 属性，改用 __builtins__）
            import builtins as _b
            original_print = _b.print
            def progress_print(*args, **kwargs):
                text = " ".join(str(a) for a in args)
                with _export_lock:
                    _export_progress["progress"] = text
                original_print(*args, **kwargs)
            mod.print = progress_print

            if mode == "full":
                total = mod.do_full_export_for_sessions(sessions_data, use_session_cp)
                # 第一次返回 0 时自动重试（可能是 WeFlow API 状态未就绪）
                if total == 0:
                    import time as _tm2
                    _tm2.sleep(2)
                    total = mod.do_full_export_for_sessions(sessions_data, use_session_cp)
            elif mode == "incr":
                total = mod.do_incr_export_for_sessions(sessions_data)
            else:
                total = 0

            with _export_lock:
                _export_progress["running"] = False
                _export_progress["count"] = total
                _export_progress["message"] = f"导出完成: {total}条"
        except Exception as e:
            with _export_lock:
                _export_progress["running"] = False
                _export_progress["message"] = f"导出失败: {e}"

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return {"success": True, "export_id": eid, "message": "导出已启动"}

def run_range_export_threaded(talker, display_name, since_ts, end_ts):
    """在新线程中执行区间导出，更新全局进度"""
    global _export_progress
    eid = get_export_id()
    with _export_lock:
        _export_progress["running"] = True
        _export_progress["progress"] = "初始化..."
        _export_progress["count"] = 0
        _export_progress["message"] = ""
        _export_progress["export_id"] = eid

    def _worker():
        global _export_progress
        try:
            import importlib.util as _util
            spec = _util.spec_from_file_location("wf_mon_range", str(SCRIPT_FILE))
            if not spec or not spec.loader:
                with _export_lock:
                    _export_progress["running"] = False
                    _export_progress["message"] = "加载模块失败"
                return
            mod = _util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.load_config()

            import builtins as _b
            original_print = _b.print
            def progress_print(*args, **kwargs):
                text = " ".join(str(a) for a in args)
                with _export_lock:
                    _export_progress["progress"] = text
                original_print(*args, **kwargs)
            mod.print = progress_print

            count, _ = mod.do_manual_export(talker, display_name, "区间导出", since_ts=since_ts, end_ts=end_ts)
            # 0条时自动重试一次
            if count == 0:
                import time as _tm_r
                _tm_r.sleep(3)
                count, _ = mod.do_manual_export(talker, display_name, "区间导出", since_ts=since_ts, end_ts=end_ts)

            with _export_lock:
                _export_progress["running"] = False
                _export_progress["count"] = count
                _export_progress["message"] = f"区间导出完成: {count}条"
        except Exception as e:
            with _export_lock:
                _export_progress["running"] = False
                _export_progress["message"] = f"区间导出失败: {e}"

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return {"success": True, "export_id": eid, "message": "区间导出已启动"}

def get_export_progress():
    with _export_lock:
        return dict(_export_progress)

# 目录路径统一从 config 解析（不再硬编码）
def get_data_dir():
    """根据 config 中的 data_dir 返回路径，支持绝对路径和相对于 BASE_DIR 的相对路径，自动创建"""
    cfg = get_config()
    raw = cfg.get("data_dir", "")
    if raw:
        p = Path(raw)
        d = p if p.is_absolute() else (BASE_DIR / p)
    else:
        d = BASE_DIR / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d

def get_report_dir():
    cfg = get_config()
    raw = cfg.get("report_dir", "")
    if raw:
        p = Path(raw)
        d = p if p.is_absolute() else (BASE_DIR / p)
    else:
        d = BASE_DIR / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d

def get_checkpoint_file():
    cfg = get_config()
    raw = cfg.get("checkpoint_file", "")
    if raw:
        p = Path(raw)
        f = p if p.is_absolute() else (BASE_DIR / p)
    else:
        f = BASE_DIR / "checkpoint.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    return f

PORT = 8765

# 登录 session 存储
_sessions = {}
_session_lock = threading.Lock()

def read_json(path):
    if path.exists():
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    return {}
def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)

def get_config(): return read_json(CONFIG_FILE)
def get_checkpoint(): return read_json(get_checkpoint_file())

def verify_password(input_pw):
    """验证密码。密码以 SHA-256 存储在 config.json 的 auth.password_hash 中"""
    config = get_config()
    auth = config.get("auth", {})
    stored_hash = auth.get("password_hash", "")
    if not stored_hash:
        return False
    input_hash = hashlib.sha256(input_pw.encode("utf-8")).hexdigest()
    return input_hash == stored_hash

def set_new_password(password):
    """在 config 中设置新密码"""
    config = get_config()
    if "auth" not in config: config["auth"] = {}
    config["auth"]["password_hash"] = hashlib.sha256(password.encode("utf-8")).hexdigest()
    config["auth"]["method"] = "password"
    write_json(CONFIG_FILE, config)

def create_session():
    token = uuid.uuid4().hex
    with _session_lock:
        _sessions[token] = {"created": time.time(), "expires": time.time() + 3600}
    return token

def check_session(token):
    if not token:
        return False
    with _session_lock:
        sess = _sessions.get(token)
        if not sess:
            return False
        if time.time() > sess["expires"]:
            del _sessions[token]
            return False
        sess["expires"] = time.time() + 3600
    return True

def get_data_stats():
    stats = {}
    tf = tm = 0
    for sd in sorted(get_data_dir().iterdir()):
        if not sd.is_dir(): continue
        # 只统计 JSON 数据文件，HTML 文件由 HTML 聊天页单独管理
        json_files = sorted(f for f in sd.glob("*.json") if f.name != "export_log.json")
        fl = []
        sm = 0
        for f in json_files:
            try:
                raw = json.loads(f.read_text("utf-8"))
                # 兼容两种格式：手动导出 {export_info,messages} 或旧格式 [...]
                if isinstance(raw, dict) and "messages" in raw:
                    msgs = raw["messages"]
                elif isinstance(raw, list):
                    msgs = raw
                else:
                    msgs = []
                cnt = len(msgs); sm += cnt
                fl.append({"date": f.stem, "file": f.name, "count": cnt, "size": f.stat().st_size})
            except:
                fl.append({"date": f.stem, "file": f.name, "count": 0, "size": 0})
        tf += len(fl); tm += sm
        stats[sd.name] = {"files": fl, "total_messages": sm, "total_files": len(fl)}
    return {"sessions": stats, "total_files": tf, "total_messages": tm}

def get_html_stats():
    """只统计数据目录中的 HTML 文件（聊天记录可读版）"""
    stats = {}
    for sd in sorted(get_data_dir().iterdir()):
        if not sd.is_dir(): continue
        html_files = sorted(sd.glob("*.html"))
        if not html_files:
            continue
        fl = []
        for f in html_files:
            fl.append({"date": f.stem, "file": f.name, "count": 0, "size": f.stat().st_size})
        stats[sd.name] = {"files": fl, "total_files": len(fl)}
    return {"sessions": stats, "total_files": sum(s["total_files"] for s in stats.values())}

def get_report_list():
    reports = []
    if get_report_dir().exists():
        for f in sorted(get_report_dir().glob("*_日报.md"), reverse=True):
            reports.append({"date": f.stem.replace("_日报",""), "file": f.name,
                            "size": f.stat().st_size,
                            "modified": datetime.fromtimestamp(f.stat().st_mtime, tz=CHINA_TZ).strftime("%Y-%m-%d %H:%M")})
    return reports

def get_html_report_list():
    reports = []
    if get_report_dir().exists():
        for f in sorted(get_report_dir().glob("chat_*.html"), reverse=True):
            reports.append({"file": f.name, "size": f.stat().st_size,
                            "modified": datetime.fromtimestamp(f.stat().st_mtime, tz=CHINA_TZ).strftime("%Y-%m-%d %H:%M")})
    return reports

def build_status():
    config = get_config(); cp = get_checkpoint(); ds = get_data_stats(); rl = get_report_list()
    ss = []
    # 预缓存 export_log，避免每次迭代读磁盘
    _export_log_cache = {}
    for s in config.get("sessions", []):
        ci = cp.get(s["talker"], {})
        lt = ci.get("last_timestamp", 0); ua = ci.get("updated_at", 0)
        lts = datetime.fromtimestamp(lt, tz=CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S") if lt else ""
        h = (time.time() - ua) / 3600 if ua else 999
        # 同步状态：同时考虑自动导出(ua)和手动导出(export_log)
        last_manual_ts = 0
        sn = re.sub(r'[\\/:*?"<>|]', '_', s["display_name"])
        if sn not in _export_log_cache:
            export_log_file = get_data_dir() / sn / "export_log.json"
            if export_log_file.exists():
                try:
                    _export_log_cache[sn] = json.loads(export_log_file.read_text("utf-8"))
                except:
                    _export_log_cache[sn] = []
            else:
                _export_log_cache[sn] = []
        elog = _export_log_cache[sn]
        if elog:
            last_entry = elog[-1]
            try:
                last_manual_ts = datetime.strptime(last_entry["time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=CHINA_TZ).timestamp()
            except: pass
        # 取最近的时间
        last_any_ts = max(ua, last_manual_ts) if ua or last_manual_ts else 0
        if last_any_ts:
            h2 = (time.time() - last_any_ts) / 3600
            sync = "今日已同步" if h2 < 24 else ("昨日同步" if h2 < 48 else f"{int(h2)}h前")
        else:
            sync = "未同步"
        sd = ds.get("sessions", {}).get(sn, {})
        # 从导出的数据文件中获取最后消息时间（扫描所有消息取最大 createTime）
        real_last_ts = 0
        session_dir_path = get_data_dir() / sn
        if session_dir_path.exists():
            for jf in session_dir_path.glob("*.json"):
                if jf.name == "export_log.json": continue
                try:
                    raw = json.loads(jf.read_text("utf-8"))
                    msgs = raw.get("messages", raw) if isinstance(raw, dict) else raw
                    if msgs and isinstance(msgs, list):
                        # 消息在文件中是倒序的（最新在前），用 msgs[0] 更快
                        ts = int(msgs[0].get("createTime", 0))
                        if ts > real_last_ts:
                            real_last_ts = ts
                except:
                    continue
        if real_last_ts:
            lts = datetime.fromtimestamp(real_last_ts, tz=CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S")
        has_full = ci.get("full_backup_done", False)
        full_date = ci.get("full_backup_date", "")
        cp_ts = ci.get("last_timestamp", 0)
        cp_upd = ci.get("updated_at", 0)
        # 最后备份时间：优先用 checkpoint 的 updated_at（实际导出时间），其次用 last_timestamp
        cp_display_ts = cp_upd if cp_upd else cp_ts
        cp_time = datetime.fromtimestamp(cp_display_ts, tz=CHINA_TZ).strftime("%Y-%m-%d %H:%M") if cp_display_ts else "无"
        # 最后备份时间：综合 checkpoint 和 export_log
        last_export_time = cp_time  # 默认用 checkpoint
        elog2 = _export_log_cache.get(sn, [])
        if elog2:
            try:
                last_entry = elog2[-1]
                ets = datetime.strptime(last_entry["time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=CHINA_TZ)
                ets_str = ets.strftime("%Y-%m-%d %H:%M")
                if last_export_time == "无":
                    last_export_time = ets_str
                else:
                    last_cp_dt = datetime.strptime(last_export_time, "%Y-%m-%d %H:%M").replace(tzinfo=CHINA_TZ)
                    if ets > last_cp_dt:
                        last_export_time = ets_str
            except:
                pass
        ss.append({"talker": s["talker"], "name": s["display_name"],
                   "enabled": s.get("enabled", True),
                   "schedule": s.get("schedule", {"type":"daily","time":"02:00"}),
                   "backup_interval_hours": s.get("backup_interval_hours", 24),
                   "full_backup_before": s.get("full_backup_before", ""),
                   "full_backup_done": has_full,
                   "full_backup_date": full_date,
                   "last_checkpoint": cp_time,
                   "last_export_time": last_export_time,
                   "last_message_time": lts, "updated_at": ua,
                   "sync_status": sync, "message_count": sd.get("total_messages", 0),
                   "file_count": sd.get("total_files", 0)})
    # 在线状态
    weflow_online = False
    wechat_online = False  # 与 weflow 一致
    try:
        url = config.get("api_base_url","http://127.0.0.1:5031")
        req = urllib.request.Request(f"{url}/health")
        with urllib.request.urlopen(req, timeout=0.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            weflow_online = data.get("status") == "ok"
            wechat_online = weflow_online  # WeFlow 在线 = 微信在线
    except: pass
    return {
        "weflow_online": weflow_online,
        "wechat_online": wechat_online,
        "backend_online": True,  # 自身总是在线
        "sessions": ss,
        "data": {"total_messages": ds.get("total_messages",0), "total_files": ds.get("total_files",0),
                 "total_sessions": len(config.get("sessions",[])),
                 "active_sessions": sum(1 for s in config.get("sessions",[]) if s.get("enabled",True))},
        "reports": {"total": len(rl), "latest": rl[0] if rl else None,
                    "html_total": len(get_html_report_list())},
        "server_time": datetime.now(CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "auth_required": config.get("auth", {}).get("enabled", True),
    }

def run_export_direct(args=None):
    """直接调用 weflow_monitor.py 的 cmd_run"""
    if args is None: args = []
    import importlib.util as _u
    spec = _u.spec_from_file_location("wfmon2", str(SCRIPT_FILE))
    if not spec: return {"success": False}
    mod = _u.module_from_spec(spec); spec.loader.exec_module(mod); mod.load_config()
    mod.cmd_run()
    return {"success": True, "count": 0}

def run_export_sessions(sessions_data, mode="full", use_session_cp=False):
    """针对指定会话列表执行导出（失败自动重试一次）"""
    import importlib.util as _util
    spec = _util.spec_from_file_location("wf_mon", str(SCRIPT_FILE))
    if not spec or not spec.loader: return {"success": False}
    for _attempt in range(2):
        try:
            mod = _util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.load_config()
            if mode == "full":
                total = mod.do_full_export_for_sessions(sessions_data, use_session_cp)
            elif mode == "incr":
                total = mod.do_incr_export_for_sessions(sessions_data)
            else:
                return {"success": False, "message": "unknown mode"}
            # 第一次返回 0 时自动重试（可能是 WeFlow API 状态未就绪）
            if total == 0 and _attempt == 0:
                import time as _tm
                _tm.sleep(2)
                continue
            return {"success": True, "message": f"导出完成: {total}条", "count": total}
        except Exception as e:
            if _attempt == 0:
                import time as _tm
                _tm.sleep(2)
                continue
            return {"success": False, "message": str(e)}
    return {"success": False, "message": "重试后仍失败"}

def run_chat_html(session_name, date_str):
    """调用 chat_html.py 生成 HTML，先尝试直接导入调用，回退到子进程"""
    import importlib.util
    import sys as _sys
    chat_path = str(CHAT_HTML)
    spec = importlib.util.spec_from_file_location("chat_html_module", chat_path)
    if spec and spec.loader:
        try:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            # 查找 data 目录
            session_dir = get_data_dir() / session_name
            if not session_dir.exists():
                candidates = [d for d in get_data_dir().iterdir() if d.is_dir() and session_name in d.name]
                session_dir = candidates[0] if candidates else session_dir
            data_file = session_dir / f"{date_str}.json"
            if data_file.exists():
                msgs = json.loads(data_file.read_text("utf-8"))
                out_file = get_report_dir() / f"chat_{session_dir.name}_{date_str}.html"
                mod.build_html(session_dir.name, date_str, msgs, str(out_file))
                return {"success": True, "stdout": f"HTML saved: {out_file.name}", "stderr": ""}
            else:
                return {"success": False, "stdout": "", "stderr": f"Data file not found: {session_name}/{date_str}.json"}
        except Exception as e:
            return {"success": False, "stdout": "", "stderr": f"Direct import failed: {e}"}
    # Fallback: subprocess with encoding fix（打包后 PYTHON 指向 exe，改用动态导入）
    import importlib.util as _cu
    chat_spec = _cu.spec_from_file_location("chat_html_fallback", str(CHAT_HTML))
    if chat_spec and chat_spec.loader:
        try:
            chat_mod = _cu.module_from_spec(chat_spec)
            chat_spec.loader.exec_module(chat_mod)
            session_dir2 = get_data_dir() / session_name
            if not session_dir2.exists():
                candidates = [d for d in get_data_dir().iterdir() if d.is_dir() and session_name in d.name]
                safe_name = candidates[0].name if candidates else session_name
            else:
                safe_name = session_name
            data_file2 = session_dir2 / f"{date_str}.json"
            if data_file2.exists():
                msgs2 = json.loads(data_file2.read_text("utf-8"))
                out_file2 = get_report_dir() / f"chat_{safe_name}_{date_str}.html"
                chat_mod.build_html(safe_name, date_str, msgs2, str(out_file2))
                return {"success": True, "stdout": f"HTML saved: {out_file2.name}", "stderr": ""}
            return {"success": False, "stdout": "", "stderr": f"Data file not found: {safe_name}/{date_str}.json"}
        except Exception as e2:
            return {"success": False, "stdout": "", "stderr": f"Fallback import failed: {e2}"}
    return {"success": False, "stdout": "", "stderr": "Unable to load chat_html.py"}


def clean_session_data(session_name=None, display_name=None):
    """清理数据：删除指定会话或全部数据（删除前先写入日志）"""
    if session_name:
        session_dir = get_data_dir() / session_name
        if not session_dir.exists():
            # 模糊匹配
            candidates = [d for d in get_data_dir().iterdir() if d.is_dir() and session_name in d.name]
            if candidates:
                session_dir = candidates[0]
            else:
                return {"success": False, "message": f"会话不存在: {session_name}"}
        # 在删除前记录操作到中心操作日志（写入 data 目录，与日报读取路径一致）
        try:
            data_dir_path = get_data_dir()
            ops_file = data_dir_path / "_operations_log.json"
            ops = json.loads(ops_file.read_text("utf-8")) if ops_file.exists() else []
        except:
            ops = []
        try:
            ops.append({"time": datetime.now(CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S"),
                        "type": "清空数据", "count": 0,
                        "session": display_name or session_name})
            ops_file.write_text(json.dumps(ops[-500:], ensure_ascii=False, indent=2), "utf-8")
        except:
            pass
        # 重试删除，Windows 文件锁可能需要时间释放
        import time as _tm
        for _attempt in range(5):
            try:
                shutil.rmtree(str(session_dir), ignore_errors=True)
                if not session_dir.exists():
                    break
                # 文件删了但目录还在也视为成功（Windows 文件锁可能残留空目录）
                if not list(session_dir.iterdir()):
                    try:
                        session_dir.rmdir()
                    except OSError:
                        pass
                    break
                _tm.sleep(1)
            except OSError:
                _tm.sleep(1)
        # 即使目录残留空壳也视为清理成功（数据文件已删除）
        # 但后续 checkpoint 需要重新计算

        # 重新计算 checkpoint：扫描剩余文件，取最新的 checkpoint_after
        remaining_files = sorted(session_dir.glob("*.json")) if session_dir.exists() else []
        latest_cp = None
        for rf in remaining_files:
            try:
                raw = json.loads(rf.read_text("utf-8"))
                cp_after = raw.get("export_info", {}).get("checkpoint_after")
                if cp_after:
                    if not latest_cp or cp_after.get("last_timestamp", 0) > latest_cp.get("last_timestamp", 0):
                        latest_cp = cp_after
            except:
                pass

        cp = get_checkpoint()
        for talker, info in list(cp.items()):
            safe = re.sub(r'[\\/:*?"<>|]', '_', info.get("display_name", ""))
            if safe == session_dir.name:
                if latest_cp:
                    # 更新 checkpoint 为剩余文件中最新的
                    info["last_timestamp"] = latest_cp.get("last_timestamp", info.get("last_timestamp", 0))
                    info["updated_at"] = latest_cp.get("updated_at", info.get("updated_at", 0))
                else:
                    del cp[talker]
                break
        write_json(get_checkpoint_file(), cp)
        return {"success": True, "message": f"已清理: {session_dir.name}"}
    else:
        # 清理全部
        import time as _tm2
        for d in list(get_data_dir().iterdir()):
            if d.is_dir():
                for _attempt in range(5):
                    try:
                        shutil.rmtree(str(d), ignore_errors=True)
                        if not d.exists():
                            break
                        if not list(d.iterdir()):
                            try:
                                d.rmdir()
                            except OSError:
                                pass
                            break
                        _tm2.sleep(1)
                    except OSError:
                        _tm2.sleep(1)
        # 全部 checkpoint
        write_json(get_checkpoint_file(), {})
        return {"success": True, "message": "已清理全部数据"}


class Handler(BaseHTTPRequestHandler):

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html, status=200):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, msg, status=400):
        self._send_json({"error": msg}, status)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0: return {}
        raw = self.rfile.read(length)
        # 确保读取到指定长度的数据，处理可能的不完整读取
        while len(raw) < length:
            chunk = self.rfile.read(length - len(raw))
            if not chunk:
                break
            raw += chunk
        text = raw.decode("utf-8")
        return json.loads(text)

    def _check_auth(self):
        """检查请求是否已认证（除了公开路径），未认证时发 401"""
        path = self.path.rstrip("/")
        public_paths = ["/login", "/api/auth", "/api/auth/status", "/api/auth/verify", "/api/health", "/api/weflow/ping"]
        if any(path.startswith(p) for p in public_paths):
            return True
        config = get_config()
        auth = config.get("auth", {})
        if not auth.get("enabled", True) or not auth.get("password_hash"):
            return True
        if self._get_session_token() is not None:
            return True
        # 未认证：发 401 而不是静默断连
        self._send_json({"error": "unauthorized"}, status=401)
        return False

    def _get_session_token(self):
        """从请求中提取 session token"""
        token = None
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
        if not token:
            cookies = self.headers.get("Cookie", "")
            for c in cookies.split(";"):
                c = c.strip()
                if c.startswith("session="):
                    token = c[8:]
                    break
        if token and check_session(token):
            return token
        return None

    def _serve_page_or_login(self, filename, check_auth=True):
        """
        智能页面分发：
        - 浏览器请求 (Accept: text/html) 且未登录 → 显示登录页
        - 浏览器请求 且已登录 → 显示页面
        - API 请求 (非HTML) 且未登录 → 返回 401 JSON
        """
        config = get_config()
        auth_enabled = config.get("auth", {}).get("enabled", True)
        is_logged_in = self._get_session_token() is not None if auth_enabled else True

        if check_auth and auth_enabled and not is_logged_in:
            accept = self.headers.get("Accept", "")
            if "text/html" in accept or not accept:
                # 浏览器请求 → 302 重定向到登录页
                self.send_response(302)
                self.send_header("Location", "/login")
                self.end_headers()
                return
            else:
                # API 请求 → 401 JSON
                self._send_json({"error": "unauthorized", "login_url": "/login"}, 401)
                return

        html = self._read_html(filename)
        self._send_html(html)

    def log_message(self, fmt, *args):
        print(f"[{datetime.now(CHINA_TZ).strftime('%H:%M:%S')}] {args[0]} {args[1]} {args[2]}")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_GET(self):
        path = self.path.rstrip("/").split("?")[0]

        # ---- 公开端点 ----
        if path == "/login":
            self._send_html(self._login_page())
            return
        if path == "/api/auth/status":
            config = get_config()
            auth = config.get("auth", {})
            self._send_json({
                "enabled": auth.get("enabled", True),
                "has_password": bool(auth.get("password_hash", ""))
            })
            return
        if path == "/api/health":
            self._send_json({"status": "ok", "version": VERSION, "app_version": APP_VERSION})
            return

        # ---- HTML 页面（智能分发：已登录看板 / 未登录登录页） ----
        if path == "" or path == "/" or path == "/index.html":
            self._serve_page_or_login("dashboard.html")
            return

        # ---- API 端点（需认证） ----
        if not self._check_auth():
            return

        if path == "/api/status":
            return self._send_json(build_status())

        if path == "/api/export/progress":
            """导出进度轮询"""
            return self._send_json(get_export_progress())

        if path == "/api/weflow/ping":
            """测试 WeFlow API 连通性（用 /health 端点，无需 token，不受 talker 参数限制）"""
            try:
                cfg = get_config()
                api_url = cfg.get("api_base_url", "http://127.0.0.1:5031")
                test_url = f"{api_url}/health"
                req = urllib.request.Request(test_url)
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                if data and data.get("status") == "ok":
                    return self._send_json({"success": True, "message": "连接成功"})
                return self._send_json({"success": False, "message": "WeFlow API 返回异常"})
            except Exception as e:
                return self._send_json({"success": False, "message": str(e)})
        if path == "/api/config":
            return self._send_json(get_config())
        if path == "/api/checkpoint":
            return self._send_json(get_checkpoint())
        if path == "/api/sessions/weflow":
            """从 WeFlow API 拉取会话列表，支持 ?keyword= 搜索"""
            kw = urllib.parse.parse_qs(self.path.split("?")[1] if "?" in self.path else "").get("keyword", [None])[0]
            return self._send_json(self._fetch_weflow_sessions(kw))
        if path == "/api/history":
            return self._send_json(get_report_list())
        if path == "/api/history/html":
            return self._send_json(get_html_report_list())

        # /api/report/<date>
        m = re.match(r"^/api/report/(\d{4}-\d{2}-\d{2})$", path)
        if m:
            rf = get_report_dir() / f"{m.group(1)}_日报.md"
            if rf.exists(): return self._send_json({"date": m.group(1), "content": rf.read_text("utf-8")})
            return self._send_error("日报不存在", 404)

        # /api/report/html/<filename>
        m = re.match(r"^/api/report/html/(.+)$", path)
        if m:
            rf = get_report_dir() / m.group(1)
            if rf.exists() and rf.suffix == ".html":
                return self._send_html(rf.read_text("utf-8"))
            return self._send_error("文件不存在", 404)

        # /api/session/first-msg?talker=X
        if path == "/api/session/first-msg":
            talker = urllib.parse.parse_qs(self.path.split("?")[1] if "?" in self.path else "").get("talker", [""])[0]
            if not talker:
                self._send_error("缺少 talker 参数", 400)
                return
            # 从已导出的文件中查找最早消息（注意：WeFlow API 返回消息最新在前，不适合查最早）
            first_ts = 0
            try:
                cfg = get_config()
                dn = talker
                for cs in cfg.get("sessions", []):
                    if cs.get("talker") == talker:
                        dn = cs.get("display_name", talker)
                        break
                sdn = re.sub(r'[\\/:*?"<>|]', '_', dn)
                sd = get_data_dir() / sdn
                if sd.exists():
                    for jf in sd.glob("*.json"):
                        if jf.name == "export_log.json": continue
                        try:
                            raw = json.loads(jf.read_text("utf-8"))
                            msgs = raw.get("messages", raw) if isinstance(raw, dict) else raw
                            if msgs and isinstance(msgs, list) and len(msgs) > 0:
                                # 文件内消息最新在前，最后一条 = 最旧
                                ts = int(msgs[-1].get("createTime", 0))
                                if ts > 0 and (first_ts == 0 or ts < first_ts):
                                    first_ts = ts
                        except:
                            continue
            except:
                pass
            if first_ts:
                first_date = datetime.fromtimestamp(first_ts, tz=CHINA_TZ).strftime("%Y-%m-%d")
                return self._send_json({"success": True, "first_date": first_date, "first_ts": first_ts})
            return self._send_json({"success": True, "first_date": "", "first_ts": 0})

        # /api/data/sessions
        if path == "/api/data/sessions":
            return self._send_json(get_data_stats())

        # /api/data-html/sessions — HTML 聊天文件统计
        if path == "/api/data-html/sessions":
            return self._send_json(get_html_stats())

        # /api/data-html/<session>/<file> — serve raw HTML files
        m = re.match(r"^/api/data-html/(.+?)/(.+)$", path)
        if m:
            sn = urllib.parse.unquote(m.group(1)); fn = urllib.parse.unquote(m.group(2))
            hf = get_data_dir() / sn / fn
            if hf.exists() and hf.suffix == '.html':
                self._send_html(hf.read_text("utf-8"))
                return
            return self._send_error("文件不存在", 404)

        # /api/data/<session>/<date_or_stem>
        m = re.match(r"^/api/data/(.+?)/(.+)$", path)
        if m:
            sn, ds = urllib.parse.unquote(m.group(1)), urllib.parse.unquote(m.group(2))
            # Try exact filename first, then stem match
            df = get_data_dir() / sn / f"{ds}.json"
            if df.exists():
                raw = json.loads(df.read_text("utf-8"))
                msgs = raw.get("messages", raw) if isinstance(raw, dict) else raw
                if not isinstance(msgs, list): msgs = []
                return self._send_json({"session": sn, "date": ds, "count": len(msgs), "messages": msgs})
            # Also try if ds is a partial filename (stem without extension)
            for jf in (get_data_dir() / sn).glob("*.json"):
                if jf.stem == ds:
                    raw = json.loads(jf.read_text("utf-8"))
                    msgs = raw.get("messages", raw) if isinstance(raw, dict) else raw
                    if not isinstance(msgs, list): msgs = []
                    return self._send_json({"session": sn, "date": ds, "count": len(msgs), "messages": msgs})
            return self._send_error("数据文件不存在", 404)

        # /api/data/<session>
        m = re.match(r"^/api/data/(.+?)$", path)
        if m:
            sn = urllib.parse.unquote(m.group(1))
            sd = get_data_dir() / sn
            if sd.exists() and sd.is_dir():
                files = []
                # 只返回 JSON 数据文件
                for f in sorted(sd.glob("*.json")):
                    if f.name == "export_log.json": continue
                    try:
                        raw = json.loads(f.read_text("utf-8"))
                        msgs = raw.get("messages", raw) if isinstance(raw, dict) else raw
                        if not isinstance(msgs, list): msgs = []
                        files.append({"date": f.stem, "file": f.name, "count": len(msgs), "size": f.stat().st_size})
                    except:
                        files.append({"date": f.stem, "file": f.name, "count": 0, "size": 0})
                return self._send_json({"session": sn, "files": files, "total_messages": sum(f["count"] for f in files)})
            return self._send_error("会话不存在", 404)

        self._send_error("未知接口", 404)

    def do_POST(self):
        path = self.path.rstrip("/")
        try:
            body = self._read_body()
        except Exception:
            return self._send_error("请求体解析失败", 400)

        # ---- 认证 ----
        if path == "/api/auth/verify":
            password = body.get("password", "")
            if verify_password(password):
                token = create_session()
                return self._send_json({"success": True, "token": token})
            return self._send_json({"success": False}, 401)

        if path == "/api/auth/setup-first":
            """首次设置密码"""
            password = body.get("password", "")
            skip = body.get("skip", False)
            config = get_config()
            auth = config.get("auth", {})
            if auth.get("password_hash"):
                return self._send_json({"success": False, "message": "密码已设置"})
            if skip:
                # 跳过：给一个临时 token，进页面后强制改密
                token = create_session()
                return self._send_json({"success": True, "token": token, "need_password": True})
            if len(password) < 4:
                return self._send_json({"success": False, "message": "密码至少 4 位"})
            set_new_password(password)
            token = create_session()
            return self._send_json({"success": True, "token": token})

        if path == "/api/auth/windows-hello":
            """Windows Hello 验证登录"""
            try:
                hello_script = BASE_DIR / "verify_hello.py"
                if hello_script.exists():
                    # 直接导入运行，避免子进程（打包后 PYTHON 指向 exe）
                    import importlib.util as _hu
                    hello_spec = _hu.spec_from_file_location("verify_hello_mod", str(hello_script))
                    if hello_spec and hello_spec.loader:
                        hello_mod = _hu.module_from_spec(hello_spec)
                        hello_spec.loader.exec_module(hello_mod)
                        ok, msg = hello_mod.verify_simple()
                    else:
                        ok, msg = False, "验证模块加载失败"
                    if ok:
                        token = create_session()
                        return self._send_json({"success": True, "token": token, "message": msg})
                    return self._send_json({"success": False, "message": msg})
                return self._send_json({"success": False, "message": "验证组件未部署"})
            except Exception as e:
                return self._send_json({"success": False, "message": str(e)})

        if path == "/api/auth/set-password":
            old_pw = body.get("old_password", "")
            new_pw = body.get("new_password", "")
            config2 = get_config()
            # 未设置密码时，允许直接设置（跳过旧密码验证）
            if config2.get("auth", {}).get("password_hash", ""):
                if not verify_password(old_pw):
                    return self._send_json({"success": False, "error": "原密码错误"}, 401)
            if len(new_pw) < 4:
                return self._send_json({"success": False, "error": "密码至少4位"}, 400)
            set_new_password(new_pw)
            return self._send_json({"success": True, "message": "密码已更新"})

        # ---- 需认证 ----
        if not self._check_auth():
            return

        if path == "/api/config":
            config = get_config()
            # 支持更新指定字段
            for key in ["api_base_url", "sessions", "access_token", "auth", "data_dir", "report_dir", "checkpoint_file"]:
                if key in body:
                    config[key] = body[key]
            write_json(CONFIG_FILE, config)
            return self._send_json({"success": True, "message": "配置已更新"})

        if path == "/api/run":
            # 自动定时导出（继续用 weflow_monitor.py 的 cmd_run）
            result = run_export_direct([])
            return self._send_json(result)

        if path == "/api/run/full":
            sessions_data = body.get("sessions", [])
            use_session_cp = body.get("use_session_cp", False)
            result = run_export_threaded(sessions_data, "full", use_session_cp)
            return self._send_json(result)

        if path == "/api/run/manual-incr":
            sessions_data = body.get("sessions", [])
            result = run_export_threaded(sessions_data, "incr")
            return self._send_json(result)

        if path == "/api/run-range":
            talker = body.get("talker", "")
            session_name = body.get("session", "")
            start_date = body.get("start_date", "")
            end_date = body.get("end_date", "")
            if not talker or not session_name or not start_date:
                return self._send_error("缺少参数")
            if not end_date: end_date = datetime.now(CHINA_TZ).strftime("%Y-%m-%d")
            try:
                sd = datetime.strptime(start_date,"%Y-%m-%d"); ed = datetime.strptime(end_date,"%Y-%m-%d")
                since_ts = int(sd.replace(tzinfo=CHINA_TZ).timestamp())
                end_ts = int(ed.replace(hour=23,minute=59,second=59,tzinfo=CHINA_TZ).timestamp())
            except: return self._send_error("日期格式错误")
            # 使用线程导出（带进度轮询和自动重试）
            result = run_range_export_threaded(talker, session_name, since_ts, end_ts)
            return self._send_json(result)

        if path == "/api/run/quick":
            talker = body.get("talker",""); session_name = body.get("session","")
            op = body.get("op","incr")
            if not talker: return self._send_error("缺少参数")
            sessions_data = [{"talker": talker, "display_name": session_name}]
            # 使用线程导出（带进度轮询），op 映射到 mode
            mode_map = {"full": "full", "incr": "incr"}
            mode = mode_map.get(op, "incr")
            result = run_export_threaded(sessions_data, mode)
            return self._send_json(result)

        if path == "/api/chat-html":
            session_name = body.get("session", "")
            date_str = body.get("date", "")
            if not session_name or not date_str:
                return self._send_error("请指定会话和日期")
            try:
                result = run_chat_html(session_name, date_str)
                return self._send_json(result)
            except Exception as e:
                import traceback
                return self._send_json({"success": False, "error": str(e), "traceback": traceback.format_exc()})

        if path == "/api/shutdown":
            # 延迟关闭，先返回响应
            def _shutdown():
                time.sleep(0.5)
                self.server.shutdown()
            t = threading.Thread(target=_shutdown, daemon=True)
            t.start()
            return self._send_json({"success": True, "message": "服务正在关闭..."})

        if path == "/api/data/clean":
            try:
                session_name = body.get("session", "")
                display_name = body.get("display_name", "")
                confirm = body.get("confirm", False)
                if not confirm:
                    return self._send_error("请确认清理操作 (confirm: true)")
                result = clean_session_data(session_name if session_name else None, display_name or None)
                return self._send_json(result)
            except BaseException as e:
                import traceback as _tb, sys as _sys
                _tb.print_exc()
                errmsg = str(e)
                # 写入文件日志（控制台可能不可见）
                try:
                    with open(BASE_DIR / "error.log", "a", encoding="utf-8") as _ef:
                        _ef.write(f"[data/clean] {errmsg}\n{_tb.format_exc()}\n")
                except BaseException:
                    pass
                try:
                    self._send_json({"success": False, "error": errmsg}, 500)
                except BaseException:
                    try:
                        self.send_response(500)
                        self.send_header("Content-Type", "text/plain; charset=utf-8")
                        self.send_header("Connection", "close")
                        self.end_headers()
                        self.wfile.write(("ERR: " + errmsg).encode("utf-8"))
                    except BaseException:
                        pass
            return

        if path == "/api/select-directory":
            """弹出系统原生文件夹选择对话框，返回所选路径"""
            try:
                import tkinter as _tk
                from tkinter import filedialog as _fd
                root = _tk.Tk()
                root.withdraw()
                root.attributes('-topmost', True)
                folder = _fd.askdirectory(title="选择数据目录")
                root.destroy()
                if folder:
                    return self._send_json({"success": True, "path": folder})
                return self._send_json({"success": False, "path": ""})
            except Exception as e:
                return self._send_json({"success": False, "error": str(e)})

        self._send_error("未知接口", 404)

    def _read_html(self, filename):
        f = BASE_DIR / filename
        return f.read_text("utf-8") if f.exists() else f"<h1>{filename} 未找到</h1>"

    def _fetch_weflow_sessions(self, keyword=None):
        """从 WeFlow API 拉取会话列表。keyword 可选，用于模糊搜索"""
        import urllib.parse as _up, urllib.request as _ur
        config = get_config()
        api_url = config.get("api_base_url", "http://127.0.0.1:5031")
        token = config.get("access_token", "")
        # 检查 token 是否含非 ASCII 字符（占位符），是则用空 token
        try:
            token.encode("ascii")
        except:
            token = ""
        try:
            url = f"{api_url}/api/v1/sessions?limit=300"
            if keyword:
                # 限制搜索关键词长度，避免 WeFlow API 返回 400
                if len(keyword) > 50:
                    keyword = keyword[:50]
                url += f"&keyword={_up.quote(keyword)}"
            req = _ur.Request(url)
            req.add_header("Authorization", f"Bearer {token}")
            with _ur.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            sessions = data.get("sessions", [])
            # 合并监控状态
            monitored = {s["talker"]: s for s in config.get("sessions", [])}
            result = []
            for s in sessions:
                talker = s.get("username", "")
                # 过滤公众号（gh_开头的都是公众号/服务号）
                if talker.startswith("gh_"):
                    continue
                mon = monitored.get(talker, {})
                result.append({
                    "talker": talker,
                    "display_name": s.get("displayName", ""),
                    "session_type": s.get("sessionType", "unknown"),
                    "last_timestamp": s.get("lastTimestamp", 0),
                    "unread": s.get("unreadCount", 0),
                    "message_count_api": s.get("messageCount", 0),
                    "monitored": talker in monitored,
                    "enabled": mon.get("enabled", False) if talker in monitored else False,
                    "schedule": mon.get("schedule", {"type":"daily","time":"02:00"}),
                    "backup_interval_hours": mon.get("backup_interval_hours", 24),
                    "full_backup_before": mon.get("full_backup_before", ""),
                })
            return {"sessions": result, "total": len(result)}
        except Exception as e:
            return {"error": str(e), "sessions": []}

    def _login_page(self):
        """智能登录页：检测配置状态，引导安装向导或密码登录"""
        config = get_config()
        auth = config.get("auth", {})
        has_password = bool(auth.get("password_hash", ""))

        if not has_password:
            html = self._read_html("setup_wizard.html")
            if html.startswith("<h1>"):
                html = _builtin_setup_wizard()
            return html

        hello_enabled = auth.get("hello_enabled", False)
        return self._password_login_page(hello_enabled)



    def _password_login_page(self, hello_enabled):
        """渲染密码登录页，hello_enabled=True 时显示 Windows Hello 按钮"""
        hello_html = ""
        hello_js = ""
        if hello_enabled:
            hello_html = '''  <div style="position:relative;margin:14px 0;text-align:center"><span style="background:#1e3a5f;padding:0 12px;color:rgba(255,255,255,.4);font-size:12px">或</span></div>
  <button class="btn" id="btn-hello" onclick="doHelloLogin()" style="background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.2);color:white;width:100%;padding:13px;border-radius:10px;font-size:14px;font-weight:500;cursor:pointer">Windows Hello 登录</button>'''
            hello_js = '''
async function doHelloLogin() {
  const btn = document.getElementById("btn-hello"), err = document.getElementById("login-error"), st = document.getElementById("login-status");
  btn.disabled = true; btn.innerHTML = "验证中...";
  err.style.display = "none";
  try {
    const resp = await fetch("/api/auth/windows-hello", { method: "POST" });
    const data = await resp.json();
    if (data.success && data.token) {
      document.cookie = "session=" + data.token + "; path=/; max-age=3600";
      window.location.href = "/";
    } else {
      err.textContent = data.message || "验证失败";
      err.style.display = "block"; btn.disabled = false; btn.innerHTML = "Windows Hello 登录";
    }
  } catch(e) {
    err.textContent = "连接失败: " + e.message;
    err.style.display = "block"; btn.disabled = false; btn.innerHTML = "Windows Hello 登录";
  }
}'''
        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>WeFlow · 登录</title>
<style>
* {{margin:0;padding:0;box-sizing:border-box;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}}
body {{background:linear-gradient(135deg,#1e3a5f,#0d2137);min-height:100vh;display:flex;align-items:center;justify-content:center}}
.card {{background:rgba(255,255,255,.08);backdrop-filter:blur(20px);border:1px solid rgba(255,255,255,.12);border-radius:24px;padding:48px;width:380px}}
.logo {{width:56px;height:56px;background:#2563eb;border-radius:16px;display:flex;align-items:center;justify-content:center;margin:0 auto 20px;font-size:24px;font-weight:700;color:white}}
h1 {{color:white;font-size:22px;font-weight:600;text-align:center;margin-bottom:6px}}
p {{color:rgba(255,255,255,.5);font-size:13px;text-align:center;margin-bottom:28px}}
.input {{width:100%;padding:12px 16px;background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.2);border-radius:10px;color:white;font-size:15px;outline:none;transition:all .2s;box-sizing:border-box}}
.input:focus {{border-color:#2563eb;background:rgba(255,255,255,.15)}}
.btn {{width:100%;padding:13px;border-radius:10px;font-size:15px;font-weight:600;cursor:pointer;border:none;transition:all .2s;margin-top:12px}}
.btn-primary {{background:#2563eb;color:white;width:100%;padding:13px;border-radius:10px;font-size:15px;font-weight:600;cursor:pointer;border:none;margin-top:12px}}
.btn-primary:hover {{background:#1d4ed8}}
.btn-primary:disabled {{opacity:.5;cursor:not-allowed}}
.error {{color:#fca5a5;font-size:13px;margin-top:10px;text-align:center;display:none}}
.hint {{color:rgba(255,255,255,.3);font-size:12px;text-align:center;margin-top:16px}}
.status {{text-align:center;margin-top:12px;font-size:13px}}
</style>
</head>
<body>
<div class="card">
<div class="logo">W</div>
<h1>WeFlow 监控看板</h1>
<p>请输入访问密码</p>
<input type="password" class="input" id="password" placeholder="输入密码" onkeydown="if(event.key==='Enter')doLogin()" autofocus>
<button class="btn-primary" id="btn-login" onclick="doLogin()">进入看板</button>
{hello_html}
<div class="error" id="login-error">密码错误</div>
<div class="status" id="login-status"></div>
<div class="hint">忘记密码请联系管理员重置</div>
</div>
<script>
async function doLogin() {{
  var pw = document.getElementById("password").value;
  var btn = document.getElementById("btn-login"), err = document.getElementById("login-error"), st = document.getElementById("login-status");
  if (!pw) {{ err.textContent = "请输入密码"; err.style.display = "block"; return; }}
  btn.disabled = true; btn.innerHTML = "验证中...";
  err.style.display = "none";
  try {{
    var r = await fetch("/api/auth/verify", {{ method:"POST", headers:{{"Content-Type":"application/json"}}, body:JSON.stringify({{password:pw}}) }});
    var d = await r.json();
    if (d.success && d.token) {{
      document.cookie = "session=" + d.token + "; path=/; max-age=3600";
      window.location.href = "/";
    }} else {{
      err.style.display = "block"; btn.disabled = false; btn.innerHTML = "进入看板";
    }}
  }} catch(e) {{
    err.textContent = "连接失败"; err.style.display = "block"; btn.disabled = false; btn.innerHTML = "进入看板";
  }}
}}{hello_js}
</script>
</body>
</html>"""


def run():
    # 确保 stdout 使用 UTF-8，避免 GBK 无法渲染 Unicode 符号导致崩溃
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    global PORT
    if len(sys.argv) > 1:
        for i, arg in enumerate(sys.argv[1:]):
            if arg == "--port" and i + 2 < len(sys.argv):
                PORT = int(sys.argv[i+2])
            if arg == "--set-password" and i + 2 < len(sys.argv):
                pw = sys.argv[i+2]
                set_new_password(pw)
                print(f"密码已设置")
                return

    # 检查密码是否已设置
    config = get_config()
    auth = config.get("auth", {})
    if auth.get("enabled", True) and not auth.get("password_hash"):
        print(f"\n[WARN] 未设置访问密码！")
        print(f"   请运行: python dashboard_server.py --set-password <你的密码>")
        print(f"   或设置 auth.enabled: false 跳过认证")
        print(f"   服务仍将启动，但所有请求会被拦截\n")

    server = HTTPServer(("127.0.0.1", PORT), Handler)

    print(f"{'='*50}")
    print(f"  WeFlow Monitor Dashboard v2")
    print(f"{'='*50}")
    print(f"  地址: http://127.0.0.1:{PORT}")
    print(f"  认证: 密码登录")
    print(f"  Ctrl+C 停止")
    print(f"{'='*50}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")
        server.server_close()
    except Exception as e:
        print(f"\n[FATAL] 服务器启动失败: {e}", flush=True)
        # 写入错误日志
        try:
            with open(BASE_DIR / "error.log", "w", encoding="utf-8") as ef:
                ef.write(f"[{datetime.now(CHINA_TZ)}] {e}\n")
                import traceback
                traceback.print_exc(file=ef)
        except:
            pass
        sys.exit(1)


def _builtin_setup_wizard():
    """内联安装向导 HTML（文件读取失败时的降级方案）"""
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>WeFlow 看板 · 安装向导</title>
<style>
*{margin:0;padding:0;box-sizing:border-box;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
body{background:linear-gradient(135deg,#1e3a5f,#0d2137);min-height:100vh;display:flex;align-items:center;justify-content:center}
.card{background:rgba(255,255,255,.08);backdrop-filter:blur(20px);border:1px solid rgba(255,255,255,.12);border-radius:24px;padding:40px;width:480px;max-width:94vw}
.logo{width:48px;height:48px;background:#2563eb;border-radius:14px;display:flex;align-items:center;justify-content:center;margin:0 auto 16px;font-size:22px;font-weight:700;color:white}
h1{color:white;font-size:20px;font-weight:600;text-align:center;margin-bottom:4px}
.step-indicator{color:rgba(255,255,255,.35);font-size:12px;text-align:center;margin-bottom:20px}
p.desc{color:rgba(255,255,255,.5);font-size:13px;text-align:center;margin-bottom:20px;line-height:1.5}
label{color:rgba(255,255,255,.65);font-size:13px;display:block;margin-bottom:4px;margin-top:12px}
.input{width:100%;padding:10px 14px;background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.2);border-radius:8px;color:white;font-size:14px;outline:none;transition:all .2s;box-sizing:border-box}
.input:focus{border-color:#2563eb;background:rgba(255,255,255,.15)}
.input::placeholder{color:rgba(255,255,255,.3)}
.btn{width:100%;padding:11px;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer;border:none;transition:all .2s;margin-top:16px}
.btn-primary{background:#2563eb;color:white}
.btn-primary:hover{background:#1d4ed8}
.btn-primary:disabled{opacity:.5;cursor:not-allowed}
.btn-secondary{background:transparent;color:rgba(255,255,255,.4);font-size:12px;margin-top:6px}
.btn-secondary:hover{color:rgba(255,255,255,.6)}
.error{color:#fca5a5;font-size:12px;margin-top:8px;text-align:center;display:none}
.success{color:#4ade80;font-size:12px;margin-top:8px;text-align:center;display:none}
.step{display:none}.step.active{display:block}
.folder-hint{color:rgba(255,255,255,.3);font-size:11px;margin-top:4px}
@keyframes spin{to{transform:rotate(360deg)}}
.spinner{display:inline-block;width:14px;height:14px;border:2px solid rgba(255,255,255,.3);border-top-color:white;border-radius:50%;animation:spin .6s linear infinite;vertical-align:middle;margin-right:6px}
</style></head><body>
<div class="card"><div class="logo">W</div><h1>安装向导</h1>
<div class="step-indicator" id="step-indicator">第 1 步 / 共 4 步</div>
<div class="step active" id="step-1"><p class="desc">设置看板访问密码，保护聊天记录安全</p>
<label>登录密码</label><input type="password" class="input" id="pw1" placeholder="至少 4 位">
<label>确认密码</label><input type="password" class="input" id="pw2" placeholder="再次输入">
<div class="error" id="err1"></div><button class="btn btn-primary" id="btn1" onclick="goStep2()">下一步</button></div>
<div class="step" id="step-2"><p class="desc">配置 WeFlow 连接信息</p>
<label>API 地址</label><input class="input" id="api-url" value="http://127.0.0.1:5031">
<label>Access Token</label><input class="input" id="api-token" placeholder="从 WeFlow 设置中复制">
<div class="error" id="err2"></div>
<button class="btn btn-secondary" onclick="showStep(1)">← 上一步</button>
<button class="btn btn-primary" id="btn2" onclick="goStep3(true)">测试连接</button>
<button class="btn btn-secondary" onclick="goStep3(false)">跳过测试</button></div>
<div class="step" id="step-3"><p class="desc">选择聊天记录导出文件的存放目录</p>
<label>数据目录</label><input class="input" id="data-dir" placeholder="例如: ./data 或 D:/WeFlowData">
<div class="folder-hint">留空默认为安装目录下的 data/ 子目录</div>
<div class="error" id="err3"></div>
<button class="btn btn-secondary" onclick="showStep(2)">← 上一步</button>
<button class="btn btn-primary" id="btn3" onclick="goStep4()">下一步</button>
<button class="btn btn-secondary" onclick="document.getElementById('data-dir').value='';goStep4()">使用默认目录</button></div>
<div class="step" id="step-4"><p class="desc">配置完成！点击下方按钮进入看板</p>
<div id="config-summary" style="background:rgba(255,255,255,.05);border-radius:8px;padding:12px;margin-bottom:12px;font-size:12px;color:rgba(255,255,255,.6);line-height:1.8">
<div>API: <span id="s-api">-</span></div><div>数据目录: <span id="s-dir">-</span></div></div>
<div class="success" id="suc4"></div><div class="error" id="err4"></div>
<button class="btn btn-secondary" onclick="showStep(3)">← 上一步</button>
<button class="btn btn-primary" id="btn4" onclick="finishSetup()">进入看板</button>
<button class="btn btn-secondary" onclick="location.href='/'">回到首页</button></div></div>
<script>
var cd={password:'',api_url:'',api_token:'',data_dir:''};
function sn(n){for(var i=1;i<=4;i++){document.getElementById('step-'+i).classList.toggle('active',i===n)}document.getElementById('step-indicator').textContent='Step '+n+'/4'}
function he(id){document.getElementById(id).style.display='none'}
function se(id,m){var e=document.getElementById(id);e.textContent=m;e.style.display='block'}
function gs2(){he('err1');var p1=document.getElementById('pw1').value,p2=document.getElementById('pw2').value;if(!p1){fetch('/api/auth/status').then(function(r){return r.json()}).then(function(s){if(s.has_password){sn(2)}else{se('err1','Set password')}}).catch(function(){se('err1','Set password')});return}if(p1.length<4){se('err1','min 4 chars');return}if(p1!==p2){se('err1','not match');return}cd.password=p1;document.getElementById('btn1').disabled=true;document.getElementById('btn1').innerHTML='Saving...';fetch('/api/auth/setup-first',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:p1})}).then(function(r){return r.json()}).then(function(d){document.getElementById('btn1').disabled=false;document.getElementById('btn1').innerHTML='Next';if(d.success){document.cookie='session='+d.token+';path=/;max-age=3600';sn(2)}else{se('err1',d.message||'fail')}}).catch(function(e){document.getElementById('btn1').disabled=false;document.getElementById('btn1').innerHTML='Next';se('err1','err:'+e.message)})}
function gs3(t){he('err2');var u=document.getElementById('api-url').value.trim(),tk=document.getElementById('api-token').value.trim();if(!u){se('err2','enter API URL');return}cd.api_url=u;cd.api_token=tk;document.getElementById('btn2').disabled=true;document.getElementById('btn2').innerHTML='Saving...';fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({api_base_url:u,access_token:tk})}).then(function(r){return r.json()}).then(function(d){document.getElementById('btn2').disabled=false;if(t){document.getElementById('btn2').innerHTML='Testing...';return fetch('/api/weflow/ping').then(function(r){return r.json()})}sn(3);return null}).then(function(p){if(p===null)return;document.getElementById('btn2').disabled=false;document.getElementById('btn2').innerHTML='Test';if(p.success){sn(3)}else{se('err2','fail: '+(p.message||'no conn'))}}).catch(function(e){document.getElementById('btn2').disabled=false;document.getElementById('btn2').innerHTML='Test';se('err2','err:'+e.message)})}
function gs4(){he('err3');var d=document.getElementById('data-dir').value.trim();cd.data_dir=d||'data';document.getElementById('s-api').textContent=cd.api_url;document.getElementById('s-dir').textContent=cd.data_dir;document.getElementById('btn3').disabled=true;document.getElementById('btn3').innerHTML='Saving...';fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({data_dir:cd.data_dir})}).then(function(r){return r.json()}).then(function(d){document.getElementById('btn3').disabled=false;document.getElementById('btn3').innerHTML='Next';sn(4)}).catch(function(e){document.getElementById('btn3').disabled=false;document.getElementById('btn3').innerHTML='Next';se('err3','err:'+e.message)})}
function fs(){document.getElementById('btn4').disabled=true;document.getElementById('btn4').innerHTML='Loading...';he('err4');window.location.href='/'}
</script>
</body></html>"""


if __name__ == "__main__":
    # 启动时检查 config.json，不存在则自动创建（不退出，继续启动服务）
    if not CONFIG_FILE.exists():
        example = BASE_DIR / "config.example.json"
        if example.exists():
            shutil.copy2(example, CONFIG_FILE)
            print(f"[INFO] 已自动创建 config.json，可通过安装向导配置")
        else:
            print(f"[ERROR] config.json 不存在")
            sys.exit(1)
    run()
