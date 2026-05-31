<#
.SYNOPSIS
    Windows Hello (PIN/指纹/人脸) 身份验证
    通过内嵌 C# 代码调用 WinRT API。
    成功 exit 0, stdout: {"success":true}
    失败 exit 1, stdout: {"success":false, "message":"..."}
#>

# 用 C# 包装 WinRT 调用，处理异步
Add-Type -Language CSharp @"
using System;
using System.Threading.Tasks;

public class WinHello
{
    public static int CheckAndVerify()
    {
        try
        {
            // 加载 WinRT 类型
            var type = Type.GetType(
                "Windows.Security.Credentials.UI.UserConsentVerifier," +
                "Windows.Security.Credentials.UI.UserConsentVerifier," +
                "ContentType=WindowsRuntime");
            if (type == null) { Console.Error.WriteLine("TYPE_NOT_FOUND"); return 1; }

            // CheckAvailabilityAsync -> 同步等待
            var checkMethod = type.GetMethod("CheckAvailabilityAsync");
            var checkOp = checkMethod.Invoke(null, new object[0]);
            dynamic checkTask = System.WindowsRuntimeSystemExtensions.AsTask(checkOp);
            checkTask.Wait();
            string availability = checkTask.Result.ToString();
            if (availability != "Available")
            {
                Console.Error.WriteLine("NOT_AVAILABLE:" + availability);
                return 1;
            }

            // RequestVerificationAsync -> 同步等待
            var verifyMethod = type.GetMethod("RequestVerificationAsync");
            var verifyOp = verifyMethod.Invoke(null, new object[] { "WeFlow 监控看板 - 请验证身份" });
            dynamic verifyTask = System.WindowsRuntimeSystemExtensions.AsTask(verifyOp);
            verifyTask.Wait();
            string result = verifyTask.Result.ToString();

            if (result == "Verified")
            {
                Console.WriteLine("{\"success\":true,\"message\":\"验证通过\"}");
                return 0;
            }
            else
            {
                Console.Error.WriteLine("VERIFY_FAILED:" + result);
                return 1;
            }
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine("ERROR:" + ex.Message);
            return 1;
        }
    }
}
"@ -ReferencedAssemblies System.Runtime.WindowsRuntime

[WinHello]::CheckAndVerify()
exit $LASTEXITCODE
