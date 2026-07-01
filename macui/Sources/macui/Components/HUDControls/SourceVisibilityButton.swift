import SwiftUI

/// Eye toggle for source-text visibility.
struct SourceVisibilityButton: View {
    @ObservedObject var state: OverlayState

    var body: some View {
        Button {
            state.toggleSourceVisibility()
        } label: {
            Image(systemName: state.showSource ? "eye.fill" : "eye.slash.fill")
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(state.showSource ? Palette.textPrimary : Palette.textSecondary)
                .frame(width: 18, height: 18)
        }
        .buttonStyle(.plain)
        .help(state.showSource ? "隐藏原文" : "显示原文")
        .hudControl(selected: state.showSource)
    }
}
