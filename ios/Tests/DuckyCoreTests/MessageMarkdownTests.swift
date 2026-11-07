import Foundation
import XCTest
@testable import DuckyCore

final class MessageMarkdownTests: XCTestCase {
    private func text(_ block: MarkdownBlock) -> String { String(block.text.characters) }

    func testHeadingsParagraphsAndInlineStylesRemainInOneBlock() {
        let parsed = MessageMarkdown("# Title\n\nFirst **bold**, *emphasis* and `code`.\n\n## Next\n\n---")
        XCTAssertEqual(parsed.blocks.map(\.kind), [.heading(1), .paragraph, .heading(2), .thematicBreak])
        XCTAssertEqual(parsed.blocks.map(\.id), [0, 1, 2, 3])
        XCTAssertEqual(text(parsed.blocks[1]), "First bold, emphasis and code.")
        let runs = parsed.blocks[1].text.runs
        XCTAssertTrue(runs.contains { $0.inlinePresentationIntent?.contains(.stronglyEmphasized) == true })
        XCTAssertTrue(runs.contains { $0.inlinePresentationIntent?.contains(.emphasized) == true })
        XCTAssertTrue(runs.contains { $0.inlinePresentationIntent?.contains(.code) == true })
        XCTAssertTrue(runs.allSatisfy { $0.presentationIntent == nil })
    }

    func testSoftAndHardLineBreaksAndUnicodeAreNotFlattened() {
        let parsed = MessageMarkdown("First **bold\nnext**\n日本語 👩🏽‍💻  \nlast\n\nSeparate paragraph")
        XCTAssertEqual(parsed.blocks.count, 2)
        XCTAssertEqual(text(parsed.blocks[0]), "First bold\nnext\n日本語 👩🏽‍💻\nlast")
        XCTAssertEqual(text(parsed.blocks[1]), "Separate paragraph")
        XCTAssertTrue(parsed.blocks[0].text.runs.allSatisfy {
            $0.inlinePresentationIntent?.contains(.softBreak) != true &&
                $0.inlinePresentationIntent?.contains(.lineBreak) != true
        })
    }

    func testNestedListsKeepDepthNumbersAndDoNotRepeatContinuationMarkers() {
        let source = "4. fourth\n5. fifth\n\n   continuation\n\n   - child **bold**\n     1. nested ordered\n\n   ```txt\n   code\n   ```\n\n6. sixth"
        let blocks = MessageMarkdown(source).blocks
        XCTAssertEqual(blocks.map(text), ["fourth", "fifth", "continuation", "child bold", "nested ordered", "code\n", "sixth"])
        XCTAssertEqual(blocks.map(\.listDepth), [1, 1, 1, 2, 3, 1, 1])
        XCTAssertEqual(blocks.map(\.listOrdinal), [4, 5, 5, nil, 1, 5, 6])
        XCTAssertEqual(blocks.map(\.isListItem), [true, true, false, true, true, false, true])
        XCTAssertEqual(blocks[5].kind, .code(language: "txt"))
    }

    func testQuotesCarryDepthIndependentlyOfLists() {
        let blocks = MessageMarkdown("> outer\n>\n> > nested\n> >\n> > - nested item\n\nOutside").blocks
        XCTAssertEqual(blocks.map(text), ["outer", "nested", "nested item", "Outside"])
        XCTAssertEqual(blocks.map(\.quoteDepth), [1, 2, 2, 0])
        XCTAssertEqual(blocks.map(\.listDepth), [0, 0, 1, 0])
    }

    func testCodePreservesIndentationBlankLinesAndLiteralMarkdown() {
        let source = "```swift\n  let a = \"**literal**\"\n\n\tlet b = \"[link](https://example.com)\"\n```"
        let blocks = MessageMarkdown(source).blocks
        XCTAssertEqual(blocks.count, 1)
        XCTAssertEqual(blocks[0].kind, .code(language: "swift"))
        XCTAssertEqual(text(blocks[0]), "  let a = \"**literal**\"\n\n\tlet b = \"[link](https://example.com)\"\n")
        XCTAssertTrue(blocks[0].text.runs.allSatisfy { $0.link == nil && $0.imageURL == nil })
    }

    func testOnlyAbsoluteHTTPLinksRemainActiveAndImagesStayText() {
        let source = "[web](https://example.com/a?q=1) [plain](http://example.com) " +
            "[pair](ducky://pair?invite=fake) [file](file:///tmp/file) [script](javascript:alert) " +
            "[mail](mailto:person@example.com) [relative](/docs) [credentials](https://user:pass@example.com) " +
            "![image description](https://example.com/image.png)"
        let blocks = MessageMarkdown(source).blocks
        XCTAssertEqual(blocks.count, 1)
        let links = blocks[0].text.runs.compactMap(\.link).map(\.absoluteString)
        XCTAssertEqual(links, ["https://example.com/a?q=1", "http://example.com"])
        XCTAssertTrue(blocks[0].text.runs.allSatisfy { $0.imageURL == nil })
        XCTAssertEqual(text(blocks[0]), "web plain pair file script mail relative credentials image description")
    }

    func testMalformedMarkdownAndUnsupportedTablesPreserveReadableSource() {
        let malformed = "Unclosed **emphasis and [link](\nnext line"
        XCTAssertEqual(MessageMarkdown(malformed).blocks.map(text), [malformed])
        let table = "Intro\n\n| A | B |\n| - | - |\n| one | two |\n\nAfter"
        let blocks = MessageMarkdown(table).blocks
        XCTAssertEqual(blocks.count, 1)
        XCTAssertEqual(blocks[0].kind, .paragraph)
        XCTAssertEqual(text(blocks[0]), table)
        XCTAssertTrue(blocks[0].text.runs.allSatisfy { $0.link == nil })
        XCTAssertEqual(MessageMarkdown("![](https://example.com/image.png)").blocks.map(text), ["![](https://example.com/image.png)"])
    }

    func testEmptyAndWhitespaceOnlyMessagesRemainLossless() {
        XCTAssertTrue(MessageMarkdown("").blocks.isEmpty)
        XCTAssertEqual(MessageMarkdown(" \n\t ").blocks.map(text), [" \n\t "])
    }

    func testLongMessagesAndManyBlocksKeepStableIdentitiesAndAllText() {
        let paragraph = String(repeating: "word ", count: 12000) + "end"
        let source = paragraph + "\n\n" + (1...120).map { "## Heading \($0)\n\nText \($0)" }.joined(separator: "\n\n")
        let first = MessageMarkdown(source), second = MessageMarkdown(source)
        XCTAssertEqual(first, second)
        XCTAssertEqual(first.blocks.count, 241)
        XCTAssertEqual(Set(first.blocks.map(\.id)).count, first.blocks.count)
        XCTAssertEqual(text(first.blocks[0]), paragraph)
        XCTAssertEqual(text(first.blocks.last!), "Text 120")
    }
}
