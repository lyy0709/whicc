import SwiftUI

/// Reports the HUD plate's measured height up the view tree so
/// `SubtitleStageView` can offset its top edge to avoid glyph
/// overlap. Default 0 keeps things stable until the first measurement
/// arrives.
struct HUDHeightKey: PreferenceKey {
    static let defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = nextValue()
    }
}

/// The root view hosted inside `NSHostingView` for the floating panel.
/// Wires the subtitle stage, the top HUD, and a periodic status-expiry
/// ticker.
struct ContentView: View {
    @ObservedObject var state: OverlayState
    @ObservedObject var langConfig: LangConfig
    @ObservedObject var glossaryState: GlossaryState
    @ObservedObject var eventAgent: EventAgentState
    var onOpenSettings: () -> Void
    var onHoverChanged: (Bool) -> Void

    // `style` is now owned by OverlayState (was @State here). The HUD's
    // AccentSwatchGroup binds through `state.style` via this binding —
    // tapping a swatch updates OverlayState directly, which is the
    // single source of truth for subtitle rendering.

    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .top) {
                SubtitleStageView(
                    committed: state.committed,
                    draftSourceText: state.draftSourceText,
                    draftTranslatedText: state.draftTranslatedText,
                    draftStablePrefixLen: state.draftStablePrefixLen,
                    history: state.history,
                    showSource: state.showSource,
                    showHistory: state.showHistory,
                    bilingualLayout: state.bilingualLayout,
                    transFontSize: state.transFontSize,
                    srcFontSize: state.srcFontSize,
                    accent: state.resolvedAccent,
                    fontChoice: state.fontChoice,
                    shadowStrong: state.strongShadowOpacity,
                    shadowSoft: state.softShadowOpacity,
                    shadowStrongRadius: state.strongShadowRadius,
                    shadowSoftRadius: state.softShadowRadius,
                    showIdlePreview: state.showIdlePreview,
                    hudHeight: state.hudHeight
                )
                .equatable()
            }
            // Startup banner and HUD both float on top of the subtitle area
            // instead of sitting in the ZStack. That way neither
            // participates in the subtitle's layout pass — the
            // committed baseline stays put while the banner fades or
            // the HUD mounts/unmounts. Critical for drag-select
            // reliability, which breaks if the layout shifts 1-2px
            // under the user's mouse.
            //
            // Mount order matters: the banner overlay is installed
            // LAST, so it draws on top of the HUD. Startup banner is
            // a transient boot-time notification; letting it cover
            // the HUD is fine (the banner auto-dismisses when the
            // first real subtitle arrives).
            .overlay(alignment: .top) {
                HUDView(
                    state: state,
                    langConfig: langConfig,
                    style: Binding(
                        get: { state.style },
                        set: { state.style = $0 }
                    ),
                    onOpenSettings: onOpenSettings
                )
                .background(
                    GeometryReader { proxy in
                        Color.clear.preference(
                            key: HUDHeightKey.self,
                            value: proxy.size.height
                        )
                    }
                )
            }
            .overlay(alignment: .top) {
                if state.startupBannerVisible, let summary = state.startupSummary {
                    StartupBanner(
                        summary: summary,
                        onDismiss: { state.dismissStartupBanner(animated: true) }
                    )
                    .frame(maxWidth: .infinity, alignment: .top)
                }
            }
            .onPreferenceChange(HUDHeightKey.self) { height in
                // Store on the state object so `SubtitleStageView`
                // can read it on the next layout pass without
                // re-rendering the root.
                state.hudHeight = height
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            // SwiftUI-native hover. Avoids inserting a custom NSView into
            // the NSPanel's view tree (which AppKit 26 now flags as
            // "unknown subview").
            .onContinuousHover { phase in
                switch phase {
                case .active:   onHoverChanged(true)
                case .ended:    onHoverChanged(false)
                }
            }
        }
        // Status banner expires itself; we just tick it.
        .onReceive(Timer.publish(every: 0.5, on: .main, in: .common).autoconnect()) { _ in
            state.tickStatus()
            state.tickIdlePreview()  // 5s 过期检查 showIdlePreview
        }
    }
}