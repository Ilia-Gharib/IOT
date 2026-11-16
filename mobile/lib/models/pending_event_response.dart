class PendingEventResponse {
  final bool hasEvent;
  final String? message;
  final String? audioUrl;
  final String? status;
  final String? eventId;

  PendingEventResponse({
    required this.hasEvent,
    this.message,
    this.audioUrl,
    this.status,
    this.eventId,
  });

  factory PendingEventResponse.fromJson(Map<String, dynamic> json) {
    return PendingEventResponse(
      hasEvent: json['has_event'] == true,
      message: json['message'] as String?,
      audioUrl: json['audio_url'] as String?,
      status: json['status'] as String?,
      eventId: json['event_id'] as String?,
    );
  }
}
