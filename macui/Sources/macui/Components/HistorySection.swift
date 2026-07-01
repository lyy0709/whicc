import SwiftUI

/// History list above the committed caption. Uses macOS 26's
/// `ScrollPosition` to auto-pin the newest row to the bottom.
///
/// `Equatable` so SwiftUI can skip re-renders when none of the
/// inputs change. Without this gate, every unrelated state change
/// (window focus, hover, bg slider) would reflow the scroll view
/// and nudge individual rows 1-2px — visible as jitter and enough
/// to break drag-selections on the history rows themselves.
struct HistorySection: View, Equatable {
    let history: [OverlayCaption]
    let accent: Color
    let maxHeight: CGFloat
    let bilingualLayout: BilingualLayout
    let showSource: Bool
    let srcFontSize: CGFloat
    let transFontSize: CGFloat
    let fontChoice: SubtitleFont

    @State private var position: ScrollPosition = .init()

    static func == (lhs: HistorySection, rhs: HistorySection) -> Bool {
        lhs.history.map(\.id) == rhs.history.map(\.id)
            && lhs.history.map(\.translatedText) == rhs.history.map(\.translatedText)
            && lhs.history.map(\.sourceText) == rhs.history.map(\.sourceText)
            && lhs.accent == rhs.accent
            && lhs.maxHeight == rhs.maxHeight
            && lhs.bilingualLayout == rhs.bilingualLayout
            && lhs.showSource == rhs.showSource
            && lhs.srcFontSize == rhs.srcFontSize
            && lhs.transFontSize == rhs.transFontSize
            && lhs.fontChoice == rhs.fontChoice
    }

    var body: some View {
        ScrollView(.vertical, showsIndicators: false) {
            let items = Array(history.suffix(30))
            VStack(alignment: .center, spacing: 4) {
                Spacer(minLength: 0)
                ForEach(Array(items.enumerated()), id: \.element.id) { index, cap in
                    HistoryRow(
                        caption: cap,
                        rankFromNewest: items.count - 1 - index,
                        accent: accent,
                        showSource: showSource,
                        srcFontSize: srcFontSize,
                        transFontSize: transFontSize,
                        bilingualLayout: bilingualLayout,
                        fontChoice: fontChoice
                    )
                }
            }
            .frame(maxWidth: .infinity,
                   minHeight: maxHeight,
                   alignment: .bottom)
            .padding(.horizontal, Palette.subtitleHPadding)
            .padding(.vertical, 8)
        }
        .frame(height: maxHeight, alignment: .bottom)
        .clipped()
        .scrollPosition($position, anchor: .bottom)
        // Re-pin to the bottom on every change that can move the
        // bottom edge: new content, view appearing, or window resize
        // (which changes `maxHeight`).
        .onChange(of: history.count) { _, _ in
            scrollToBottom()
        }
        .task(id: maxHeight) {
            // Debounce: while the user is dragging the window edge,
            // `maxHeight` updates every frame. Wait 150ms of stability
            // before re-pinning to the bottom, otherwise the history
            // visibly jitters as the resize happens.
            try? await Task.sleep(for: .milliseconds(150))
            if Task.isCancelled { return }
            scrollToBottom()
        }
        .onAppear { scrollToBottom() }
    }

    private func scrollToBottom() {
        withAnimation(.easeOut(duration: 0.22)) {
            position.scrollTo(edge: .bottom)
        }
    }
}

/// One faded history row. Honors the active `BilingualLayout` so the
/// ordering matches the committed caption and the live draft.
struct HistoryRow: View, Equatable {
    let caption: OverlayCaption
    let rankFromNewest: Int
    let accent: Color
    let showSource: Bool
    let srcFontSize: CGFloat
    let transFontSize: CGFloat
    let bilingualLayout: BilingualLayout
    let fontChoice: SubtitleFont

    static func == (lhs: HistoryRow, rhs: HistoryRow) -> Bool {
        lhs.caption == rhs.caption
            && lhs.rankFromNewest == rhs.rankFromNewest
            && lhs.accent == rhs.accent
            && lhs.showSource == rhs.showSource
            && lhs.srcFontSize == rhs.srcFontSize
            && lhs.transFontSize == rhs.transFontSize
            && lhs.bilingualLayout == rhs.bilingualLayout
            && lhs.fontChoice == rhs.fontChoice
    }

    var body: some View {
        let opacity = max(Palette.historyMinOpacity,
                          Palette.historyBaseOpacity - Double(rankFromNewest) * Palette.historyOpacityStep)
        let primaryColor = accent.opacity(opacity)
        let secondaryColor = accent.opacity(opacity * 0.7)

        BilingualStack(layout: bilingualLayout) {
            if !caption.translatedText.isEmpty {
                Text(caption.translatedText)
                    .font(fontChoice.font(size: transFontSize * 0.65, weight: .medium))
                    .foregroundColor(primaryColor)
                    .multilineTextAlignment(.center)
                    .lineLimit(1)
                    .frame(maxWidth: .infinity)
                    .textSelection(.enabled)
            }
        } source: {
            if showSource, !caption.sourceText.isEmpty {
                Text(caption.sourceText)
                    .font(fontChoice.font(size: srcFontSize * 0.75, weight: .regular))
                    .foregroundColor(secondaryColor)
                    .multilineTextAlignment(.center)
                    .lineLimit(1)
                    .frame(maxWidth: .infinity)
                    .textSelection(.enabled)
            }
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 3)
    }
}