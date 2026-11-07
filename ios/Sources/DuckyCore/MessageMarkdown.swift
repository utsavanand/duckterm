import Foundation

public struct MarkdownBlock: Identifiable, Equatable, Sendable {
    public enum Kind: Equatable, Sendable {
        case paragraph
        case heading(Int)
        case code(language: String?)
        case thematicBreak
    }

    public let id: Int
    public let kind: Kind
    public let text: AttributedString
    /// Zero outside a list; one for a top-level list item.
    public let listDepth: Int
    public let listOrdinal: Int?
    /// Only the first block of an item displays its bullet or number.
    public let isListItem: Bool
    public let quoteDepth: Int
}

public struct MessageMarkdown: Equatable, Sendable {
    public static let maximumParsedBytes = 32 * 1024
    public static let maximumDisplayDepth = 6
    public let blocks: [MarkdownBlock]

    public init(_ source: String) {
        // Conversation text is untrusted. Bound synchronous Markdown work while
        // keeping an oversized reply fully readable and selectable as plain text.
        guard source.utf8.count <= Self.maximumParsedBytes else {
            blocks = Self.plain(source)
            return
        }
        let options = AttributedString.MarkdownParsingOptions(
            allowsExtendedAttributes: false, interpretedSyntax: .full, failurePolicy: .throwError)
        guard let parsed = try? AttributedString(markdown: source, options: options),
              !parsed.characters.isEmpty || source.isEmpty else {
            blocks = Self.plain(source)
            return
        }

        var result: [MarkdownBlock] = []
        var current: Context?
        var text = AttributedString()
        var listedItems: Set<Int> = []
        func finishBlock() {
            guard let current else { return }
            let firstInItem = current.itemID.map { listedItems.insert($0).inserted } ?? false
            result.append(MarkdownBlock(id: result.count, kind: current.kind, text: text,
                listDepth: current.listDepth, listOrdinal: current.ordinal,
                isListItem: firstInItem, quoteDepth: current.quoteDepth))
        }

        for run in parsed.runs {
            guard let context = Context(run.presentationIntent) else {
                // Unsupported blocks (such as tables) must not lose their structure.
                blocks = Self.plain(source)
                return
            }
            if current?.identity != context.identity {
                finishBlock()
                current = context
                text = AttributedString()
            }
            var part = AttributedString(parsed[run.range])
            if part.imageURL != nil, part.characters.contains("\u{fffc}") {
                // An image without alt text becomes an attachment placeholder.
                // Preserve its source rather than displaying an invisible object.
                blocks = Self.plain(source)
                return
            }
            if run.inlinePresentationIntent?.contains(.softBreak) == true ||
                run.inlinePresentationIntent?.contains(.lineBreak) == true {
                part = AttributedString("\n", attributes: run.attributes)
                var inline = part.inlinePresentationIntent
                inline?.remove([.softBreak, .lineBreak])
                part.inlinePresentationIntent = inline
            }
            part.presentationIntent = nil
            part.imageURL = nil
            // Even an HTTPS destination can disguise a different host behind its
            // label or open another app through a universal link. Replies do not
            // grant authority to navigate; preserve formatting without actions.
            part.link = nil
            text.append(part)
        }
        finishBlock()
        blocks = result
    }

    private static func plain(_ source: String) -> [MarkdownBlock] {
        guard !source.isEmpty else { return [] }
        return [MarkdownBlock(id: 0, kind: .paragraph, text: AttributedString(source),
            listDepth: 0, listOrdinal: nil, isListItem: false, quoteDepth: 0)]
    }

    private struct Context {
        let identity: Int
        let kind: MarkdownBlock.Kind
        let listDepth: Int
        let ordinal: Int?
        let itemID: Int?
        let quoteDepth: Int

        init?(_ intent: PresentationIntent?) {
            guard let components = intent?.components, let leaf = components.first else { return nil }
            identity = leaf.identity
            switch leaf.kind {
            case .paragraph: kind = .paragraph
            case .header(let level): kind = .heading(level)
            case .codeBlock(let language): kind = .code(language: language)
            case .thematicBreak: kind = .thematicBreak
            default: return nil
            }
            var depth = 0, quotes = 0
            var nearestListIsOrdered: Bool?
            var item: Int?, itemOrdinal: Int?
            for component in components {
                switch component.kind {
                case .orderedList:
                    depth += 1
                    if nearestListIsOrdered == nil { nearestListIsOrdered = true }
                case .unorderedList:
                    depth += 1
                    if nearestListIsOrdered == nil { nearestListIsOrdered = false }
                case .listItem(let number):
                    if item == nil { item = component.identity; itemOrdinal = number }
                case .blockQuote: quotes += 1
                default: break
                }
            }
            listDepth = min(depth, MessageMarkdown.maximumDisplayDepth)
            ordinal = nearestListIsOrdered == true ? itemOrdinal : nil
            itemID = item
            quoteDepth = min(quotes, MessageMarkdown.maximumDisplayDepth)
        }
    }
}
