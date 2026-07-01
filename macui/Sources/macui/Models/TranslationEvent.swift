import Foundation

/// One line in the JSONL stream produced by `translate_stream.py` / `whicc.py`.
///
/// Field names and JSON keys are kept identical to the wire format
/// so the Python side needs no change.
struct TranslationEvent: Decodable, Equatable, Sendable {
    let eventType: String
    let sourceKey: String?
    let sourceUpdateMode: String?
    let sourceText: String?
    let deltaSourceText: String?
    let translatedDeltaText: String?
    let translatedFullText: String?
    let translateMs: Double?
    let sharedPrefixLen: Int?
    let glossaryHits: [String]?
    let retried: Bool?
    let fallbackReason: String?
    let error: String?
    // whicc transcription fields
    let text: String?
    let status: String?
    let statusColor: String?
    // streaming-translation fields. Present on token-level
    // translation_partial events emitted by translate_stream.py
    // while a translation is in flight (one event per token).
    // legacy / non-streaming partials leave these as nil.
    let isStreamingToken: Bool?
    let streamingPiece: String?

    enum CodingKeys: String, CodingKey {
        case eventType = "event_type"
        case sourceKey = "source_key"
        case sourceUpdateMode = "source_update_mode"
        case sourceText = "source_text"
        case deltaSourceText = "delta_source_text"
        case translatedDeltaText = "translated_delta_text"
        case translatedFullText = "translated_full_text"
        case translateMs = "translate_ms"
        case sharedPrefixLen = "shared_prefix_len"
        case glossaryHits = "glossary_hits"
        case retried
        case fallbackReason = "fallback_reason"
        case error
        case text
        case status
        case statusColor = "status_color"
        case isStreamingToken = "is_streaming_token"
        case streamingPiece = "streaming_piece"
    }
}

// MARK: - Event kind helpers

extension TranslationEvent {
    var isTranslationFinal: Bool { eventType == "translation_final" || eventType == "translation_reset" }
    var isTranslationPartial: Bool { eventType == "translation_partial" }
    var isTranslationError: Bool { eventType == "translation_error" }
    var isPartial: Bool { eventType == "partial" }
    var isFinal: Bool { eventType == "final" }
    var isStatus: Bool { eventType == "status" }
}
