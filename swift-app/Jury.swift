// Jury.swift - menubar app for the Jury filesystem monitor.
//
// AppKit NSStatusItem + NSPopover hosting a SwiftUI panel. The dropdown
// is laid out in the project's house voice: deadpan courtroom register,
// formal procedural phrasing, no jokes. A contextual Latin motto at the
// foot of the popover shifts subtly with state (easter egg).
//
// Menubar glyph: an outlined silhouette of a judicial wig, drawn as a
// single NSBezierPath with stroke (not fill). Solid 1.5pt strokes so
// @1x rendering on external monitors keeps the contour clean.
//
// Idle resident memory: ~50 MB. Per popover open: one filesystem read,
// ~1 ms. No timers, no polling.

import AppKit
import Foundation
import SwiftUI

// MARK: - Paths

struct Paths {
    let home: URL
    let appSupport: URL
    let quarantineRoot: URL
    let undoLog: URL
    let stateFile: URL
    let lastRunFile: URL
    let stderrLog: URL
    let stdoutLog: URL
    let digestFile: URL

    init() {
        home = FileManager.default.homeDirectoryForCurrentUser
        appSupport = home
            .appendingPathComponent("Library")
            .appendingPathComponent("Application Support")
            .appendingPathComponent("jury")
        quarantineRoot = appSupport.appendingPathComponent("quarantine")
        undoLog = appSupport.appendingPathComponent("undo.log")
        stateFile = appSupport.appendingPathComponent("state.json")
        lastRunFile = appSupport.appendingPathComponent("monitor.last_run")
        stderrLog = home
            .appendingPathComponent("Library/Logs/jury-monitor.stderr.log")
        stdoutLog = home
            .appendingPathComponent("Library/Logs/jury-monitor.stdout.log")
        digestFile = home
            .appendingPathComponent("Documents/cleanup-digest.md")
    }
}

// MARK: - Domain types

struct QuarantineEntry: Identifiable {
    let id = UUID()
    let currentPath: URL
    let originalPath: URL
    let quarantinedAt: Date

    static let ttlDays = 30
    static let expiringSoonDays = 7

    var ageDays: Int {
        Calendar.current.dateComponents([.day], from: quarantinedAt, to: Date()).day ?? 0
    }

    var daysUntilPurge: Int { Self.ttlDays - ageDays }
}

struct Snapshot {
    var entries: [QuarantineEntry] = []
    var expiring: [QuarantineEntry] = []
    var quarEver: Int = 0
    var trashedEver: Int = 0
    var todayActions: Int = 0
    var lastRunDate: Date? = nil
    var digestExists: Bool = false

    static let dailyCeiling = 200
    /// Below this threshold there is nothing to flag; the counter stays hidden.
    static let flaggedActionThreshold = 180
    /// After this many days idle, the court is "in recess".
    static let recessThresholdDays = 7

    var actionFlagged: Bool { todayActions >= Self.flaggedActionThreshold }
    var docketFull: Bool { todayActions >= Self.dailyCeiling }

    var inRecess: Bool {
        guard let d = lastRunDate else { return false }
        return Date().timeIntervalSince(d) > Double(Self.recessThresholdDays) * 86400
    }

    /// Display string for the "Last in session" row. Time-of-day if today;
    /// abbreviated date otherwise. Empty when the monitor has never fired.
    var lastSessionDisplay: String {
        guard let d = lastRunDate else { return "never" }
        let fmt = DateFormatter()
        if Calendar.current.isDateInToday(d) {
            fmt.dateFormat = "HH:mm:ss"
        } else if inRecess {
            fmt.dateFormat = "MMM d"
        } else {
            fmt.dateFormat = "MMM d, HH:mm"
        }
        return fmt.string(from: d)
    }

    /// Contextual Latin maxim shown at the foot of the popover. Each is a
    /// real classical phrase; the wit is in deploying them at the right
    /// moment. The user who pays attention notices the shift.
    ///   - default:  "Audi, vide, tace."         (hear, see, be silent;
    ///                                            attested in Coke)
    ///   - appeals:  "Audi alteram partem."      (hear the other side;
    ///                                            Augustine, De Duabus Animabus)
    ///   - quiet:    "De minimis non curat lex." (the law does not concern
    ///                                            itself with trifles)
    ///   - full:     "Status quo."               (the existing state)
    ///   - recess:   "Otium cum dignitate."      (leisure with dignity;
    ///                                            Cicero, Pro Sestio)
    var motto: String {
        if docketFull { return "Status quo." }
        if inRecess { return "Otium cum dignitate." }
        if !expiring.isEmpty { return "Audi alteram partem." }
        if entries.isEmpty && quarEver > 50 { return "De minimis non curat lex." }
        return "Audi, vide, tace."
    }
}

enum JuryAction {
    case openQuarantine
    case openUndoLog
    case openStateFile
    case openMonitorLog
    case openDigest
    case reveal(URL)
    case quit
}

// MARK: - State readers

enum State {
    // Cached formatters - DateFormatter and ISO8601DateFormatter are
    // not cheap to allocate (each creates locale + calendar state).
    // Sharing one instance per format avoids that cost on every menu
    // open. Both types are documented as thread-safe for reads after
    // their format options are configured.
    private static let isoFullFractional: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()
    private static let isoFull: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()
    private static let isoDate: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withFullDate]
        return f
    }()
    private static let isoLocal: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        return f
    }()

    static func snapshot(paths: Paths) -> Snapshot {
        let entries = listQuarantine(root: paths.quarantineRoot)
        let expiring = entries.filter { $0.daysUntilPurge <= QuarantineEntry.expiringSoonDays }
        let raw = readRawLastRun(file: paths.lastRunFile)
        return Snapshot(
            entries: entries,
            expiring: expiring,
            quarEver: countUndo(log: paths.undoLog, action: "quarantine"),
            trashedEver: countUndo(log: paths.undoLog, action: "final_trash"),
            todayActions: todayActions(stateFile: paths.stateFile),
            lastRunDate: parseLastRunDate(raw),
            digestExists: FileManager.default.fileExists(atPath: paths.digestFile.path)
        )
    }

    static func listQuarantine(root: URL) -> [QuarantineEntry] {
        let fm = FileManager.default
        guard let dayDirs = try? fm.contentsOfDirectory(
            at: root,
            includingPropertiesForKeys: [.isDirectoryKey],
            options: [.skipsHiddenFiles]
        ) else { return [] }

        var entries: [QuarantineEntry] = []
        for dayDir in dayDirs {
            guard let isDir = try? dayDir.resourceValues(forKeys: [.isDirectoryKey]).isDirectory,
                  isDir,
                  let day = isoDate.date(from: dayDir.lastPathComponent) else { continue }
            guard let files = try? fm.subpathsOfDirectory(atPath: dayDir.path) else { continue }
            for sub in files {
                if sub.hasSuffix(".jury-restore.json") { continue }
                let fileURL = dayDir.appendingPathComponent(sub)
                var isDir2: ObjCBool = false
                guard fm.fileExists(atPath: fileURL.path, isDirectory: &isDir2),
                      !isDir2.boolValue else { continue }
                let sidecarURL = fileURL.deletingLastPathComponent()
                    .appendingPathComponent(fileURL.lastPathComponent + ".jury-restore.json")
                var originalPath = fileURL
                var quarantinedAt = day
                if let data = try? Data(contentsOf: sidecarURL),
                   let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                    if let p = obj["original_path"] as? String {
                        originalPath = URL(fileURLWithPath: p)
                    }
                    if let s = obj["quarantined_at"] as? String,
                       let d = isoDate.date(from: s) {
                        quarantinedAt = d
                    }
                }
                entries.append(QuarantineEntry(
                    currentPath: fileURL,
                    originalPath: originalPath,
                    quarantinedAt: quarantinedAt
                ))
            }
        }
        entries.sort { $0.quarantinedAt < $1.quarantinedAt }
        return entries
    }

    static func countUndo(log: URL, action: String) -> Int {
        guard let data = try? Data(contentsOf: log),
              let text = String(data: data, encoding: .utf8) else { return 0 }
        var n = 0
        for line in text.split(separator: "\n", omittingEmptySubsequences: true) {
            let parts = line.split(separator: "\t", maxSplits: 2, omittingEmptySubsequences: false)
            if parts.count >= 2 && parts[1] == action { n += 1 }
        }
        return n
    }

    static func todayActions(stateFile: URL) -> Int {
        guard let data = try? Data(contentsOf: stateFile),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return 0 }
        guard let d = obj["date"] as? String, d == todayISO() else { return 0 }
        return (obj["actions"] as? Int) ?? 0
    }

    static func readRawLastRun(file: URL) -> String {
        (try? String(contentsOf: file, encoding: .utf8))?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    }

    static func parseLastRunDate(_ raw: String) -> Date? {
        guard !raw.isEmpty else { return nil }
        if let d = isoFullFractional.date(from: raw) { return d }
        if let d = isoFull.date(from: raw) { return d }
        return isoLocal.date(from: raw)
    }

    private static func todayISO() -> String {
        isoDate.string(from: Date())
    }
}

// MARK: - Menubar glyph (outlined judicial wig)

enum MenubarGlyph {
    /// Courthouse drawn as clean lines. One triangle outline for the
    /// pediment, three vertical strokes for the columns, one horizontal
    /// stroke for the base. Nothing else - no rounded rectangles, no
    /// entablature, no plinth. Single NSBezierPath with five subpaths,
    /// stroked at 1.4pt.
    static let icon: NSImage = {
        let size = NSSize(width: 22, height: 16)
        let img = NSImage(size: size, flipped: false) { _ in
            NSColor.black.setStroke()

            let path = NSBezierPath()
            path.lineWidth = 1.4
            path.lineJoinStyle = .round
            path.lineCapStyle = .round

            // Pediment - closed triangle outline
            path.move(to: NSPoint(x: 2, y: 10))
            path.line(to: NSPoint(x: 11, y: 14.5))
            path.line(to: NSPoint(x: 20, y: 10))
            path.close()

            // Three column strokes (vertical lines)
            for x in [5.0, 11.0, 17.0] as [CGFloat] {
                path.move(to: NSPoint(x: x, y: 9.5))
                path.line(to: NSPoint(x: x, y: 3))
            }

            // Base - single horizontal stroke
            path.move(to: NSPoint(x: 1.5, y: 2))
            path.line(to: NSPoint(x: 20.5, y: 2))

            path.stroke()
            return true
        }
        img.isTemplate = true
        return img
    }()
}

// MARK: - Status controller

/// Borderless NSPanel that's allowed to become key, so SwiftUI buttons
/// inside it receive mouse events. The default `NSPanel` won't accept
/// key status when the style mask is `.borderless` alone, which causes
/// clicks inside the panel to be ignored.
final class StatusPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
}

final class JuryStatusController: NSObject {
    let statusItem: NSStatusItem
    let paths = Paths()
    private var panel: StatusPanel?
    private var eventMonitor: Any?

    static let popoverWidth: CGFloat = 300

    override init() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        super.init()

        if let button = statusItem.button {
            button.image = MenubarGlyph.icon
            button.imagePosition = .imageOnly
            button.target = self
            button.action = #selector(togglePanel(_:))
            button.toolTip = "Jury"
        }
    }

    // We use a borderless NSPanel rather than NSPopover. The macOS 26
    // NSPopover wraps content in a translucent "liquid glass" frame that
    // can't be disabled via public API and shows through to whatever the
    // wallpaper is, breaking the parchment/walnut surface. With an
    // NSPanel we own every pixel: the SwiftUI view paints solid colour
    // edge-to-edge, rounds its own corners, and casts its own shadow.

    @objc func togglePanel(_ sender: Any?) {
        if panel != nil {
            closePanel()
        } else {
            showPanel()
        }
    }

    private func showPanel() {
        guard let button = statusItem.button else { return }

        let snapshot = State.snapshot(paths: paths)
        let view = StatsPanel(snapshot: snapshot) { [weak self] action in
            self?.handle(action)
        }

        // NSHostingView (not Controller). Its `fittingSize` returns the
        // SwiftUI view's intrinsic size eagerly, before the view is in
        // any window. That lets us size the panel correctly the first
        // time, instead of relying on a layout cycle after attachment.
        let hostingView = NSHostingView(rootView: view)
        let size = hostingView.fittingSize
        hostingView.frame = NSRect(origin: .zero, size: size)

        let panel = StatusPanel(
            contentRect: NSRect(origin: .zero, size: size),
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        panel.contentView = hostingView
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = false
        panel.level = .floating
        panel.isMovable = false
        panel.hidesOnDeactivate = false
        panel.isReleasedWhenClosed = false
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]

        if let buttonWindow = button.window {
            let buttonInWindow = button.convert(button.bounds, to: nil)
            let buttonOnScreen = buttonWindow.convertToScreen(buttonInWindow)
            // Shift the panel up by the outer top padding so the popover
            // arrow tip sits right at the menubar bottom edge.
            let outerTopPadding: CGFloat = 6
            let origin = NSPoint(
                x: buttonOnScreen.midX - size.width / 2,
                y: buttonOnScreen.minY + outerTopPadding - size.height
            )
            panel.setFrameOrigin(origin)
        }

        panel.makeKeyAndOrderFront(nil)
        self.panel = panel

        // Install the click-outside monitor on a brief delay - the
        // mouse-down that opened the panel is a system-owned menubar
        // event, and a synchronously-installed monitor sees it as
        // "outside our app", closing the panel before it renders.
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) { [weak self] in
            guard let self = self, self.panel != nil else { return }
            self.eventMonitor = NSEvent.addGlobalMonitorForEvents(
                matching: [.leftMouseDown, .rightMouseDown]
            ) { [weak self] _ in
                self?.closePanel()
            }
        }
    }

    private func closePanel() {
        panel?.orderOut(nil)
        panel = nil
        if let m = eventMonitor {
            NSEvent.removeMonitor(m)
            eventMonitor = nil
        }
    }

    private func handle(_ action: JuryAction) {
        switch action {
        case .openQuarantine:
            ensureExists(paths.quarantineRoot, asDirectory: true)
            NSWorkspace.shared.open(paths.quarantineRoot)
        case .openUndoLog:
            ensureExists(paths.undoLog, asDirectory: false)
            NSWorkspace.shared.open(paths.undoLog)
        case .openStateFile:
            ensureExists(paths.stateFile, asDirectory: false)
            NSWorkspace.shared.open(paths.stateFile)
        case .openMonitorLog:
            let target = FileManager.default.fileExists(atPath: paths.stderrLog.path)
                ? paths.stderrLog : paths.stdoutLog
            ensureExists(target, asDirectory: false)
            NSWorkspace.shared.open(target)
        case .openDigest:
            NSWorkspace.shared.open(paths.digestFile)
        case .reveal(let url):
            NSWorkspace.shared.selectFile(url.path, inFileViewerRootedAtPath: "")
        case .quit:
            NSApplication.shared.terminate(nil)
            return
        }
        closePanel()
    }

    private func ensureExists(_ url: URL, asDirectory: Bool) {
        let fm = FileManager.default
        if fm.fileExists(atPath: url.path) { return }
        if asDirectory {
            try? fm.createDirectory(at: url, withIntermediateDirectories: true)
        } else {
            try? fm.createDirectory(
                at: url.deletingLastPathComponent(),
                withIntermediateDirectories: true
            )
            fm.createFile(atPath: url.path, contents: Data())
        }
    }
}

// MARK: - Ink palette
//
// Light mode: dark sepia ink on aged cream paper.
// Dark mode:  warm cream lettering on walnut bench.
// Every text and icon in the panel pulls from this palette so the
// finish is coherent rather than just a tinted background under
// system-grey text.
struct InkPalette {
    let primary: Color
    let secondary: Color
    let tertiary: Color
    let accent: Color
    let bg: Color
    let cardBg: Color

    static func forScheme(_ scheme: ColorScheme) -> InkPalette {
        if scheme == .light {
            return InkPalette(
                primary:   Color(red: 0.18, green: 0.12, blue: 0.05),
                secondary: Color(red: 0.40, green: 0.30, blue: 0.16),
                tertiary:  Color(red: 0.58, green: 0.48, blue: 0.32),
                accent:    Color(red: 0.45, green: 0.26, blue: 0.06),
                bg:        Color(red: 0.95, green: 0.89, blue: 0.76),
                cardBg:    Color(red: 0.18, green: 0.12, blue: 0.05).opacity(0.06)
            )
        } else {
            return InkPalette(
                primary:   Color(red: 0.94, green: 0.87, blue: 0.72),
                secondary: Color(red: 0.72, green: 0.62, blue: 0.45),
                tertiary:  Color(red: 0.55, green: 0.46, blue: 0.32),
                accent:    Color(red: 0.92, green: 0.74, blue: 0.42),
                bg:        Color(red: 0.42, green: 0.30, blue: 0.18),
                cardBg:    Color(red: 0.94, green: 0.87, blue: 0.72).opacity(0.08)
            )
        }
    }
}

// MARK: - Popover shape (rounded rect with an upward arrow)

/// Rounded rectangle with a soft, curved arrow protruding from the
/// top centre - inspired by Earshot's menubar popover. The arrow uses
/// cubic Beziers so it grows smoothly out of the top edge and meets at
/// a gently-rounded peak rather than a sharp point. Used as both the
/// background fill and the clip shape so the contour is continuous.
struct PopoverShape: Shape {
    var cornerRadius: CGFloat = 14
    var arrowWidth: CGFloat = 22
    var arrowHeight: CGFloat = 8

    func path(in rect: CGRect) -> Path {
        let arrowCenterX = rect.midX
        let arrowBaseY = rect.minY + arrowHeight
        let arrowLeft = arrowCenterX - arrowWidth / 2
        let arrowRight = arrowCenterX + arrowWidth / 2
        let r = cornerRadius
        // Tangent control - higher dx = flatter at the base / softer peak.
        let dx = arrowWidth / 3.5

        var p = Path()
        // Top edge: left rounded corner end → arrow base left
        p.move(to: CGPoint(x: rect.minX + r, y: arrowBaseY))
        p.addLine(to: CGPoint(x: arrowLeft, y: arrowBaseY))
        // Smooth bump up to the peak. Both control points keep the
        // tangent horizontal at start (so it eases out of the top edge)
        // and at the peak (so the apex is round, not pointed).
        p.addCurve(
            to: CGPoint(x: arrowCenterX, y: rect.minY),
            control1: CGPoint(x: arrowLeft + dx, y: arrowBaseY),
            control2: CGPoint(x: arrowCenterX - dx, y: rect.minY)
        )
        // Mirror down from peak to arrow base right
        p.addCurve(
            to: CGPoint(x: arrowRight, y: arrowBaseY),
            control1: CGPoint(x: arrowCenterX + dx, y: rect.minY),
            control2: CGPoint(x: arrowRight - dx, y: arrowBaseY)
        )
        // Right along top to top-right corner
        p.addLine(to: CGPoint(x: rect.maxX - r, y: arrowBaseY))
        // Top-right rounded corner
        p.addArc(
            center: CGPoint(x: rect.maxX - r, y: arrowBaseY + r),
            radius: r,
            startAngle: .degrees(-90),
            endAngle: .degrees(0),
            clockwise: false
        )
        // Right edge
        p.addLine(to: CGPoint(x: rect.maxX, y: rect.maxY - r))
        // Bottom-right corner
        p.addArc(
            center: CGPoint(x: rect.maxX - r, y: rect.maxY - r),
            radius: r,
            startAngle: .degrees(0),
            endAngle: .degrees(90),
            clockwise: false
        )
        // Bottom edge
        p.addLine(to: CGPoint(x: rect.minX + r, y: rect.maxY))
        // Bottom-left corner
        p.addArc(
            center: CGPoint(x: rect.minX + r, y: rect.maxY - r),
            radius: r,
            startAngle: .degrees(90),
            endAngle: .degrees(180),
            clockwise: false
        )
        // Left edge
        p.addLine(to: CGPoint(x: rect.minX, y: arrowBaseY + r))
        // Top-left corner
        p.addArc(
            center: CGPoint(x: rect.minX + r, y: arrowBaseY + r),
            radius: r,
            startAngle: .degrees(180),
            endAngle: .degrees(270),
            clockwise: false
        )
        p.closeSubpath()
        return p
    }
}

// MARK: - SwiftUI panel

struct StatsPanel: View {
    let snapshot: Snapshot
    let dispatch: (JuryAction) -> Void
    @Environment(\.colorScheme) private var colorScheme

    private var ink: InkPalette { .forScheme(colorScheme) }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            header
            statCards
            if !snapshot.expiring.isEmpty {
                expiringSection
            }
            healthRow
            if snapshot.actionFlagged {
                flaggedBanner
            }
            mottoLine
        }
        // Top padding bumped by arrow height (8pt) so content clears
        // the upward-pointing arrow at the top of the popover shape.
        .padding(EdgeInsets(top: 19, leading: 11, bottom: 11, trailing: 11))
        .frame(width: JuryStatusController.popoverWidth)
        .background(PopoverShape().fill(parchmentFill))
        .clipShape(PopoverShape())
        // Outer padding inside the NSPanel. Top is small - the arrow needs
        // to sit right under the menubar.
        .padding(.horizontal, 16)
        .padding(.top, 6)
        .padding(.bottom, 18)
    }

    /// Parchment / walnut surface as a fillable ShapeStyle. Two stops
    /// ~3% apart in lightness give a barely-there gradient that adds
    /// depth without reading as flashy. Solid (not translucent) so the
    /// wallpaper behind the app doesn't tint our surface.
    private var parchmentFill: LinearGradient {
        if colorScheme == .light {
            return LinearGradient(
                colors: [
                    Color(red: 0.96, green: 0.92, blue: 0.80),
                    Color(red: 0.93, green: 0.88, blue: 0.74),
                ],
                startPoint: .top,
                endPoint: .bottom
            )
        } else {
            return LinearGradient(
                colors: [
                    Color(red: 0.18, green: 0.13, blue: 0.08),
                    Color(red: 0.14, green: 0.10, blue: 0.06),
                ],
                startPoint: .top,
                endPoint: .bottom
            )
        }
    }

    // Header: wig glyph + title/subtitle on the left; three small icon
    // buttons on the right (digest, log, adjourn). Replaces the older
    // pill-row at the bottom and removes the duplicate "Quarantine"
    // shortcut (the stat cards already act as that entry point).
    private var header: some View {
        HStack(alignment: .center, spacing: 8) {
            Image(nsImage: MenubarGlyph.icon)
                .renderingMode(.template)
                .interpolation(.high)
                .foregroundStyle(ink.primary)
                .frame(width: 22, height: 16)
            Text("Jury")
                .font(.system(.title3, design: .serif).weight(.semibold))
                .foregroundStyle(ink.primary)
            Spacer()
            HStack(spacing: 2) {
                if snapshot.digestExists {
                    HeaderIconButton(
                        symbol: "doc.text.fill",
                        tooltip: "Open the weekly digest written by the Sunday run.",
                        ink: ink
                    ) { dispatch(.openDigest) }
                }
                HeaderIconButton(
                    symbol: "text.alignleft",
                    tooltip: "Open the append-only log of every quarantine and final-trash action.",
                    ink: ink
                ) { dispatch(.openUndoLog) }
                HeaderIconButton(
                    symbol: "power",
                    tooltip: "Quit Jury (the monitor keeps running independently).",
                    ink: ink
                ) { dispatch(.quit) }
            }
        }
    }

    private var statCards: some View {
        HStack(spacing: 8) {
            StatCard(
                icon: "tray.full.fill",
                label: "Quarantined",
                value: "\(snapshot.entries.count)",
                tint: snapshot.entries.isEmpty ? ink.tertiary : ink.accent,
                ink: ink
            ) { dispatch(.openQuarantine) }
            .help("Files currently in quarantine. Click to open the folder.")
            StatCard(
                icon: "clock.badge.exclamationmark.fill",
                label: "Expiring soon",
                value: "\(snapshot.expiring.count)",
                tint: snapshot.expiring.isEmpty ? ink.tertiary : .orange,
                ink: ink
            ) { dispatch(.openQuarantine) }
            .help("Files within 7 days of being final-trashed. Click to review.")
        }
    }

    private var expiringSection: some View {
        VStack(alignment: .leading, spacing: 3) {
            sectionLabel("Expiring soon")
            ForEach(Array(snapshot.expiring.prefix(6))) { e in
                ExpiringRow(entry: e, ink: ink) { dispatch(.reveal(e.currentPath)) }
            }
            if snapshot.expiring.count > 6 {
                Text("+\(snapshot.expiring.count - 6) more")
                    .font(.caption2)
                    .foregroundStyle(ink.tertiary)
                    .padding(.top, 1)
            }
        }
    }

    private var healthRow: some View {
        Button {
            dispatch(.openMonitorLog)
        } label: {
            HStack(spacing: 7) {
                Image(systemName: snapshot.inRecess
                      ? "moon.zzz"
                      : "dot.radiowaves.up.forward")
                    .font(.caption)
                    .foregroundStyle(ink.secondary)
                Text(snapshot.inRecess ? "Idle since" : "Last run")
                    .font(.callout)
                    .foregroundStyle(ink.secondary)
                Spacer()
                Text(snapshot.lastSessionDisplay)
                    .font(.callout.monospacedDigit())
                    .foregroundStyle(ink.primary)
                Image(systemName: "arrow.up.right")
                    .font(.system(size: 9, weight: .semibold))
                    .foregroundStyle(ink.tertiary)
            }
            .padding(.vertical, 5)
            .padding(.horizontal, 9)
            .background(ink.cardBg)
            .clipShape(RoundedRectangle(cornerRadius: 7))
        }
        .buttonStyle(.plain)
        .help("Time the filesystem monitor last fired. Click to open its log.")
    }

    private var flaggedBanner: some View {
        let copy = snapshot.docketFull
            ? "Daily action limit reached (\(Snapshot.dailyCeiling))."
            : "Approaching daily limit (\(snapshot.todayActions) of \(Snapshot.dailyCeiling))."
        return HStack(spacing: 7) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(.red)
            Text(copy)
                .font(.caption.weight(.medium))
                .foregroundStyle(.red)
            Spacer()
        }
        .padding(.vertical, 5)
        .padding(.horizontal, 9)
        .background(Color.red.opacity(0.12))
        .clipShape(RoundedRectangle(cornerRadius: 7))
        .onTapGesture { dispatch(.openStateFile) }
        .help("Approaching the daily action ceiling. Click to inspect the state file.")
    }

    private var mottoLine: some View {
        HStack {
            Spacer()
            Text(snapshot.motto)
                .font(.system(.caption2, design: .serif).italic())
                .foregroundStyle(ink.tertiary)
            Spacer()
        }
        .padding(.top, 1)
    }

    private func sectionLabel(_ text: String) -> some View {
        Text(text.uppercased())
            .font(.caption2.weight(.semibold))
            .tracking(0.5)
            .foregroundStyle(ink.secondary)
    }
}

// MARK: - SwiftUI components

struct HeaderIconButton: View {
    let symbol: String
    let tooltip: String
    let ink: InkPalette
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.callout)
                .foregroundStyle(ink.secondary)
                .frame(width: 22, height: 22)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help(tooltip)
    }
}

struct StatCard: View {
    let icon: String
    let label: String
    let value: String
    let tint: Color
    let ink: InkPalette
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 4) {
                    Image(systemName: icon)
                        .font(.footnote)
                        .foregroundStyle(tint)
                    Text(label)
                        .font(.caption)
                        .foregroundStyle(ink.secondary)
                    Spacer(minLength: 4)
                    // "Opens elsewhere" affordance - subtle visual hint
                    // that the whole card is a click target.
                    Image(systemName: "arrow.up.right")
                        .font(.system(size: 9, weight: .semibold))
                        .foregroundStyle(ink.tertiary)
                }
                Text(value)
                    .font(.system(size: 24, weight: .semibold, design: .rounded))
                    .monospacedDigit()
                    .foregroundStyle(tint)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 10)
            .padding(.vertical, 7)
            .background(ink.cardBg)
            .clipShape(RoundedRectangle(cornerRadius: 10))
        }
        .buttonStyle(.plain)
    }
}

struct ExpiringRow: View {
    let entry: QuarantineEntry
    let ink: InkPalette
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 7) {
                Text("\(entry.daysUntilPurge)d")
                    .font(.caption.weight(.semibold).monospacedDigit())
                    .padding(.horizontal, 6)
                    .padding(.vertical, 1.5)
                    .background(Color.orange.opacity(0.18))
                    .foregroundStyle(.orange)
                    .clipShape(Capsule())
                Text(entry.originalPath.lastPathComponent)
                    .font(.callout)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .foregroundStyle(ink.primary)
                Spacer()
                Image(systemName: "arrow.up.right")
                    .font(.caption2)
                    .foregroundStyle(ink.secondary)
            }
            .padding(.vertical, 3)
            .padding(.horizontal, 5)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

// MARK: - Entry point

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let controller = JuryStatusController()
app.run()
