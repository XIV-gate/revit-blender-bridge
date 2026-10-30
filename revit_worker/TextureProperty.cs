using System;
using System.IO;
using System.Linq;

namespace XIVgate.RevitBridge;

public static class TextureProperty
{
    // Asset keys use '/' between connected-property hierarchy segments. A thumbnail
    // is appearance-editor metadata, even if a child is called bitmap or ends in PNG.
    public static bool IsBitmap(string key, string value) => !string.IsNullOrWhiteSpace(value) &&
        !key.Split('/').Any(segment => string.Equals(segment, "thumbnail", StringComparison.OrdinalIgnoreCase)) &&
        (key.Contains("bitmap", StringComparison.OrdinalIgnoreCase) ||
        new[] { ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".tga", ".exr", ".hdr" }.Contains(Path.GetExtension(value.Split('|')[0]).ToLowerInvariant()));
}
