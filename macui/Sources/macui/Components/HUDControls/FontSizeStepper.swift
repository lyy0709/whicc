import SwiftUI

/// Plus/minus stepper that adjusts the primary (translated) font size.
struct FontSizeStepper: View {
    @ObservedObject var state: OverlayState

    var body: some View {
        HStack(spacing: 0) {
            Button {
                state.decreaseFontSize()
            } label: {
                Image(systemName: "minus")
                    .font(.system(size: 11, weight: .bold))
                    .foregroundStyle(Palette.textSecondary)
                    .frame(width: 16, height: 18)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .focusable(false)
            .focusEffectDisabled()
            .help("缩小字幕")

            Button {
                state.increaseFontSize()
            } label: {
                Image(systemName: "plus")
                    .font(.system(size: 11, weight: .bold))
                    .foregroundStyle(Palette.textSecondary)
                    .frame(width: 16, height: 18)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .focusable(false)
            .focusEffectDisabled()
            .help("放大字幕")
        }
        .frame(width: 38, height: Palette.controlHeight)
        .background(Capsule().fill(Palette.controlFill))
    }
}
