import AVFoundation
import Foundation

final class MeetingCaptureRecorder {
    private let audioSession = AVAudioSession.sharedInstance()
    private let conversionQueue = DispatchQueue(label: "com.siqresearch.meeting-capture.convert", qos: .userInitiated)
    private var engine = AVAudioEngine()
    private var converter: AVAudioConverter?
    private var targetFormat: AVAudioFormat?
    private var tapInstalled = false
    private var interruptionStartedNs: UInt64?
    private var suppressConfigurationUntilNs: UInt64 = 0

    var onPCM: ((Data, UInt64) -> Void)?
    var onInterrupted: ((String, UInt64) -> Void)?
    var onInterruptionEnded: ((UInt64, Bool) -> Void)?
    var onConfigurationChanged: ((String) -> Void)?
    var onError: ((MeetingCaptureError) -> Void)?

    init() {
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(handleInterruption(_:)),
            name: AVAudioSession.interruptionNotification,
            object: audioSession
        )
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(handleRouteChange(_:)),
            name: AVAudioSession.routeChangeNotification,
            object: audioSession
        )
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(handleMediaServicesReset(_:)),
            name: AVAudioSession.mediaServicesWereResetNotification,
            object: audioSession
        )
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(handleEngineConfigurationChange(_:)),
            name: .AVAudioEngineConfigurationChange,
            object: engine
        )
    }

    deinit {
        NotificationCenter.default.removeObserver(self)
    }

    func requestPermission(completion: @escaping (Result<Void, MeetingCaptureError>) -> Void) {
        switch audioSession.recordPermission {
        case .granted:
            completion(.success(Void()))
        case .denied:
            completion(.failure(.microphoneDenied))
        case .undetermined:
            audioSession.requestRecordPermission { granted in
                DispatchQueue.main.async {
                    completion(granted ? .success(Void()) : .failure(.microphoneDenied))
                }
            }
        @unknown default:
            completion(.failure(.microphoneDenied))
        }
    }

    func start() throws {
        try configureSession()
        try installInputTap()
        engine.prepare()
        try engine.start()
    }

    func pauseAndDrain(completion: @escaping () -> Void) {
        engine.pause()
        conversionQueue.async(execute: completion)
    }

    func resume() throws {
        try audioSession.setActive(true)
        if !tapInstalled { try installInputTap() }
        engine.prepare()
        try engine.start()
    }

    func stop() {
        stopInput()
        converter = nil
        targetFormat = nil
    }

    private func stopInput() {
        if tapInstalled {
            engine.inputNode.removeTap(onBus: 0)
            tapInstalled = false
        }
        engine.stop()
        try? audioSession.setActive(false, options: [.notifyOthersOnDeactivation])
    }

    func stopForReconfiguration() {
        suppressConfigurationUntilNs = DispatchTime.now().uptimeNanoseconds + 1_000_000_000
        stopInput()
    }

    func stopForReconfigurationAndDrain(completion: @escaping () -> Void) {
        stopForReconfiguration()
        conversionQueue.async { [weak self] in
            self?.converter = nil
            self?.targetFormat = nil
            completion()
        }
    }

    func stopAndDrain(completion: @escaping () -> Void) {
        stopInput()
        conversionQueue.async { [weak self] in
            self?.converter = nil
            self?.targetFormat = nil
            completion()
        }
    }

    private func configureSession() throws {
        try audioSession.setCategory(
            .playAndRecord,
            mode: .spokenAudio,
            options: [.allowBluetooth, .defaultToSpeaker]
        )
        try audioSession.setPreferredSampleRate(Double(meetingCaptureSampleRate))
        try audioSession.setPreferredIOBufferDuration(0.02)
        try audioSession.setPreferredInputNumberOfChannels(1)
        try audioSession.setActive(true)
    }

    private func installInputTap() throws {
        let input = engine.inputNode
        let sourceFormat = input.outputFormat(forBus: 0)
        guard sourceFormat.sampleRate > 0,
              let target = AVAudioFormat(
                commonFormat: .pcmFormatInt16,
                sampleRate: Double(meetingCaptureSampleRate),
                channels: 1,
                interleaved: true
              ),
              let converter = AVAudioConverter(from: sourceFormat, to: target) else {
            throw MeetingCaptureError.invalidState("unsupported audio format")
        }
        self.converter = converter
        targetFormat = target
        if tapInstalled { input.removeTap(onBus: 0) }
        input.installTap(onBus: 0, bufferSize: 4_096, format: sourceFormat) { [weak self] buffer, _ in
            let capturedNs = DispatchTime.now().uptimeNanoseconds
            guard let copy = Self.copy(buffer: buffer) else {
                self?.onError?(.storageUnavailable)
                return
            }
            self?.conversionQueue.async {
                self?.convert(buffer: copy, capturedMonotonicNs: capturedNs)
            }
        }
        tapInstalled = true
    }

    private static func copy(buffer: AVAudioPCMBuffer) -> AVAudioPCMBuffer? {
        guard let copy = AVAudioPCMBuffer(pcmFormat: buffer.format, frameCapacity: buffer.frameLength) else {
            return nil
        }
        copy.frameLength = buffer.frameLength
        let source = UnsafeMutableAudioBufferListPointer(buffer.mutableAudioBufferList)
        let destination = UnsafeMutableAudioBufferListPointer(copy.mutableAudioBufferList)
        for index in 0..<min(source.count, destination.count) {
            guard let sourceData = source[index].mData, let destinationData = destination[index].mData else { continue }
            let byteCount = Int(source[index].mDataByteSize)
            memcpy(destinationData, sourceData, byteCount)
            destination[index].mDataByteSize = source[index].mDataByteSize
        }
        return copy
    }

    private func convert(buffer: AVAudioPCMBuffer, capturedMonotonicNs: UInt64) {
        guard let converter, let targetFormat else { return }
        let ratio = targetFormat.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(max(1, ceil(Double(buffer.frameLength) * ratio) + 32))
        guard let output = AVAudioPCMBuffer(pcmFormat: targetFormat, frameCapacity: capacity) else {
            onError?(.storageUnavailable)
            return
        }
        var supplied = false
        var conversionError: NSError?
        let status = converter.convert(to: output, error: &conversionError) { _, inputStatus in
            if supplied {
                inputStatus.pointee = .noDataNow
                return nil
            }
            supplied = true
            inputStatus.pointee = .haveData
            return buffer
        }
        guard conversionError == nil,
              status != .error,
              output.frameLength > 0,
              let samples = output.int16ChannelData?.pointee else {
            onError?(.invalidState("audio conversion failed"))
            return
        }
        let byteCount = Int(output.frameLength) * MemoryLayout<Int16>.size
        onPCM?(Data(bytes: samples, count: byteCount), capturedMonotonicNs)
    }

    @objc private func handleInterruption(_ notification: Notification) {
        guard let rawType = notification.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
              let type = AVAudioSession.InterruptionType(rawValue: rawType) else { return }
        switch type {
        case .began:
            let started = DispatchTime.now().uptimeNanoseconds
            interruptionStartedNs = started
            engine.pause()
            conversionQueue.async { [weak self] in
                self?.onInterrupted?("audio_session_interruption", started)
            }
        case .ended:
            let now = DispatchTime.now().uptimeNanoseconds
            let duration = now - (interruptionStartedNs ?? now)
            interruptionStartedNs = nil
            let rawOptions = notification.userInfo?[AVAudioSessionInterruptionOptionKey] as? UInt ?? 0
            let shouldResume = AVAudioSession.InterruptionOptions(rawValue: rawOptions).contains(.shouldResume)
            conversionQueue.async { [weak self] in
                self?.onInterruptionEnded?(duration, shouldResume)
            }
        @unknown default:
            break
        }
    }

    @objc private func handleRouteChange(_ notification: Notification) {
        guard DispatchTime.now().uptimeNanoseconds >= suppressConfigurationUntilNs else { return }
        let rawReason = notification.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt ?? 0
        let reason = AVAudioSession.RouteChangeReason(rawValue: rawReason)?.rawValue ?? 0
        onConfigurationChanged?("route_change_\(reason)")
    }

    @objc private func handleMediaServicesReset(_ notification: Notification) {
        stopForReconfiguration()
        NotificationCenter.default.removeObserver(
            self,
            name: .AVAudioEngineConfigurationChange,
            object: engine
        )
        engine = AVAudioEngine()
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(handleEngineConfigurationChange(_:)),
            name: .AVAudioEngineConfigurationChange,
            object: engine
        )
        tapInstalled = false
        onConfigurationChanged?("media_services_reset")
    }

    @objc private func handleEngineConfigurationChange(_ notification: Notification) {
        guard DispatchTime.now().uptimeNanoseconds >= suppressConfigurationUntilNs else { return }
        onConfigurationChanged?("engine_configuration_change")
    }
}
