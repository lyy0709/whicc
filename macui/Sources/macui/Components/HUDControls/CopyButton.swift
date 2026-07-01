import SwiftUI
import AppKit

/// HUD control that copies the user's caption history to the
/// clipboard. Click → NSMenu asks "translation / source / both",
/// then `CaptionClipboard` builds the payload and pushes it to
/// the general pasteboard.
struct CopyButton: View {
    @ObservedObject var state: OverlayState

    var body: some View {
        Button {
            showMenu()
        } label: {
            Image(systemName: "doc.on.doc")
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(Palette.textSecondary)
                .frame(width: 18, height: 18)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help("复制全部字幕（译文 / 原文 / 全部）")
        .hudControl()
    }

    private func showMenu() {
        let menu = NSMenu()

        for format in CaptionClipboard.Format.allCases {
            let item = NSMenuItem(
                title: format.displayName,
                action: #selector(MenuActionHandler.copyCaptions(_:)),
                keyEquivalent: ""
            )
            item.target = MenuActionHandler.shared
            item.representedObject = CopyMenuItem(format: format, state: state)
            menu.addItem(item)
        }

        if let event = NSApp.currentEvent {
            NSMenu.popUpContextMenu(
                menu, with: event,
                for: NSApp.keyWindow?.contentView ?? NSView()
            )
        }
    }
}

/// What `MenuActionHandler.copyCaptions` reads off the menu item
/// to know which format to use and where to get the data from.
/// We wrap the live `OverlayState` so the closure runs against
/// the same object the user is looking at when they click.
@MainActor
private final class CopyMenuItem: NSObject {
    let format: CaptionClipboard.Format
    weak var state: OverlayState?

    init(format: CaptionClipboard.Format, state: OverlayState) {
        self.format = format
        self.state = state
    }
}

extension MenuActionHandler {
    @objc func copyCaptions(_ sender: NSMenuItem) {
        guard let item = sender.representedObject as? CopyMenuItem else { return }
        let payload = CaptionClipboard.makePayload(
            history: item.state?.history ?? [],
            committed: item.state?.committed,
            format: item.format
        )
        if !CaptionClipboard.copy(payload) {
            NSSound.beep()
        }
    }
}