import 'dart:async';
import 'dart:io';
import 'package:flutter/foundation.dart';
import 'package:path_provider/path_provider.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:record/record.dart';

typedef OnSpeechStarted = void Function();
typedef OnSpeechFinished = void Function(File audioFile);

/// سرویس سبک و مستقل شنود سطح صدا (VAD) بدون ایجاد لوپ‌های دیکته گوگل یا بوق زدن سیستم‌عامل
class SoundLevelVadService {
  static final SoundLevelVadService _instance = SoundLevelVadService._internal();
  factory SoundLevelVadService() => _instance;
  SoundLevelVadService._internal();

  final AudioRecorder _recorder = AudioRecorder();
  StreamSubscription<Amplitude>? _amplitudeSub;
  Timer? _silenceTimer;
  Timer? _maxRecordingTimer;
  Timer? _rolloverTimer;

  bool _isListening = false;
  bool _isSpeaking = false;
  String? _currentFilePath;

  OnSpeechStarted? _onSpeechStarted;
  OnSpeechFinished? _onSpeechFinished;

  /// آستانه عبور سطح بلندی صدا (dBFS). کمتر از -32 سکوت/نویز محیط و بالاتر از آن صحبت واقعی کاربر است.
  double voiceThresholdDb = -30.0;

  /// مهلت سکوت برای پایان صحبت و ارسال فایل (۱.۵ ثانیه طبق مشخصات کاربر)
  final Duration silenceDuration = const Duration(milliseconds: 1500);

  /// حداکثر زمان مجاز یک فرمان صوتی (سقف ایمنی ۸ ثانیه‌ای)
  final Duration maxRecordingDuration = const Duration(seconds: 8);

  bool get isListening => _isListening;
  bool get isSpeaking => _isSpeaking;

  Future<bool> checkPermission() async {
    final status = await Permission.microphone.status;
    if (status.isGranted) return true;
    final res = await Permission.microphone.request();
    return res.isGranted;
  }

  /// آغاز شنود سطح صدا برای تشخیص صحبت در حالت Hands-Free
  Future<void> startListening({
    required OnSpeechStarted onSpeechStarted,
    required OnSpeechFinished onSpeechFinished,
  }) async {
    if (_isListening) return;

    final hasPerm = await checkPermission();
    if (!hasPerm) {
      debugPrint('[SoundLevelVadService] عدم اعطای دسترسی به میکروفون.');
      return;
    }

    _onSpeechStarted = onSpeechStarted;
    _onSpeechFinished = onSpeechFinished;
    _isListening = true;
    _isSpeaking = false;

    await _startNewRecordingSession();
  }

  Future<void> _startNewRecordingSession() async {
    if (!_isListening) return;

    try {
      if (await _recorder.isRecording()) {
        await _recorder.stop();
      }

      final tempDir = await getTemporaryDirectory();
      final fileName = 'vad_${DateTime.now().millisecondsSinceEpoch}.m4a';
      _currentFilePath = '${tempDir.path}/$fileName';

      await _recorder.start(
        const RecordConfig(
          encoder: AudioEncoder.aacLc,
          bitRate: 128000,
          sampleRate: 44100,
        ),
        path: _currentFilePath!,
      );

      _amplitudeSub?.cancel();
      _amplitudeSub = _recorder
          .onAmplitudeChanged(const Duration(milliseconds: 100))
          .listen(_handleAmplitude);

      // در صورت سکوت ممتد بیش از ۱۲ ثانیه، نشست رول‌اور می‌شود تا دیسک پر نشود
      _rolloverTimer?.cancel();
      _rolloverTimer = Timer(const Duration(seconds: 12), () {
        if (_isListening && !_isSpeaking) {
          _rolloverSession();
        }
      });
    } catch (e) {
      debugPrint('[SoundLevelVadService] خطا در اجرای نشست ضبط: $e');
    }
  }

  void _handleAmplitude(Amplitude amp) {
    if (!_isListening) return;

    final currentDb = amp.current;

    if (!_isSpeaking) {
      // بررسی عبور صدا از آستانه صحبت
      if (currentDb > voiceThresholdDb) {
        _isSpeaking = true;
        _rolloverTimer?.cancel();
        _onSpeechStarted?.call();

        _maxRecordingTimer?.cancel();
        _maxRecordingTimer = Timer(maxRecordingDuration, () {
          if (_isListening && _isSpeaking) {
            _finishSpeech();
          }
        });
      }
    } else {
      // کاربر در حال صحبت است
      if (currentDb > voiceThresholdDb) {
        // بازنشانی تایمر سکوت به دلیل تداوم گفتار
        _silenceTimer?.cancel();
        _silenceTimer = null;
      } else {
        // شروع شمارش معکوس ۱.۵ ثانیه سکوت
        _silenceTimer ??= Timer(silenceDuration, () {
          if (_isListening && _isSpeaking) {
            _finishSpeech();
          }
        });
      }
    }
  }

  Future<void> _rolloverSession() async {
    if (!_isListening || _isSpeaking) return;
    try {
      final oldPath = await _recorder.stop();
      if (oldPath != null) {
        final f = File(oldPath);
        if (f.existsSync()) f.deleteSync();
      }
      await _startNewRecordingSession();
    } catch (e) {
      debugPrint('[SoundLevelVadService] خطا در بازآغاز نشست: $e');
    }
  }

  Future<void> _finishSpeech() async {
    _silenceTimer?.cancel();
    _silenceTimer = null;
    _maxRecordingTimer?.cancel();
    _maxRecordingTimer = null;
    _rolloverTimer?.cancel();
    _rolloverTimer = null;
    _amplitudeSub?.cancel();
    _amplitudeSub = null;

    _isSpeaking = false;
    _isListening = false;

    try {
      final path = await _recorder.stop();
      final actualPath = path ?? _currentFilePath;
      if (actualPath != null) {
        final file = File(actualPath);
        if (file.existsSync() && file.lengthSync() > 500) {
          _onSpeechFinished?.call(file);
          return;
        }
      }
      debugPrint('[SoundLevelVadService] فایل ضبط‌شده معتبر نیست.');
    } catch (e) {
      debugPrint('[SoundLevelVadService] خطا در پایان ضبط گفتار: $e');
    }
  }

  /// توقف فوری شنود هوشمند
  Future<void> stopListening() async {
    _isListening = false;
    _isSpeaking = false;
    _silenceTimer?.cancel();
    _silenceTimer = null;
    _maxRecordingTimer?.cancel();
    _maxRecordingTimer = null;
    _rolloverTimer?.cancel();
    _rolloverTimer = null;
    _amplitudeSub?.cancel();
    _amplitudeSub = null;

    try {
      if (await _recorder.isRecording()) {
        final p = await _recorder.stop();
        if (p != null) {
          final f = File(p);
          if (f.existsSync()) f.deleteSync();
        }
      }
    } catch (_) {}
  }

  Future<void> dispose() async {
    await stopListening();
    await _recorder.dispose();
  }
}
