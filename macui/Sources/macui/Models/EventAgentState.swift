import Foundation

/// Polls `event_state.json` for the event-recognition agent's status. The
/// `event_agent.py` script is invoked when the user clicks "获取当前事件".
@MainActor
final class EventAgentState: ObservableObject {

    @Published var status: String = "idle"
    @Published var eventName: String = ""
    @Published var eventType: String = ""
    @Published var questionForUser: String = ""
    @Published var confidence: Double = 0
    @Published var reason: String = ""
    @Published var progress: String = ""
    @Published var userHint: String = ""

    private let statePath: String
    private let agentPath: String
    private var timer: Timer?
    private var lastMtime: TimeInterval = 0

    init(outDir: String = AppPaths.runDir,
         srcDir: String = AppPaths.srcDir) {
        self.statePath = "\(outDir)/event_state.json"
        self.agentPath = "\(srcDir)/event_agent.py"
    }

    func startPolling() {
        loadState()
        timer = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.loadState() }
        }
    }

    func stopPolling() {
        timer?.invalidate()
        timer = nil
    }

    // MARK: - Actions

    func trigger() {
        status = "running"
        progress = "正在启动…"
        reason = ""
        if !userHint.isEmpty {
            var state = readStateFile()
            state["user_hint"] = userHint
            writeStateFile(state)
        }
        runAgent(args: [])
    }

    func confirm() {
        status = "running"
        progress = "正在生成术语表…"
        runAgent(args: ["--confirm"])
    }

    func dismiss() {
        runAgent(args: ["--clear"])
        status = "idle"
        eventName = ""
        questionForUser = ""
    }

    // MARK: - Private

    private func runAgent(args: [String]) {
        let process = Process()
        // 用 AppPaths.pythonExecutable 替代 /usr/bin/env python3:
        // 打包模式 (.app) 下根本没有系统 Python,只能调 bundle 内嵌的 venv。
        // 开发模式下,venv 也在项目里,跟之前行为一致。
        process.executableURL = URL(fileURLWithPath: AppPaths.pythonExecutable)
        process.arguments = [agentPath] + args
        process.qualityOfService = .utility

        DispatchQueue.global(qos: .utility).async {
            do {
                try process.run()
                process.waitUntilExit()
                Task { @MainActor [weak self] in self?.loadState() }
            } catch {
                Task { @MainActor [weak self] in
                    self?.status = "no_match"
                    self?.reason = "Agent 启动失败: \(error.localizedDescription)"
                }
            }
        }
    }

    private func readStateFile() -> [String: Any] {
        guard let data = FileManager.default.contents(atPath: statePath),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return [:] }
        return json
    }

    private func writeStateFile(_ dict: [String: Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject: dict, options: .prettyPrinted) else { return }
        let tmp = statePath + ".tmp"
        try? data.write(to: URL(fileURLWithPath: tmp))
        try? FileManager.default.removeItem(atPath: statePath)
        try? FileManager.default.moveItem(atPath: tmp, toPath: statePath)
    }

    private func loadState() {
        guard let data = FileManager.default.contents(atPath: statePath),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }

        if let attrs = try? FileManager.default.attributesOfItem(atPath: statePath),
           let mtime = attrs[.modificationDate] as? Date {
            let mt = mtime.timeIntervalSince1970
            if mt <= lastMtime { return }
            lastMtime = mt
        }

        status = json["status"] as? String ?? "idle"
        eventName = json["event_name"] as? String ?? ""
        eventType = json["event_type"] as? String ?? ""
        questionForUser = json["question_for_user"] as? String ?? ""
        confidence = json["confidence"] as? Double ?? 0
        reason = json["reason"] as? String ?? ""
        progress = json["progress"] as? String ?? ""
    }
}
