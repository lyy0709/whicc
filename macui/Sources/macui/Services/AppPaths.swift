import Foundation

/// 中央路径解析：决定 Python 解释器、Python 源码、运行时目录的位置。
///
/// 设计目标:支持两种运行模式,从同一个二进制出发:
///   1. **开发模式** (默认):从项目源码目录运行 (whicc/src/...)
///      解释器 = <project>/venv/bin/python
///      python src = <project>/src
///   2. **打包模式** (在 .app bundle 内):Bundle.main.bundlePath 解析
///      解释器 = Bundle.main/Contents/Resources/venv/bin/python3
///      python src = Bundle.main/Contents/Resources/src
///
/// 判断条件:Bundle.main.bundlePath 结尾是否包含 ".app"。
///   - 包含 → 在 .app 里,走打包模式
///   - 不含 → 在 build output 里 (.build/debug/whicc-macui),走开发模式
///
/// 注意:开发模式下 .build/debug 路径取决于开发者机器, 不能用来判断模式;
/// 所以**不能用 bundlePath 包含 build 字串判断**——而是要看
/// Bundle.main.bundlePath 是不是一个 .app bundle。
enum AppPaths {
    /// Bundle 内嵌的 Python 解释器 (相对路径)
    static let embeddedPythonRelative = "Contents/Resources/venv/bin/python3"

    /// Bundle 内嵌的 Python 源码目录 (相对路径)
    static let embeddedSrcRelative = "Contents/Resources/src"

    /// Bundle 内嵌的 venv 根目录 (相对路径)
    static let embeddedVenvRelative = "Contents/Resources/venv"

    /// 运行时输出目录 (JSONL 事件 / lang_config.json 等):
    ///   打包模式 & 开发模式都用 `/tmp/whicc-out`
    ///   (macOS 26 有时 unlink ~/Library/Application Support/.../run,
    ///   /tmp 更可靠;BackendLauncher 用此路径写 jsonl 占位文件)。
    ///
    /// **注意**:`runDirOverride` 由 main.swift 在启动时设置(从 --out-dir
    /// 参数),留作未来切到 Application Support 目录的接入点。
    nonisolated(unsafe) static var runDirOverride: String?

    static var runDir: String {
        if let runDirOverride { return runDirOverride }
        // BundledApp + dev mode both use /tmp/whicc-out (macOS 26 sometimes
        // unlinks ~/Library/.../run, /tmp is more reliable)
        return "/tmp/whicc-out"
    }

    /// Python 解释器的绝对路径
    static var pythonExecutable: String {
        if isBundledApp {
            return Bundle.main.bundlePath + "/" + embeddedPythonRelative
        }
        return projectRoot + "/venv/bin/python"
    }

    /// Python 源码目录 (whicc.py / translate_stream.py / glossary_refresher.py
    /// / model_downloader.py / event_agent.py 都在这)
    static var srcDir: String {
        if isBundledApp {
            return Bundle.main.bundlePath + "/" + embeddedSrcRelative
        }
        return projectRoot + "/src"
    }

    /// 术语表 JSON 路径 (whicc.py / BackendLauncher 都用 srcDir/glossary.json)
    static var glossaryPath: String {
        srcDir + "/glossary.json"
    }

    /// 是否在 .app bundle 内运行。true → 打包模式
    static var isBundledApp: Bool {
        Bundle.main.bundlePath.hasSuffix(".app")
    }

    /// 仓库根目录 (whicc/)。
    /// 开发模式 = main.swift 的工作目录的 ../../ (从 macui 目录上溯)。
    /// 这里用 cwd 解析 + 兜底硬编码:
    ///   - 用户在 whicc/ 跑 swift run → cwd=whicc → projectRoot=whicc
    ///   - 用户在 whicc/macui 跑 swift run → cwd=whicc/macui → 上溯 = whicc
    ///   - XCode 跑 → 各种怪 cwd,兜底用硬编码
    static var projectRoot: String {
        let cwd = FileManager.default.currentDirectoryPath
        // cwd 形如 /Users/.../whicc 时直接返回
        if cwd.hasSuffix("/whicc") || cwd.hasSuffix("/whicc/") {
            return cwd.hasSuffix("/") ? String(cwd.dropLast()) : cwd
        }
        // cwd 形如 /Users/.../whicc/macui 时上溯一级
        if cwd.hasSuffix("/whicc/macui") || cwd.hasSuffix("/whicc/macui/") {
            let trimmed = cwd.hasSuffix("/") ? String(cwd.dropLast()) : cwd
            return (trimmed as NSString).deletingLastPathComponent
        }
        // 兜底：从 Bundle.main.bundlePath 向上查找包含 src/whicc.py 的目录。
        // 开发模式下 bundlePath = <project>/macui/.build/<debug|release>，
        // 上溯 3 层即到 whicc/ 根。
        let bundleURL = URL(fileURLWithPath: Bundle.main.bundlePath, isDirectory: true)
        var probe = bundleURL
        for _ in 0..<4 {
            let candidate = probe.appendingPathComponent("src/whicc.py").path
            if FileManager.default.fileExists(atPath: candidate) {
                return probe.path
            }
            probe = probe.deletingLastPathComponent()
        }
        fatalError("""
            Cannot find project root. Run from within the whicc/ directory, \
            or set the WHICC_ROOT environment variable.
            """)
    }
}
