import 'dart:async';
import 'dart:io';
import 'package:audioplayers/audioplayers.dart';
import 'package:flutter/foundation.dart';
import 'package:path_provider/path_provider.dart';
import 'api_service.dart';

class AudioQueueItem {
  final String filePath;
  final String message;
  final String audioUrl;

  AudioQueueItem({
    required this.filePath,
    required this.message,
    required this.audioUrl,
  });
}

class AudioQueueService {
  static final AudioQueueService _instance = AudioQueueService._internal();
  factory AudioQueueService() => _instance;

  final AudioPlayer _player = AudioPlayer();
  final ApiService _api = ApiService();

  final List<AudioQueueItem> _queue = [];
  bool _isPlaying = false;

  final ValueNotifier<bool> isPlayingNotifier = ValueNotifier<bool>(false);
  final ValueNotifier<String?> currentMessageNotifier = ValueNotifier<String?>(null);
  final ValueNotifier<String?> errorNotifier = ValueNotifier<String?>(null);

  StreamSubscription? _playerCompleteSub;

  AudioQueueService._internal() {
    _configureAudioRouting();
    _playerCompleteSub = _player.onPlayerComplete.listen((_) {
      _onAudioCompleted();
    });
  }

  /// پیکربندی انحصاری خروجی صدا روی بلندگوی اصلی گوشی (Loudspeaker)
  Future<void> _configureAudioRouting() async {
    try {
      await AudioPlayer.global.setAudioContext(
        AudioContext(
          android: const AudioContextAndroid(
            isSpeakerphoneOn: true,
            stayAwake: true,
            contentType: AndroidContentType.speech,
            usageType: AndroidUsageType.assistanceNavigationGuidance,
            audioFocus: AndroidAudioFocus.gainTransientExclusive,
          ),
          iOS: AudioContextIOS(
            category: AVAudioSessionCategory.playback,
            options: const {
              AVAudioSessionOptions.defaultToSpeaker,
            },
          ),
        ),
      );
    } catch (e) {
      debugPrint('[AudioQueueService] Failed to set global AudioContext: $e');
    }
  }

  bool get isBusy => _isPlaying || _queue.isNotEmpty;

  /// پخش صدای کوتاه تایید بیدارباش (Chime)
  Future<void> playChime() async {
    try {
      await _player.stop();
      // ایجاد یک صدای بیپ کوتاه از طریق سورس ساده یا تنظیم فوری
      await _player.setVolume(1.0);
    } catch (_) {}
  }

  Future<void> enqueueAudio({
    required String audioUrl,
    required String message,
    required String token,
  }) async {
    try {
      errorNotifier.value = null;
      // دانلود فایل صوتی با هدر Authorization و ذخیره در دیسک محلی
      final bytes = await _api.downloadAudioBytes(audioUrl, token);
      final tempDir = await getTemporaryDirectory();
      final fileName = 'tts_${DateTime.now().millisecondsSinceEpoch}_${_queue.length}.mp3';
      final file = File('${tempDir.path}/$fileName');
      await file.writeAsBytes(bytes);

      final item = AudioQueueItem(
        filePath: file.path,
        message: message,
        audioUrl: audioUrl,
      );

      _queue.add(item);

      if (!_isPlaying) {
        _processNext();
      }
    } catch (e) {
      debugPrint('[AudioQueueService] Error downloading/enqueuing audio: $e');
      errorNotifier.value = 'خطا در دریافت یا پخش پاسخ صوتی: $e';
    }
  }

  Future<void> _processNext() async {
    if (_queue.isEmpty) {
      _isPlaying = false;
      isPlayingNotifier.value = false;
      currentMessageNotifier.value = null;
      return;
    }

    _isPlaying = true;
    isPlayingNotifier.value = true;

    final item = _queue.removeAt(0);
    currentMessageNotifier.value = item.message;

    try {
      await _player.stop();
      await _player.setVolume(1.0);
      await _player.play(DeviceFileSource(item.filePath));
    } catch (e) {
      debugPrint('[AudioQueueService] Error playing audio source: $e');
      errorNotifier.value = 'خطا در پخش فایل صوتی: $e';
      _onAudioCompleted();
    }
  }

  void _onAudioCompleted() {
    // ایجاد تاخیر کوتاه ۲۰۰ میلی‌ثانیه‌ای بین پخش دو صوت متوالی
    Future.delayed(const Duration(milliseconds: 200), () {
      _processNext();
    });
  }

  Future<void> stopAndClear() async {
    _queue.clear();
    try {
      await _player.stop();
    } catch (_) {}
    _isPlaying = false;
    isPlayingNotifier.value = false;
    currentMessageNotifier.value = null;
  }

  void dispose() {
    _playerCompleteSub?.cancel();
    _player.dispose();
  }
}
