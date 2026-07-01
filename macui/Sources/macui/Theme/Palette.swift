import SwiftUI

/// Centralized tokens for layout, spacing, radius, and shadow.
enum Palette {
    // Window
    //
    // minWidth — must be at least the HUD's natural width plus 20pt of
    // breathing room (10pt on each side). The HUD holds 7 controls;
    // their combined natural width is roughly 480pt, so 500 is the
    // safe floor.
    static let minWindowWidth: CGFloat = 500
    //
    // minHeight — must fit at least one line of the committed caption
    // (the formal subtitle). HUD overlay and draft slot auto-hide when
    // the window is at this floor; taller windows bring them back.
    // 68pt is enough for one line of translated text without clipping.
    static let minWindowHeight: CGFloat = 68
    //
    // defaultHeight — height the panel opens at. Larger than the
    // floor so the HUD (~70pt) plus a line of committed caption
    // (~30pt) both fit comfortably. The user can still drag down to
    // `minWindowHeight`.
    static let defaultWindowHeight: CGFloat = 100

    // Backwards-compat aliases used in a few spots.
    static let minWidth: CGFloat = minWindowWidth
    static let minHeight: CGFloat = minWindowHeight

    // Padding
    static let hudHPadding: CGFloat = 12
    static let hudVPadding: CGFloat = 8
    static let subtitleHPadding: CGFloat = 16
    static let subtitleVPadding: CGFloat = 6

    // Radius
    static let hudCorner: CGFloat = 14
    static let controlCorner: CGFloat = 8

    // HUD control sizing
    static let controlHeight: CGFloat = 22

    // Stage
    static let historyMinVisible: CGFloat = 185
    static let sourceMinVisible: CGFloat = 62
    static let historyMaxHeight: CGFloat = 1200

    // History row opacity decay
    static let historyBaseOpacity: Double = 0.45
    static let historyOpacityStep: Double = 0.06
    static let historyMinOpacity: Double = 0.15

    // Shadow (for subtitle text legibility)
    static let textShadowRadius: CGFloat = 16
    static let textShadowSoftRadius: CGFloat = 4

    // Glass overlay tints
    static let plateBorder: Color = .white.opacity(0.12)
    static let plateInner: Color = .white.opacity(0.05)
    static let plateTint: Color = .black.opacity(0.18)
    static let plateShadow: Color = .black.opacity(0.30)

    // HUD text colors (always explicit white-tinted for guaranteed contrast)
    static let textPrimary: Color = .white.opacity(0.96)
    static let textSecondary: Color = .white.opacity(0.72)
    static let textTertiary: Color = .white.opacity(0.48)

    // Control fill (frosted background)
    static let controlFill: Color = .white.opacity(0.08)
    static let controlFillStrong: Color = .white.opacity(0.12)
    static let controlSelected: Color = .white.opacity(0.18)
}

// MARK: - Subtitle text coloring policy
//
// The slider only controls background opacity. The subtitle text color
// is always the user-picked accent (or white when the accent theme is
// `.theater`). The background is what changes, not the text — that
// keeps the user's color choice stable while they tune see-through.
//
// The accent-vs-white decision is based on the `OverlayStyle` (the
// user explicitly chose a white theme), not on the slider position.

extension OverlayState {
    /// `true` when the background is mostly transparent — used as a
    /// legacy hint for code that still wants to bump shadow opacity
    /// based on background transparency. The new shadow opacity is
    /// user-controlled via Settings (see `strongShadowOpacity` /
    /// `softShadowOpacity` properties), so this property is now only
    /// used in fallback paths.
    var hasLowBackground: Bool { bgOpacity < 0.22 }
}
