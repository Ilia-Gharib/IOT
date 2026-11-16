class ProcessAudioResponse {
  final String status;
  final String intent;
  final String transcript;
  final String reply;
  final String? audioUrl;
  final Map<String, dynamic>? parsedCommand;
  final Map<String, dynamic>? haExecution;
  final Map<String, dynamic>? ttsStatus;
  final String? errorCode;
  final String? detail;
  final bool ignored;

  ProcessAudioResponse({
    required this.status,
    required this.intent,
    required this.transcript,
    required this.reply,
    this.audioUrl,
    this.parsedCommand,
    this.haExecution,
    this.ttsStatus,
    this.errorCode,
    this.detail,
    this.ignored = false,
  });

  bool get isSuccess => status == 'success';
  bool get isChat => intent == 'chat';
  bool get isIgnored => status == 'ignored' || ignored == true;

  String get floorDisplay {
    if (isChat) return 'عمومی / گفت‌وگو';
    final cmd = parsedCommand;
    final floor = cmd != null ? cmd['floor'] : null;
    if (floor == 2 || floor == '2') return 'طبقه ۲';
    return 'طبقه ۱';
  }

  String get serviceDisplay {
    if (isChat) return 'گفت‌وگو (هوش مصنوعی)';
    final cmd = parsedCommand;
    if (cmd == null) return '—';
    final domain = cmd['domain']?.toString();
    final service = cmd['service']?.toString();
    if (domain != null && service != null) {
      return '$domain.$service';
    }
    return '—';
  }

  String get entityDisplay {
    if (isChat) return 'دستیار صوتی';
    final cmd = parsedCommand;
    return cmd?['entity_id']?.toString() ?? '—';
  }

  String get haStatusDisplay {
    if (isChat) return 'پاسخ گفت‌وگو';
    final ha = haExecution;
    if (ha == null) return 'در حال ارسال';
    if (ha['status'] == 'pending') return 'در حال اجرا (پس‌زمینه)';
    if (ha['ok'] == true) return 'با موفقیت اجرا شد';
    final code = ha['status_code'];
    return 'خطا در Home Assistant ${code != null ? "($code)" : ""}';
  }

  factory ProcessAudioResponse.fromJson(Map<String, dynamic> json) {
    return ProcessAudioResponse(
      status: json['status'] as String? ?? 'error',
      intent: json['intent'] as String? ?? 'chat',
      transcript: json['transcript'] as String? ?? '',
      reply: json['initial_reply'] as String? ??
          json['reply'] as String? ??
          (json['parsed_command'] is Map
              ? (json['parsed_command']['ack_message'] ?? json['parsed_command']['reply'])
              : null) as String? ??
          '',
      audioUrl: json['audio_url'] as String?,
      parsedCommand: json['parsed_command'] is Map<String, dynamic>
          ? json['parsed_command'] as Map<String, dynamic>
          : null,
      haExecution: json['ha_execution'] is Map<String, dynamic>
          ? json['ha_execution'] as Map<String, dynamic>
          : null,
      ttsStatus: json['tts_status'] is Map<String, dynamic>
          ? json['tts_status'] as Map<String, dynamic>
          : null,
      errorCode: json['error_code'] as String?,
      detail: json['detail'] as String?,
      ignored: json['ignored'] as bool? ?? (json['status'] == 'ignored'),
    );
  }
}
