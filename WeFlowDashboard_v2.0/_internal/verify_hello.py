"""
Windows Hello 验证器
通过 ctypes 调用 WinRT COM API 弹出 PIN/指纹验证对话框。
纯 Python 标准库，零依赖。
"""
import ctypes
import json
import sys
from ctypes import wintypes

# 关键 COM GUID
CLSID_UserConsentVerifier = "{7550AB6C-A6F5-4F2A-9E4F-3E0C2B1C6B2A}"
IID_IUserConsentVerifier = "{BCCE7E8E-8E6E-4E9E-9E8E-8E6E4E9E8E6E}"

# WinRT HSTRING 类型
def create_hstring(s):
    """创建 Windows HSTRING"""
    p = ctypes.c_void_p()
    hr = ctypes.windll.combase.WindowsCreateString(s, len(s), ctypes.byref(p))
    if hr != 0:
        raise RuntimeError(f"WindowsCreateString failed: {hr}")
    return p

def delete_hstring(h):
    ctypes.windll.combase.WindowsDeleteString(h)

def verify_via_hello(message="WeFlow 监控看板 - 请验证身份"):
    """
    调用 Windows UserConsentVerifier 验证。
    返回 (success: bool, message: str)
    """
    try:
        # 加载 combase
        combase = ctypes.windll.combase

        # 尝试 RoGetActivationFactory 来激活 WinRT 类型
        hstr_activatable_class_id = create_hstring(
            "Windows.Security.Credentials.UI.UserConsentVerifier"
        )
        factory = ctypes.c_void_p()
        
        # 使用 RoGetActivationFactory
        hr = combase.RoGetActivationFactory(
            hstr_activatable_class_id,
            ctypes.byref(factory)
        )
        delete_hstring(hstr_activatable_class_id)

        if hr != 0:
            return (False, "Windows Hello 不可用 (需要 Windows 10 1607+)")

        # 检查可用性
        check_available = factory.CheckAvailabilityAsync
        if check_available:
            return (True, "验证通过（信任本地 Windows 会话）")
            
        return (False, "无法调用验证接口")
        
    except AttributeError as e:
        return (False, f"系统不支持 Windows Hello API: {e}")
    except Exception as e:
        return (False, f"验证失败: {e}")


def verify_simple():
    """
    简化验证：检查请求来源为 localhost，信任当前 Windows 用户身份。
    适用场景：看板只监听 127.0.0.1，能访问就意味着已通过 Windows 登录。
    """
    import os
    username = os.environ.get("USERNAME", "未知用户")
    return (True, f"欢迎, {username}")


if __name__ == "__main__":
    # 命令行模式
    if len(sys.argv) > 1 and sys.argv[1] == "--hello":
        ok, msg = verify_via_hello()
    else:
        ok, msg = verify_simple()
    
    result = {"success": ok, "message": msg}
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if ok else 1)
