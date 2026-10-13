// Experiment: can a second process make a Chrome drag acceptable to Slack
// desktop by adding a real `public.file-url` to the live drag pasteboard?
//
// The probe (drag_pasteboard_probe.swift) showed the only type both working
// sources (screenshot thumbnail, Finder) carry and both failing sources
// (Chrome DownloadURL / <img> drags) lack is `public.file-url` -- an on-disk
// path. Chromium forbids a web page from putting a file: URL on a drag, but
// the drag pasteboard is system-wide, so another local process may be able to.
//
// Run with an existing PNG:
//   swift scripts/drag_pasteboard_inject.swift ~/Desktop/some.png
// then, within 60 s, drag a Ziya diagram or table into Slack desktop.
// If Slack uploads some.png, the injection route is viable.
import AppKit

let args = CommandLine.arguments
guard args.count > 1 else {
    print("usage: swift drag_pasteboard_inject.swift /path/to/image.png")
    exit(1)
}
let fileURL = URL(fileURLWithPath: args[1]).standardizedFileURL
guard FileManager.default.fileExists(atPath: fileURL.path) else {
    print("no such file: \(fileURL.path)")
    exit(1)
}
let pngData = try? Data(contentsOf: fileURL)

let pb = NSPasteboard(name: .drag)
var last = pb.changeCount
let start = Date()
print("Waiting up to 60 s for a Chromium-initiated drag; will inject \(fileURL.path)")

while Date().timeIntervalSince(start) < 60 {
    if pb.changeCount != last {
        last = pb.changeCount
        let types = (pb.types ?? []).map { $0.rawValue }
        let fromChromium = types.contains("org.chromium.chromium-initiated-drag")
        let hasFile = types.contains(NSPasteboard.PasteboardType.fileURL.rawValue)
        print("\n=== change #\(last): chromium=\(fromChromium) hasFileURL=\(hasFile) types=\(types.count)")
        if fromChromium && !hasFile {
            // Path 1: add types to Chrome's existing first item without clearing it.
            let added = pb.addTypes([.fileURL, .png], owner: nil)
            var ok = pb.setString(fileURL.absoluteString, forType: .fileURL)
            if let d = pngData { _ = pb.setData(d, forType: .png) }
            print("  addTypes -> \(added); setString(file-url) -> \(ok)")
            if added == 0 || !ok {
                // Path 2: re-declare. Clobbers Chrome's lazy promise providers, so
                // only the concrete data we can read back survives.
                print("  addTypes refused; falling back to re-declare")
                // Drop Chromium's own marker types. A Chromium RECEIVER (Slack's
                // Electron) treats `org.chromium.chromium-renderer-initiated-drag`
                // as "this drag came from a web renderer" and, in
                // RenderWidgetHostImpl::FilterDropData, clears every filename
                // before the page sees the drop -- so a file-url we add is
                // discarded on arrival. Attempt #1 kept these types and Slack
                // showed its drop cursor but uploaded nothing.
                let item = NSPasteboardItem()
                for t in pb.types ?? [] {
                    if t.rawValue.hasPrefix("org.chromium.") { continue }
                    if let d = pb.data(forType: t) { item.setData(d, forType: t) }
                }
                item.setString(fileURL.absoluteString, forType: .fileURL)
                if let d = pngData { item.setData(d, forType: .png) }
                pb.clearContents()
                ok = pb.writeObjects([item])
                print("  writeObjects -> \(ok)")
            }
            last = pb.changeCount
            print("  now: \((pb.types ?? []).map { $0.rawValue })")
        }
    }
    Thread.sleep(forTimeInterval: 0.005)
}
print("\ndone")
