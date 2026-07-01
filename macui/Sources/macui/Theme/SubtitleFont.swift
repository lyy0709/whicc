import SwiftUI
#if canImport(AppKit)
import AppKit
#endif

/// 用户可选的字幕字体。两种模式：
///
///   • `.rounded` / `.serif` —— SwiftUI 内置 design，跨平台稳定。
///     HUD 循环按钮仍然能用这两个（跟之前行为一致）。
///
///   • `.systemFont(name)` —— 指向系统安装的具体字体（Helvetica、
///     Menlo、Times New Roman、Avenir Next 等）。`rawValue` = 字体
///     display name（NSFontManager 显示的字面名，如 "Helvetica"，
///     不是 PostScript 名）。重命名字体后旧配置失效但不会崩——fallback
///     到 .system(.rounded)。
///
/// Settings 字体下拉框用系统全部可用字体（`NSFontManager.shared`）枚举，
/// 加上两个推荐字体作为首段（分割线分组）。HUD 不支持 8 个循环，
/// 只在 rounded/serif 之间切换。
enum SubtitleFont: Hashable {
    /// SF Pro Rounded。SwiftUI .system 的 rounded design。
    case rounded
    /// Times New Roman serif。SwiftUI .custom。
    case serif
    /// 任意系统字体（名字解析失败时 fall back 到 .system default）。
    case systemFont(name: String)

    /// 用 rawValue 持久化。systemFont 的 rawValue 是字体 display name
    /// （如 "Helvetica"），便于用户在 lang_config.json 里读懂。
    init?(rawValue: String) {
        switch rawValue {
        case "rounded": self = .rounded
        case "serif":    self = .serif
        default:
            if !rawValue.isEmpty {
                self = .systemFont(name: rawValue)
            } else {
                return nil
            }
        }
    }

    var rawValue: String {
        switch self {
        case .rounded:       return "rounded"
        case .serif:          return "serif"
        case .systemFont(let name): return name
        }
    }

    /// 下拉框里显示的字面名。HUD 用 `icon` 区分两种快速预设。
    var displayName: String {
        switch self {
        case .rounded: return "SF Pro Rounded"
        case .serif:    return "Times New Roman"
        case .systemFont(let name): return name
        }
    }

    /// SF Symbol used in the HUD font picker. Only meaningful for
    /// .rounded / .serif — .systemFont cases get the same icon as
    /// the .serif preset (book) since they're conceptually similar.
    var icon: String {
        switch self {
        case .rounded: return "textformat"
        case .serif, .systemFont: return "text.book.closed"
        }
    }

    /// 字体是否属于"推荐字体"段（rounded + serif）。下拉框用这个
    /// 标记第一段跟其他系统字体段之间加分隔线。
    var isRecommended: Bool {
        switch self {
        case .rounded, .serif: return true
        case .systemFont:      return false
        }
    }

    /// Build a `Font` at the given point size and weight.
    func font(size: CGFloat, weight: Font.Weight = .regular) -> Font {
        switch self {
        case .rounded:
            return .system(size: size, weight: weight, design: .rounded)
        case .serif:
            // Times New Roman, with looser leading for the rounded serif feel.
            return Font.custom("Times New Roman", size: size, relativeTo: .body)
                .weight(weight)
                .leading(.loose)
        case .systemFont(let name):
            // 系统字体：尝试用 PostScript name 注册字体，失败 fall back
            // 到 .system default design（rounded）。postScriptName 可能
            // 跟 displayName 不同，但 Font.custom 在 macOS 上两种都能接。
            if let _ = NSFont(name: name, size: size) {
                return Font.custom(name, size: size, relativeTo: .body)
                    .weight(weight)
            } else {
                return .system(size: size, weight: weight, design: .rounded)
            }
        }
    }
}

/// 枚举系统安装的所有字体（用于 Settings 下拉框）。
///
/// 返回 `[String]`（字体 display names，按字母排序）。NSFontManager
/// 的 `availableFontNames` 给 ~250+ 字体（系统自带 + 用户装），
/// 适合做下拉框。
func availableSystemFontNames() -> [String] {
    // availableFontNames(with:) 等 overload 都要参数;无参版本不存在,
    // 用 availableFonts ([String] 字体名) 直接就是 display name。
    NSFontManager.shared.availableFonts.sorted {
        $0.localizedCaseInsensitiveCompare($1) == .orderedAscending
    }
}

/// 推荐字体列表（subtotalFont 下拉框第一段显示）。
let recommendedSubtitleFonts: [SubtitleFont] = [.rounded, .serif]
