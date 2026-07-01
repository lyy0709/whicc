import SwiftUI

/// The single horizontal HUD strip that combines all interactive controls.
/// Hosts a single Liquid Glass plate so the whole row morphs together.
struct HUDBar: View {
    @ObservedObject var state: OverlayState
    @ObservedObject var langConfig: LangConfig
    @Binding var style: OverlayStyle
    var onOpenSettings: () -> Void

    var body: some View {
        HStack(spacing: 6) {
            OpacitySlider(state: state)
            FontSizeStepper(state: state)
            FontPickerButton(state: state, langConfig: langConfig)
            AccentSwatchGroup(style: $style)
            BilingualLayoutButton(state: state)
            SourceVisibilityButton(state: state)
            LanguageMenuButton(langConfig: langConfig)
            CopyButton(state: state)
            SettingsButton(action: onOpenSettings)
        }
        .frame(height: Palette.controlHeight)
    }
}
