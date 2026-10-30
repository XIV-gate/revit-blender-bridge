using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using System.Security.Cryptography;
using System.Text.Json;
using Autodesk.Revit.DB;
using Autodesk.Revit.DB.Visual;

namespace XIVgate.RevitBridge;

// CustomExporter is driven synchronously by Revit on its API thread.
internal sealed class GeometryExporter : IPhotoRenderContext
{
    readonly Document doc;
    readonly string output, sourceDirectory;
    readonly BinaryWriter binary;
    readonly HashSet<long> selection;
    readonly Func<bool> canceled;
    readonly List<string> warnings;
    readonly Stack<Transform> transforms = new();
    readonly Stack<Frame> elements = new();
    readonly Dictionary<string, int> hashes = new();
    readonly List<object> prototypes = new(), occurrences = new();
    readonly Dictionary<long, object> materials = new();
    readonly Dictionary<string, long> materialSignatures = new();
    readonly HashSet<long> exported = new();
    readonly Dictionary<long, long> childOwners = new();
    readonly Dictionary<long, Piece> pieces = new();
    readonly FileStream spool;
    readonly Dictionary<string, string?> textureCache = new(StringComparer.OrdinalIgnoreCase);
    List<string>? textureFiles;
    long material = -1, triangles;
    sealed class Piece
    {
        public Element Element = null!;
        public Transform Transform = null!;
        public string? ParentId;
        public MemoryStream Stream = new();
        public BinaryWriter Writer = null!;
        public int Triangles;
        public HashSet<string> SourceIds = new();
        public List<(long Offset, int Bytes)> Segments = new();
    }
    sealed record Frame(long Id, Piece? Target);
    GeometryExporter(Document doc, string output, string source, BinaryWriter binary, HashSet<long> selection, Func<bool> canceled, List<string> warnings)
    {
        this.doc = doc; this.output = output; sourceDirectory = Path.GetDirectoryName(source)!;
        this.binary = binary; this.selection = selection; this.canceled = canceled; this.warnings = warnings;
        transforms.Push(Transform.Identity);
        BuildOwnership();
        spool = new FileStream(Path.Combine(output, ".geometry-spool.pending"), FileMode.CreateNew, FileAccess.ReadWrite, FileShare.None);
    }
    void BuildOwnership()
    {
        foreach (long owner in selection.OrderBy(x => x)) {
            var queue = new Queue<ElementId>(); queue.Enqueue(new ElementId(owner));
            var visited = new HashSet<long>();
            while (queue.Count > 0) {
                var id = queue.Dequeue(); if (!visited.Add(id.Value)) continue;
                var e = doc.GetElement(id); if (e == null) continue;
                IEnumerable<ElementId> children = Array.Empty<ElementId>();
                if (e is AssemblyInstance assembly) children = assembly.GetMemberIds();
                else if (e is Group group) children = group.GetMemberIds();
                else if (e is Wall wall && wall.CurtainGrid != null) children = wall.CurtainGrid.GetPanelIds().Concat(wall.CurtainGrid.GetMullionIds());
                else if (e is FamilyInstance family) children = family.GetSubComponentIds();
                else if (e is Autodesk.Revit.DB.Architecture.Railing railing) children = railing.GetHandRails().Concat(new[] { railing.TopRail });
                else if (e is Autodesk.Revit.DB.Architecture.Stairs stairs) children = stairs.GetStairsRuns().Concat(stairs.GetStairsLandings()).Concat(stairs.GetStairsSupports());
                foreach (var child in children) {
                    if (child == ElementId.InvalidElementId || selection.Contains(child.Value)) continue;
                    if (!childOwners.ContainsKey(child.Value)) childOwners.Add(child.Value, owner);
                    queue.Enqueue(child);
                }
            }
        }
    }
    public static void Run(Document doc, string output, HashSet<long> selection, object source, string original, string detail, Func<bool> canceled, List<string> warnings)
    {
        string manifest = Path.Combine(output, "manifest.json");
        if (File.Exists(manifest)) throw new IOException("Успешная ревизия уже существует.");
        using var data = new BinaryWriter(File.Open(Path.Combine(output, "geometry.bin"), FileMode.CreateNew));
        var context = new GeometryExporter(doc, output, original, data, selection, canceled, warnings);
        try {
            // The entire temporary view, visibility edits and transaction are rolled back.
            using var group = new TransactionGroup(doc, "XIVgate temporary export");
            if (group.Start() != TransactionStatus.Started) throw new InvalidOperationException("Невозможно начать временную транзакцию.");
            try {
                View3D view;
                using (var t = new Transaction(doc, "XIVgate temporary view")) {
                    t.Start();
                    var type = new FilteredElementCollector(doc).OfClass(typeof(ViewFamilyType)).Cast<ViewFamilyType>().First(v => v.ViewFamily == ViewFamily.ThreeDimensional);
                    view = View3D.CreateIsometric(doc, type.Id);
                    view.Name = "XIVgate_RBB_" + Guid.NewGuid().ToString("N");
                    view.DetailLevel = detail.ToLowerInvariant() switch { "coarse" => ViewDetailLevel.Coarse, "fine" => ViewDetailLevel.Fine, _ => ViewDetailLevel.Medium };
                    view.DisplayStyle = DisplayStyle.Shading;
                    foreach (Category c in doc.Settings.Categories) {
                        if (c.CategoryType == CategoryType.Model && view.CanCategoryBeHidden(c.Id)) view.SetCategoryHidden(c.Id, false);
                    }
                    if (doc.IsWorkshared) foreach (Workset w in new FilteredWorksetCollector(doc).OfKind(WorksetKind.UserWorkset)) view.SetWorksetVisibility(w.Id, WorksetVisibility.Visible);
                    if (t.Commit() != TransactionStatus.Committed) throw new InvalidOperationException("Не удалось создать временный вид экспорта.");
                }
                using var exporter = new CustomExporter(doc, context) { IncludeGeometricObjects = false, ShouldStopOnError = true };
                exporter.Export(view);
                context.CompletePieces();
            } finally {
                if (group.GetStatus() == TransactionStatus.Started && group.RollBack() != TransactionStatus.RolledBack)
                    throw new InvalidOperationException("Не удалось откатить временный вид. Копия модели не будет сохранена.");
            }
            if (canceled()) throw new OperationCanceledException("Экспорт отменён пользователем.");
            foreach (long id in selection.Except(context.exported)) warnings.Add("Выбранный элемент не дал видимой геометрии (фаза, вариант, отсутствие сетки или неподдерживаемая связь): " + id);
            if (context.triangles == 0) throw new InvalidOperationException("Выбранные элементы не дали геометрии. Проверьте фазы, варианты и состав выбора.");
            data.Flush();
            long bytes = data.BaseStream.Position;
            // Close binary before making the terminal manifest visible to a consumer.
            data.Dispose();
            var result = new { schema = 1, units = "metres", up = "Z", source, layout = "triangle_soup_float32_position_normal_uv_then_int64_material_per_triangle",
                summary = new { occurrences = context.occurrences.Count, prototypes = context.prototypes.Count, triangles = context.triangles, materials = context.materials.Count, bytes },
                prototypes = context.prototypes, occurrences = context.occurrences, materials = context.materials, warnings };
            // Directory is a new revision and never reused; only the manifest is promoted last.
            string pending = manifest + ".pending";
            File.WriteAllText(pending, JsonSerializer.Serialize(result, Protocol.Json), new System.Text.UTF8Encoding(false));
            File.Move(pending, manifest);
        } finally {
            foreach (var piece in context.pieces.Values) { piece.Writer.Dispose(); piece.Stream.Dispose(); }
            context.spool.Dispose();
            try { File.Delete(Path.Combine(output, ".geometry-spool.pending")); }
            catch (IOException cleanupError) { warnings.Add("Не удалось удалить временный буфер: " + cleanupError.Message); }
        }
    }
    public bool Start() => true;
    public void Finish() { }
    public bool IsCanceled() => canceled();
    public RenderNodeAction OnViewBegin(ViewNode node) { node.LevelOfDetail = 5; return RenderNodeAction.Proceed; }
    public void OnViewEnd(ElementId id) { }
    public RenderNodeAction OnElementBegin(ElementId id)
    {
        var ancestor = elements.Count == 0 ? null : elements.Peek().Target;
        Piece? piece = null;
        long ownerId = selection.Contains(id.Value) ? id.Value : childOwners.TryGetValue(id.Value, out long mapped) ? mapped : -1;
        var e = ownerId == -1 ? null : doc.GetElement(new ElementId(ownerId));
        if (e != null && !pieces.TryGetValue(ownerId, out piece)) {
            var frame = Transform.Identity;
            if (e is FamilyInstance instance) frame = instance.GetTransform();
            else { var box = e.get_BoundingBox(null); if (box != null) frame.Origin = box.Transform.OfPoint((box.Min + box.Max) * 0.5); }
            piece = new Piece { Element = e, Transform = frame, ParentId = ancestor?.Element.Id.Value.ToString() };
            piece.Writer = new BinaryWriter(piece.Stream);
            pieces.Add(ownerId, piece);
        }
        var target = piece ?? ancestor;
        target?.SourceIds.Add(id.Value.ToString());
        elements.Push(new Frame(id.Value, target));
        // Never skip an unselected container: explicitly selected descendants must still be reached.
        // Child meshes without their own selected ID belong to the selected parent assembly.
        return RenderNodeAction.Proceed;
    }
    public void OnElementEnd(ElementId id)
    {
        if (elements.Count == 0 || elements.Peek().Id != id.Value) throw new InvalidOperationException("Нарушен стек элементов CustomExporter.");
        var frame = elements.Pop();
        if (frame.Target != null) FlushPiece(frame.Target);
    }
    void FlushPiece(Piece p)
    {
        p.Writer.Flush();
        int count = checked((int)p.Stream.Length);
        if (count == 0) return;
        long offset = spool.Seek(0, SeekOrigin.End);
        spool.Write(p.Stream.GetBuffer(), 0, count);
        p.Segments.Add((offset, count));
        p.Stream.SetLength(0); p.Stream.Position = 0;
        p.Stream.Capacity = 0;
    }
    void CompletePieces()
    {
        foreach (var p in pieces.Values) FlushPiece(p);
        spool.Flush();
        foreach (var p in pieces.Values) CompletePiece(p);
    }
    void CompletePiece(Piece p)
    {
            p.Writer.Flush();
            if (p.Triangles == 0) return;
            long length = (long)p.Triangles * 104;
            if (length > int.MaxValue) throw new InvalidDataException("Один выбранный элемент содержит более 2 ГБ геометрии; выберите его составные части отдельно: " + p.Element.Id.Value);
            byte[] bytes = new byte[(int)length];
            int position = 0;
            foreach (var segment in p.Segments) {
                spool.Position = segment.Offset;
                spool.ReadExactly(bytes.AsSpan(position, segment.Bytes));
                position += segment.Bytes;
            }
            if (position != bytes.Length) throw new InvalidDataException("Нарушена длина буфера геометрии.");
            string hash = Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
            if (!hashes.TryGetValue(hash, out int prototype)) {
                prototype = prototypes.Count; hashes.Add(hash, prototype);
                long offset = binary.BaseStream.Position; binary.Write(bytes);
                prototypes.Add(new { id = prototype, offset, bytes = bytes.Length, triangles = p.Triangles, sha256 = hash });
            }
            var t = p.Transform; var e = p.Element;
            occurrences.Add(new { id = e.Id.Value.ToString(), unique_id = e.UniqueId, name = e.Name,
                category = e.Category?.Name ?? "", category_id = e.Category?.Id.Value.ToString() ?? "", workset = WorkerApp.Workset(e), workset_id = e.WorksetId.IntegerValue.ToString(),
                type_id = e.GetTypeId().Value.ToString(), type_name = doc.GetElement(e.GetTypeId())?.Name ?? "", parent_id = p.ParentId, source_element_ids = p.SourceIds.OrderBy(x => x).ToArray(),
                decision = "include", prototype,
                transform = new[] { t.BasisX.X, t.BasisX.Y, t.BasisX.Z, t.BasisY.X, t.BasisY.Y, t.BasisY.Z, t.BasisZ.X, t.BasisZ.Y, t.BasisZ.Z, t.Origin.X * 0.3048, t.Origin.Y * 0.3048, t.Origin.Z * 0.3048 } });
            triangles += p.Triangles; exported.Add(e.Id.Value);
    }
    public RenderNodeAction OnInstanceBegin(InstanceNode node) { transforms.Push(transforms.Peek().Multiply(node.GetTransform())); return RenderNodeAction.Proceed; }
    public void OnInstanceEnd(InstanceNode node) { transforms.Pop(); }
    public RenderNodeAction OnLinkBegin(LinkNode node) { warnings.Add("Связанный документ пропущен: " + node.GetDocument()?.Title); return RenderNodeAction.Skip; }
    public void OnLinkEnd(LinkNode node) { }
    public RenderNodeAction OnFaceBegin(FaceNode node) => RenderNodeAction.Proceed;
    public void OnFaceEnd(FaceNode node) { }
    public void OnLight(LightNode node) { }
    public void OnRPC(RPCNode node) { warnings.Add("RPC-геометрия не поддерживается; замените её сеткой в исходной модели."); }
    public void OnPolymesh(PolymeshTopology node)
    {
        if (elements.Count == 0 || elements.Peek().Target is not Piece p) return;
        var map = p.Transform.Inverse.Multiply(transforms.Peek());
        var inverse = map.Inverse;
        var points = node.GetPoints(); var normals = node.GetNormals(); var uvs = node.GetUVs(); var facets = node.GetFacets();
        int face = 0;
        foreach (var f in facets) {
            if (canceled()) throw new OperationCanceledException("Экспорт отменён.");
            int[] indices = { f.V1, f.V2, f.V3 };
            if (map.Determinant < 0) (indices[1], indices[2]) = (indices[2], indices[1]);
            var geometric = (points[f.V2] - points[f.V1]).CrossProduct(points[f.V3] - points[f.V1]);
            foreach (int i in indices) {
                XYZ v = map.OfPoint(points[i]);
                XYZ n = normals.Count == 0 ? geometric : node.DistributionOfNormals switch {
                    DistributionOfNormals.AtEachPoint => normals[i], DistributionOfNormals.OnEachFacet => normals[face], _ => normals[0] };
                // Inverse transpose preserves normals under mirrored/non-uniform coordinate maps.
                var normal = new XYZ(inverse.BasisX.DotProduct(n), inverse.BasisY.DotProduct(n), inverse.BasisZ.DotProduct(n));
                normal = normal.GetLength() > 1e-15 ? normal.Normalize() : XYZ.BasisZ;
                UV uv = uvs.Count > i ? uvs[i] : new UV(0, 0);
                foreach (double c in new[] { v.X * 0.3048, v.Y * 0.3048, v.Z * 0.3048, normal.X, normal.Y, normal.Z, uv.U, uv.V }) {
                    if (!double.IsFinite(c) || Math.Abs(c) > float.MaxValue) throw new InvalidDataException("Некорректная координата сетки элемента " + p.Element.Id.Value);
                    p.Writer.Write((float)c);
                }
            }
            p.Writer.Write(material); p.Triangles++; face++;
            if (p.Stream.Length >= 4 * 1024 * 1024) FlushPiece(p);
        }
    }
    public void OnMaterial(MaterialNode node)
    {
        long sourceMaterial = node.MaterialId.Value;
        var m = doc.GetElement(node.MaterialId) as Material;
        var props = new SortedDictionary<string, object>(StringComparer.Ordinal);
        try { ReadAsset(node.GetAppearance(), "", props, 0); } catch (Exception e) { warnings.Add("Материал " + sourceMaterial + ": " + e.Message); }
        var color = node.Color;
        double[] rgb = { color.Red / 255.0, color.Green / 255.0, color.Blue / 255.0 };
        string signature = JsonSerializer.Serialize(new { sourceMaterial, rgb, transparency = node.Transparency, smoothness = node.Smoothness, props });
        if (materialSignatures.TryGetValue(signature, out material)) return;
        material = materials.Count; materialSignatures.Add(signature, material);
        var textures = new SortedDictionary<string, string>(StringComparer.Ordinal);
        foreach (var property in props) {
            if (property.Value is not string path || !IsTextureProperty(property.Key, path)) continue;
            try {
                string? copied = CopyTexture(path);
                if (copied != null) textures[property.Key] = copied;
            } catch (Exception e) when (e is IOException || e is UnauthorizedAccessException || e is ArgumentException) {
                warnings.Add("Не удалось разрешить текстуру " + path + ": " + e.Message);
            }
        }
        string? baseTexture = textures.FirstOrDefault(p => p.Key.Contains("diffuse", StringComparison.OrdinalIgnoreCase) || p.Key.Contains("albedo", StringComparison.OrdinalIgnoreCase) || p.Key.Contains("concrete_color", StringComparison.OrdinalIgnoreCase)).Value;
        if (baseTexture == null && textures.Count == 1 && !new[] { "bump", "normal", "roughness", "cutout", "opacity" }.Any(k => textures.First().Key.Contains(k, StringComparison.OrdinalIgnoreCase))) baseTexture = textures.First().Value;
        materials[material] = new { id = material, source_material_id = sourceMaterial.ToString(), name = m?.Name ?? "Default", color = rgb,
            transparency = node.Transparency, smoothness = node.Smoothness, properties = props, textures, base_texture = baseTexture,
            signature_sha256 = Convert.ToHexString(SHA256.HashData(System.Text.Encoding.UTF8.GetBytes(signature))).ToLowerInvariant() };
    }
    static bool IsTextureProperty(string key, string value) => !string.IsNullOrWhiteSpace(value) &&
        (key.Contains("bitmap", StringComparison.OrdinalIgnoreCase) ||
        new[] { ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".tga", ".exr", ".hdr" }.Contains(Path.GetExtension(value.Split('|')[0]).ToLowerInvariant()));
    string? CopyTexture(string raw)
    {
        if (textureCache.TryGetValue(raw, out var cached)) return cached;
        string source = Environment.ExpandEnvironmentVariables(raw.Split('|')[0].Trim());
        if (string.IsNullOrWhiteSpace(source)) { textureCache[raw] = null; return null; }
        // No downloads or network searches. Explicit network assets are reported as unavailable.
        if (source.StartsWith("\\\\", StringComparison.Ordinal) || source.Contains("://", StringComparison.Ordinal)) {
            warnings.Add("Сетевая текстура не скопирована: " + raw); textureCache[raw] = null; return null;
        }
        string[] libraryRoots = {
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonProgramFiles), "Autodesk Shared", "Materials", "Textures"),
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonProgramFilesX86), "Autodesk Shared", "Materials", "Textures")
        };
        string? found = TextureLookup.Exact(source, sourceDirectory, libraryRoots, out bool exactAmbiguous);
        if (exactAmbiguous) {
            warnings.Add("Различающиеся текстуры в библиотеках по одному точному пути: " + raw); textureCache[raw] = null; return null;
        }
        if (found == null) {
            if (textureFiles == null) {
                textureFiles = new();
                foreach (string baseDir in libraryRoots) {
                    if (!Directory.Exists(baseDir)) continue;
                    try { textureFiles.AddRange(Directory.EnumerateFiles(baseDir, "*", SearchOption.AllDirectories).Take(50000)); } catch (Exception e) { warnings.Add("Поиск текстур: " + e.Message); }
                }
            }
            string besideSource = Path.Combine(sourceDirectory, Path.GetFileName(source));
            found = TextureLookup.UniqueBasename(source, textureFiles.Concat(File.Exists(besideSource) ? new[] { besideSource } : Array.Empty<string>()), out bool basenameAmbiguous);
            if (basenameAmbiguous) warnings.Add("Неоднозначное имя текстуры; требуется исходный путь: " + raw);
        }
        if (found == null) { warnings.Add("Текстура не найдена: " + raw); textureCache[raw] = null; return null; }
        try {
            using var input = new FileStream(found, FileMode.Open, FileAccess.Read, FileShare.Read);
            string hash = Convert.ToHexString(SHA256.HashData(input)).ToLowerInvariant(); input.Position = 0;
            string name = hash + Path.GetExtension(found).ToLowerInvariant();
            string folder = Path.Combine(output, "textures"); Directory.CreateDirectory(folder);
            string dest = Path.Combine(folder, name);
            if (!File.Exists(dest)) { using var copy = new FileStream(dest, FileMode.CreateNew, FileAccess.Write); input.CopyTo(copy); }
            string relative = "textures/" + name; textureCache[raw] = relative; return relative;
        } catch (Exception e) { warnings.Add("Не удалось скопировать текстуру " + raw + ": " + e.Message); textureCache[raw] = null; return null; }
    }
    void ReadAsset(Asset asset, string prefix, SortedDictionary<string, object> props, int depth)
    {
        if (depth > 8) { warnings.Add("Глубина описания материала ограничена: " + prefix); return; }
        for (int i = 0; i < asset.Size; i++) {
            var p = asset[i]; string key = prefix + p.Name;
            try {
                if (p is AssetPropertyString s) props[key] = s.Value;
                else if (p is AssetPropertyDouble d) props[key] = d.Value;
                else if (p is AssetPropertyDistance distance) props[key] = distance.Value;
                else if (p is AssetPropertyDoubleArray4d a) props[key] = a.GetValueAsDoubles().ToArray();
                else if (p is AssetPropertyBoolean b) props[key] = b.Value;
                else if (p is AssetPropertyInteger integer) props[key] = integer.Value;
                for (int j = 0; j < p.NumberOfConnectedProperties; j++) if (p.GetConnectedProperty(j) is Asset child) ReadAsset(child, key + "/" + j + "/", props, depth + 1);
            } catch (Exception e) { warnings.Add("Свойство материала " + key + ": " + e.Message); }
        }
    }
}
