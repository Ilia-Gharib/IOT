import 'dart:async';
import 'dart:io';
import 'dart:math' as math;
import 'package:flutter/foundation.dart';
import 'package:path_provider/path_provider.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:record/record.dart';

typedef OnSpeechDetected = void Function();
typedef OnRecordingFinished = void Function(File audioFile);

enum AudioServiceMode {
  inactive,
  manual,
  handsFree,
}

/// سرویس یکپارچه و ضدکرش مدیریت میکروفون، کالیبراسیون پویای نویز و تشخیص سکوت ۱.۵ ثانیه‌ای
class UnifiedAudioService {
  static final UnifiedAudioService _instance = UnifiedAudioService._internal();
  factory UnifiedAudioService() => _instance;
  UnifiedAudioService._internal();

  /// نمونه موقت AudioRecorder که برای هر نشست ضبط از صفر ساخته و تخریب می‌شود
  AudioRecorder? _recorder;

  AudioServiceMode _mode = AudioServiceMode.inactive;
  bool _isSpeaking = false;
  String? _currentFilePath;

  StreamSubscription<Amplitude>? _amplitudeSub;
  Timer? _silenceTimer;
  Timer? _maxDurationTimer;
  Timer? _idleCleanupTimer;

  OnSpeechDetected? _onSpeechDetected;
  OnRecordingFinished? _onRecordingFinished;

  /// متغیرهای کالیبراسیون پویای نویز محیط در ۳۰۰ میلی‌ثانیه اول
  DateTime? _recordingStartTime;
  final List<double> _calibrationSamples = [];
  bool _isCalibrated = false;
  double voiceThresholdDb = -30.0;

  /// مدت زمان سکوت ممتد برای پایان صحبت و ارسال فایل (۱.۵ ثانیه)
  static const Duration silenceDuration = Duration(milliseconds: 1500);

  /// حداکثر مدت مجاز ضبط یک فرمان صوتی (سقف ایمنی ۸ ثانیه‌ای)
  static const Duration maxDuration = Duration(seconds: 8);

  bool get isRecording => _mode != AudioServiceMode.inactive;
  bool get isSpeaking => _isSpeaking;
  AudioServiceMode get mode => _mode;
  double get dynamicThresholdDb => voiceThresholdDb;

  Future<bool> checkAndRequestPermission() async {
    final status = await Permission.microphone.status;
    if (status.isGranted) return true;
    final res = await Permission.microphone.request();
    return res.isGranted;
  }

  /// آغاز ضبط دستی (با فعال بودن تشخیص خودکار ۱.۵ ثانیه سکوت)
  Future<void> startManualRecording({
    required OnRecordingFinished onFinished,
  }) async {
    await stop(discard: true);

    final hasPerm = await checkAndRequestPermission();
    if (!hasPerm) {
      throw Exception('مجوز استفاده از میکروفون صادر نشده است.');
    }

    _mode = AudioServiceMode.manual;
    _onRecordingFinished = onFinished;
    _isSpeaking = true; // در حالت دستی کاربر بلافاصله شروع به صحبت می‌کند

    await _startNativeRecording();
  }

  /// آغاز شنود هوشمند در حالت Hands-Free
  Future<void> startHandsFreeListening({
    required OnSpeechDetected onSpeechDetected,
    required OnRecordingFinished onFinished,
  }) async {
    if (_mode == AudioServiceMode.handsFree) return;
    await stop(discard: true);

    final hasPerm = await checkAndRequestPermission();
    if (!hasPerm) {
      debugPrint('[UnifiedAudioService] مجوز میکروفون صادر نشده است.');
      return;
    }

    _mode = AudioServiceMode.handsFree;
    _onSpeechDetected = onSpeechDetected;
    _onRecordingFinished = onFinished;
    _isSpeaking = false;

    await _startNativeRecording();
  }

  /// پاکسازی کامل و ساخت یک نمونه کاملاً تازه از AudioRecorder
  Future<void> _cleanupRecorder() async {
    try {
      await _amplitudeSub?.cancel();
      _amplitudeSub = null;

      if (_recorder != null) {
        if (await _recorder!.isRecording()) {
          try {
            await _recorder!.stop();
          } catch (_) {}
        }
        try {
          await _recorder!.dispose();
        } catch (_) {}
        _recorder = null;
      }
    } catch (e) {
      debugPrint('[UnifiedAudioService] Warning during recorder cleanup: $e');
    }
  }

  Future<void> _startNativeRecording() async {
    try {
      // ریشه‌کن کردن قطعی خطای "Stream has already been listened to" با تخریب و بازسازی کامل
      await _cleanupRecorder();
      _recorder = AudioRecorder();

      final tempDir = await getTemporaryDirectory();
      final fileName = 'cmd_${DateTime.now().millisecondsSinceEpoch}.m4a';
      _currentFilePath = '${tempDir.path}/$fileName';

      _recordingStartTime = DateTime.now();
      _calibrationSamples.clear();
      _isCalibrated = false;
      voiceThresholdDb = -30.0;

      await _recorder!.start(
        const RecordConfig(
          encoder: AudioEncoder.aacLc,
          bitRate: 128000,
          sampleRate: 44100,
        ),
        path: _currentFilePath!,
      );

      _amplitudeSub = _recorder!
          .onAmplitudeChanged(const Duration(milliseconds: 100))
          .listen(_handleAmplitude);

      _maxDurationTimer?.cancel();
      _maxDurationTimer = Timer(maxDuration, () {
        if (_mode != AudioServiceMode.inactive) {
          debugPrint('[UnifiedAudioService] سقف ایمنی ۸ ثانیه‌ای ضبط به پایان رسید.');
          _completeSession();
        }
      });

      // در حالت Hands-Free اگر تا ۳۰ ثانیه هیچ صحبتی نشد، فایل بازنشانی شود
      if (_mode == AudioServiceMode.handsFree) {
        _scheduleIdleCleanup();
      }
    } catch (e) {
      debugPrint('[UnifiedAudioService] خطا در شروع ضبط نیتیو: $e');
      _mode = AudioServiceMode.inactive;
      rethrow;
    }
  }

  void _scheduleIdleCleanup() {
    _idleCleanupTimer?.cancel();
    _idleCleanupTimer = Timer(const Duration(seconds: 30), () async {
      if (_mode == AudioServiceMode.handsFree && !_isSpeaking) {
        try {
          if (_recorder != null && await _recorder!.isRecording()) {
            final p = await _recorder!.stop();
            if (p != null) {
              final f = File(p);
              if (f.existsSync()) f.deleteSync();
            }
          }
          if (_mode == AudioServiceMode.handsFree) {
            await _startNativeRecording();
          }
        } catch (_) {}
      }
    });
  }

  void _handleAmplitude(Amplitude amp) {
    if (_mode == AudioServiceMode.inactive) return;

    final db = amp.current;

    // ۱. کالیبراسیون پویای نویز محیط در ۳۰۰ میلی‌ثانیه ابتدایی
    if (!_isCalibrated) {
      if (db.isFinite) {
        _calibrationSamples.add(db);
      }
      final elapsedMs = _recordingStartTime != null
          ? DateTime.now().difference(_recordingStartTime!).inMilliseconds
          : 0;

      if ((elapsedMs >= 300 || _calibrationSamples.length >= 3) && _calibrationSamples.isNotEmpty) {
        final ambientDb = _calibrationSamples.reduce((a, b) => a + b) / _calibrationSamples.length;
        // آستانه صحبت فعال: max(-32.0, ambientDb + 6.0) با سقف حداکثر -22.0 dBFS
        var threshold = math.max(-32.0, ambientDb + 6.0);
        if (threshold > -22.0) {
          threshold = -22.0;
        }
        voiceThresholdDb = threshold;
        _isCalibrated = true;
        debugPrint('[UnifiedAudioService] کالیبراسیون نویز محیط: ambientDb=${ambientDb.toStringAsFixed(1)}, voiceThresholdDb=${voiceThresholdDb.toStringAsFixed(1)} dBFS');
      }
      return;
    }

    final isVoice = db > voiceThresholdDb;

    if (!_isSpeaking) {
      // فقط در حالت Hands-Free منتظر آغاز صحبت می‌مانیم
      if (isVoice) {
        _isSpeaking = true;
        _idleCleanupTimer?.cancel();
        _onSpeechDetected?.call();
      }
    } else {
      // کاربر در حال صحبت است (در هر دو حالت دستی و بیدارباش Hands-Free)
      if (isVoice) {
        // صدا بالاست، تایمر سکوت ریست شود
        _silenceTimer?.cancel();
        _silenceTimer = null;
      } else {
        // بازگشت صدا به سطح نویز اتاق -> شروع تایمر ۱.۵ ثانیه سکوت
        _silenceTimer ??= Timer(silenceDuration, () {
          if (_mode != AudioServiceMode.inactive && _isSpeaking) {
            debugPrint('[UnifiedAudioService] ۱.۵ ثانیه سکوت پیوسته -> اتمام ضبط و ارسال');
            _completeSession();
          }
        });
      }
    }
  }

  /// توقف و اتمام طبیعی فرمان صوتی پس از ۱.۵ ثانیه سکوت
  Future<void> _completeSession() async {
    _silenceTimer?.cancel();
    _silenceTimer = null;
    _maxDurationTimer?.cancel();
    _maxDurationTimer = null;
    _idleCleanupTimer?.cancel();
    _idleCleanupTimer = null;
    await _amplitudeSub?.cancel();
    _amplitudeSub = null;

    final currentCb = _onRecordingFinished;
    _mode = AudioServiceMode.inactive;
    _isSpeaking = false;
    _isCalibrated = false;
    _calibrationSamples.clear();

    File? resultFile;
    if (_recorder != null) {
      try {
        final path = await _recorder!.stop();
        final actualPath = path ?? _currentFilePath;
        if (actualPath != null) {
          final file = File(actualPath);
          if (file.existsSync() && file.lengthSync() > 200) {
            resultFile = file;
          }
        }
      } catch (e) {
        debugPrint('[UnifiedAudioService] خطا در بستن فایل صوتی: $e');
      }
      try {
        await _recorder!.dispose();
      } catch (_) {}
      _recorder = null;
    }

    if (resultFile != null) {
      currentCb?.call(resultFile);
    }
  }

  /// توقف دستی صریح توسط کاربر (لمس مجدد دکمه)
  Future<File?> stopRecordingAndGetFile() async {
    _silenceTimer?.cancel();
    _silenceTimer = null;
    _maxDurationTimer?.cancel();
    _maxDurationTimer = null;
    _idleCleanupTimer?.cancel();
    _idleCleanupTimer = null;
    await _amplitudeSub?.cancel();
    _amplitudeSub = null;

    _mode = AudioServiceMode.inactive;
    _isSpeaking = false;
    _isCalibrated = false;
    _calibrationSamples.clear();

    File? resultFile;
    if (_recorder != null) {
      try {
        final path = await _recorder!.stop();
        final actualPath = path ?? _currentFilePath;
        if (actualPath != null) {
          final file = File(actualPath);
          if (file.existsSync() && file.lengthSync() > 200) {
            resultFile = file;
          }
        }
      } catch (e) {
        debugPrint('[UnifiedAudioService] خطا در توقف ضبط دستی: $e');
      }
      try {
        await _recorder!.dispose();
      } catch (_) {}
      _recorder = null;
    }
    return resultFile;
  }

  /// توقف فوری و لغو نشست جاری
  Future<void> stop({bool discard = true}) async {
    _silenceTimer?.cancel();
    _silenceTimer = null;
    _maxDurationTimer?.cancel();
    _maxDurationTimer = null;
    _idleCleanupTimer?.cancel();
    _idleCleanupTimer = null;
    await _amplitudeSub?.cancel();
    _amplitudeSub = null;

    _mode = AudioServiceMode.inactive;
    _isSpeaking = false;
    _isCalibrated = false;
    _calibrationSamples.clear();

    if (_recorder != null) {
      try {
        if (await _recorder!.isRecording()) {
          final p = await _recorder!.stop();
          if (discard && p != null) {
            final f = File(p);
            if (f.existsSync()) f.deleteSync();
          }
        }
      } catch (_) {}
      try {
        await _recorder!.dispose();
      } catch (_) {}
      _recorder = null;
    }
  }

  Future<void> dispose() async {
    await stop(discard: true);
  }
}
