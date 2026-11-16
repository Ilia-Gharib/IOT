import 'dart:async';
import 'dart:io';
import 'package:flutter/material.dart';
import '../models/auth_user.dart';
import '../models/process_audio_response.dart';
import '../services/api_service.dart';
import '../services/audio_queue_service.dart';
import '../services/connection_service.dart';
import '../services/storage_service.dart';
import '../services/unified_audio_service.dart';
import '../services/wake_word_service.dart';
import '../widgets/connection_badge.dart';
import '../widgets/metrics_card.dart';
import '../widgets/mic_button.dart';
import '../widgets/ping_test_panel.dart';
import 'settings_screen.dart';

class MainScreen extends StatefulWidget {
  final AuthUser user;

  const MainScreen({super.key, required this.user});

  @override
  State<MainScreen> createState() => _MainScreenState();
}

class _MainScreenState extends State<MainScreen> {
  final _audio = UnifiedAudioService();
  final _wakeWord = WakeWordService();
  final _api = ApiService();
  final _audioQueue = AudioQueueService();
  final _conn = ConnectionService();

  MicState _micState = MicState.idle;
  String _statusLabel = 'آماده دریافت فرمان صوتی';
  String _userTranscript = '';
  String _avaReply = '';
  ProcessAudioResponse? _lastResponse;
  String? _errorMessage;

  bool _isHandsFree = false;
  Timer? _idlePollingTimer;
  bool _isPolling = false;

  @override
  void initState() {
    super.initState();
    _conn.startMonitoring();
    _startIdlePolling();
    _audioQueue.isPlayingNotifier.addListener(_onAudioPlayingChanged);
    _audioQueue.errorNotifier.addListener(_onAudioQueueError);
    _initHandsFree();
  }

  void _onAudioQueueError() {
    final err = _audioQueue.errorNotifier.value;
    if (err != null && mounted) {
      setState(() {
        _errorMessage = err;
      });
    }
  }

  Future<void> _initHandsFree() async {
    final savedHandsFree = await StorageService.getHandsFree();
    if (mounted) {
      setState(() {
        _isHandsFree = savedHandsFree;
      });
      if (_isHandsFree && _micState == MicState.idle) {
        _startHandsFreeListening();
      }
    }
  }

  void _startHandsFreeListening() {
    if (!_isHandsFree || _micState != MicState.idle || _audioQueue.isBusy) return;

    _wakeWord.startListening(
      onDetected: (keyword) async {
        if (!mounted || _micState != MicState.idle) return;

        // پخش صدای کوتاه تایید بیدارباش
        await _audioQueue.playChime();

        setState(() {
          _micState = MicState.recording;
          _statusLabel = 'بیدارباش «$keyword» دریافت شد! در حال شنیدن فرمان...';
          _errorMessage = null;
        });

        // شروع ضبط فرمان اصلی تا ۱.۵ ثانیه سکوت
        await _audio.startManualRecording(
          onFinished: (audioFile) {
            if (mounted) {
              _processAudioFile(audioFile, isHandsFree: true);
            }
          },
        );
      },
    );
  }

  Future<void> _toggleHandsFree(bool value) async {
    await StorageService.setHandsFree(value);
    if (!mounted) return;

    setState(() {
      _isHandsFree = value;
    });

    if (_isHandsFree) {
      if (_micState == MicState.idle) {
        _startHandsFreeListening();
      }
    } else {
      await _wakeWord.stopListening();
      await _audio.stop(discard: true);
      if (_micState == MicState.recording) {
        setState(() {
          _micState = MicState.idle;
          _statusLabel = 'آماده دریافت فرمان صوتی';
        });
      }
    }
  }

  @override
  void dispose() {
    _wakeWord.stopListening();
    _audio.dispose();
    _audioQueue.isPlayingNotifier.removeListener(_onAudioPlayingChanged);
    _audioQueue.errorNotifier.removeListener(_onAudioQueueError);
    _stopIdlePolling();
    super.dispose();
  }

  void _onAudioPlayingChanged() {
    if (!_audioQueue.isPlayingNotifier.value && _micState == MicState.playing) {
      if (mounted) {
        setState(() {
          _micState = MicState.idle;
          _statusLabel = 'آماده دریافت فرمان صوتی';
        });
        if (_isHandsFree) {
          _startHandsFreeListening();
        }
      }
    }
  }

  void _startIdlePolling() {
    _idlePollingTimer?.cancel();
    _idlePollingTimer = Timer.periodic(const Duration(seconds: 2), (_) {
      _pollPendingEvents();
    });
  }

  void _stopIdlePolling() {
    _idlePollingTimer?.cancel();
    _idlePollingTimer = null;
  }

  Future<void> _pollPendingEvents() async {
    if (_micState == MicState.recording ||
        _micState == MicState.processing ||
        _audioQueue.isBusy ||
        _isPolling) {
      return;
    }

    _isPolling = true;
    try {
      final event = await _api.checkPendingEvent(widget.user.token);
      if (event != null && event.hasEvent && mounted) {
        final msg = event.message ?? 'گزارش اتمام عملیات Home Assistant';
        setState(() {
          _avaReply = msg;
          _statusLabel = 'فرمان در Home Assistant اجرا شد';
        });

        if (event.audioUrl != null && event.audioUrl!.isNotEmpty) {
          await _wakeWord.pauseListening();
          await _audio.stop(discard: true);
          setState(() {
            _micState = MicState.playing;
          });
          await _audioQueue.enqueueAudio(
            audioUrl: event.audioUrl!,
            message: msg,
            token: widget.user.token,
          );
        }
      }
    } catch (_) {
      // Background idle poll error is silently ignored
    } finally {
      _isPolling = false;
    }
  }

  Future<void> _toggleManualRecording() async {
    if (_micState == MicState.recording) {
      // کاربر پیش از سکوت ۱.۵ ثانیه‌ای، دکمه را لمس کرد تا ضبط متوقف شود
      final audioFile = await _audio.stopRecordingAndGetFile();
      if (audioFile != null) {
        await _processAudioFile(audioFile, isHandsFree: false);
      } else {
        setState(() {
          _micState = MicState.idle;
          _statusLabel = 'آماده دریافت فرمان صوتی';
        });
        if (_isHandsFree) _startHandsFreeListening();
      }
    } else if (_micState == MicState.idle) {
      await _startManualRecording();
    } else if (_micState == MicState.playing) {
      await _audioQueue.stopAndClear();
      await _startManualRecording();
    }
  }

  Future<void> _startManualRecording() async {
    try {
      await _wakeWord.pauseListening();
      await _audioQueue.stopAndClear();
      setState(() {
        _errorMessage = null;
        _userTranscript = '';
        _avaReply = '';
        _micState = MicState.recording;
        _statusLabel = 'در حال شنیدن صدای شما... (سکوت ۱.۵ ثانیه = ارسال خودکار)';
      });

      await _audio.startManualRecording(
        onFinished: (audioFile) {
          if (mounted) {
            _processAudioFile(audioFile, isHandsFree: false);
          }
        },
      );
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _micState = MicState.idle;
        _statusLabel = 'خطا در دسترسی به میکروفون';
        _errorMessage = e.toString().replaceFirst('Exception: ', '');
      });
      if (_isHandsFree) {
        _startHandsFreeListening();
      }
    }
  }

  Future<void> _processAudioFile(File audioFile, {bool isHandsFree = false}) async {
    await _wakeWord.pauseListening();
    await _audio.stop(discard: false);
    if (!mounted) return;

    setState(() {
      _micState = MicState.processing;
      _statusLabel = 'تبدیل گفتار به متن (Whisper)...';
    });

    try {
      Future.delayed(const Duration(milliseconds: 900), () {
        if (mounted && _micState == MicState.processing) {
          setState(() {
            _statusLabel = 'تحلیل فرمان در مدل محلی (Qwen)...';
          });
        }
      });

      final response = await _api.processAudio(
        audioFile,
        widget.user.token,
        handsFree: isHandsFree,
      );

      if (!mounted) return;

      // در حالت Hands-Free اگر نویز یا صحبت متفرقه بود، آرام و بی‌صدا به شنود بازگرد
      if (response.isIgnored) {
        setState(() {
          _micState = MicState.idle;
          _statusLabel = 'آماده دریافت فرمان صوتی';
        });
        if (_isHandsFree) {
          _startHandsFreeListening();
        }
        return;
      }

      setState(() {
        _lastResponse = response;
        _userTranscript = response.transcript;
        _avaReply = response.reply;

        if (response.isChat) {
          _statusLabel = 'پاسخ گفت‌وگو دریافت شد';
        } else if (response.haExecution != null) {
          final isPending = response.haExecution!['status'] == 'pending';
          _statusLabel = isPending
              ? 'در حال اجرای دستور در Home Assistant...'
              : 'دستور به Home Assistant ارسال شد';
        } else if (!response.isSuccess) {
          _statusLabel = 'خطا در پردازش فرمان';
        } else {
          _statusLabel = 'فرمان پردازش شد';
        }
      });

      // افزودن پاسخ صوتی (حتی صدای توضیحی خطای سرور) به صف پخش ترتیبی
      if (response.audioUrl != null && response.audioUrl!.isNotEmpty) {
        setState(() {
          _micState = MicState.playing;
        });
        await _audioQueue.enqueueAudio(
          audioUrl: response.audioUrl!,
          message: response.reply,
          token: widget.user.token,
        );
      } else {
        setState(() {
          _micState = MicState.idle;
          _statusLabel = 'آماده دریافت فرمان صوتی';
        });
        if (_isHandsFree) {
          _startHandsFreeListening();
        }
      }
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _micState = MicState.idle;
        _statusLabel = 'خطا در پردازش فرمان';
        _errorMessage = e.toString().replaceFirst('Exception: ', '');
      });
      if (_isHandsFree) {
        _startHandsFreeListening();
      }
    }
  }

  String _getMicHintText(MicState state) {
    switch (state) {
      case MicState.idle:
        return _isHandsFree
            ? 'بیدارباش «هی آوا / Hey Ava» فعال است'
            : 'برای صحبت ضربه بزنید (با ۱.۵ ثانیه سکوت خودکار ارسال می‌شود)';
      case MicState.recording:
        return 'در حال شنیدن... (پس از اتمام صحبت، ۱.۵ ثانیه سکوت کنید)';
      case MicState.processing:
        return 'در حال تحلیل و پردازش...';
      case MicState.playing:
        return 'در حال پخش پاسخ از اسپیکر گوشی...';
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF0F172A),
      appBar: AppBar(
        backgroundColor: const Color(0xFF1E293B),
        elevation: 0,
        title: Row(
          children: [
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
              decoration: BoxDecoration(
                color: const Color(0xFF10B981).withOpacity(0.15),
                borderRadius: BorderRadius.circular(8),
              ),
              child: Text(
                'طبقه ${widget.user.floor}',
                style: const TextStyle(
                  color: Color(0xFF10B981),
                  fontSize: 11,
                  fontWeight: FontWeight.bold,
                ),
              ),
            ),
            const SizedBox(width: 8),
            const Text(
              'AVA 8',
              style: TextStyle(
                color: Colors.white,
                fontSize: 16,
                fontWeight: FontWeight.bold,
              ),
            ),
          ],
        ),
        actions: [
          IconButton(
            icon: const Icon(Icons.settings_outlined, color: Color(0xFF94A3B8)),
            tooltip: 'تنظیمات',
            onPressed: () {
              Navigator.push(
                context,
                MaterialPageRoute(builder: (_) => const SettingsScreen()),
              );
            },
          ),
        ],
      ),
      body: Directionality(
        textDirection: TextDirection.rtl,
        child: SafeArea(
          child: Column(
            children: [
              // Connection badge header
              Container(
                padding: const EdgeInsets.symmetric(vertical: 6, horizontal: 16),
                color: const Color(0xFF0F172A),
                child: const Row(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    ConnectionBadge(),
                  ],
                ),
              ),

              // تنها کارت پینگ مجاز در بالای صفحه
              const PingTestPanel(),

              // Hands-Free Toggle Card (سوئیچ حالت شنود خودکار)
              Container(
                margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
                padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 6),
                decoration: BoxDecoration(
                  color: const Color(0xFF1E293B),
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(
                    color: _isHandsFree ? const Color(0xFF10B981) : const Color(0xFF334155),
                    width: _isHandsFree ? 1.5 : 1.0,
                  ),
                ),
                child: Row(
                  children: [
                    Icon(
                      _isHandsFree ? Icons.hearing_rounded : Icons.touch_app_rounded,
                      color: _isHandsFree ? const Color(0xFF10B981) : const Color(0xFF94A3B8),
                      size: 22,
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          const Text(
                            'بیدارباش پس‌زمینه (Hey Ava / هی آوا)',
                            style: TextStyle(
                              color: Colors.white,
                              fontSize: 13,
                              fontWeight: FontWeight.bold,
                            ),
                          ),
                          Text(
                            _isHandsFree
                                ? 'میکروفون آفلاین با Sherpa-ONNX در پس‌زمینه فعال است'
                                : 'حالت لمسی (پیش‌فرض، بدون مصرف باتری)',
                            style: TextStyle(
                              color: _isHandsFree ? const Color(0xFF34D399) : const Color(0xFF94A3B8),
                              fontSize: 11,
                            ),
                          ),
                        ],
                      ),
                    ),
                    Switch(
                      value: _isHandsFree,
                      activeColor: const Color(0xFF10B981),
                      onChanged: (val) {
                        _toggleHandsFree(val);
                      },
                    ),
                  ],
                ),
              ),

              // Scrollable feedback area (فضای باز و کامل نمایش پیام‌ها و خطاها)
              Expanded(
                child: SingleChildScrollView(
                  padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 10),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      // Status Pill
                      Center(
                        child: ValueListenableBuilder<bool>(
                          valueListenable: _audioQueue.isPlayingNotifier,
                          builder: (context, isPlaying, _) {
                            final label = isPlaying ? 'پخش پاسخ صوتی...' : _statusLabel;
                            return Container(
                              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 6),
                              decoration: BoxDecoration(
                                color: const Color(0xFF1E293B),
                                borderRadius: BorderRadius.circular(20),
                                border: Border.all(color: const Color(0xFF334155)),
                              ),
                              child: Text(
                                label,
                                style: const TextStyle(
                                  color: Color(0xFF38BDF8),
                                  fontSize: 12,
                                  fontWeight: FontWeight.w600,
                                ),
                              ),
                            );
                          },
                        ),
                      ),
                      const SizedBox(height: 16),

                      // Error message banner
                      if (_errorMessage != null) ...[
                        Container(
                          padding: const EdgeInsets.all(12),
                          decoration: BoxDecoration(
                            color: const Color(0xFFEF4444).withOpacity(0.12),
                            borderRadius: BorderRadius.circular(12),
                            border: Border.all(color: const Color(0xFFEF4444).withOpacity(0.4)),
                          ),
                          child: Row(
                            children: [
                              const Icon(Icons.error_outline, color: Color(0xFFEF4444), size: 18),
                              const SizedBox(width: 8),
                              Expanded(
                                child: Text(
                                  _errorMessage!,
                                  style: const TextStyle(color: Color(0xFFEF4444), fontSize: 12),
                                ),
                              ),
                            ],
                          ),
                        ),
                        const SizedBox(height: 14),
                      ],

                      // User transcript card
                      if (_userTranscript.isNotEmpty) ...[
                        Container(
                          padding: const EdgeInsets.all(14),
                          decoration: BoxDecoration(
                            color: const Color(0xFF1E293B),
                            borderRadius: BorderRadius.circular(16),
                            border: Border.all(color: const Color(0xFF334155)),
                          ),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              const Row(
                                children: [
                                  Icon(Icons.record_voice_over_outlined,
                                      size: 16, color: Color(0xFF94A3B8)),
                                  SizedBox(width: 6),
                                  Text(
                                    'گفتار شما',
                                    style: TextStyle(
                                      color: Color(0xFF94A3B8),
                                      fontSize: 11,
                                      fontWeight: FontWeight.bold,
                                    ),
                                  ),
                                ],
                              ),
                              const SizedBox(height: 8),
                              Text(
                                _userTranscript,
                                style: const TextStyle(
                                  color: Colors.white,
                                  fontSize: 14,
                                  height: 1.4,
                                ),
                              ),
                            ],
                          ),
                        ),
                        const SizedBox(height: 14),
                      ],

                      // Ava reply card
                      if (_avaReply.isNotEmpty) ...[
                        Container(
                          padding: const EdgeInsets.all(14),
                          decoration: BoxDecoration(
                            color: const Color(0xFF064E3B).withOpacity(0.3),
                            borderRadius: BorderRadius.circular(16),
                            border: Border.all(color: const Color(0xFF10B981).withOpacity(0.4)),
                          ),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              const Row(
                                children: [
                                  Icon(Icons.smart_toy_outlined,
                                      size: 16, color: Color(0xFF10B981)),
                                  SizedBox(width: 6),
                                  Text(
                                    'پاسخ دستیار آوا',
                                    style: TextStyle(
                                      color: Color(0xFF10B981),
                                      fontSize: 11,
                                      fontWeight: FontWeight.bold,
                                    ),
                                  ),
                                ],
                              ),
                              const SizedBox(height: 8),
                              Text(
                                _avaReply,
                                style: const TextStyle(
                                  color: Colors.white,
                                  fontSize: 14,
                                  fontWeight: FontWeight.w600,
                                  height: 1.4,
                                ),
                              ),
                            ],
                          ),
                        ),
                        const SizedBox(height: 14),
                      ],

                      // IoT Metrics Card
                      MetricsCard(response: _lastResponse),
                    ],
                  ),
                ),
              ),

              // Bottom microphone control area
              Container(
                padding: const EdgeInsets.only(top: 8, bottom: 20),
                child: Column(
                  children: [
                    ValueListenableBuilder<bool>(
                      valueListenable: _audioQueue.isPlayingNotifier,
                      builder: (context, isPlaying, _) {
                        MicState effectiveState = _micState;
                        if (isPlaying && _micState == MicState.idle) {
                          effectiveState = MicState.playing;
                        }

                        return Column(
                          children: [
                            if (_isHandsFree && effectiveState == MicState.idle)
                              Container(
                                margin: const EdgeInsets.only(bottom: 10),
                                padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 5),
                                decoration: BoxDecoration(
                                  color: const Color(0xFF10B981).withOpacity(0.12),
                                  borderRadius: BorderRadius.circular(20),
                                  border: Border.all(
                                    color: const Color(0xFF10B981).withOpacity(0.4),
                                  ),
                                ),
                                child: const Row(
                                  mainAxisSize: MainAxisSize.min,
                                  children: [
                                    Icon(Icons.graphic_eq_rounded, size: 14, color: Color(0xFF10B981)),
                                    SizedBox(width: 6),
                                    Text(
                                      'شنود کلمه «هی آوا / Hey Ava» فعال است 🎙️',
                                      style: TextStyle(
                                        color: Color(0xFF10B981),
                                        fontSize: 11,
                                        fontWeight: FontWeight.bold,
                                      ),
                                    ),
                                  ],
                                ),
                              )
                            else
                              const SizedBox(height: 6),

                            MicButton(
                              state: effectiveState,
                              onTap: _toggleManualRecording,
                            ),
                            const SizedBox(height: 10),
                            Text(
                              _getMicHintText(effectiveState),
                              style: const TextStyle(
                                color: Color(0xFF64748B),
                                fontSize: 11,
                              ),
                            ),
                          ],
                        );
                      },
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
