import SwiftUI

/// Cycle the bilingual layout (translation-top ↔ source-top).
struct BilingualLayoutButton: View {
    @ObservedObject var state: OverlayState

    var body: some View {
        Button {
            state.cycleBilingualLayout()
        } label: {
            Image(systemName: state.bilingualLayout.icon)
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(Palette.textSecondary)
                .frame(width: 18, height: 18)
        }
        .buttonStyle(.plain)
        .help(state.bilingualLayout.help)
        .hudControl()
    }
}
