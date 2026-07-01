import SwiftUI

/// A view that renders one of two stacked children based on the active
/// `BilingualLayout`.
///
/// `.translationTop` — translation first, source second.
/// `.sourceTop`      — source first, translation second.
///
/// Both children are built eagerly so the same transition animates
/// smoothly when the layout flips; this matches the legacy overlay's
/// behavior.
struct BilingualStack<Translation: View, Source: View>: View {
    let layout: BilingualLayout
    let spacing: CGFloat
    @ViewBuilder var translation: () -> Translation
    @ViewBuilder var source: () -> Source

    init(
        layout: BilingualLayout,
        spacing: CGFloat = 4,
        @ViewBuilder translation: @escaping () -> Translation,
        @ViewBuilder source: @escaping () -> Source
    ) {
        self.layout = layout
        self.spacing = spacing
        self.translation = translation
        self.source = source
    }

    var body: some View {
        switch layout {
        case .translationTop:
            VStack(spacing: spacing) { translation(); source() }
        case .sourceTop:
            VStack(spacing: spacing) { source(); translation() }
        }
    }
}
