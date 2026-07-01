import SwiftUI

// MARK: - Glass plate shell
//
// A self-contained view modifier that produces the macOS 26 Liquid Glass
// "floating plate" look. The plate is the chrome that hosts HUD
// controls. We use `GlassEffectContainer` to coalesce neighbouring glass
// shapes for performance and morphing, and an explicit `ultraThinMaterial`
// underneath for the universal fallback.

struct GlassPlate: ViewModifier {
    var corner: CGFloat = Palette.hudCorner

    func body(content: Content) -> some View {
        content
            .background {
                GlassEffectContainer(spacing: 0) {
                    ZStack {
                        RoundedRectangle(cornerRadius: corner, style: .continuous)
                            .fill(.ultraThinMaterial)
                        RoundedRectangle(cornerRadius: corner, style: .continuous)
                            .fill(Palette.plateTint)
                    }
                }
            }
            .overlay {
                RoundedRectangle(cornerRadius: corner, style: .continuous)
                    .stroke(Palette.plateBorder, lineWidth: 0.8)
            }
            .overlay {
                RoundedRectangle(cornerRadius: corner - 1, style: .continuous)
                    .inset(by: 1)
                    .stroke(Palette.plateInner, lineWidth: 0.5)
            }
            .shadow(color: Palette.plateShadow, radius: 18, x: 0, y: 8)
    }
}

extension View {
    /// Apply the standard Liquid Glass chrome to a HUD container.
    func glassPlate(corner: CGFloat = Palette.hudCorner) -> some View {
        modifier(GlassPlate(corner: corner))
    }
}

// MARK: - Control chip
//
// Capsule background used by every interactive control inside the HUD.
// `interactive(true)` lets the user click through the glass.

struct HUDControlBackground: ViewModifier {
    var selected: Bool = false

    func body(content: Content) -> some View {
        content
            .padding(.horizontal, 6)
            .frame(height: Palette.controlHeight)
            .background {
                Capsule(style: .continuous)
                    .fill(selected ? Palette.controlSelected : Palette.controlFill)
            }
    }
}

extension View {
    func hudControl(selected: Bool = false) -> some View {
        modifier(HUDControlBackground(selected: selected))
    }
}
