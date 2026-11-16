import 'dart:async';
import 'dart:io';
import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart' show rootBundle;
import 'package:path_provider/path_provider.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:record/record.dart';
import 'package:sherpa_onnx/sherpa_onnx.dart' as sherpa;
import 'package:flutter_foreground_task/flutter_foreground_task.dart';

@pragma('vm:entry-point')
void startForegroundCallback() {
  FlutterForegroundTask.setTaskHandler(WakeWordTaskHandler());
}

class WakeWordTaskHandler extends TaskHandler {
  @override
  Future<void> onStart(DateTime timestamp, TaskStarter starter) async {
    debugPrint('[WakeWordForegroundTask] Background service started: $timestamp');
  }

  @override
  void onRepeatEvent(DateTime timestamp) {}

  @override
  Future<void> onDestroy(DateTime timestamp, bool isTimeout) async {
    debugPrint('[WakeWordForegroundTask] Background service stopped: $timestamp');
  }
}

typedef OnWakeWordDetected = void Function(String keyword);

class WakeWordService {
  static final WakeWordService _instance = WakeWordService._internal();
  factory WakeWordService() => _instance;
  WakeWordService._internal();

  AudioRecorder? _kwsRecorder;
  StreamSubscription<Uint8List>? _recordStreamSub;

  sherpa.KeywordSpotter? _spotter;
  sherpa.OnlineStream? _onlineStream;

  bool _isInitialized = false;
  bool _isListening = false;
  OnWakeWordDetected? _onDetected;

  final ValueNotifier<bool> isListeningNotifier = ValueNotifier<bool>(false);

  bool get isListening => _isListening;
  bool get isInitialized => _isInitialized;

  /// مقداردهی اولیه مدل سبک Sherpa-ONNX و آماده‌سازی فایل‌های محلی
  Future<bool> initialize() async {
    if (_isInitialized) return true;

    try {
      sherpa.initBindings();

      final encoderPath = await _copyAssetToLocal('assets/kws/encoder.int8.onnx', 'encoder.int8.onnx');
      final decoderPath = await _copyAssetToLocal('assets/kws/decoder.int8.onnx', 'decoder.int8.onnx');
      final joinerPath = await _copyAssetToLocal('assets/kws/joiner.int8.onnx', 'joiner.int8.onnx');
      final tokensPath = await _copyAssetToLocal('assets/kws/tokens.txt', 'tokens.txt');
      final keywordsPath = await _copyAssetToLocal('assets/kws/keywords.txt', 'keywords.txt');

      final config = sherpa.KeywordSpotterConfig(
        feat: const sherpa.FeatureConfig(sampleRate: 16000, featureDim: 80),
        model: sherpa.OnlineModelConfig(
          transducer: sherpa.OnlineTransducerModelConfig(
            encoder: encoderPath,
            decoder: decoderPath,
            joiner: joinerPath,
          ),
          tokens: tokensPath,
          numThreads: 1,
          provider: 'cpu',
          debug: false,
        ),
        keywordsFile: keywordsPath,
        keywordsScore: 1.0,
        keywordsThreshold: 0.25,
        numTrailingBlanks: 1,
      );

      _spotter = sherpa.KeywordSpotter(config);
      _initForegroundTask();
      _isInitialized = true;
      debugPrint('[WakeWordService] Sherpa-ONNX KWS initialized successfully.');
      return true;
    } catch (e) {
      debugPrint('[WakeWordService] Failed to initialize Sherpa-ONNX KWS: $e');
      return false;
    }
  }

  void _initForegroundTask() {
    FlutterForegroundTask.init(
      androidNotificationOptions: AndroidNotificationOptions(
        channelId: 'ava_wake_service',
        channelName: 'سرویس بیدارباش آوا',
        channelDescription: 'شنود آفلاین کلمه بیدارباش هی آوا در پس‌زمینه',
        channelImportance: NotificationChannelImportance.LOW,
        priority: NotificationPriority.LOW,
      ),
      iosNotificationOptions: const IOSNotificationOptions(),
      foregroundTaskOptions: ForegroundTaskOptions(
        eventAction: ForegroundTaskEventAction.repeat(5000),
        autoRunOnBoot: false,
        autoRunOnMyPackageReplaced: false,
        allowWakeLock: true,
        allowWifiLock: true,
      ),
    );
  }

  static final RegExp _wakeWordRegex = RegExp(
    r'(^|[\s\u200c])(هی[\s\u200c]*آوا|هی[\s\u200c]*اوا|هیاوا|ای[\s\u200c]*آوا|ای[\s\u200c]*اوا|های[\s\u200c]*آوا|های[\s\u200c]*اوا|هی[\s\u200c]*هوا|هوا|اووا|آبا|آوا|اوا|hey\s*ava|heyava|ava)([\s\u200c.,!؟]|$)',
    caseSensitive: false,
  );

  /// بررسی متنی وجود کلمه بیدارباش در متن (تست و پشتیبانی)
  static bool containsWakeWord(String text) {
    if (text.trim().isEmpty) return false;
    return _wakeWordRegex.hasMatch(text.trim());
  }

  Future<String> _copyAssetToLocal(String assetPath, String fileName) async {
    final docDir = await getApplicationDocumentsDirectory();
    final kwsDir = Directory('${docDir.path}/kws');
    if (!kwsDir.existsSync()) {
      kwsDir.createSync(recursive: true);
    }
    final file = File('${kwsDir.path}/$fileName');
    if (!file.existsSync() || file.lengthSync() == 0) {
      final byteData = await rootBundle.load(assetPath);
      await file.writeAsBytes(byteData.buffer.asUint8List(
        byteData.offsetInBytes,
        byteData.lengthInBytes,
      ));
    }
    return file.path;
  }

  /// درخواست مجوزهای لازم (میکروفون، نوتیفیکیشن، بهینه‌سازی باتری)
  Future<bool> requestPermissions() async {
    final micStatus = await Permission.microphone.request();
    if (!micStatus.isGranted) return false;

    if (Platform.isAndroid) {
      final notifStatus = await Permission.notification.request();
      if (!notifStatus.isGranted) {
        debugPrint('[WakeWordService] Notification permission not granted');
      }
      try {
        await FlutterForegroundTask.requestIgnoreBatteryOptimization();
      } catch (_) {}
    }
    return true;
  }

  /// شروع شنود آفلاین برای کلمه بیدارباش «هی آوا / Hey Ava»
  Future<void> startListening({required OnWakeWordDetected onDetected}) async {
    if (_isListening) return;

    if (!_isInitialized) {
      final ok = await initialize();
      if (!ok) return;
    }

    final hasPerm = await requestPermissions();
    if (!hasPerm) {
      debugPrint('[WakeWordService] Microphone permission is required.');
      return;
    }

    _onDetected = onDetected;

    try {
      // اجرای سرویس Foreground اندروید برای تضمین بیدار ماندن در پس‌زمینه و حالت خاموش بودن صفحه
      if (Platform.isAndroid && !await FlutterForegroundTask.isRunningService) {
        await FlutterForegroundTask.startService(
          serviceId: 256,
          serviceTypes: [ForegroundServiceTypes.microphone],
          notificationTitle: 'AVA 8 · دستیار هوشمند آوا',
          notificationText: 'در حال شنود کلمه بیدارباش «هی آوا»...',
          callback: startForegroundCallback,
        );
      }

      await _stopAudioStream();

      _kwsRecorder = AudioRecorder();
      final stream = await _kwsRecorder!.startStream(
        const RecordConfig(
          encoder: AudioEncoder.pcm16bits,
          sampleRate: 16000,
          numChannels: 1,
        ),
      );

      _onlineStream = _spotter?.createStream();
      _isListening = true;
      isListeningNotifier.value = true;

      _recordStreamSub = stream.listen(_processAudioChunk);
      debugPrint('[WakeWordService] KWS listening active.');
    } catch (e) {
      debugPrint('[WakeWordService] Error starting KWS listening: $e');
      await stopListening();
    }
  }

  void _processAudioChunk(Uint8List data) {
    if (!_isListening || _spotter == null || _onlineStream == null) return;

    try {
      final sampleCount = data.length ~/ 2;
      if (sampleCount == 0) return;

      final byteData = ByteData.sublistView(data);
      final floats = Float32List(sampleCount);
      for (int i = 0; i < sampleCount; i++) {
        floats[i] = byteData.getInt16(i * 2, Endian.little) / 32768.0;
      }

      _onlineStream!.acceptWaveform(samples: floats, sampleRate: 16000);

      while (_spotter!.isReady(_onlineStream!)) {
        _spotter!.decode(_onlineStream!);
      }

      final result = _spotter!.getResult(_onlineStream!);
      if (result.keyword.isNotEmpty) {
        final detectedKw = result.keyword;
        debugPrint('[WakeWordService] *** WAKE WORD DETECTED: $detectedKw ***');

        // توقف فوری استریم شنود برای آزادسازی میکروفون برای ضبط فرمان اصلی
        unawaited(_onWakeWordTriggered(detectedKw));
      }
    } catch (e) {
      debugPrint('[WakeWordService] Error processing KWS audio chunk: $e');
    }
  }

  Future<void> _onWakeWordTriggered(String keyword) async {
    // استریم KWS را متوقف و آزاد می‌کنیم تا میکروفون فوراً برای ضبط باز شود
    await pauseListening();
    _onDetected?.call(keyword);
  }

  /// توقف موقت شنود (مثلاً هنگام ضبط فرمان اصلی یا پخش صوت)
  Future<void> pauseListening() async {
    _isListening = false;
    isListeningNotifier.value = false;
    await _stopAudioStream();
  }

  Future<void> _stopAudioStream() async {
    try {
      await _recordStreamSub?.cancel();
      _recordStreamSub = null;

      if (_onlineStream != null) {
        _onlineStream!.free();
        _onlineStream = null;
      }

      if (_kwsRecorder != null) {
        if (await _kwsRecorder!.isRecording()) {
          await _kwsRecorder!.stop();
        }
        await _kwsRecorder!.dispose();
        _kwsRecorder = null;
      }
    } catch (e) {
      debugPrint('[WakeWordService] Error stopping audio stream: $e');
    }
  }

  /// توقف کامل شنود و بستن سرویس پس‌زمینه
  Future<void> stopListening() async {
    _isListening = false;
    isListeningNotifier.value = false;
    await _stopAudioStream();

    if (Platform.isAndroid && await FlutterForegroundTask.isRunningService) {
      try {
        await FlutterForegroundTask.stopService();
      } catch (_) {}
    }
    debugPrint('[WakeWordService] KWS listening stopped completely.');
  }

  void dispose() {
    stopListening();
    try {
      _spotter?.free();
      _spotter = null;
    } catch (_) {}
    _isInitialized = false;
  }
}
