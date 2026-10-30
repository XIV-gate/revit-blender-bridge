#if NETFRAMEWORK
namespace System.Runtime.CompilerServices
{
    internal static class IsExternalInit { }
}
namespace XIVgate.RevitBridge
{
    internal static class FrameworkStringExtensions
    {
        public static bool Contains(this string value, string part, System.StringComparison comparison) => value.IndexOf(part, comparison) >= 0;
    }
}
#endif
