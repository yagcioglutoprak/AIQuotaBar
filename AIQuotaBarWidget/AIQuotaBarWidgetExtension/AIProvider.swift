import AppIntents
import SwiftUI
import WidgetKit

// MARK: - Provider enum

enum AIProvider: String, AppEnum, CaseIterable, Codable {
    case claude, chatgpt, cursor, copilot

    static var typeDisplayRepresentation = TypeDisplayRepresentation(name: "AI Provider")
    static var caseDisplayRepresentations: [AIProvider: DisplayRepresentation] = [
        .claude:  "Claude",
        .chatgpt: "ChatGPT",
        .cursor:  "Cursor",
        .copilot: "Copilot",
    ]

    var displayName: String {
        switch self {
        case .claude:  return "Claude"
        case .chatgpt: return "ChatGPT"
        case .cursor:  return "Cursor"
        case .copilot: return "Copilot"
        }
    }

    var color: Color {
        switch self {
        case .claude:  return Color(red: 0.85, green: 0.55, blue: 0.35) // terracotta
        case .chatgpt: return Color(red: 0.45, green: 0.78, blue: 0.65) // mint
        case .cursor:  return Color(red: 0.40, green: 0.60, blue: 1.00) // blue
        case .copilot: return Color(red: 0.55, green: 0.75, blue: 0.95) // sky blue
        }
    }

    var iconName: String {
        switch self {
        case .claude:  return "claude_icon"
        case .chatgpt: return "chatgpt_icon"
        case .cursor:  return "cursor_icon"
        case .copilot: return "copilot_icon"
        }
    }

    var iconNeedsTemplate: Bool {
        switch self {
        case .claude:  return false
        case .chatgpt: return true
        case .cursor:  return true
        case .copilot: return true
        }
    }
}

// MARK: - Widget configuration intent

struct SelectProvidersIntent: WidgetConfigurationIntent {
    static var title: LocalizedStringResource = "Select Providers"
    static var description = IntentDescription("Choose which AI providers to display in the widget.")

    @Parameter(title: "Provider 1", default: .claude)
    var provider1: AIProvider

    @Parameter(title: "Provider 2", default: .chatgpt)
    var provider2: AIProvider

    @Parameter(title: "Provider 3")
    var provider3: AIProvider?

    @Parameter(title: "Provider 4")
    var provider4: AIProvider?

    /// Configured providers in order; empty slots drop out.
    var activeProviders: [AIProvider] {
        [provider1, provider2, provider3, provider4].compactMap { $0 }
    }

    /// True when user hasn't touched the widget config (all slots at compile-time defaults).
    var isUsingDefaults: Bool {
        provider1 == .claude && provider2 == .chatgpt
            && provider3 == nil && provider4 == nil
    }
}

// MARK: - Display data extraction

struct ProviderDisplayData {
    let mainPct: Int
    let rows: [LimitRow]
    let error: String?
    let extraInfo: String?
    let isConfigured: Bool
}

extension AIProvider {
    func displayData(from snap: UsageSnapshot) -> ProviderDisplayData {
        switch self {
        case .claude:
            let session = snap.claude.session
            var rows: [LimitRow] = []
            if let s = session { rows.append(s) }
            if let w = snap.claude.weeklyAll { rows.append(w) }
            if let ws = snap.claude.weeklySonnet { rows.append(ws) }
            let extra: String? = snap.claudeCode.todayMessages > 0
                ? "\(snap.claudeCode.todayMessages) msgs today"
                : nil
            return ProviderDisplayData(
                mainPct: session?.pct ?? 0,
                rows: rows,
                error: nil,
                extraInfo: extra,
                isConfigured: true
            )

        case .chatgpt:
            if let err = snap.chatgpt.error {
                return ProviderDisplayData(mainPct: 0, rows: [], error: err, extraInfo: nil, isConfigured: true)
            }
            if let rows = snap.chatgpt.rows {
                let maxPct = rows.map(\.pct).max() ?? 0
                return ProviderDisplayData(mainPct: maxPct, rows: rows, error: nil, extraInfo: nil, isConfigured: true)
            }
            return ProviderDisplayData(mainPct: 0, rows: [], error: nil, extraInfo: nil, isConfigured: false)

        case .cursor:
            guard let cursor = snap.cursor else {
                return ProviderDisplayData(mainPct: 0, rows: [], error: nil, extraInfo: nil, isConfigured: false)
            }
            if let err = cursor.error {
                return ProviderDisplayData(mainPct: 0, rows: [], error: err, extraInfo: nil, isConfigured: true)
            }
            if let rows = cursor.rows {
                let maxPct = rows.map(\.pct).max() ?? 0
                return ProviderDisplayData(mainPct: maxPct, rows: rows, error: nil, extraInfo: nil, isConfigured: true)
            }
            return ProviderDisplayData(mainPct: 0, rows: [], error: nil, extraInfo: nil, isConfigured: false)

        case .copilot:
            guard let copilot = snap.copilot else {
                return ProviderDisplayData(mainPct: 0, rows: [], error: nil, extraInfo: nil, isConfigured: false)
            }
            if let err = copilot.error {
                return ProviderDisplayData(mainPct: 0, rows: [], error: err, extraInfo: nil, isConfigured: true)
            }
            let pct = copilot.pct ?? 0
            var rows: [LimitRow] = []
            if let spent = copilot.spent, let limit = copilot.limit, limit > 0 {
                rows.append(LimitRow(
                    label: "\(Int(spent))/\(Int(limit)) reqs",
                    pct: pct,
                    resetStr: "resets monthly"
                ))
            }
            return ProviderDisplayData(mainPct: pct, rows: rows, error: nil, extraInfo: nil, isConfigured: true)
        }
    }
}
