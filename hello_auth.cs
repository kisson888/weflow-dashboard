// WeFlow Windows Hello Authenticator
// Compile: csc.exe /target:exe /reference:System.Runtime.WindowsRuntime.dll hello_auth.cs
// Usage:   hello_auth.exe  (exit 0 = success, exit 1 = failure)

using System;
using System.Threading.Tasks;
using Windows.Security.Credentials.UI;

class HelloAuth
{
    static int Main()
    {
        try
        {
            // Check availability
            var checkTask = UserConsentVerifier.CheckAvailabilityAsync().AsTask();
            checkTask.Wait();
            var availability = checkTask.Result;

            if (availability != UserConsentVerifierAvailability.Available)
            {
                Console.WriteLine("{{\"success\":false,\"message\":\"Windows Hello 不可用: {0}\"}}", availability);
                return 1;
            }

            // Show verification dialog
            var verifyTask = UserConsentVerifier.RequestVerificationAsync("WeFlow 监控看板 - 请验证身份").AsTask();
            verifyTask.Wait();
            var result = verifyTask.Result;

            if (result == UserConsentVerificationResult.Verified)
            {
                Console.WriteLine("{\"success\":true,\"message\":\"验证通过\"}");
                return 0;
            }
            else
            {
                Console.WriteLine("{{\"success\":false,\"message\":\"验证失败: {0}\"}}", result);
                return 1;
            }
        }
        catch (Exception ex)
        {
            Console.WriteLine("{{\"success\":false,\"message\":\"{0}\"}}", ex.Message.Replace("\"", "'"));
            return 1;
        }
    }
}
