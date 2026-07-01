import Foundation

/// macui 端的模型下载进度状态。
///
/// 关注一件事：当前正在下载的模型（如果有）的实时进度。
///
/// 数据流：
/// - Python 端 model_downloader daemon 写 `/tmp/whicc-out/model_download.jsonl`
/// - macui 端订阅这个文件，跟 EventWatcher 同款模式
/// - 解析每行事件（started/progress/completed/failed/cancelled）更新 @Published
///
/// 第一阶段：UI 先摆好，用 mock 数据测试 UI 效果。
/// 第二阶段：接 Python daemon 真实数据流。
///
/// 并发模型：
/// - 类不标 @MainActor（保持跟 EventWatcher / GlossaryState 同款非隔离风格）
/// - 后台线程读 JSONL，主线程更新 @Published（避免 SwiftUI 警告）
final class ModelDownloadState: ObservableObject {

    /// 单个模型的下载状态。Identifiable 让 ForEach 能稳定地识别行，
    /// 避免 reload 时整个列表重渲染导致「行变来变去」的视觉跳动。
    struct DownloadState: Equatable, Identifiable {
        let modelId: String
        var status: Status
        var pct: Double          // 0.0 ~ 1.0，进度
        var downloadedBytes: Int64
        var totalBytes: Int64
        var error: String?
        /// 状态变成 .completed / .failed / .cancelled 的时间，
        /// 用于 macOS HIG 保留 2 秒后清除（用户看到 ✓ 后清掉，避免 UI 空间）。
        /// .downloading 时这个值为 nil。
        var finishedAt: Date?

        var id: String { modelId }

        enum Status: Equatable {
            case downloading
            case completed
            case failed
            case cancelled
        }

        var isActive: Bool {
            status == .downloading
        }

        /// 是否到了保留期（应清除）。下载中的永远 false。
        func shouldCleanup(now: Date, retention: TimeInterval) -> Bool {
            guard let finishedAt else { return false }
            return now.timeIntervalSince(finishedAt) > retention
        }
    }

    /// 当前所有正在追踪的下载（按 modelId 索引）。
    /// 字典 storage 避免重复，但**只用字典遍历会让 SwiftUI 跳行**——
    /// 因为 Array(dict.values) 每次 reload 都新建 array，SwiftUI 看到
    /// array identity 变化会重新创建所有行。改用「稳定顺序的 array」
    /// 暴露给 View，配合 Identifiable 让 ForEach 不重建行。
    @Published private(set) var downloads: [String: DownloadState] = [:]

    /// 按 modelId 排序的 downloads，供 SwiftUI ForEach 用。
    /// 计算属性，SwiftUI 会在 body 求值时调用，但只要 downloads 字典
    /// 内容稳定，排序结果也稳定（SwiftUI 会 diff 行）。
    var downloadsSorted: [DownloadState] {
        downloads.values.sorted { $0.modelId < $1.modelId }
    }

    /// macOS HIG：保留已完成状态 2 秒再清掉（让用户看到 "✓ 完成"）
    private static let completionRetention: TimeInterval = 2.0

    private let stateFileURL: URL
    private var pollThread: Thread?
    private var isPolling: Bool = false

    /// 上次读到的文件 byte offset。增量读时跳过已读部分，
    /// 避免每次都从头读整个 JSONL（文件可能几 MB，3 秒一次全读会卡 UI）。
    /// 每次 reload 维护这个值；文件被截断/重写时回退到 0。
    private var lastReadOffset: UInt64 = 0

    init(stateFile: URL = URL(fileURLWithPath: AppPaths.runDir + "/model_download.jsonl")) {
        self.stateFileURL = stateFile
    }

    /// 启动后台轮询线程（3 秒一次读 JSONL）
    func startPolling() {
        guard pollThread == nil else { return }
        isPolling = true
        let thread = Thread { [weak self] in
            let timer = Timer(timeInterval: 0.5, repeats: true) { [weak self] _ in
                self?.reload()
            }
            RunLoop.current.add(timer, forMode: .default)
            RunLoop.current.run()
        }
        thread.name = "ModelDownloadState.poll"
        thread.qualityOfService = .utility
        pollThread = thread
        thread.start()
    }

    func stopPolling() {
        isPolling = false
        pollThread?.cancel()
        pollThread = nil
    }

    deinit {
        // Thread 不支持直接 cancel，让 RunLoop 自然结束
        isPolling = false
    }

    /// 触发下载请求：写 <runDir>/model_download_request.json
    /// Python daemon 轮询这个文件来启动下载
    func requestDownload(modelId: String) {
        let requestFile = URL(fileURLWithPath: AppPaths.runDir + "/model_download_request.json")
        let payload: [String: String] = [
            "action": "download",
            "model_id": modelId,
            "ts": String(Int(Date().timeIntervalSince1970)),
        ]
        guard let data = try? JSONSerialization.data(withJSONObject: payload, options: .prettyPrinted) else {
            return
        }
        try? data.write(to: requestFile, options: .atomic)
    }

    /// 触发取消下载：写同样的 request 文件但 action=cancel
    func requestCancel(modelId: String) {
        let requestFile = URL(fileURLWithPath: AppPaths.runDir + "/model_download_request.json")
        let payload: [String: String] = [
            "action": "cancel",
            "model_id": modelId,
            "ts": String(Int(Date().timeIntervalSince1970)),
        ]
        guard let data = try? JSONSerialization.data(withJSONObject: payload, options: .prettyPrinted) else {
            return
        }
        try? data.write(to: requestFile, options: .atomic)
    }

    /// 重新加载：从 JSONL 读最新事件。
    /// 增量读：只读 lastReadOffset 之后的字节，避免大文件全读卡 UI。
    /// 后台线程调用，需要 hop 到主线程写 @Published。
    private func reload() {
        let update = Self.readNewEvents(from: stateFileURL, lastOffset: lastReadOffset)
        if let update = update {
            lastReadOffset = update.newOffset
            // 合并到已有字典（不替换整个字典）
            // 注意：self.downloads 是 actor-isolated 的 @Published，
            // 在后台线程读它没问题（读不要 actor），但写要在主线程。
            // 这里我们不直接读 self.downloads（避免线程问题）——
            // 把增量 events 传给主线程，让主线程合并。
            DispatchQueue.main.async { [weak self] in
                guard let self else { return }
                if update.resetFromStart {
                    self.downloads = [:]
                }
                for event in update.newEvents {
                    if let state = Self.parseEvent(modelId: event.modelId, eventDict: event.dict) {
                        self.downloads[event.modelId] = state
                    }
                }
                // macOS HIG：完成后保留几秒让用户看到 ✓，再清除。
                // 每次 reload 都检查一遍，避免过期项长期占 UI。
                self.cleanupFinished(retention: Self.completionRetention)
            }
        }
        // update == nil 表示文件未变或读取失败，不做任何事
    }

    /// 清理已过期的完成/失败/取消项（保留期已过）。
    /// 必须在主线程调用（修改 @Published）。
    private func cleanupFinished(retention: TimeInterval) {
        let now = Date()
        let expiredKeys = downloads
            .filter { $0.value.shouldCleanup(now: now, retention: retention) }
            .map { $0.key }
        for key in expiredKeys {
            downloads.removeValue(forKey: key)
        }
    }

    /// 单个事件（解析后的中间表示）
    private struct ParsedEvent {
        let modelId: String
        let dict: [String: Any]
    }

    /// 读 JSONL 的返回值
    private struct ReadResult {
        let newEvents: [ParsedEvent]
        let newOffset: UInt64
        let resetFromStart: Bool
    }

    /// 增量读 JSONL：从 lastOffset 开始读新追加的字节，解析为 events
    private static func readNewEvents(
        from url: URL,
        lastOffset: UInt64
    ) -> ReadResult? {
        let fm = FileManager.default
        guard let attrs = try? fm.attributesOfItem(atPath: url.path),
              let fileSize = attrs[.size] as? UInt64 else {
            return nil
        }
        // 文件被截断/重写（如 daemon 重启）→ 重置 offset，从头读
        let (readOffset, resetFromStart): (UInt64, Bool)
        if fileSize < lastOffset {
            readOffset = 0
            resetFromStart = true
        } else {
            readOffset = lastOffset
            resetFromStart = false
        }
        if fileSize == readOffset {
            // 文件未变，没新事件
            return ReadResult(newEvents: [], newOffset: readOffset, resetFromStart: resetFromStart)
        }
        guard let handle = try? FileHandle(forReadingFrom: url) else {
            return nil
        }
        defer { try? handle.close() }
        do {
            try handle.seek(toOffset: readOffset)
        } catch {
            return nil
        }
        let newData: Data
        do {
            newData = try handle.readToEnd() ?? Data()
        } catch {
            return nil
        }
        // 解析新行
        guard let newText = String(data: newData, encoding: .utf8) else {
            return ReadResult(newEvents: [], newOffset: fileSize, resetFromStart: resetFromStart)
        }
        var events: [ParsedEvent] = []
        for line in newText.split(separator: "\n", omittingEmptySubsequences: true) {
            guard let lineData = line.data(using: .utf8),
                  let event = try? JSONSerialization.jsonObject(with: lineData) as? [String: Any],
                  let modelId = event["model_id"] as? String else { continue }
            events.append(ParsedEvent(modelId: modelId, dict: event))
        }
        return ReadResult(newEvents: events, newOffset: fileSize, resetFromStart: resetFromStart)
    }

    /// 把 JSONL event dict 转成 DownloadState。
    /// 与之前的 readAllEvents 同样的解析逻辑，提取出来便于复用。
    private static func parseEvent(modelId: String, eventDict: [String: Any]) -> DownloadState? {
        guard let status = eventDict["event"] as? String else { return nil }
        switch status {
        case "started":
            return DownloadState(
                modelId: modelId,
                status: .downloading,
                pct: 0,
                downloadedBytes: 0,
                totalBytes: 0,
                error: nil
            )
        case "progress":
            return DownloadState(
                modelId: modelId,
                status: .downloading,
                pct: eventDict["pct"] as? Double ?? 0,
                downloadedBytes: eventDict["downloaded_bytes"] as? Int64 ?? 0,
                totalBytes: eventDict["total_bytes"] as? Int64 ?? 0,
                error: nil
            )
        case "completed":
            return DownloadState(
                modelId: modelId,
                status: .completed,
                pct: 1.0,
                downloadedBytes: eventDict["total_bytes"] as? Int64 ?? 0,
                totalBytes: eventDict["total_bytes"] as? Int64 ?? 0,
                error: nil,
                finishedAt: Date()  // 触发保留期计时
            )
        case "failed":
            return DownloadState(
                modelId: modelId,
                status: .failed,
                pct: 0,
                downloadedBytes: 0,
                totalBytes: 0,
                error: eventDict["error"] as? String,
                finishedAt: Date()
            )
        case "cancelled":
            return DownloadState(
                modelId: modelId,
                status: .cancelled,
                pct: 0,
                downloadedBytes: 0,
                totalBytes: 0,
                error: nil,
                finishedAt: Date()
            )
        default:
            return nil
        }
    }

    /// 读整个 JSONL 文件，按 modelId 聚合最新状态。
    /// 文件可能很大（几 MB），用 line-by-line 读取。
    /// 第一阶段：mock 数据，等 Python daemon 接上后改成真实读取。
    private static func readAllEvents(from url: URL) -> [String: DownloadState] {
        // 不再使用——由 readNewEvents 增量读替代，避免大文件全读卡 UI。
        return [:]
    }
}