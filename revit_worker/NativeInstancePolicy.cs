namespace XIVgate.RevitBridge;

/// <summary>Conservative host-independent proof that one piece contains one native symbol root.</summary>
public sealed class NativeInstancePolicy
{
    readonly bool eligible;
    int depth, roots;
    bool invalid, sawTriangles;
    string identifier = "";
    public NativeInstancePolicy(bool eligible) { this.eligible = eligible; }
    public bool EnterInstance(string nativeIdentifier, bool alreadyHasTriangles)
    {
        bool root = depth == 0;
        depth++;
        if (!root || !eligible) return false;
        roots++;
        if (invalid || roots != 1 || alreadyHasTriangles || string.IsNullOrWhiteSpace(nativeIdentifier)) {
            invalid = true; identifier = ""; return false;
        }
        identifier = nativeIdentifier;
        return true;
    }
    public void LeaveInstance()
    {
        if (depth > 0) depth--;
        else { invalid = true; identifier = ""; }
    }
    public void Polymesh(bool containsTriangles)
    {
        if (!containsTriangles) return;
        sawTriangles = true;
        if (depth == 0) { invalid = true; identifier = ""; }
    }
    public bool Safe => eligible && !invalid && roots == 1 && depth == 0 && sawTriangles && identifier.Length > 0;
    public string Key => Safe ? identifier : "";
}
