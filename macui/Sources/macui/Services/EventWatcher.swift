import Foundation

/// Tails a JSONL file and dispatches `TranslationEvent`s.
///
/// Uses a `DispatchSource` to wake on append/extend, and an internal
/// `Data` buffer to split incoming bytes by newline. Designed to be
/// retried if the file is not yet present at startup.
final class EventWatcher: @unchecked Sendable {

    private let path: String
    private let onEvent: @MainActor (TranslationEvent) -> Void
    private let queue: DispatchQueue

    private var byteOffset: UInt64 = 0
    private var source: DispatchSourceFileSystemObject?
    private var fd: Int32 = -1
    private var buffer = Data()
    private let decoder = JSONDecoder()

    init(path: String,
         queue: DispatchQueue = .main,
         onEvent: @escaping @MainActor (TranslationEvent) -> Void) {
        self.path = path
        self.queue = queue
        self.onEvent = onEvent
    }

    deinit {
        source?.cancel()
        // cancel handler 已经 close 过(并把 fd 置 -1),这里 fd >= 0
        // 检查防 source 没起来 / cancel handler 没跑的情况。
        if fd >= 0 { close(fd) }
    }

    func start() {
        fd = open(path, O_RDONLY | O_EVTONLY)
        if fd < 0 {
            fputs("[macui] file not found: \(path), retrying…\n", stderr)
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { [weak self] in
                self?.start()
            }
            return
        }

        // 启动时 seek 到文件尾 — 跳过启动前累积的旧 events,只读启动后
        // 新写的事件。重启 = 全新开始 (没有历史字幕残留)。
        // 之前从 0 开始读会把所有老的 final/partial 重放一遍,导致字幕
        // 区闪一下老字幕 + history 里塞一堆上一会话的句子。
        if let fh = FileHandle(forReadingAtPath: path) {
            let endOffset = fh.seekToEndOfFile()
            fh.closeFile()
            byteOffset = endOffset
        }
        fputs("[macui] watching \(path), offset=\(byteOffset) (seeked to end)\n", stderr)

        let src = DispatchSource.makeFileSystemObjectSource(
            fileDescriptor: fd,
            eventMask: [.write, .extend, .rename],
            queue: queue
        )
        src.setEventHandler { [weak self] in
            self?.readNewData()
        }
        src.setCancelHandler { [weak self] in
            // close 后置 fd = -1,避免 deinit 二次 close 已关闭的 fd
            // (macOS 上 fd 是进程级句柄,误关可能错杀进程内另一个 fd)。
            if let fd = self?.fd, fd >= 0 {
                self?.fd = -1
                close(fd)
            }
        }
        src.resume()
        source = src
    }

    func stop() {
        source?.cancel()
        source = nil
    }

    private func readNewData() {
        guard let fh = FileHandle(forReadingAtPath: path) else { return }
        defer { fh.closeFile() }

        fh.seek(toFileOffset: byteOffset)
        let newData = fh.readDataToEndOfFile()
        guard !newData.isEmpty else { return }
        byteOffset += UInt64(newData.count)
        buffer.append(newData)

        // OOM 保护:如果 buffer 累积超过 1MB(无 \n 的半行卡住),
        // 说明生产者可能在写入一行但忘记换行 — 清空 buffer + 重置 offset,
        // 避免 SwiftUI 进程内存被吃光。
        let maxBuffer = 1_000_000
        if buffer.count > maxBuffer {
            fputs("[EventWatcher] \(path): 半行 buffer > \(maxBuffer) bytes, " +
                  "truncate (可能生产者丢 newline)\n", stderr)
            byteOffset -= UInt64(buffer.count)
            buffer.removeAll(keepingCapacity: false)
            return
        }

        while let nl = buffer.range(of: Data([0x0A])) {
            let lineData = buffer.subdata(in: buffer.startIndex..<nl.lowerBound)
            buffer = Data(buffer.dropFirst(nl.upperBound))

            guard !lineData.isEmpty,
                  let line = String(data: lineData, encoding: .utf8)?
                    .trimmingCharacters(in: .whitespacesAndNewlines),
                  !line.isEmpty else { continue }

            if let event = try? decoder.decode(TranslationEvent.self, from: Data(line.utf8)) {
                Task { @MainActor in
                    self.onEvent(event)
                }
            }
        }
    }
}
