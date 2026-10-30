namespace XIVgate.RevitBridge;

internal static class LoaderBuild
{
#if LOADER_2024
    public const string Key = "2024";
#elif LOADER_2025
    public const string Key = "2025";
#elif LOADER_2025_NET10
    public const string Key = "2025-net10";
#elif LOADER_2026
    public const string Key = "2026";
#elif LOADER_2026_NET10
    public const string Key = "2026-net10";
#elif LOADER_2027
    public const string Key = "2027";
#else
#error Select one explicit LOADER_<worker key> build variant.
#endif
}
