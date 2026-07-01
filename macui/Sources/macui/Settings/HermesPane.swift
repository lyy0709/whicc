import SwiftUI

/// Hermes 设置页:Hermes 节点配置 + 事件流 + 词库三块,共享一个 ScrollView。
/// GlossaryPane / EventPane 暴露无外壳 content,避免双 ScrollView 嵌套。
struct HermesPane: View {
    @ObservedObject var state: GlossaryState
    @ObservedObject var langConfig: LangConfig
    @ObservedObject var eventAgent: EventAgentState

    var body: some View {
        // HermesPane 自己是 detail 容器,而不是直接渲染 GlossaryPane /
        // EventPane 的 body(那两个 body 各自带 SettingsDetailContainer 包装,
        // 嵌套会双 ScrollView)。改用它们的 .content——拿掉外壳。
        // 三块内容(节点 / 事件 / 词库)各自有自己的 section header 和 card,
        // 视觉上已经清晰,中间的 Divider 反而割裂——删掉,让 VStack 的
        // spacing=16 直接撑开。
        SettingsDetailContainer {
            VStack(alignment: .leading, spacing: 16) {
                // Hermes 地址在顶部:用户进 Hermes 页第一件事是确认节点配置。
                hermesHostSection

                // 事件在中:事件流是临时状态(识别一次就走),放显眼位置。
                EventPane(eventAgent: eventAgent).content

                // 词库在底部:词库是长期数据(用户持续维护),放最下面。
                GlossaryPane(state: state).content
            }
        }
    }

    /// Hermes 服务节点 host 配置。原来在 ServerPane 末尾,挪到这里跟
    /// Hermes 相关内容放在一起——配置、识别、词库三块同页,工作流连贯。
    /// 没用 ServerPane 私有 hostRow,而是直接写一份简化版:
    ///   - Hermes 行原来就走 `debounce: false`(每次按键写盘),不像翻译
    ///     URL 那样需要防抖
    ///   - 只有 host 一行,不需要 label 子标题
    ///   - 复用 hostRow 要把它从 ServerPane 提升到模块级,改动大
    @ViewBuilder
    private var hermesHostSection: some View {
        SettingsSectionHeader(
            icon: "brain",
            title: "Hermes地址",
            trailing: { EmptyView() }
        )

        SettingsCard {
            HStack(spacing: 8) {
                Image(systemName: "network")
                    .foregroundColor(.secondary)
                    .frame(width: 16)
                TextField("如 192.168.1.5", text: Binding(
                    get: { langConfig.hermesHost },
                    set: { langConfig.setHermesHost($0) }
                ))
                .textFieldStyle(.plain)
                .font(.system(size: 13))
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .background(
                    RoundedRectangle(cornerRadius: 7, style: .continuous)
                        .fill(Color(nsColor: .textBackgroundColor))
                )
                .overlay(
                    RoundedRectangle(cornerRadius: 7, style: .continuous)
                        .stroke(Color.primary.opacity(0.10), lineWidth: 0.5)
                )
                // Reachability dot:绿/红/灰 三态,跟 ServerPane 那行一致。
                reachabilityDot(langConfig.hermesReachable)
                // 刷新按钮:手动触发 ssh 探测,见 LangConfig.detectHermes。
                Button(action: { langConfig.detectHermes() }) {
                    Image(systemName: "arrow.clockwise")
                        .font(.system(size: 12))
                }
                .buttonStyle(.plain)
                .foregroundColor(.secondary)
                .help("检测 Hermes")
            }
        }
    }

    private func reachabilityDot(_ reachable: Bool?) -> some View {
        Group {
            switch reachable {
            case .some(true):
                Image(systemName: "circle.fill").foregroundColor(.green)
            case .some(false):
                Image(systemName: "circle.fill").foregroundColor(.red)
            default:
                Image(systemName: "circle.dotted").foregroundColor(.gray)
            }
        }
        .font(.system(size: 10))
    }
}