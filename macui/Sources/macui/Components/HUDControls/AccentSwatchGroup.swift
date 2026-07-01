import SwiftUI

/// Seven-color accent swatch group. The HUD only offers the seven
/// presets — the `.custom` case (full color picker) is a Settings-only
/// affordance because the HUD has no place to render a `ColorPicker`.
struct AccentSwatchGroup: View {
    @Binding var style: OverlayStyle

    /// All non-custom presets. `OverlayStyle.allCases` also includes
    /// `.custom` for the Settings pane to enumerate, but the HUD
    /// skips it so a HUD-only user can never accidentally land on
    /// "custom" without a way to pick a color.
    private var presets: [OverlayStyle] {
        OverlayStyle.allCases.filter { $0 != .custom }
    }

    var body: some View {
        HStack(spacing: 3) {
            ForEach(presets) { candidate in
                Button {
                    withAnimation(.spring(response: 0.28, dampingFraction: 0.86)) {
                        style = candidate
                    }
                } label: {
                    Circle()
                        .fill(candidate.accent.opacity(style == candidate ? 0.95 : 0.42))
                        .frame(width: 10, height: 10)
                        .overlay {
                            Circle()
                                .stroke(
                                    style == candidate
                                        ? Color.white.opacity(0.6)
                                        : Color.white.opacity(0.18),
                                    lineWidth: 0.8
                                )
                        }
                        .contentShape(Circle())
                }
                .buttonStyle(.plain)
                .help(candidate.label)
            }
        }
        .padding(.horizontal, 6)
        .frame(height: Palette.controlHeight)
        .background(Capsule().fill(Palette.controlFill))
    }
}
