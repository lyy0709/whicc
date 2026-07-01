import SwiftUI

/// Gear icon that opens the Settings window.
struct SettingsButton: View {
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: "gearshape")
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(Palette.textSecondary)
                .frame(width: 18, height: 18)
        }
        .buttonStyle(.plain)
        .help("词库 / 场景 / 事件 / 配置")
        .hudControl()
    }
}
