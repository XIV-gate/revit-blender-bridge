namespace XIVgate.RevitBridge;

/// <summary>Binary float conversion shared by export and offline regression replay.</summary>
public static class GeometryPrecision
{
    // Positions are metres; normals and UVs are dimensionless. Remove only near-zero
    // float32 arithmetic residue. Every value above the floor keeps its exact float32 bits.
    // Callers retain their finite/range checks; material keys and topology never pass here.
    public static float ExportFloat(double value)
    {
        float result = (float)value;
        return System.Math.Abs(result) <= 1e-9f ? 0f : result;
    }
}
