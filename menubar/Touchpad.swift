// Touchpad: a small menu bar icon for the iPhone Touchpad server.
//
// It shows whether a phone is connected, lets you copy the PIN and the address, and opens the
// QR page. It never moves the mouse itself: it only reads http://127.0.0.1:PORT/status from the
// local server, so it needs no special macOS permissions.
//
// Build and install: ./install-menubar.sh      Debug: Touchpad --dump
import Cocoa

let port = ProcessInfo.processInfo.environment["TOUCHPAD_PORT"] ?? "8899"
let base = "http://127.0.0.1:\(port)"
let isRussian = (Locale.preferredLanguages.first ?? "en").hasPrefix("ru")

func tr(_ en: String, _ ru: String) -> String { isRussian ? ru : en }

struct ServerStatus: Decodable {
    let pin: String
    let port: Int
    let host: String
    let urls: [String]
    let clients: Int
    let trusted: Bool
}

func fetchStatus(_ done: @escaping (ServerStatus?) -> Void) {
    var request = URLRequest(url: URL(string: base + "/status")!)
    request.timeoutInterval = 1.0
    request.cachePolicy = .reloadIgnoringLocalCacheData
    URLSession.shared.dataTask(with: request) { data, response, _ in
        var result: ServerStatus?
        if let data = data, (response as? HTTPURLResponse)?.statusCode == 200 {
            result = try? JSONDecoder().decode(ServerStatus.self, from: data)
        }
        DispatchQueue.main.async { done(result) }
    }.resume()
}

func shell(_ path: String, _ args: [String]) -> String {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: path)
    process.arguments = args
    let pipe = Pipe()
    process.standardOutput = pipe
    process.standardError = Pipe()
    do { try process.run() } catch { return "" }
    process.waitUntilExit()
    return String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
}

/// The launchd label of the touchpad server, if it runs under autostart.
func serverLabel() -> String? {
    for line in shell("/bin/launchctl", ["list"]).split(separator: "\n") {
        guard let label = line.split(separator: "\t").last.map(String.init) else { continue }
        if label.contains("iphone-touchpad") && !label.contains("menubar") { return label }
    }
    return nil
}

/// Template icon (macOS tints it for light and dark menu bars): cursor plus a halftone trail.
func makeIcon() -> NSImage {
    let image = NSImage(size: NSSize(width: 20, height: 18), flipped: true) { _ in
        let arrow: [(CGFloat, CGFloat)] = [(0, 0), (0, 16.5), (4.2, 12.6), (7, 19.5), (9.9, 18.3), (7.1, 11.7), (12.6, 11.6)]
        let scale: CGFloat = 0.55, ox: CGFloat = 2.5, oy: CGFloat = 1.5
        let path = NSBezierPath()
        for (i, p) in arrow.enumerated() {
            let point = NSPoint(x: ox + p.0 * scale, y: oy + p.1 * scale)
            if i == 0 { path.move(to: point) } else { path.line(to: point) }
        }
        path.close()
        NSColor.black.setFill()
        path.fill()
        let dots: [(CGFloat, CGFloat, CGFloat)] = [(10.9, 8.8, 1.5), (13.7, 10.6, 1.2), (16.2, 12.8, 0.9), (12.2, 13.4, 0.9), (15.0, 15.6, 0.6)]
        for d in dots {
            NSBezierPath(ovalIn: NSRect(x: d.0 - d.2, y: d.1 - d.2, width: d.2 * 2, height: d.2 * 2)).fill()
        }
        return true
    }
    image.isTemplate = true
    return image
}

struct Entry {
    var title: String
    var action: Selector? = nil
    var payload: String? = nil
    var enabled = true
    var separator = false
}

func entries(for status: ServerStatus?) -> [Entry] {
    let sep = Entry(title: "", separator: true)
    guard let s = status else {
        var list = [Entry(title: tr("Server is not running", "Сервер не запущен"), enabled: false), sep]
        if serverLabel() != nil {
            list.append(Entry(title: tr("Start server", "Запустить сервер"), action: #selector(AppDelegate.restartServer)))
        } else {
            list.append(Entry(title: tr("Run start.command or install-autostart.sh", "Запусти start.command или install-autostart.sh"), enabled: false))
        }
        list += [sep, Entry(title: tr("Hide icon", "Скрыть значок"), action: #selector(AppDelegate.quit))]
        return list
    }
    var list: [Entry] = []
    let state = s.clients > 0
        ? "●  " + tr("iPhone connected", "iPhone подключён")
        : "○  " + tr("Waiting for iPhone", "Ждёт iPhone")
    list.append(Entry(title: state, enabled: false))
    list.append(sep)
    list.append(Entry(title: "PIN   \(s.pin)", action: #selector(AppDelegate.copyText), payload: s.pin))
    list.append(Entry(title: "\(s.host):\(s.port)", action: #selector(AppDelegate.copyText), payload: "http://\(s.host):\(s.port)/"))
    if let url = s.urls.first {
        list.append(Entry(title: url.replacingOccurrences(of: "http://", with: "").replacingOccurrences(of: "/", with: ""),
                          action: #selector(AppDelegate.copyText), payload: url))
    }
    list.append(Entry(title: tr("Show QR code", "Показать QR-код"), action: #selector(AppDelegate.openQR)))
    if !s.trusted {
        list.append(sep)
        list.append(Entry(title: tr("⚠︎ Allow Accessibility access…", "⚠︎ Разрешить Универсальный доступ…"), action: #selector(AppDelegate.openAccessibility)))
    }
    list.append(sep)
    if serverLabel() != nil {
        list.append(Entry(title: tr("Restart server", "Перезапустить сервер"), action: #selector(AppDelegate.restartServer)))
    }
    list.append(Entry(title: tr("Hide icon (the server keeps running)", "Скрыть значок (сервер продолжит работать)"), action: #selector(AppDelegate.quit)))
    return list
}

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    let menu = NSMenu()
    var status: ServerStatus?
    var timer: Timer?

    func applicationDidFinishLaunching(_ notification: Notification) {
        item.button?.image = makeIcon()
        item.button?.toolTip = tr("iPhone Touchpad", "Тачпад для iPhone")
        menu.delegate = self
        item.menu = menu
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 3, repeats: true) { [weak self] _ in self?.refresh() }
    }

    func refresh() {
        fetchStatus { [weak self] s in
            self?.status = s
            self?.item.button?.appearsDisabled = (s == nil)
        }
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        menu.removeAllItems()
        for e in entries(for: status) {
            if e.separator { menu.addItem(.separator()); continue }
            let mi = NSMenuItem(title: e.title, action: e.action, keyEquivalent: "")
            mi.target = self
            mi.isEnabled = e.enabled
            mi.representedObject = e.payload
            if e.title.hasPrefix("PIN") {
                mi.attributedTitle = NSAttributedString(string: e.title, attributes: [.font: NSFont.monospacedSystemFont(ofSize: 14, weight: .semibold)])
            }
            menu.addItem(mi)
        }
        refresh()
    }

    @objc func copyText(_ sender: NSMenuItem) {
        guard let text = sender.representedObject as? String else { return }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
        item.button?.title = tr(" Copied", " Скопировано")
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { [weak self] in self?.item.button?.title = "" }
    }

    @objc func openQR() { NSWorkspace.shared.open(URL(string: base + "/qr")!) }

    @objc func openAccessibility() {
        NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")!)
    }

    @objc func restartServer() {
        guard let label = serverLabel() else { return }
        _ = shell("/bin/launchctl", ["kickstart", "-k", "gui/\(getuid())/\(label)"])
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in self?.refresh() }
    }

    @objc func quit() { NSApp.terminate(nil) }
}

if CommandLine.arguments.contains("--dump") {
    // Debug: print the menu as text (the PIN is masked) and exit.
    var done = false
    var result: ServerStatus?
    fetchStatus { result = $0; done = true }
    while !done { RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.05)) }
    for e in entries(for: result) {
        print(e.separator ? "---" : (e.enabled ? "[ ] " : "    ") + (e.title.hasPrefix("PIN") ? "PIN   ******" : e.title))
    }
    exit(0)
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let delegate = AppDelegate()
app.delegate = delegate
app.run()
