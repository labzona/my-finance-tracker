// Local PDF verification using the macOS frameworks. No extra packages.
import Foundation
import AppKit
import PDFKit

guard CommandLine.arguments.count == 3 else {
    fatalError("Usage: verify_pdf.swift INPUT.pdf OUTPUT_DIRECTORY")
}
let source = URL(fileURLWithPath: CommandLine.arguments[1])
let output = URL(fileURLWithPath: CommandLine.arguments[2], isDirectory: true)
try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
guard let document = PDFDocument(url: source) else { fatalError("Cannot read PDF") }
var pages: [[String: Any]] = []
var allText = ""
for index in 0..<document.pageCount {
    guard let page = document.page(at: index) else { fatalError("Missing page") }
    let bounds = page.bounds(for: .mediaBox)
    let scale: CGFloat = 1.7
    let width = Int(ceil(bounds.width * scale))
    let height = Int(ceil(bounds.height * scale))
    guard let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: width,
        pixelsHigh: height, bitsPerSample: 8, samplesPerPixel: 4,
        hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
        bytesPerRow: 0, bitsPerPixel: 0),
        let context = NSGraphicsContext(bitmapImageRep: bitmap) else {
        fatalError("Cannot allocate page image")
    }
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = context
    let graphics = context.cgContext
    graphics.setFillColor(NSColor.white.cgColor)
    graphics.fill(CGRect(x: 0, y: 0, width: CGFloat(width), height: CGFloat(height)))
    graphics.scaleBy(x: scale, y: scale)
    page.draw(with: .mediaBox, to: graphics)
    NSGraphicsContext.restoreGraphicsState()
    let filename = String(format: "page-%02d.png", index + 1)
    guard let png = bitmap.representation(using: .png, properties: [:]) else {
        fatalError("Cannot encode image")
    }
    try png.write(to: output.appendingPathComponent(filename))
    let text = page.string ?? ""
    allText += "\n--- PAGE \(index + 1) ---\n" + text
    let links = page.annotations.compactMap { $0.url?.absoluteString }
    pages.append(["page": index + 1, "characters": text.count,
                  "widthPoints": bounds.width, "heightPoints": bounds.height,
                  "links": links, "image": filename])
}
let report: [String: Any] = ["pageCount": document.pageCount, "pages": pages]
let json = try JSONSerialization.data(withJSONObject: report,
    options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes])
try json.write(to: output.appendingPathComponent("inspection.json"))
try allText.write(to: output.appendingPathComponent("extracted.txt"),
                  atomically: true, encoding: .utf8)
print("PDF pages: \(document.pageCount)")
for page in pages {
    print("Page \(page["page"]!): \(page["characters"]!) characters; " +
          "\((page["links"] as! [String]).count) links; \(page["image"]!)")
}
guard document.pageCount == 5 else { fatalError("Expected exactly 5 pages") }
