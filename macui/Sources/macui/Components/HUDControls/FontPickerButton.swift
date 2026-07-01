import SwiftUI

/// HUD control that cycles through the user's `SubtitleFont` choices.
/// Click to advance; the button label is a literal "A" rendered in
/// Times New Roman so the user can tell at a glance that this is
/// the font picker — even if the current font is SF Pro Rounded.
struct FontPickerButton: View {
    @ObservedObject var state: OverlayState
    /// `LangConfig` is the persistence seam — we keep a weak ref so
    /// we don't extend its lifetime just to write a string to disk.
    var langConfig: LangConfig?

    var body: some View {
        Button {
            withAnimation(.easeOut(duration: 0.18)) {
                state.cycleFont()
                langConfig?.setSubtitleFont(state.fontChoice.rawValue)
            }
        } label: {
            Text("A")
                .font(Font.custom("Times New Roman", size: 14, relativeTo: .body)
                    .weight(.semibold))
                .foregroundStyle(Palette.textSecondary)
                .frame(width: 18, height: 18)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help("字体：\(state.fontChoice.displayName)（点击切换）")
        .hudControl()
    }
}