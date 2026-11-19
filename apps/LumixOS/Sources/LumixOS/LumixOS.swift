// LumixOS: native macOS live view + settings for a LUMIX camera on USB.
// Talks to `python -m lumix.server` on 127.0.0.1:<lumixPort> (started automatically from the repo's .venv,
// log in ~/Library/Logs/LumixOS/server.log). The live picture carries a camera-style HUD: hover a value to
// open its options, click to set it.
import AppKit
import ImageIO
import SwiftUI

/// Service port: LUMIX_PORT, else `defaults write local.lumixos.app LumixPort -int N`, else 8765.
/// 8765 is also AnkiConnect's port, so pick another one when Anki runs.
let lumixPort: Int = {
    if let s = ProcessInfo.processInfo.environment["LUMIX_PORT"], let p = Int(s), p > 0 { return p }
    let p = UserDefaults.standard.integer(forKey: "LumixPort")
    return p > 0 ? p : 8765
}()
let serverURL = URL(string: "http://127.0.0.1:\(lumixPort)")!

// MARK: - Backend process (start, log, restart, stop cleanly)

@MainActor
final class Backend: ObservableObject {
    static let shared = Backend()
    @Published var problem: String?
    private var process: Process?
    private var stopping = false
    private var restarts: [Date] = []

    var repo: URL {
        // LUMIX_REPO, else `defaults write local.lumixos.app LumixRepo <path>`, else the repo build_app.sh ran in
        if let r = ProcessInfo.processInfo.environment["LUMIX_REPO"] { return URL(fileURLWithPath: r) }
        if let r = UserDefaults.standard.string(forKey: "LumixRepo") {
            return URL(fileURLWithPath: (r as NSString).expandingTildeInPath)
        }
        if let r = Bundle.main.object(forInfoDictionaryKey: "LumixRepo") as? String, !r.isEmpty {
            return URL(fileURLWithPath: r)
        }
        return FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Projects/lumix-lut-tools")
    }

    let logURL: URL = {
        let d = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/LumixOS")
        try? FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
        return d.appendingPathComponent("server.log")
    }()

    func start() {
        Task {
            if await Backend.serverAnswers() { problem = nil; return }  // already running (started by hand): use it
            launch()
        }
    }

    func retry() {
        restarts.removeAll()
        if process == nil { start() }
    }

    static func serverAnswers() async -> Bool {
        var req = URLRequest(url: serverURL.appendingPathComponent("state"))
        req.timeoutInterval = 1
        guard let (d, r) = try? await URLSession.shared.data(for: req) else { return false }
        // another program on the port (AnkiConnect answers any GET with 200 and plain text) is not the camera service
        return (r as? HTTPURLResponse)?.statusCode == 200 && (try? JSONSerialization.jsonObject(with: d)) is [String: Any]
    }

    private func launch() {
        let python = repo.appendingPathComponent(".venv/bin/python")
        guard FileManager.default.isExecutableFile(atPath: python.path) else {
            problem = "Camera service not found: \(python.path)\nSet up the repo's .venv (see README) or set LUMIX_REPO."
            return
        }
        if !FileManager.default.fileExists(atPath: logURL.path) {
            FileManager.default.createFile(atPath: logURL.path, contents: nil)
        }
        let log = try? FileHandle(forWritingTo: logURL)
        _ = try? log?.seekToEnd()
        let p = Process()
        p.executableURL = python
        p.arguments = ["-u", "-m", "lumix.server", "--size", "1280x960", "--port", String(lumixPort)]
        p.currentDirectoryURL = repo
        p.standardOutput = log ?? FileHandle.nullDevice
        p.standardError = log ?? FileHandle.nullDevice
        p.terminationHandler = { proc in
            let code = proc.terminationStatus
            Task { @MainActor in Backend.shared.exited(code) }
        }
        do {
            try p.run()
            process = p
            problem = nil
        } catch {
            problem = "Could not start the camera service: \(error.localizedDescription)"
        }
    }

    private func exited(_ code: Int32) {
        process = nil
        guard !stopping else { return }
        if code == 2 {  // port taken: use the other lumix-server if it answers
            Task {
                problem = await Backend.serverAnswers() ? nil
                    : "Port \(lumixPort) is used by another program (Anki's AnkiConnect uses 8765). Quit it, or pick "
                      + "another port: defaults write local.lumixos.app LumixPort -int 8767, reopen LumixOS."
            }
            return
        }
        restarts = restarts.filter { Date().timeIntervalSince($0) < 60 } + [Date()]
        let last = lastLogLine()
        if restarts.count > 5 {
            problem = "Camera service keeps stopping (exit \(code)).\n\(last)"
            return
        }
        problem = "Camera service stopped (exit \(code)): \(last)\nRestarting…"
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) { if !self.stopping { self.launch() } }
    }

    func lastLogLine() -> String {
        guard let d = try? Data(contentsOf: logURL) else { return "no log" }
        let lines = String(decoding: d.suffix(4000), as: UTF8.self).split(separator: "\n").map(String.init)
        return lines.last(where: { !$0.trimmingCharacters(in: .whitespaces).isEmpty }) ?? "no output"
    }

    func openLog() { NSWorkspace.shared.open(logURL) }

    func stop() {
        stopping = true
        guard let p = process, p.isRunning else { return }
        p.terminate()  // SIGTERM: the service stops live view on the camera first
        let deadline = Date().addingTimeInterval(2.5)
        while p.isRunning && Date() < deadline { usleep(50_000) }
        if p.isRunning { kill(p.processIdentifier, SIGKILL) }
    }
}

let obsFeed = OBSCameraFeed()

/// Camera menu action: send the live view to OBS's virtual camera (remembered across launches).
@MainActor func setOBSFeed(_ on: Bool) {
    UserDefaults.standard.set(on, forKey: "obsFeed")
    on ? obsFeed.start() : obsFeed.stop()
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ n: Notification) {
        MainActor.assumeIsolated { Backend.shared.start() }
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
    }
    func applicationWillTerminate(_ n: Notification) { MainActor.assumeIsolated { Backend.shared.stop() } }
    func applicationShouldTerminateAfterLastWindowClosed(_ s: NSApplication) -> Bool { true }
}

// MARK: - MJPEG stream (newest frame only)

final class LiveStream: NSObject, ObservableObject, URLSessionDataDelegate {
    @Published var image: CGImage?
    @Published var peakImage: CGImage?  // focus peaking overlay for `image`
    private let peaking = Peaking()
    private var peakSettings: (Float, PeakColor)?  // guarded by lock
    func setPeaking(_ level: PeakLevel, _ color: PeakColor) {
        lock.lock(); peakSettings = level.threshold.map { ($0, color) }; lock.unlock()
        if level == .off { peakImage = nil }
    }
    @Published var displayFPS: Double = 0
    @Published var lastFrameAt: Date?
    private var buffer = Data()
    private var session: URLSession?
    private var frames = 0
    private var fpsStart = Date()
    private let decodeQueue = DispatchQueue(label: "decode", qos: .userInteractive)
    private let lock = NSLock()
    private var decoding = false
    var onFrame: ((CGImage) -> Void)?  // every decoded frame (decode queue), e.g. for the OBS virtual camera

    func connect() {
        buffer.removeAll()
        let cfg = URLSessionConfiguration.ephemeral
        cfg.timeoutIntervalForRequest = 5  // no bytes for 5 s = stalled: reconnect
        cfg.timeoutIntervalForResource = 7 * 24 * 3600
        let q = OperationQueue()
        q.maxConcurrentOperationCount = 1
        let s = URLSession(configuration: cfg, delegate: self, delegateQueue: q)
        session = s
        // raw: URLSession would otherwise split the multipart itself and hand a frame over only when the next starts
        s.dataTask(with: URL(string: "stream?raw=1", relativeTo: serverURL)!).resume()
    }

    func urlSession(_ s: URLSession, dataTask: URLSessionDataTask, didReceive response: URLResponse,
                    completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        completionHandler((response as? HTTPURLResponse)?.statusCode == 200 ? .allow : .cancel)
    }

    func urlSession(_ s: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        buffer.append(data)
        if buffer.count > 32 << 20 { buffer.removeAll(); return }  // garbage: resync on the next header
        var latest: Data?
        // take every complete part, keep only the newest JPEG
        while let hdrEnd = buffer.range(of: Data("\r\n\r\n".utf8)) {
            let header = String(decoding: buffer[buffer.startIndex..<hdrEnd.lowerBound], as: UTF8.self)
            guard let lenLine = header.split(separator: "\r\n").first(where: { $0.lowercased().hasPrefix("content-length") }),
                  let len = Int(lenLine.split(separator: ":").last?.trimmingCharacters(in: .whitespaces) ?? "") else {
                buffer.removeSubrange(buffer.startIndex..<hdrEnd.upperBound); continue
            }
            let start = hdrEnd.upperBound
            guard buffer.distance(from: start, to: buffer.endIndex) >= len else { break }
            latest = buffer.subdata(in: start..<buffer.index(start, offsetBy: len))
            buffer.removeSubrange(buffer.startIndex..<buffer.index(start, offsetBy: len))
        }
        guard let jpg = latest else { return }
        lock.lock()
        if decoding { lock.unlock(); return }  // drop frames while decoding: lowest latency
        decoding = true
        lock.unlock()
        decodeQueue.async {
            let opts = [kCGImageSourceShouldCacheImmediately: true] as CFDictionary
            let img = CGImageSourceCreateWithData(jpg as CFData, nil).flatMap { CGImageSourceCreateImageAtIndex($0, 0, opts) }
            self.lock.lock(); self.decoding = false; self.lock.unlock()
            guard let img else { return }  // broken JPEG: skip it
            self.onFrame?(img)
            self.lock.lock(); let ps = self.peakSettings; self.lock.unlock()
            let peak = ps.flatMap { self.peaking.overlay(img, threshold: $0.0, color: $0.1) }
            DispatchQueue.main.async {
                self.image = img
                self.peakImage = peak
                self.lastFrameAt = Date()
                self.tick()
            }
        }
    }

    func urlSession(_ s: URLSession, task: URLSessionTask, didCompleteWithError e: Error?) {
        s.finishTasksAndInvalidate()
        DispatchQueue.main.asyncAfter(deadline: .now() + 1) { self.connect() }  // service starting, restarted or stalled
    }

    private func tick() {
        frames += 1
        let dt = Date().timeIntervalSince(fpsStart)
        if dt >= 1 { displayFPS = Double(frames) / dt; frames = 0; fpsStart = Date() }
    }
}

// MARK: - Camera state and commands

struct CameraInfo: Decodable {
    var manufacturer: String
    var model: String
    var firmware: String
}

struct AFFrame: Decodable, Equatable {
    var x: Int
    var y: Int
    var w: Int
    var h: Int
}

struct HUDData: Decodable {
    var af_frame: AFFrame?
    var battery: Int?
    var shots: Int?
    var mode: String?
    var shutter: String?
    var level: [Int]?
    var histogram: [Int]?
}

struct CameraState: Decodable {
    var status: String
    var phase: String?
    var error: String?
    var camera: CameraInfo?
    var fps: Double
    var fetch_ms: Double
    var frame_age_ms: Int?
    var size: String
    var hud: HUDData?
    var options: [String: [String]]
    var current: [String: String]
}

struct Notice: Equatable {
    let text: String
    let isError: Bool
    let id = UUID()
}

@MainActor
final class CameraModel: ObservableObject {
    @Published var state: CameraState?
    @Published var serviceError: String?
    @Published var notice: Notice?
    private var timer: Timer?
    private var lastOK = Date()
    private var inFlight = false

    func startPolling() {
        timer = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { _ in Task { @MainActor in await self.refresh() } }
    }

    func refresh() async {
        guard !inFlight else { return }
        inFlight = true
        defer { inFlight = false }
        var req = URLRequest(url: serverURL.appendingPathComponent("state"))
        req.timeoutInterval = 2
        do {
            let (data, r) = try await URLSession.shared.data(for: req)
            guard (r as? HTTPURLResponse)?.statusCode == 200 else { throw URLError(.badServerResponse) }
            state = try JSONDecoder().decode(CameraState.self, from: data)
            lastOK = Date()
            serviceError = nil
        } catch is DecodingError {
            serviceError = "The camera service sent data this app doesn't understand. Rebuild LumixOS and the service from the same repo version."
        } catch {
            if Date().timeIntervalSince(lastOK) > 2.5 { serviceError = "Camera service not responding." }
        }
    }

    func set(_ key: String, _ label: String) {
        if key == "focus_mode" && label != "MF" { mfAssistEnd() }
        state?.current[key] = label  // optimistic: the HUD follows at once, the next poll corrects it
        post("set", ["key": key, "label": label])
    }

    func focus(_ step: String, ms: Int = 150) {
        post("focus", ["step": step, "ms": ms], quiet: true)
    }

    @Published var mfAssist = false

    /// MF assist (camera's magnified view) at (x, y) in 0..1000; mode 2 = magnify (verified on a DC-S9).
    func mfAssistStart(x: Double, y: Double) {
        mfAssist = true
        post("mfassist", ["action": "start", "x": Int(x.rounded()), "y": Int(y.rounded()), "mode": 2], quiet: true)
    }

    func mfAssistEnd() {
        guard mfAssist else { return }
        mfAssist = false
        post("mfassist", ["action": "end"], quiet: true)
    }

    /// Touch AF: move the AF area to (x, y) in 0..1000 and focus there.
    func touch(x: Double, y: Double) {
        post("touch", ["x": Int(x.rounded()), "y": Int(y.rounded()), "af": true], quiet: true)
    }

    func post(_ path: String, _ body: [String: Any] = [:], quiet: Bool = false) {
        var req = URLRequest(url: serverURL.appendingPathComponent(path))
        req.httpMethod = "POST"
        req.timeoutInterval = 12
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        Task {
            do {
                let (data, r) = try await URLSession.shared.data(for: req)
                let j = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
                let code = (r as? HTTPURLResponse)?.statusCode ?? 0
                if code == 200 { if !quiet { show(j?["message"] as? String ?? "done", error: false) } }
                else { show(j?["error"] as? String ?? "camera service answered HTTP \(code)", error: true) }
            } catch {
                show("Camera service not reachable: \(error.localizedDescription)", error: true)
            }
            if !quiet { await refresh() }
        }
    }

    func show(_ text: String, error: Bool) {
        let n = Notice(text: text, isError: error)
        notice = n
        DispatchQueue.main.asyncAfter(deadline: .now() + (error ? 6 : 1.5)) { if self.notice == n { self.notice = nil } }
    }
}

// MARK: - Labels as the camera shows them

enum Fmt {
    static let styles = ["standard": "STD", "vivid": "VIVD", "natural": "NAT", "monochrome": "MONO", "scenery": "SCNY",
                         "portrait": "PORT", "l_monochrome": "L.MONO", "cinelike_d2": "CNL-D2", "vlog": "V-LOG",
                         "custom1": "C1", "custom2": "C2", "custom3": "C3", "real_time_lut": "LUT"]
    static let wb = ["auto": "AWB", "daylight": "Daylight", "fluorescent": "Fluor.", "tungsten": "Tungsten", "flash": "Flash",
                     "cloudy": "Cloudy", "white_set": "WB Set", "shade": "Shade", "kelvin": "K1", "kelvin2": "K2",
                     "kelvin3": "K3", "kelvin4": "K4", "auto_cool": "AWBc", "auto_warm": "AWBw"]

    static func value(_ key: String, _ label: String) -> String {
        switch key {
        case "photo_style": return styles[label] ?? label.uppercased()
        case "white_balance": return wb[label] ?? label
        case "kelvin": return label.replacingOccurrences(of: " ", with: "")
        case "image_format": return label == "raw+jpeg" ? "RAW+JPG" : label.uppercased()
        case "aperture":
            guard let v = Double(label.replacingOccurrences(of: "f/", with: "")) else { return label }
            return "F" + String(format: v >= 10 ? "%.0f" : "%.1f", v)
        case "exposure_comp": return ev(label)
        case "iso": return label == "auto" ? "AUTO" : label
        case "lut1_opacity": return label
        case "focus_mode": return label
        default: return label
        }
    }

    /// "-0.67 EV" -> "-2/3", "+1.33 EV" -> "+1 1/3", "0 EV" -> "±0"
    static func ev(_ label: String) -> String {
        guard let v = Double(label.replacingOccurrences(of: " EV", with: "")) else { return label }
        let t = Int((v * 3).rounded())
        if t == 0 { return "0" }
        let a = abs(t), w = a / 3, f = a % 3
        return (t < 0 ? "-" : "+") + (w > 0 ? "\(w)" : "") + (f > 0 ? (w > 0 ? " " : "") + "\(f)/3" : "")
    }

    static func isKelvin(_ wb: String?) -> Bool { wb?.hasPrefix("kelvin") ?? false }
}

// MARK: - Hover menus

@MainActor
final class HoverMenu: ObservableObject {
    @Published var open: String?
    private var closeWork: DispatchWorkItem?

    func enter(_ id: String) {
        closeWork?.cancel()
        if open != id { withAnimation(menuSpring) { open = id } }
    }
    func leave(_ id: String) {
        closeWork?.cancel()
        let w = DispatchWorkItem { [weak self] in
            if self?.open == id { withAnimation(.easeOut(duration: 0.14)) { self?.open = nil } }
        }
        closeWork = w
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.35, execute: w)
    }
    func toggle(_ id: String) { closeWork?.cancel(); withAnimation(menuSpring) { open = open == id ? nil : id } }
    func close() { closeWork?.cancel(); withAnimation(.easeOut(duration: 0.14)) { open = nil } }
}

struct MenuColumn: Identifiable {
    let key: String
    let title: String
    var id: String { key }
}

let panasonicYellow = Color(red: 1.0, green: 0.82, blue: 0.0)
let menuSpring = Animation.spring(response: 0.28, dampingFraction: 0.82)

extension View {
    /// Liquid Glass on macOS 26+, material before.
    @ViewBuilder func glass<S: Shape>(_ shape: S, tint: Color? = nil, interactive: Bool = false) -> some View {
        if #available(macOS 26.0, *) {
            self.glassEffect(interactive ? Glass.regular.tint(tint).interactive() : Glass.regular.tint(tint), in: shape)
        } else {
            self.background(.ultraThinMaterial, in: shape)
        }
    }

    @ViewBuilder func glassButtonStyle(prominent: Bool = false) -> some View {
        if #available(macOS 26.0, *) {
            if prominent { self.buttonStyle(.glassProminent) } else { self.buttonStyle(.glass) }
        } else {
            self.buttonStyle(.bordered)
        }
    }
}

/// One value on the HUD. Hover (or click) opens its option columns; clicking an option sets it.
struct HUDItem<Label: View>: View {
    let id: String
    let columns: [MenuColumn]
    let opensDown: Bool
    let fs: CGFloat
    let maxMenuHeight: CGFloat
    @ObservedObject var hover: HoverMenu
    @ObservedObject var camera: CameraModel
    @ViewBuilder let label: () -> Label

    var itemHeight: CGFloat { fs * 1.7 }
    var isOpen: Bool { hover.open == id && !columns.isEmpty }

    var body: some View {
        label()
            .font(.system(size: fs, weight: .semibold, design: .rounded))
            .foregroundStyle(.white)
            .shadow(color: .black.opacity(0.35), radius: 4)
            .padding(.horizontal, fs * 0.35)
            .frame(height: itemHeight)
            .background(isOpen ? Color.white.opacity(0.18) : (columns.isEmpty ? .clear : Color.white.opacity(0.001)),
                        in: RoundedRectangle(cornerRadius: fs * 0.3))
            .contentShape(Rectangle())
            .onHover { inside in
                guard !columns.isEmpty else { return }
                inside ? hover.enter(id) : hover.leave(id)
            }
            .onTapGesture { if !columns.isEmpty { hover.toggle(id) } }
            .overlay(alignment: opensDown ? .top : .bottom) {  // centred: the pointer sits right over the value
                if isOpen {
                    menu
                        .onHover { inside in inside ? hover.enter(id) : hover.leave(id) }
                        .offset(y: opensDown ? itemHeight + 6 : -(itemHeight + 6))
                        .transition(.asymmetric(
                            insertion: .scale(scale: 0.86, anchor: opensDown ? .top : .bottom)
                                .combined(with: .opacity)
                                .combined(with: .offset(y: opensDown ? -8 : 8)),
                            removal: .opacity.combined(with: .scale(scale: 0.96, anchor: opensDown ? .top : .bottom))))
                }
            }
            .zIndex(isOpen ? 10 : 0)
    }

    var menu: some View {
        VStack(spacing: fs * 0.2) {
            ForEach(columns) { col in
                if let opts = camera.state?.options[col.key], !opts.isEmpty {
                    ValueDial(key: col.key, title: col.title, options: opts, current: camera.state?.current[col.key],
                              fs: fs) { camera.set(col.key, $0) }
                }
            }
        }
        .padding(.vertical, fs * 0.35)
        .padding(.horizontal, fs * 0.3)
        .contentShape(Rectangle())  // keeps hover alive between the dials
        .fixedSize()
    }
}

/// Horizontal dial in the style of the iOS timer / the MF dial: values on an orange scale under a fixed pointer.
/// Drag to scroll (a trackpad tick per value, sets on release) or click a value.
struct ValueDial: View {
    let key: String
    let title: String
    let options: [String]
    let current: String?
    let fs: CGFloat
    let pick: (String) -> Void
    let tint = Color.white  // HUD white; orange is the MF dial's
    @State private var dragStart: CGFloat?
    @State private var pos: CGFloat?  // fractional index while dragging
    @State private var lastTick = 0

    var labels: [String] { options.map { Fmt.value(key, $0) } }
    var spacing: CGFloat {
        let longest = CGFloat(labels.map(\.count).max() ?? 3)
        return max(fs * 2.6, longest * fs * 0.52 + fs * 0.9)
    }
    var currentIndex: CGFloat { CGFloat(options.firstIndex(where: { $0 == current }) ?? 0) }
    var width: CGFloat { min(fs * 19, spacing * CGFloat(min(options.count, 7)) + fs) }

    var body: some View {
        let p = pos ?? currentIndex
        let w = width, sp = spacing
        VStack(spacing: fs * 0.1) {
            Text(title.uppercased()).font(.system(size: fs * 0.42, weight: .heavy, design: .rounded))
                .foregroundStyle(tint.opacity(0.75))
            ZStack {
                // ticks: one major per value, two minor between
                Canvas { ctx, size in
                    let mid = size.width / 2
                    let first = Int(floor(p - mid / sp)) - 1, last = Int(ceil(p + mid / sp)) + 1
                    for i in first...last where i >= 0 && i < options.count {
                        for k in 0..<3 {
                            if k > 0 && i == options.count - 1 { break }
                            let x = mid + (CGFloat(i) + CGFloat(k) / 3 - p) * sp
                            guard x > -2, x < size.width + 2 else { continue }
                            var path = Path()
                            let major = k == 0
                            path.move(to: CGPoint(x: x, y: size.height * (major ? 0.05 : 0.3)))
                            path.addLine(to: CGPoint(x: x, y: size.height * 0.95))
                            ctx.stroke(path, with: .color(tint.opacity(major ? 0.95 : 0.55)),
                                       style: StrokeStyle(lineWidth: major ? 2 : 1.3, lineCap: .round))
                        }
                    }
                }
                .frame(height: fs * 0.9)
                .offset(y: fs * 0.55)
                // value labels (clickable)
                ForEach(Array(labels.enumerated()), id: \.offset) { i, label in
                    let x = w / 2 + (CGFloat(i) - p) * sp
                    if x > -sp, x < w + sp {
                        let centred = abs(CGFloat(i) - p) < 0.5
                        Text(label)
                            .font(.system(size: fs * (centred ? 0.82 : 0.66), weight: centred ? .bold : .semibold, design: .rounded))
                            .foregroundStyle(tint.opacity(centred ? 1 : 0.7))
                            .fixedSize()
                            .position(x: x, y: fs * 0.3)
                            .onTapGesture { pick(options[i]) }
                    }
                }
            }
            .frame(width: w, height: fs * 1.6)
            .mask(LinearGradient(stops: [.init(color: .clear, location: 0), .init(color: .black, location: 0.2),
                                         .init(color: .black, location: 0.8), .init(color: .clear, location: 1)],
                                 startPoint: .leading, endPoint: .trailing))
            .contentShape(Rectangle())
            .gesture(DragGesture(minimumDistance: 3)
                .onChanged { v in
                    if dragStart == nil { dragStart = currentIndex }
                    let np = min(max(dragStart! - v.translation.width / sp, 0), CGFloat(options.count - 1))
                    pos = np
                    let tick = Int(np.rounded())
                    if tick != lastTick {
                        lastTick = tick
                        NSHapticFeedbackManager.defaultPerformer.perform(.alignment, performanceTime: .now)
                    }
                }
                .onEnded { _ in
                    let i = Int((pos ?? currentIndex).rounded())
                    withAnimation(.spring(response: 0.3, dampingFraction: 0.8)) { pos = nil }
                    dragStart = nil
                    if options.indices.contains(i), options[i] != current { pick(options[i]) }
                })
            Triangle()
                .fill(tint)
                .frame(width: fs * 0.42, height: fs * 0.36)
                .rotationEffect(.degrees(-90))
        }
        .shadow(color: .black.opacity(0.35), radius: 4)
        .animation(.spring(response: 0.3, dampingFraction: 0.8), value: current)
        .onAppear { lastTick = Int(currentIndex) }
    }
}

// MARK: - Camera-style overlay

struct CameraHUD: View {
    @ObservedObject var camera: CameraModel
    @ObservedObject var hover: HoverMenu
    let size: CGSize
    let showGrid: Bool
    let showLevel: Bool
    let showHistogram: Bool

    var fs: CGFloat { max(11, min(22, size.width / 46)) }
    var cur: [String: String] { camera.state?.current ?? [:] }
    var hud: HUDData? { camera.state?.hud }

    func item<L: View>(_ id: String, _ cols: [MenuColumn], down: Bool, @ViewBuilder _ l: @escaping () -> L) -> some View {
        HUDItem(id: id, columns: cols, opensDown: down, fs: fs, maxMenuHeight: size.height * 0.62,
                hover: hover, camera: camera, label: l)
    }

    var body: some View {
        ZStack {
            if showGrid { grid }
            if showLevel, let l = hud?.level, l.count >= 1 { level(Double(l[0]) / 10) }
            centerCross
            VStack(spacing: 0) {
                topBar.zIndex(hover.open?.hasPrefix("top") == true ? 2 : 1)
                HStack {
                    Spacer()
                    if showHistogram, let h = hud?.histogram { histogram(h) }
                }
                .padding(.horizontal, fs * 0.6)
                Spacer()
                HStack {
                    Spacer()
                    FocusRing(fs: fs, height: size.height * 0.26, camera: camera)
                }
                Spacer()
                bottomBar.zIndex(hover.open?.hasPrefix("bottom") == true ? 2 : 1)
            }
        }
        .frame(width: size.width, height: size.height)
    }

    var topBar: some View {
        HStack(spacing: fs * 0.9) {
            item("top.wb", [MenuColumn(key: "white_balance", title: "White balance")]
                 + (Fmt.isKelvin(cur["white_balance"]) ? [MenuColumn(key: "kelvin", title: "Kelvin")] : []), down: true) {
                if Fmt.isKelvin(cur["white_balance"]) {
                    HStack(spacing: 3) { boxed("K"); Text(Fmt.value("kelvin", cur["kelvin"] ?? "")) }
                } else {
                    Text(Fmt.value("white_balance", cur["white_balance"] ?? "WB"))
                }
            }
            item("top.style", [MenuColumn(key: "photo_style", title: "Photo Style")]
                 + (cur["photo_style"] == "real_time_lut" ? [MenuColumn(key: "lut1_opacity", title: "LUT opacity"),
                                                              MenuColumn(key: "color_noise", title: "Colour noise")] : []),
                 down: true) {
                HStack(spacing: 3) {
                    boxed(Fmt.value("photo_style", cur["photo_style"] ?? "—"))
                    if cur["photo_style"] == "real_time_lut", let o = cur["lut1_opacity"], o != "100%" { Text(o) }
                }
            }
            item("top.format", [MenuColumn(key: "image_format", title: "Quality")], down: true) {
                Text(Fmt.value("image_format", cur["image_format"] ?? "—"))
            }
            if cur["focus_mode"] != nil {
                item("top.focus", [MenuColumn(key: "focus_mode", title: "Focus mode")], down: true) {
                    Text(cur["focus_mode"] ?? "")
                }
            }
            Spacer()
            item("top.usb", [], down: true) {
                HStack(spacing: 4) {
                    Image(systemName: "cable.connector")
                    Text(camera.state?.camera?.model ?? "USB")
                }
                .font(.system(size: fs * 0.75, weight: .semibold))
            }
        }
        .padding(.horizontal, fs * 0.6)
        .padding(.top, fs * 0.35)
    }

    var bottomBar: some View {
        HStack(spacing: fs * 0.9) {
            item("bottom.mode", [], down: false) { boxed(hud?.mode ?? "–") }
                .help("Exposure mode: set it on the camera dial")
            Spacer()
            item("bottom.aperture", [MenuColumn(key: "aperture", title: "Aperture")], down: false) {
                Text(Fmt.value("aperture", cur["aperture"] ?? "F–"))
            }
            item("bottom.ev", [MenuColumn(key: "exposure_comp", title: "Exposure comp.")], down: false) {
                HStack(spacing: 2) {
                    Image(systemName: "plusminus").font(.system(size: fs * 0.75, weight: .bold))
                    Text(Fmt.value("exposure_comp", cur["exposure_comp"] ?? "0 EV"))
                }
            }
            item("bottom.iso", [MenuColumn(key: "iso", title: "ISO")], down: false) {
                HStack(alignment: .firstTextBaseline, spacing: 2) {
                    Text("ISO").font(.system(size: fs * 0.6, weight: .bold))
                    Text(Fmt.value("iso", cur["iso"] ?? "—"))
                }
            }
            item("bottom.shutter", camera.state?.options["shutter"] != nil ? [MenuColumn(key: "shutter", title: "Shutter")] : [],
                 down: false) {
                Text(hud?.shutter ?? cur["shutter"] ?? "")
            }
            Spacer()
            item("bottom.shots", [], down: false) {
                HStack(spacing: 6) {
                    Text(hud?.shots.map(String.init) ?? "")
                    if let b = hud?.battery { batteryIcon(b) }
                }
            }
            .help(hud?.battery.map { "Battery \($0)% · \(hud?.shots ?? 0) shots left on the card" } ?? "")
        }
        .padding(.horizontal, fs * 0.6)
        .padding(.bottom, fs * 0.35)
    }

    func boxed(_ s: String) -> some View {
        Text(s)
            .font(.system(size: fs * 0.72, weight: .heavy, design: .rounded))
            .padding(.horizontal, 3).padding(.vertical, 1)
            .overlay(RoundedRectangle(cornerRadius: 2).stroke(.white, lineWidth: 1.2))
    }

    func batteryIcon(_ pct: Int) -> some View {
        let name = pct > 87 ? "battery.100percent" : pct > 62 ? "battery.75percent" : pct > 37 ? "battery.50percent"
            : pct > 12 ? "battery.25percent" : "battery.0percent"
        return Image(systemName: name).foregroundStyle(pct <= 12 ? .red : .white)
    }

    var grid: some View {
        Path { p in
            for i in 1...2 {
                let x = size.width * CGFloat(i) / 3, y = size.height * CGFloat(i) / 3
                p.move(to: CGPoint(x: x, y: 0)); p.addLine(to: CGPoint(x: x, y: size.height))
                p.move(to: CGPoint(x: 0, y: y)); p.addLine(to: CGPoint(x: size.width, y: y))
            }
        }
        .stroke(.white.opacity(0.55), lineWidth: 0.8)
        .shadow(color: .black.opacity(0.5), radius: 0.5)
        .allowsHitTesting(false)
    }

    var centerCross: some View {
        let s = fs * 0.6
        return Path { p in
            p.move(to: CGPoint(x: -s, y: 0)); p.addLine(to: CGPoint(x: s, y: 0))
            p.move(to: CGPoint(x: 0, y: -s)); p.addLine(to: CGPoint(x: 0, y: s))
        }
        .offsetBy(dx: size.width / 2, dy: size.height / 2)
        .stroke(.white.opacity(0.8), lineWidth: 1)
        .allowsHitTesting(false)
    }

    /// Horizon line from the live view LevelInfo (roll, assumed 0.1 deg units); green when level within 1 deg.
    func level(_ roll: Double) -> some View {
        let ok = abs(roll) < 1
        return Rectangle()
            .fill(ok ? Color.green : Color.white)
            .frame(width: size.width * 0.7, height: ok ? 2 : 1.5)
            .shadow(color: .black.opacity(0.6), radius: 1)
            .rotationEffect(.degrees(roll))
            .opacity(0.85)
            .allowsHitTesting(false)
    }

    func histogram(_ h: [Int]) -> some View {
        let m = CGFloat(max(h.max() ?? 1, 1))
        let w = fs * 6, ht = fs * 3
        return Path { p in
            let bw = w / CGFloat(h.count)
            for (i, v) in h.enumerated() {
                let bh = ht * CGFloat(v) / m
                p.addRect(CGRect(x: CGFloat(i) * bw, y: ht - bh, width: bw, height: bh))
            }
        }
        .fill(.white.opacity(0.85))
        .frame(width: w, height: ht)
        .padding(4)
        .background(.black.opacity(0.45), in: RoundedRectangle(cornerRadius: 4))
        .allowsHitTesting(false)
    }
}

/// AF point like the camera's: yellow and slightly larger while focusing, then white.
struct AFPointBox: View {
    let active: Bool
    let side: CGFloat

    var body: some View {
        let c = side * 0.28
        Path { p in
            for (x, y, dx, dy) in [(0.0, 0.0, 1.0, 1.0), (side, 0, -1, 1), (0, side, 1, -1), (side, side, -1, -1)] {
                p.move(to: CGPoint(x: x + dx * c, y: y)); p.addLine(to: CGPoint(x: x, y: y))
                p.addLine(to: CGPoint(x: x, y: y + dy * c))
            }
        }
        .stroke(active ? panasonicYellow : .white, style: StrokeStyle(lineWidth: 2, lineCap: .round))
        .frame(width: side, height: side)
        .shadow(color: .black.opacity(0.7), radius: 1)
        .scaleEffect(active ? 1.15 : 1)
    }
}

let dialOrange = Color(red: 0.98, green: 0.62, blue: 0.32)

/// MF dial in the style of the iOS timer dial: orange ticks scroll under a fixed pointer. Drag up = far, down = near;
/// faster drags drive faster. Grabbing it in AF switches to MF; the MF pill toggles MF / AFC.
struct FocusRing: View {
    let fs: CGFloat
    let height: CGFloat
    @ObservedObject var camera: CameraModel
    @State private var phase: CGFloat = 0
    @State private var lastY: CGFloat?
    @State private var pending: CGFloat = 0
    @State private var lastSend = Date.distantPast
    @State private var lastMajor = 0
    @State private var grabbed = false
    @State private var hovering = false

    @AppStorage("lastAFMode") private var lastAF = "AFC"  // AF mode to return to from MF

    var isMF: Bool { camera.state?.current["focus_mode"] == "MF" }
    var focusMode: String { camera.state?.current["focus_mode"] ?? "MF" }
    var expanded: Bool { hovering || grabbed }
    var gap: CGFloat { max(5, fs * 0.38) }

    var body: some View {
        let w = fs * 1.9
        // hot zone at the right edge: hovering slides the dial out, otherwise only a sliver of ticks peeks in
        ZStack(alignment: .trailing) {
            Color.clear.frame(width: w * 1.9).contentShape(Rectangle())
            content(width: w)
                .offset(x: expanded ? 0 : w * 0.38)
                .opacity(expanded ? 1 : (isMF ? 0.8 : 0.55))
        }
        .onHover { h in withAnimation(.spring(response: 0.32, dampingFraction: 0.82)) { hovering = h } }
        .onChange(of: focusMode) { _, m in if m != "MF", m.hasPrefix("AF") { lastAF = m } }
        .animation(.spring(response: 0.32, dampingFraction: 0.82), value: grabbed)
    }

    func content(width w: CGFloat) -> some View {
        VStack(spacing: fs * 0.2) {
            Text("∞").font(.system(size: fs * 0.62, weight: .semibold, design: .rounded)).opacity(expanded ? 1 : 0)
            dial(width: w)
            Text("NEAR").font(.system(size: fs * 0.38, weight: .bold, design: .rounded)).opacity(expanded ? 1 : 0)
            Button {
                camera.set("focus_mode", isMF ? lastAF : "MF")
            } label: {
                Text(focusMode)
                    .font(.system(size: fs * 0.48, weight: .bold, design: .rounded))
                    .padding(.horizontal, fs * 0.4).padding(.vertical, fs * 0.12)
                    .background(dialOrange.opacity(isMF ? 0.28 : 0.1), in: Capsule())
                    .opacity(isMF ? 1 : 0.6)
            }
            .buttonStyle(.plain)
            .padding(.top, fs * 0.15)
            .opacity(expanded ? 1 : 0)
            .help(isMF ? "Back to \(lastAF)" : "Manual focus")
        }
        .foregroundStyle(dialOrange)
        .shadow(color: .black.opacity(0.35), radius: 4)
        .padding(.vertical, fs * 0.5)
        .padding(.leading, fs * 0.2)
        .padding(.trailing, fs * 0.1)
        .animation(.easeOut(duration: 0.2), value: isMF)
        .help("MF dial: drag up for far, down for near (faster = faster). Grabbing it switches the camera to MF. "
              + "Also [ ] keys ({ } fast) and the scroll wheel.")
    }

    func dial(width w: CGFloat) -> some View {
        let g = gap
        return ZStack(alignment: .leading) {
            Canvas { ctx, size in
                let first = Int(floor(-phase / g)) - 1
                var i = first
                while true {
                    let y = phase + CGFloat(i) * g
                    if y > size.height + g { break }
                    if y >= -g {
                        let major = i % 5 == 0
                        let len = major ? size.width * 0.7 : size.width * 0.45
                        var p = Path()
                        p.move(to: CGPoint(x: size.width - len, y: y)); p.addLine(to: CGPoint(x: size.width - 4, y: y))
                        ctx.stroke(p, with: .color(dialOrange.opacity(major ? 1 : 0.7)),
                                   style: StrokeStyle(lineWidth: major ? 2 : 1.3, lineCap: .round))
                    }
                    i += 1
                }
            }
            .mask(LinearGradient(stops: [.init(color: .clear, location: 0), .init(color: .black, location: 0.28),
                                         .init(color: .black, location: 0.72), .init(color: .clear, location: 1)],
                                 startPoint: .top, endPoint: .bottom))
            // fixed pointer
            Triangle()
                .fill(dialOrange)
                .frame(width: fs * 0.38, height: fs * 0.42)
                .offset(x: 1)
        }
        .frame(width: w, height: height)
        .scaleEffect(grabbed ? 1.03 : 1)
        .animation(.spring(response: 0.25, dampingFraction: 0.75), value: grabbed)
        .contentShape(Rectangle())
        .gesture(DragGesture(minimumDistance: 0)
            .onChanged { v in
                if !grabbed {
                    grabbed = true
                    if !isMF { camera.set("focus_mode", "MF") }
                }
                let y = v.location.y
                let dy = y - (lastY ?? y)
                lastY = y
                phase += dy
                pending += dy
                let major = Int(floor(phase / (g * 5)))
                if major != lastMajor {
                    lastMajor = major
                    NSHapticFeedbackManager.defaultPerformer.perform(.alignment, performanceTime: .now)
                }
                let dt = Date().timeIntervalSince(lastSend)
                if dt > 0.07, abs(pending) >= 1 {
                    let speed = abs(pending) / CGFloat(min(dt, 0.25))  // points per second
                    let step = (pending < 0 ? "far" : "near") + (speed > 220 ? "_fast" : "_slow")
                    camera.focus(step, ms: 160)
                    pending = 0
                    lastSend = Date()
                }
            }
            .onEnded { _ in
                grabbed = false
                lastY = nil
                pending = 0
                camera.focus("stop")
            })
    }
}

/// Pointer pointing right (into the dial).
struct Triangle: Shape {
    func path(in r: CGRect) -> Path {
        var p = Path()
        p.move(to: CGPoint(x: r.minX, y: r.minY))
        p.addLine(to: CGPoint(x: r.maxX, y: r.midY))
        p.addLine(to: CGPoint(x: r.minX, y: r.maxY))
        p.closeSubpath()
        return p
    }
}

// MARK: - Window

/// Frosted, see-through window like a frosted Ghostty window (background-opacity 0.88, background-blur 20,
/// Kanagawa Dragon #181616): behind-window blur + tinted fill; title bar blends into the content.
struct FrostedWindow: NSViewRepresentable {
    static let tint = NSColor(srgbRed: 0x18 / 255, green: 0x16 / 255, blue: 0x16 / 255, alpha: 0.80)

    func makeNSView(context: Context) -> NSView {
        let blur = NSVisualEffectView()
        blur.blendingMode = .behindWindow
        blur.material = .underWindowBackground
        blur.state = .active
        let tint = NSView()
        tint.wantsLayer = true
        tint.layer?.backgroundColor = Self.tint.cgColor
        tint.autoresizingMask = [.width, .height]
        blur.addSubview(tint)
        DispatchQueue.main.async {
            guard let w = blur.window else { return }
            w.isOpaque = false
            w.backgroundColor = .clear
            w.titlebarAppearsTransparent = true
            w.styleMask.insert(.fullSizeContentView)
            w.appearance = NSAppearance(named: .darkAqua)  // camera UI stays dark
        }
        return blur
    }

    func updateNSView(_ v: NSView, context: Context) {
        v.subviews.first?.frame = v.bounds
    }
}

extension View {
    @ViewBuilder func hiddenToolbarBackground() -> some View {
        if #available(macOS 15.0, *) { self.toolbarBackground(.hidden, for: .windowToolbar) } else { self }
    }
}

let settingOrder: [(String, String)] = [
    ("photo_style", "Photo Style"), ("lut1_opacity", "LUT opacity"), ("white_balance", "White balance"),
    ("kelvin", "Kelvin"), ("iso", "ISO"), ("exposure_comp", "Exposure comp."), ("aperture", "Aperture"),
    ("shutter", "Shutter"), ("focus_mode", "Focus mode"), ("color_noise", "Colour noise"), ("image_format", "Image format"),
]

struct ContentView: View {
    @StateObject var stream = LiveStream()
    @StateObject var camera = CameraModel()
    @ObservedObject var backend = Backend.shared
    @State var showPanel = false
    @State var showGrid = true
    @State var showLevel = true
    @State var showHistogram = false
    @AppStorage("peakLevel") var peakLevel = PeakLevel.off
    @AppStorage("peakColor") var peakColor = PeakColor.red
    @StateObject var hover = HoverMenu()
    @State var lastScroll = Date.distantPast
    @State var touchPoint: CGPoint?  // last touch AF point, 0..1 of the picture
    @State var touchActive = false

    var body: some View {
        GeometryReader { geo in
            let wide = geo.size.width >= 900 || geo.size.width > geo.size.height * 1.25
            Group {
                if showPanel && wide {
                    HStack(spacing: 0) { live; Divider(); panel.frame(width: min(280, max(220, geo.size.width * 0.22))) }
                } else if showPanel {
                    VStack(spacing: 0) { live; Divider(); panel.frame(height: min(320, geo.size.height * 0.42)) }
                } else {
                    live
                }
            }
        }
        .frame(minWidth: 560, minHeight: 440)
        .background(FrostedWindow().ignoresSafeArea())
        .onAppear {
            stream.onFrame = { obsFeed.send($0) }
            obsFeed.onStatus = { error, running in
                if let error {
                    UserDefaults.standard.set(false, forKey: "obsFeed")
                    VirtualCamera.shared.alert("OBS Virtual Camera", error)
                } else {
                    camera.show(running ? "Live view → OBS Virtual Camera" : "Stopped sending to OBS Virtual Camera", error: false)
                }
            }
            stream.connect()
            camera.startPolling()
            if UserDefaults.standard.bool(forKey: "obsFeed") { obsFeed.start() }
            stream.setPeaking(peakLevel, peakColor)
        }
        .background(KeyCatcher(onKey: key, onScroll: scroll))
        .toolbar {
            ToolbarItemGroup {
                Toggle(isOn: $showGrid) { Image(systemName: "grid") }.help("Grid (g)")
                Toggle(isOn: $showLevel) { Image(systemName: "level") }.help("Level (l)")
                Toggle(isOn: $showHistogram) { Image(systemName: "chart.bar") }.help("Histogram (h)")
                Menu {
                    Picker("Peaking", selection: $peakLevel) { ForEach(PeakLevel.allCases) { Text($0.rawValue).tag($0) } }
                        .pickerStyle(.inline)
                    Picker("Colour", selection: $peakColor) { ForEach(PeakColor.allCases) { Text($0.rawValue).tag($0) } }
                        .pickerStyle(.inline)
                } label: {
                    Image(systemName: peakLevel == .off ? "scope" : "scope").symbolVariant(peakLevel == .off ? .none : .fill)
                }
                .help("Focus peaking (p)")
                Toggle(isOn: $showPanel) { Image(systemName: "sidebar.right") }.help("Settings panel (s)")
            }
        }
        .onChange(of: peakLevel) { _, v in stream.setPeaking(v, peakColor) }
        .onChange(of: peakColor) { _, v in stream.setPeaking(peakLevel, v) }
        .navigationTitle(camera.state?.camera.map { "LumixOS — \($0.model)" } ?? "LumixOS")
        .hiddenToolbarBackground()
    }

    /// What's wrong right now, worst first; nil when the picture is live.
    var problem: (text: String, busy: Bool)? {
        if let p = backend.problem { return (p, p.hasSuffix("Restarting…")) }
        if let e = camera.serviceError { return (e, false) }
        guard let s = camera.state else { return ("Starting camera service…", true) }
        if s.phase == "error" { return (s.error ?? s.status, false) }
        if s.phase == "connecting" { return ("Connecting to the camera…", true) }
        if let t = stream.lastFrameAt, Date().timeIntervalSince(t) > 2 {
            return ("Live view stalled for \(Int(Date().timeIntervalSince(t))) s, reconnecting…", true)
        }
        if stream.image == nil { return ("Waiting for the first frame…", true) }
        return nil
    }

    var live: some View {
        VStack(spacing: 0) {
            GeometryReader { g in
                let aspect = stream.image.map { CGFloat($0.width) / CGFloat($0.height) } ?? 4 / 3
                let fit = g.size.width / g.size.height > aspect
                    ? CGSize(width: g.size.height * aspect, height: g.size.height)
                    : CGSize(width: g.size.width, height: g.size.width / aspect)
                ZStack {
                    Color.clear
                    ZStack {
                        if let img = stream.image {
                            Image(decorative: img, scale: 1).resizable().interpolation(.medium)
                                .frame(width: fit.width, height: fit.height)
                                .opacity(problem == nil ? 1 : 0.35)
                                .contentShape(Rectangle())
                                .onTapGesture(coordinateSpace: .local) { p in
                                    guard problem == nil else { return }
                                    hover.close()
                                    let n = CGPoint(x: p.x / fit.width, y: p.y / fit.height)
                                    if camera.state?.current["focus_mode"] == "MF" {
                                        // MF: click opens the magnified MF assist there, the next click closes it
                                        if camera.mfAssist { camera.mfAssistEnd() } else { camera.mfAssistStart(x: n.x * 1000, y: n.y * 1000) }
                                        return
                                    }
                                    camera.touch(x: n.x * 1000, y: n.y * 1000)
                                    withAnimation(.spring(response: 0.25, dampingFraction: 0.7)) { touchPoint = n; touchActive = true }
                                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.9) {
                                        if touchPoint == n { withAnimation(.easeOut(duration: 0.3)) { touchActive = false } }
                                    }
                                }
                        }
                        if let pk = stream.peakImage, peakLevel != .off, problem == nil {
                            Image(decorative: pk, scale: 1).resizable().interpolation(.none)
                                .frame(width: fit.width, height: fit.height)
                                .allowsHitTesting(false)
                        }
                        if camera.mfAssist {
                            VStack {
                                HStack(spacing: 6) {
                                    Image(systemName: "plus.magnifyingglass")
                                    Text("MF ASSIST").font(.system(size: 12, weight: .bold, design: .rounded))
                                    Text("click or Esc to close").font(.system(size: 11)).opacity(0.7)
                                }
                                .foregroundStyle(.white)
                                .padding(.horizontal, 12).padding(.vertical, 6)
                                .glass(Capsule())
                                .padding(.top, fit.height * 0.09)
                                Spacer()
                            }
                            .allowsHitTesting(false)
                            .transition(.opacity.combined(with: .scale(scale: 0.95)))
                        }
                        if let m = touchPoint, !camera.mfAssist, camera.state?.current["focus_mode"] != "MF" {
                            AFPointBox(active: touchActive, side: fit.width * 0.09)
                                .position(x: m.x * fit.width, y: m.y * fit.height)
                                .allowsHitTesting(false)
                        }
                        CameraHUD(camera: camera, hover: hover, size: fit, showGrid: showGrid, showLevel: showLevel, showHistogram: showHistogram)
                        if let p = problem { problemCard(p.text, busy: p.busy) }
                        if let n = camera.notice { noticeView(n, fs: max(11, min(18, fit.width / 55))) }
                    }
                    .frame(width: fit.width, height: fit.height)
                }
                .frame(width: g.size.width, height: g.size.height)
            }
            Text(statusLine)
                .font(.system(size: 10, design: .monospaced)).foregroundStyle(.white.opacity(0.6))
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 10).padding(.vertical, 4)
        }
    }

    func problemCard(_ text: String, busy: Bool) -> some View {
        VStack(spacing: 10) {
            if busy { ProgressView().controlSize(.small).tint(.white) }
            else { Image(systemName: "exclamationmark.triangle.fill").font(.title).foregroundStyle(panasonicYellow) }
            Text(text).multilineTextAlignment(.center).foregroundStyle(.white).font(.system(size: 13, weight: .medium))
                .textSelection(.enabled)
            if !busy {
                HStack {
                    Button("Retry") { backend.retry(); Task { await camera.refresh() } }.glassButtonStyle(prominent: true)
                    Button("Open log") { backend.openLog() }.glassButtonStyle()
                }
            }
        }
        .padding(20)
        .frame(maxWidth: 420)
        .glass(RoundedRectangle(cornerRadius: 22), tint: .black.opacity(0.35))
        .transition(.scale(scale: 0.94).combined(with: .opacity))
    }

    func noticeView(_ n: Notice, fs: CGFloat) -> some View {
        VStack {
            Spacer()
            HStack(spacing: 6) {
                if n.isError { Image(systemName: "xmark.octagon.fill").foregroundStyle(.red) }
                Text(n.text).foregroundStyle(.white).lineLimit(3)
            }
            .font(.system(size: fs, weight: .medium))
            .padding(.horizontal, 12).padding(.vertical, 7)
            .glass(Capsule(), tint: n.isError ? Color.red.opacity(0.45) : Color.black.opacity(0.3))
            .padding(.bottom, fs * 3)
            .transition(.move(edge: .bottom).combined(with: .opacity))
        }
        .allowsHitTesting(false)
        .animation(.easeOut(duration: 0.15), value: n)
    }

    var statusLine: String {
        guard let s = camera.state else { return "connecting…" }
        return String(format: "%.0f fps shown · camera %.0f fps · fetch %.0f ms · %@ · %@",
                      stream.displayFPS, s.fps, s.fetch_ms, s.size, s.status)
    }

    var panelHeader: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(camera.state?.camera?.model ?? "LUMIX").font(.title2.bold())
            Text(cameraSubtitle).font(.caption).foregroundStyle(.secondary)
        }
    }

    var cameraSubtitle: String {
        guard let c = camera.state?.camera else { return camera.state == nil ? "starting camera service…" : "no camera connected" }
        return "\(c.manufacturer) · firmware \(c.firmware) · USB"
    }

    var panel: some View {
        ScrollView {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 220), alignment: .topLeading)], alignment: .leading, spacing: 10) {
                panelHeader
                HStack {
                    Button("AF  (f)") { camera.post("af") }.glassButtonStyle()
                    Button("Shutter  (space)") { camera.post("shutter") }.glassButtonStyle(prominent: true)
                }
                if let s = camera.state {
                    ForEach(settingOrder, id: \.0) { key, label in
                        if let opts = s.options[key], !opts.isEmpty {
                            Picker(label, selection: Binding(
                                get: { s.current[key] ?? "" },
                                set: { camera.set(key, $0) })) {
                                ForEach(opts, id: \.self) { Text(Fmt.value(key, $0)).tag($0) }
                            }
                        }
                    }
                    Picker("Live view", selection: Binding(get: { s.size }, set: { camera.post("size", ["size": $0]) })) {
                        ForEach(["640x480", "960x720", "1280x960"], id: \.self) { Text($0).tag($0) }
                    }
                }
                Button("Undo all changes") { camera.post("undo") }.glassButtonStyle()
                Text("P/A/S/M and the active LUT are chosen on the camera.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            .padding(16)
        }
        .glass(RoundedRectangle(cornerRadius: 20))
        .padding(8)
    }

    /// Scroll wheel over the window drives MF (not while a HUD menu is open: it scrolls the list then).
    func scroll(_ dy: CGFloat) -> Bool {
        guard hover.open == nil, camera.state?.current["focus_mode"] == "MF", abs(dy) > 0.5 else { return false }
        if Date().timeIntervalSince(lastScroll) > 0.08 {
            lastScroll = Date()
            camera.focus(dy > 0 ? "far_slow" : "near_slow", ms: min(400, 60 + Int(abs(dy) * 15)))
        }
        return true
    }

    func key(_ c: String) {
        switch c.lowercased() {
        case "f": camera.post("af")
        case " ": camera.post("shutter")
        case "g": showGrid.toggle()
        case "l": showLevel.toggle()
        case "h": showHistogram.toggle()
        case "p": peakLevel = peakLevel == .off ? .mid : .off
        case "\u{1b}": camera.mfAssistEnd()
        case "s": showPanel.toggle()
        case "[": camera.focus("near_slow")
        case "]": camera.focus("far_slow")
        case "{": camera.focus("near_fast", ms: 200)
        case "}": camera.focus("far_fast", ms: 200)
        default: break
        }
    }
}

struct KeyCatcher: NSViewRepresentable {
    let onKey: (String) -> Void
    var onScroll: ((CGFloat) -> Bool)? = nil
    func makeNSView(context: Context) -> NSView {
        let v = KeyView()
        v.onKey = onKey
        v.onScroll = onScroll
        v.monitor = NSEvent.addLocalMonitorForEvents(matching: .scrollWheel) { [weak v] e in
            guard let v, e.window === v.window, let f = v.onScroll else { return e }
            return f(e.scrollingDeltaY) ? nil : e
        }
        DispatchQueue.main.async { v.window?.makeFirstResponder(v) }
        return v
    }
    func updateNSView(_ v: NSView, context: Context) {
        (v as? KeyView)?.onKey = onKey
        (v as? KeyView)?.onScroll = onScroll
    }
    static func dismantleNSView(_ v: NSView, coordinator: ()) {
        if let m = (v as? KeyView)?.monitor { NSEvent.removeMonitor(m) }
    }
    final class KeyView: NSView {
        var onKey: ((String) -> Void)?
        var onScroll: ((CGFloat) -> Bool)?
        var monitor: Any?
        override var acceptsFirstResponder: Bool { true }
        override func keyDown(with e: NSEvent) {
            if e.modifierFlags.intersection([.command, .control, .option]).isEmpty { onKey?(e.charactersIgnoringModifiers ?? "") }
            else { super.keyDown(with: e) }
        }
    }
}

@main
struct LumixOSApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate
    var body: some Scene {
        WindowGroup("LumixOS") { ContentView() }
            .windowResizability(.contentMinSize)
            .defaultSize(width: 1100, height: 860)
            .commands {
                CommandMenu("Camera") {
                    Button("Send Live View to OBS Virtual Camera") { setOBSFeed(true) }
                    Button("Stop Sending to OBS Virtual Camera") { setOBSFeed(false) }
                    Divider()
                    Button("Install LumixOS Camera (needs Developer team)") { VirtualCamera.shared.install() }
                    Button("Remove LumixOS Camera") { VirtualCamera.shared.remove() }
                    Divider()
                    Button("Copy Stream URL (OBS Media Source)") {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString("http://127.0.0.1:\(lumixPort)/stream", forType: .string)
                    }
                    Button("Open Service Log") { Backend.shared.openLog() }
                }
            }
    }
}
