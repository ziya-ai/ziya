// Watch the macOS drag pasteboard and print every type each drag source
// puts on it. Run with:   swift scripts/drag_pasteboard_probe.swift
// then start a drag (and hold it, or drop it anywhere) within 60 s.
//
// Purpose: find out why a Chrome `DownloadURL` / <img> drag is refused by
// Slack desktop while a macOS screenshot-thumbnail drag is accepted, by
// comparing the exact pasteboard types and promise flavours each one offers.
// Expected vocabulary:
//   public.file-url                               a real on-disk file (Finder)
//   com.apple.pasteboard.promised-file-url         modern NSFilePromiseProvider
//   com.apple.pasteboard.promised-file-content-type  (screenshot thumbnail uses these)
//   Apple files promise pasteboard type            legacy NSFilesPromisePboardType
//   org.chromium.*, public.tiff, public.png, public.html, public.utf8-plain-text
import AppKit

let pb = NSPasteboard(name: .drag)
var lastChange = -1
let start = Date()
print("Watching the drag pasteboard for 60 s — start a drag now…")
while Date().timeIntervalSince(start) < 60 {
    if pb.changeCount != lastChange {
        lastChange = pb.changeCount
        print("\n=== drag pasteboard change #\(lastChange) at +\(String(format: "%.1f", Date().timeIntervalSince(start)))s ===")
        let items = pb.pasteboardItems ?? []
        if items.isEmpty { print("  (empty)") }
        for (i, item) in items.enumerated() {
            print("  item \(i):")
            for t in item.types {
                let bytes = item.data(forType: t)?.count ?? 0
                var line = "    \(t.rawValue)  [\(bytes) bytes]"
                if let s = item.string(forType: t), s.count <= 160 { line += "  = \(s)" }
                print(line)
            }
        }
        if let urls = pb.readObjects(forClasses: [NSURL.self], options: nil) as? [URL], !urls.isEmpty {
            for u in urls { print("  URL object: \(u)") }
        }
    }
    Thread.sleep(forTimeInterval: 0.1)
}
print("\ndone")
