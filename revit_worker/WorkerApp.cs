using System;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Security.Cryptography;
using System.Collections.Generic;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using Autodesk.Revit.UI.Events;
using Autodesk.Revit.DB.Events;

namespace XIVgate.RevitBridge;

internal sealed class WorkerImplementation : IExternalApplication
{
    string root = "", token = "";
    string? session;
    readonly HashSet<string> seen = new();
    readonly List<string> warnings = new();
    readonly List<string> scanWarnings = new();
    readonly List<object> warningRecords = new();
    Document? document;
    string original = "", originalHash = "", sourceVersion = "";
    JsonElement active;
    bool busy, enabled, scanReady, idlingSeen;
    DateTime nextPoll;
    DateTime nextCancelPoll;
    bool cancellationCached;
    bool openingOwnedCopy, exportingOwnedCopy;
    string ownedCopyPath = "", failureAbortReason = "";
    ExternalEvent? requestEvent;
    RequestWake? requestWake;
    sealed class RequestEventHandler : IExternalEventHandler
    {
        readonly WorkerImplementation owner;
        public RequestEventHandler(WorkerImplementation owner) { this.owner = owner; }
        public string GetName() => "XIVgate Revit Blender Bridge requests";
        public void Execute(UIApplication app)
        {
            try { owner.ExecutePending(app, true); }
            catch (Exception ex) { owner.Log("ExternalEvent.Execute failed: " + ex); }
        }
    }
    public Result OnStartup(UIControlledApplication app)
    {
        string? job = Environment.GetEnvironmentVariable("XIVGATE_RBB_JOB_ROOT");
        string? secret = Environment.GetEnvironmentVariable("XIVGATE_RBB_SESSION_TOKEN");
        if (string.IsNullOrWhiteSpace(job) || string.IsNullOrWhiteSpace(secret)) return Result.Succeeded;
        bool rootValidated = false;
        string stage = "validate_job_root";
        try {
            root = Path.GetFullPath(job);
            if (!Compat.FullyQualified(job) || !Directory.Exists(root)) throw new IOException("Рабочий каталог Revit не существует или задан относительным путём.");
            Protocol.Below(root, Path.Combine(root, "request.json"));
            rootValidated = true;
            Log("Worker bootstrap entered; process " + Compat.ProcessId);
            token = secret;
            stage = "validate_session";
            session = Environment.GetEnvironmentVariable("XIVGATE_RBB_SESSION_ID");
            if (session != null && !Guid.TryParse(session, out _)) throw new ArgumentException("XIVGATE_RBB_SESSION_ID должен содержать UUID.");
            stage = "read_startup_request";
            TryReadStartupRequest();
            if (active.ValueKind == JsonValueKind.Object) Status("starting", "Запуск обработчика Revit; ожидание готовности интерфейса", 0);
            // Never re-execute a previously answered request after a worker restart.
            string response = Path.Combine(root, "response.json");
            if (File.Exists(response)) {
                try { using var old = Protocol.Read(response); seen.Add(Protocol.String(old.RootElement, "request_id")); } catch { }
            }
            string ledger = Path.Combine(root, "worker_requests.json");
            if (File.Exists(ledger)) {
                try {
                    using var old = Protocol.Read(ledger);
                    foreach (var id in old.RootElement.GetProperty("processed").EnumerateArray())
                        if (id.ValueKind == JsonValueKind.String) seen.Add(id.GetString()!);
                } catch { }
            }
            stage = "subscribe_main_thread_events";
            // Create belongs to this valid Revit API context; only Raise is used on watcher threads.
            requestEvent = ExternalEvent.Create(new RequestEventHandler(this));
            enabled = true;
            app.Idling += OnIdling;
            app.DialogBoxShowing += OnDialog;
            app.ControlledApplication.FailuresProcessing += OnFailuresProcessing;
            requestWake = new RequestWake(root, () => requestEvent.Raise().ToString(), Log);
            requestWake.Signal(); // A prewritten scan may precede watcher registration.
            Log("Worker startup; Revit " + app.ControlledApplication.VersionNumber);
            return Result.Succeeded;
        } catch (Exception ex) {
            enabled = false;
            DisposeDispatch();
            try { app.Idling -= OnIdling; app.DialogBoxShowing -= OnDialog; app.ControlledApplication.FailuresProcessing -= OnFailuresProcessing; } catch { }
            if (rootValidated) {
                Log("Worker startup failure at " + stage + ": " + ex);
                try {
                    Protocol.Atomic(Path.Combine(root, "startup_error.json"), new { schema = 1, session_id = session, request_id = Protocol.String(active, "request_id"), stage, error = ex.ToString(), process_id = Compat.ProcessId, utc = DateTime.UtcNow.ToString("O") });
                    if (active.ValueKind == JsonValueKind.Object) { Status("error", "Ошибка запуска обработчика Revit: " + ex.Message, 0); Respond(false, error: ex.Message); }
                } catch (Exception reportingError) { Log("Cannot report startup failure: " + reportingError); }
            }
            return Result.Failed;
        }
    }
    void TryReadStartupRequest()
    {
        try {
            string path = Path.Combine(root, "request.json");
            if (!File.Exists(path)) return;
            using var json = Protocol.Read(path);
            if (!Protocol.Authenticated(json.RootElement, token, session)) return;
            active = json.RootElement.Clone();
            session ??= Protocol.String(active, "session_id");
        } catch (Exception ex) when (ex is IOException || ex is JsonException) { Log("Startup request not yet readable: " + ex.Message); }
    }
    public Result OnShutdown(UIControlledApplication app)
    {
        DisposeDispatch();
        if (enabled) { app.Idling -= OnIdling; app.DialogBoxShowing -= OnDialog; app.ControlledApplication.FailuresProcessing -= OnFailuresProcessing; }
        // Normal shutdown request closes our owned document in the valid Idling API context.
        return Result.Succeeded;
    }
    void DisposeDispatch()
    {
        // Waits out the short Raise callback before disposing the API event on the Revit thread.
        try { requestWake?.Dispose(); } catch (Exception ex) { Log("Request watcher disposal: " + ex.Message); }
        requestWake = null;
        try { requestEvent?.Dispose(); } catch (Exception ex) { Log("ExternalEvent disposal: " + ex.Message); }
        requestEvent = null;
    }
    void OnIdling(object? sender, IdlingEventArgs args)
    {
        if (enabled && !idlingSeen) { idlingSeen = true; Log("First Idling sender: " + (sender?.GetType().FullName ?? "null")); }
        if (sender is UIApplication ui) ExecutePending(ui, false);
    }
    void ExecutePending(UIApplication ui, bool externalWake)
    {
        if (!enabled || busy || (!externalWake && DateTime.UtcNow < nextPoll)) return;
        nextPoll = DateTime.UtcNow.AddMilliseconds(200);
        string path = Path.Combine(root, "request.json");
        if (!File.Exists(path)) return;
        JsonElement request;
        try {
            using var json = Protocol.Read(path); request = json.RootElement.Clone();
            requestWake?.ReadSucceeded();
            if (!Protocol.Authenticated(request, token, session)) return;
        } catch (Exception e) when (e is IOException || e is JsonException) {
            Log("Transient request read failure: " + e.Message);
            requestWake?.ScheduleReadRetry();
            return;
        }
        string id = Protocol.String(request, "request_id");
        if (!seen.Add(id)) return;
        Log("Consuming request " + id + " via " + (externalWake ? "ExternalEvent" : "Idling"));
        // Mark before executing, including failures, to avoid a destructive retry loop.
        session ??= Protocol.String(request, "session_id"); active = request; busy = true;
        nextCancelPoll = DateTime.MinValue; cancellationCached = false;
        warnings.Clear();
        failureAbortReason = "";
        string action = Protocol.String(request, "action");
        try {
            Protocol.Atomic(Path.Combine(root, "worker_requests.json"), new { schema = 1, session_id = session, processed = seen.OrderBy(x => x).ToArray() });
            if (action == "scan") Scan(ui, request);
            else if (action == "export") { warnings.AddRange(scanWarnings); Export(request); }
            else if (action == "shutdown") Shutdown(ui);
            else throw new ArgumentException("Неизвестная операция: " + action);
        } catch (Exception ex) {
            Log(ex.ToString());
            if (action == "scan") {
                scanReady = false; scanWarnings.Clear();
                if (document != null) { try { document.Close(false); document = null; } catch (Exception closeError) { Log("Cannot close failed scan copy: " + closeError.Message); } }
            }
            try { Status("error", ex.Message, 0); Respond(false, error: ex.Message); }
            catch (Exception reportingError) { Log("Cannot report request failure: " + reportingError); }
        } finally { busy = false; }
        // A new atomic request may have arrived while a long API operation was running.
        // Queue one tail check; a seen/absent request returns above and does not reschedule itself.
        if (action != "shutdown") requestWake?.Signal();
    }
    void Scan(UIApplication ui, JsonElement request)
    {
        scanReady = false; scanWarnings.Clear();
        warningRecords.Clear();
        CheckCancel(); Status("opening", "Подготовка безопасной копии RVT", 0.02);
        string source = Protocol.String(request, "source_rvt");
        if (!Compat.FullyQualified(source) || !File.Exists(source) || !string.Equals(Path.GetExtension(source), ".rvt", StringComparison.OrdinalIgnoreCase))
            throw new IOException("Не найден исходный файл RVT.");
        string copy = Protocol.Below(Path.Combine(root, "input"), Protocol.String(request, "source_copy"));
        if (string.Equals(Path.GetFullPath(source), copy, StringComparison.OrdinalIgnoreCase)) throw new IOException("Копия совпадает с исходным RVT.");
        if (document != null) { document.Close(false); document = null; }
        Directory.CreateDirectory(Path.GetDirectoryName(copy)!);
        // Open source read-only at the OS level. Hash precisely the copied stream.
        using (var input = new FileStream(source, FileMode.Open, FileAccess.Read, FileShare.Read))
        using (var output = new FileStream(copy, FileMode.CreateNew, FileAccess.Write, FileShare.None)) input.CopyTo(output);
        original = Path.GetFullPath(source);
        using (var hashInput = File.OpenRead(copy)) originalHash = Compat.Hex(Compat.Sha256(hashInput)).ToLowerInvariant();
        using var info = BasicFileInfo.Extract(copy);
        sourceVersion = info.Format;
        if (info.IsSavedInLaterVersion) throw new IOException("Модель создана в более новой версии Revit.");
        if (!info.IsSavedInCurrentVersion) warnings.Add("Модель " + info.Format + " обновляется только в рабочей копии; исходный RVT не сохраняется.");
        string centralPath = info.IsWorkshared ? info.CentralPath : "";
        bool links = request.TryGetProperty("options", out var opts) && opts.TryGetProperty("load_links", out var load) && load.ValueKind == JsonValueKind.True;
        bool normalized = false;
        Log("Copy ready; source format " + info.Format + "; workshared=" + info.IsWorkshared + "; server-origin=" + OpenPolicy.NormalizeServerSnapshot(info.IsWorkshared, centralPath));
        if (OpenPolicy.PrepareLocalWorksharedCopy(info.IsWorkshared, links)) {
            // Public API only. First detach without expanding user worksets, so
            // server links cannot be loaded before their desired state is set.
            Status("detaching", "Отсоединение RVT в локальной рабочей копии с закрытыми рабочими наборами", 0.04);
            using (var detach = new OpenOptions { Audit = false, DetachFromCentralOption = DetachFromCentralOption.DetachAndPreserveWorksets })
            using (var closed = new WorksetConfiguration(WorksetConfigurationOption.CloseAllWorksets)) {
                detach.SetOpenWorksetsConfiguration(closed);
                ownedCopyPath = copy;
                openingOwnedCopy = true;
                try { document = ui.Application.OpenDocumentFile(ModelPathUtils.ConvertUserVisiblePathToModelPath(copy), detach); }
                finally { openingOwnedCopy = false; }
            }
            if (failureAbortReason.Length > 0) throw new InvalidOperationException(failureAbortReason);
            CheckCancel();
            foreach (var linkType in new FilteredElementCollector(document).OfClass(typeof(RevitLinkType)).Cast<RevitLinkType>()) {
                if (linkType.IsNestedLink) continue; // Top-level unload also covers nested references.
                bool loaded = RevitLinkType.IsLoaded(document, linkType.Id);
                Log("Local reference before save: " + linkType.Id.Value + "; loaded=" + loaded);
                // Unload uses its own API transaction; do not wrap it in ours.
                // IsLoaded is false for links in closed worksets even when the
                // saved global load state is Loaded. Unload them explicitly.
                linkType.Unload(null);
                warnings.Add("Связь оставлена незагруженной в рабочей копии: " + linkType.Name);
            }
            // Do not silently promise the old TransmissionData path rebasing
            // for CAD/other references. Inspect document metadata without
            // resolving or reloading their external files.
            var relativeReferences = new List<string>();
            foreach (var referenceId in ExternalFileUtils.GetAllExternalFileReferences(document)) {
                using var reference = ExternalFileUtils.GetExternalFileReference(document, referenceId);
                if (reference.ExternalFileReferenceType != ExternalFileReferenceType.RevitLink && reference.PathType == PathType.Relative)
                    relativeReferences.Add(document.GetElement(referenceId)?.Name ?? referenceId.Value.ToString());
            }
            if (relativeReferences.Count > 0)
                warnings.Add("Относительные внешние ссылки, кроме RVT: " + relativeReferences.Count + "; проверьте их геометрию после импорта. Пути в отсоединённой копии автоматически не переназначались: " + string.Join(", ", relativeReferences.Take(8)));
            string prepared = Protocol.Below(Path.Combine(root, "input"), Path.Combine(root, "input", Protocol.String(request, "request_id") + "-detached.rvt"));
            if (File.Exists(prepared)) throw new IOException("Подготовленная копия уже существует.");
            Status("normalizing", "Сохранение отсоединённой локальной рабочей копии", 0.06);
            using (var save = new SaveAsOptions { OverwriteExistingFile = false, MaximumBackups = 1 }) {
                if (document.IsWorkshared) {
                    using var worksharing = new WorksharingSaveAsOptions { SaveAsCentral = true };
                    save.SetWorksharingOptions(worksharing);
                }
                document.SaveAs(prepared, save);
            }
            document.Close(false);
            document = null;
            using (var preparedInfo = BasicFileInfo.Extract(prepared)) {
                if (preparedInfo.IsWorkshared && OpenPolicy.NormalizeServerSnapshot(true, preparedInfo.CentralPath))
                    throw new IOException("Рабочая копия всё ещё ссылается на Revit Server; сетевое чтение ссылок не выполнялось.");
            }
            copy = prepared;
            normalized = true;
            warnings.Add("Модель общего проекта отсоединена и сохранена только в рабочей папке задания; исходный RVT не изменён.");
            Log("Workshared snapshot normalized locally with saved unloaded links.");
        }
        var modelPath = ModelPathUtils.ConvertUserVisiblePathToModelPath(copy);
        Status("references", "Подготовка внешних ссылок локальной копии", 0.07);
        // DoNotLoadLinks is documented by the shipped XML but absent from the public 2027 DLL.
        // TransmissionData is the public API for suppressing link loads, applied ONLY to our copy.
        if (!normalized) {
            Log("Reading local TransmissionData.");
            using var transmission = TransmissionData.ReadTransmissionData(modelPath);
            if (transmission != null) {
                bool changed = false;
                foreach (var referenceId in transmission.GetAllExternalFileReferenceIds()) {
                    var reference = transmission.GetLastSavedReferenceData(referenceId);
                    bool suppress = !links && reference.ExternalFileReferenceType == ExternalFileReferenceType.RevitLink;
                    bool relative = reference.PathType == PathType.Relative;
                    if (suppress || relative) {
                        var referencePath = relative ? ModelPathUtils.ConvertUserVisiblePathToModelPath(Path.GetFullPath(Path.Combine(Path.GetDirectoryName(original)!, ModelPathUtils.ConvertModelPathToUserVisiblePath(reference.GetPath())))) : reference.GetPath();
                        var status = reference.GetLinkedFileStatus();
                        bool shouldLoad = !suppress && status != LinkedFileStatus.Unloaded && status != LinkedFileStatus.LocallyUnloaded && status != LinkedFileStatus.InClosedWorkset;
                        transmission.SetDesiredReferenceData(referenceId, referencePath, relative ? PathType.Absolute : reference.PathType, shouldLoad);
                        changed = true;
                        if (suppress) warnings.Add("Связь оставлена незагруженной в копии: " + ModelPathUtils.ConvertModelPathToUserVisiblePath(referencePath));
                    }
                }
                if (changed) { transmission.IsTransmitted = true; TransmissionData.WriteTransmissionData(modelPath, transmission); }
            }
        } else {
            // ReadTransmissionData can resolve RSN references even in a newly
            // saved disk-central file. Native Unload + SaveAs already persisted
            // the required state; never invoke that server-aware path here.
            Log("Using saved native link-unload state; TransmissionData skipped for normalized copy.");
        }
        using var options = new OpenOptions { Audit = false };
        if (info.IsWorkshared) options.DetachFromCentralOption = DetachFromCentralOption.DetachAndPreserveWorksets;
        using var worksets = new WorksetConfiguration(WorksetConfigurationOption.OpenAllWorksets);
        options.SetOpenWorksetsConfiguration(worksets);
        string openingMessage = normalized ? "Открытие подготовленной локальной копии RVT" : info.IsSavedInCurrentVersion ? "Открытие копии RVT" : "Обновление копии RVT с " + info.Format + " до " + ui.Application.VersionNumber + "; исходный файл не изменяется";
        Status("opening", openingMessage, 0.08);
        ownedCopyPath = copy;
        openingOwnedCopy = true;
        try { document = ui.Application.OpenDocumentFile(modelPath, options); }
        finally { openingOwnedCopy = false; }
        if (failureAbortReason.Length > 0) throw new InvalidOperationException(failureAbortReason);
        foreach (var warning in document.GetWarnings()) {
            CaptureWarning("persistent", warning.GetFailureDefinitionId().Guid.ToString(), warning.GetDescriptionText(), warning.GetSeverity().ToString(),
                warning.GetFailingElements().Select(id => id.Value.ToString()).ToArray(), "reported");
        }
        WriteWarningReport();
        CheckCancel(); Status("scanning", "Чтение категорий, рабочих наборов и элементов", 0.35);
        foreach (RevitLinkInstance link in new FilteredElementCollector(document).OfClass(typeof(RevitLinkInstance)))
            warnings.Add("Связь не экспортируется: " + link.Name + (link.GetLinkDocument() == null ? " (не загружена)" : " (загружена)"));
        var rows = new List<CatalogRow>();
        foreach (Element e in new FilteredElementCollector(document).WhereElementIsNotElementType()) {
            CheckCancel();
            if (e.ViewSpecific || e.Category == null || e.Category.CategoryType != CategoryType.Model) continue;
            // Include model categories even when currently hidden or lacking a bounding box.
            if (e is View || e is Material || e is GraphicsStyle) continue;
            var (recommendation, reason) = Recommend(e);
            var elementType = document.GetElement(e.GetTypeId());
            string typeName = elementType?.Name ?? "";
            string familyName = FamilyName(e, elementType);
            var (bboxMin, bboxMax) = WorldBounds(e);
            rows.Add(new CatalogRow(e.Id.Value.ToString(), e.UniqueId, e.Name, e.Category.Id.Value.ToString(), e.Category.Name,
                e.WorksetId.IntegerValue.ToString(), Workset(e), e.GetTypeId().Value.ToString(), typeName, familyName, bboxMin, bboxMax, recommendation, reason));
        }
        string catalog = Protocol.Below(root, Path.Combine(root, "catalog.json"));
        scanWarnings.Clear(); scanWarnings.AddRange(warnings);
        Protocol.Atomic(catalog, new { schema = 1, source = Source(),
            categories = rows.GroupBy(e => e.category_id).Select(g => new { id = g.Key, name = g.First().category_name, count = g.Count() }),
            worksets = rows.GroupBy(e => e.workset_id).Select(g => new { id = g.Key, name = g.First().workset_name, count = g.Count() }), elements = rows, warnings });
        Status("ready", "Каталог готов: " + rows.Count + " элементов", 1);
        Respond(true, catalogPath: catalog);
        scanReady = true;
    }
    void Export(JsonElement request)
    {
        if (!scanReady || document == null || !document.IsValidObject) throw new InvalidOperationException("Сначала успешно завершите сканирование текущего RVT.");
        var selected = Protocol.Selection(request);
        string output = Protocol.Below(root, Protocol.String(request, "output_dir"));
        if (Directory.Exists(output) && Directory.EnumerateFileSystemEntries(output).Any()) throw new IOException("Каталог экспорта уже содержит данные; создайте новую ревизию.");
        Directory.CreateDirectory(output);
        foreach (long id in selected) if (document.GetElement(new ElementId(id)) == null) throw new ArgumentException("Выбранный элемент отсутствует в модели: " + id);
        string detail = request.TryGetProperty("options", out var options) ? Protocol.String(options, "detail_level", "medium") : "medium";
        Status("exporting", "Экспорт выбранных элементов", 0.05);
        exportingOwnedCopy = true;
        try { GeometryExporter.Run(document, output, selected, Source(), original, detail, IsCanceled, warnings); }
        finally { exportingOwnedCopy = false; }
        if (failureAbortReason.Length > 0) throw new InvalidOperationException(failureAbortReason);
        CheckCancel();
        Status("completed", "Экспорт завершён", 1);
        Respond(true, manifestPath: Path.Combine(output, "manifest.json"));
    }
    void Shutdown(UIApplication ui)
    {
        Status("closing", "Закрытие собственной копии Revit", 1);
        if (document != null) { document.Close(false); document = null; }
        var exit = RevitCommandId.LookupPostableCommandId(PostableCommand.ExitRevit);
        // Environment opt-in identifies this process; do not close if a human opened another document in it.
        if (ui.Application.Documents.Cast<Document>().Any(d => !d.IsLinked)) throw new InvalidOperationException("В процессе Revit открыт посторонний документ. Автоматический выход отменён.");
        if (!ui.CanPostCommand(exit)) throw new InvalidOperationException("Revit пока не может выполнить выход.");
        ui.PostCommand(exit); Respond(true);
    }
    object Source() => new { path = original, sha256 = originalHash, revit_version = document?.Application.VersionNumber ?? "2027", revit_build = document?.Application.VersionBuild ?? "", original_version = sourceVersion };
    internal static string Workset(Element e) { try { return e.Document.GetWorksetTable().GetWorkset(e.WorksetId)?.Name ?? ""; } catch { return ""; } }
    static (string, string) Recommend(Element e)
    {
        long c = e.Category!.Id.Value;
        if (e is RevitLinkInstance) return ("exclude", "Связанные документы пока не экспортируются");
        if (c == (long)BuiltInCategory.OST_Windows || c == (long)BuiltInCategory.OST_Doors) return ("include", "Окно или дверь; флаг внутренней стены не используется");
        if (c == (long)BuiltInCategory.OST_CurtainWallPanels || c == (long)BuiltInCategory.OST_CurtainWallMullions || c == (long)BuiltInCategory.OST_Roofs || c == (long)BuiltInCategory.OST_Railings) return ("include", "Элемент внешней оболочки");
        if (e is Wall wall && wall.WallType.Function == WallFunction.Exterior) return ("include", "Стена с назначением Exterior");
        if (c == (long)BuiltInCategory.OST_Furniture || c == (long)BuiltInCategory.OST_PlumbingFixtures || c == (long)BuiltInCategory.OST_LightingFixtures) return ("exclude", "Оснащение; можно включить вручную");
        return ("review", "Проверьте назначение элемента; по умолчанию включён");
    }
    bool IsCanceled()
    {
        if (DateTime.UtcNow < nextCancelPoll) return cancellationCached;
        nextCancelPoll = DateTime.UtcNow.AddMilliseconds(100);
        foreach (string name in new[] { "cancel.json", "request.json" }) {
            try {
                string path = Path.Combine(root, name); if (!File.Exists(path)) continue;
                using var json = Protocol.Read(path); var r = json.RootElement;
                if (!Protocol.Authenticated(r, token, session)) continue;
                if (name == "cancel.json" && Protocol.String(r, "request_id") == Protocol.String(active, "request_id")) return cancellationCached = true;
                if (Protocol.String(r, "action") == "cancel" && Protocol.String(r, "target_request_id") == Protocol.String(active, "request_id")) return cancellationCached = true;
            } catch (Exception e) when (e is IOException || e is JsonException) { }
        }
        return false;
    }
    void CheckCancel() { if (IsCanceled()) throw new OperationCanceledException("Операция отменена пользователем."); }
    bool AuthenticatedOperation => enabled && busy && active.ValueKind == JsonValueKind.Object && Protocol.Authenticated(active, token, session);
    void OnFailuresProcessing(object? sender, FailuresProcessingEventArgs e)
    {
        if (!AuthenticatedOperation || !(openingOwnedCopy || exportingOwnedCopy)) return;
        try {
            var accessor = e.GetFailuresAccessor();
            if (!accessor.IsActive()) return;
            var failedDocument = accessor.GetDocument();
            // Revit may return a different managed wrapper. SDK Equals compares the underlying
            // open document; ReferenceEquals fails, and detached copies deliberately have no PathName.
            bool ownDocument = (document != null && document.IsValidObject && failedDocument.Equals(document)) ||
                (!string.IsNullOrWhiteSpace(failedDocument.PathName) && string.Equals(Path.GetFullPath(failedDocument.PathName), ownedCopyPath, StringComparison.OrdinalIgnoreCase));
            if (!ownDocument) {
                Log("Failure event outside verified copied document; left unchanged: " + failedDocument.Title);
                bool unverifiedError = false;
                foreach (var failure in accessor.GetFailureMessages()) {
                    string severity = failure.GetSeverity().ToString();
                    unverifiedError |= WarningPolicy.IsError(severity);
                    CaptureWarning("failures_processing_unverified_document", failure.GetFailureDefinitionId().Guid.ToString(), failure.GetDescriptionText(), severity,
                        failure.GetFailingElementIds().Select(id => id.Value.ToString()).ToArray(), "document_not_verified_no_changes");
                }
                if (unverifiedError) {
                    failureAbortReason = "Revit сообщил об ошибке документа, принадлежность которого рабочей копии не подтверждена. Автоматическое исправление отключено; подробности: warnings.json";
                    Status("requires_action", failureAbortReason, 0);
                }
                WriteWarningReport();
                return;
            }
            bool errors = false, deleted = false;
            foreach (var failure in accessor.GetFailureMessages()) {
                string severity = failure.GetSeverity().ToString();
                bool warning = WarningPolicy.DeleteWarning(severity);
                errors |= WarningPolicy.IsError(severity);
                CaptureWarning("failures_processing", failure.GetFailureDefinitionId().Guid.ToString(), failure.GetDescriptionText(), severity,
                    failure.GetFailingElementIds().Select(id => id.Value.ToString()).ToArray(), warning ? "warning_suppression_requested" : "not_resolved");
                if (warning) { accessor.DeleteWarning(failure); deleted = true; }
            }
            if (errors) {
                failureAbortReason = "Revit обнаружил ошибку в рабочей копии. Операция остановлена; элементы не исправлялись и не удалялись. Подробности: warnings.json";
                if (WarningPolicy.RequestRollback(accessor.IsActive(), accessor.IsTransactionBeingCommitted(), errors)) {
                    var handling = accessor.GetFailureHandlingOptions();
                    handling.SetClearAfterRollback(true);
                    accessor.SetFailureHandlingOptions(handling);
                    e.SetProcessingResult(FailureProcessingResult.ProceedWithRollBack);
                    Log("Owned-copy error: transaction rollback requested; errors recorded before clearing after rollback.");
                } else {
                    Log("Owned-copy error: rollback request is not valid in this failure context; default Revit handling preserved.");
                    Status("requires_action", failureAbortReason + "; Revit может потребовать решения в своём окне", 0);
                }
            } else if (deleted) e.SetProcessingResult(FailureProcessingResult.Continue);
            WriteWarningReport();
        } catch (Exception handlerError) {
            // Never attempt repair, element deletion or a permissive retry if failure handling is unavailable.
            failureAbortReason = "Не удалось безопасно обработать сообщения Revit: " + handlerError.Message;
            Log("Failure handler: " + handlerError);
            try { Status("requires_action", failureAbortReason, 0); WriteWarningReport(); } catch { }
        }
    }
    void CaptureWarning(string origin, string id, string description, string severity, string[] elementIds, string disposition)
    {
        warningRecords.Add(new { origin, id, description, severity, element_ids = elementIds, disposition, request_id = Protocol.String(active, "request_id") });
        string message = "Revit " + severity + " [" + id + "]: " + description;
        if (!warnings.Contains(message)) warnings.Add(message);
        Log(message + " (" + disposition + ")");
    }
    void WriteWarningReport()
    {
        Protocol.Atomic(Path.Combine(root, "warnings.json"), new { schema = 1, session_id = session,
            request_id = Protocol.String(active, "request_id"), source = original, records = warningRecords });
    }
    void OnDialog(object? sender, DialogBoxShowingEventArgs e)
    {
        if (!enabled) return;
        string message = e is TaskDialogShowingEventArgs task ? task.Message : e is MessageBoxShowingEventArgs box ? box.Message : "Revit ожидает ответа в диалоге";
        Log("Dialog " + e.DialogId + ": " + message);
        try {
            if (active.ValueKind != JsonValueKind.Object && File.Exists(Path.Combine(root, "request.json"))) {
                using var pending = Protocol.Read(Path.Combine(root, "request.json"));
                if (Protocol.Authenticated(pending.RootElement, token, session)) { active = pending.RootElement.Clone(); session ??= Protocol.String(active, "session_id"); }
            }
            if (WarningPolicy.AcknowledgeDocumentWarning(enabled, AuthenticatedOperation, busy, openingOwnedCopy, failureAbortReason.Length > 0, e.DialogId)) {
                bool accepted = e.OverrideResult(1);
                CaptureWarning("document_warning_dialog", e.DialogId,
                    (accepted ? "Стандартное предупреждение документа Revit принято (OK). " : "Revit не принял автоматическое подтверждение предупреждения. ") + message,
                    "Warning", Array.Empty<string>(), accepted ? "standard_document_warning_acknowledged" : "requires_action");
                WriteWarningReport();
                if (accepted) return;
                Log("Document warning OK override was not accepted; human action required.");
            }
            if (active.ValueKind == JsonValueKind.Object) {
                warnings.Add("Диалог Revit " + e.DialogId + ": " + message);
                Status("requires_action", message, 0, e.DialogId);
            }
        } catch (Exception reportingError) { Log("Cannot report dialog: " + reportingError.Message); }
        // The single exact document-warning allowlist above never applies to license, security or repair dialogs.
    }
    void Status(string state, string message, double progress, string? dialog = null) => Protocol.Atomic(Path.Combine(root, "status.json"),
        new { schema = 1, session_id = session, request_id = Protocol.String(active, "request_id"), state, message, progress, dialog_id = dialog });
    void Respond(bool success, string? catalogPath = null, string? manifestPath = null, string? error = null) => Protocol.Atomic(Path.Combine(root, "response.json"),
        new { schema = 1, session_id = session, request_id = Protocol.String(active, "request_id"), success, action = Protocol.String(active, "action"), catalog_path = catalogPath, manifest_path = manifestPath, warnings_report_path = File.Exists(Path.Combine(root, "warnings.json")) ? Path.Combine(root, "warnings.json") : null, error, warnings });
    void Log(string message) { try { File.AppendAllText(Path.Combine(root, "worker.log"), DateTime.UtcNow.ToString("O") + " " + message + Environment.NewLine); } catch { } }
    static string FamilyName(Element element, Element? elementType)
    {
        try {
            if (element is FamilyInstance instance) {
                string? name = instance.Symbol?.Family?.Name;
                if (name != null) return name;
            }
        } catch { }
        try { return (elementType as ElementType)?.FamilyName ?? ""; }
        catch { return ""; }
    }
    static (double[]?, double[]?) WorldBounds(Element element)
    {
        try {
            using var box = element.get_BoundingBox(null);
            if (box == null) return (null, null);
            XYZ low = box.Min, high = box.Max;
            var transform = box.Transform;
            double[] minimum = { double.PositiveInfinity, double.PositiveInfinity, double.PositiveInfinity };
            double[] maximum = { double.NegativeInfinity, double.NegativeInfinity, double.NegativeInfinity };
            for (int corner = 0; corner < 8; corner++) {
                var point = transform.OfPoint(new XYZ((corner & 1) == 0 ? low.X : high.X, (corner & 2) == 0 ? low.Y : high.Y, (corner & 4) == 0 ? low.Z : high.Z));
                double[] values = { point.X * 0.3048, point.Y * 0.3048, point.Z * 0.3048 };
                for (int axis = 0; axis < 3; axis++) {
                    if (!Compat.Finite(values[axis])) return (null, null);
                    minimum[axis] = Math.Min(minimum[axis], values[axis]);
                    maximum[axis] = Math.Max(maximum[axis], values[axis]);
                }
            }
            return (minimum, maximum);
        } catch { return (null, null); }
    }
    sealed record CatalogRow(string id, string unique_id, string name, string category_id, string category_name, string workset_id, string workset_name, string type_id, string type_name,
        string family_name, double[]? bbox_min, double[]? bbox_max, string recommendation, string reason);
}
