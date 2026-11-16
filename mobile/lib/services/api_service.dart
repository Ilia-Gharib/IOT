import 'dart:convert';
import 'dart:io';
import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import '../models/auth_user.dart';
import '../models/process_audio_response.dart';
import '../models/pending_event_response.dart';
import 'connection_service.dart';
import 'storage_service.dart';

class ApiService {
  final ConnectionService _conn = ConnectionService();

  Future<AuthUser> login(String username, String password) async {
    var baseUrl = _conn.activeBaseUrl;
    try {
      final res = await _loginInternal(baseUrl, username, password);
      _conn.recordSuccess();
      return res;
    } catch (e) {
      final failoverUrl = await _conn.handleRequestFailure(baseUrl);
      if (failoverUrl != baseUrl) {
        debugPrint('[ApiService] تلاش مجدد لاگین روی آدرس جایگزین: $failoverUrl');
        try {
          final res = await _loginInternal(failoverUrl, username, password);
          _conn.recordSuccess();
          return res;
        } catch (e2) {
          final secondFailover = await _conn.handleRequestFailure(failoverUrl);
          if (secondFailover != failoverUrl && secondFailover != baseUrl) {
            debugPrint('[ApiService] تلاش سوم لاگین روی آدرس لوکال: $secondFailover');
            final res = await _loginInternal(secondFailover, username, password);
            _conn.recordSuccess();
            return res;
          }
          rethrow;
        }
      }
      rethrow;
    }
  }

  Future<AuthUser> _loginInternal(String baseUrl, String username, String password) async {
    final uri = Uri.parse('$baseUrl/api/login');
    final response = await http
        .post(
          uri,
          headers: {'Content-Type': 'application/json'},
          body: jsonEncode({'username': username.trim(), 'password': password}),
        )
        .timeout(const Duration(seconds: 10));

    final data = jsonDecode(utf8.decode(response.bodyBytes)) as Map<String, dynamic>;

    if (response.statusCode == 200 && data['authenticated'] == true) {
      return AuthUser.fromJson(data);
    }

    final detail = data['detail']?.toString() ?? 'نام کاربری یا رمز عبور اشتباه است.';
    throw Exception(detail);
  }

  Future<AuthUser> registerResident({
    required String fullName,
    required String phone,
    required String email,
    required String password,
    required String floorCode,
  }) async {
    var baseUrl = _conn.activeBaseUrl;
    AuthUser user;
    try {
      user = await _registerInternal(
        baseUrl: baseUrl,
        fullName: fullName,
        phone: phone,
        email: email,
        password: password,
        floorCode: floorCode,
      );
      _conn.recordSuccess();
    } catch (e) {
      final failoverUrl = await _conn.handleRequestFailure(baseUrl);
      if (failoverUrl != baseUrl) {
        debugPrint('[ApiService] تلاش مجدد ثبت‌نام روی آدرس جایگزین: $failoverUrl');
        try {
          user = await _registerInternal(
            baseUrl: failoverUrl,
            fullName: fullName,
            phone: phone,
            email: email,
            password: password,
            floorCode: floorCode,
          );
          _conn.recordSuccess();
        } catch (e2) {
          final secondFailover = await _conn.handleRequestFailure(failoverUrl);
          if (secondFailover != failoverUrl && secondFailover != baseUrl) {
            debugPrint('[ApiService] تلاش سوم ثبت‌نام روی آدرس لوکال: $secondFailover');
            user = await _registerInternal(
              baseUrl: secondFailover,
              fullName: fullName,
              phone: phone,
              email: email,
              password: password,
              floorCode: floorCode,
            );
            _conn.recordSuccess();
          } else {
            rethrow;
          }
        }
      } else {
        rethrow;
      }
    }

    // ذخیره توکن و مشخصات کاربر/طبقه در StorageService
    await StorageService.saveAuth(user);
    return user;
  }

  Future<AuthUser> _registerInternal({
    required String baseUrl,
    required String fullName,
    required String phone,
    required String email,
    required String password,
    required String floorCode,
  }) async {
    final uri = Uri.parse('$baseUrl/api/auth/register');
    final response = await http
        .post(
          uri,
          headers: {'Content-Type': 'application/json'},
          body: jsonEncode({
            'full_name': fullName.trim(),
            'phone': phone.trim(),
            'email': email.trim(),
            'password': password,
            'floor_code': floorCode.trim(),
          }),
        )
        .timeout(const Duration(seconds: 10));

    Map<String, dynamic> data;
    try {
      data = jsonDecode(utf8.decode(response.bodyBytes)) as Map<String, dynamic>;
    } catch (_) {
      throw Exception('پاسخ سرور قابل پردازش نیست (HTTP ${response.statusCode})');
    }

    if (response.statusCode >= 200 && response.statusCode < 300) {
      if (data['user'] is Map<String, dynamic>) {
        final userMap = Map<String, dynamic>.from(data['user'] as Map);
        if (!userMap.containsKey('token') && (data.containsKey('token') || data.containsKey('access_token'))) {
          userMap['token'] = data['token'] ?? data['access_token'];
        }
        return AuthUser.fromJson(userMap);
      }
      return AuthUser.fromJson(data);
    }

    final detail = data['detail']?.toString() ??
        data['message']?.toString() ??
        'خطا در ثبت‌نام ساکن جدید (HTTP ${response.statusCode})';
    throw Exception(detail);
  }

  Future<ProcessAudioResponse> processAudio(
    File audioFile,
    String token, {
    bool handsFree = false,
  }) async {
    var baseUrl = _conn.activeBaseUrl;
    try {
      final res = await _processAudioInternal(baseUrl, audioFile, token, handsFree: handsFree);
      _conn.recordSuccess();
      return res;
    } catch (e) {
      final failoverUrl = await _conn.handleRequestFailure(baseUrl);
      if (failoverUrl != baseUrl) {
        debugPrint('[ApiService] تلاش مجدد ارسال صدا روی آدرس جایگزین: $failoverUrl');
        try {
          final res = await _processAudioInternal(failoverUrl, audioFile, token, handsFree: handsFree);
          _conn.recordSuccess();
          return res;
        } catch (e2) {
          final secondFailover = await _conn.handleRequestFailure(failoverUrl);
          if (secondFailover != failoverUrl && secondFailover != baseUrl) {
            debugPrint('[ApiService] تلاش سوم ارسال صدا روی آدرس لوکال: $secondFailover');
            final res = await _processAudioInternal(secondFailover, audioFile, token, handsFree: handsFree);
            _conn.recordSuccess();
            return res;
          }
          rethrow;
        }
      }
      rethrow;
    }
  }

  Future<ProcessAudioResponse> _processAudioInternal(
    String baseUrl,
    File audioFile,
    String token, {
    bool handsFree = false,
  }) async {
    final uri = Uri.parse('$baseUrl/process-audio');

    final request = http.MultipartRequest('POST', uri);
    request.headers['Authorization'] = 'Bearer $token';
    if (handsFree) {
      request.fields['hands_free'] = 'true';
    }

    final bytes = await audioFile.readAsBytes();
    final filename = audioFile.path.endsWith('.wav') ? 'command.wav' : 'command.m4a';
    final multipartFile = http.MultipartFile.fromBytes(
      'audio',
      bytes,
      filename: filename,
    );
    request.files.add(multipartFile);

    final streamedResponse = await request.send().timeout(ConnectionService.receiveTimeout);
    final response = await http.Response.fromStream(streamedResponse);
    final decoded = utf8.decode(response.bodyBytes);

    Map<String, dynamic> data;
    try {
      data = jsonDecode(decoded) as Map<String, dynamic>;
    } catch (_) {
      throw Exception('پاسخ سرور قابل پردازش نیست (HTTP ${response.statusCode})');
    }

    // 1. Successful or ignored responses (2xx)
    if (response.statusCode >= 200 && response.statusCode < 300) {
      return ProcessAudioResponse.fromJson(data);
    }

    // 2. Server errors that include spoken error audio (e.g. 422 "متوجه نشدم...")
    if (data.containsKey('audio_url') &&
        data['audio_url'] != null &&
        data['audio_url'].toString().isNotEmpty) {
      return ProcessAudioResponse(
        status: 'error',
        intent: 'chat',
        transcript: data['transcript']?.toString() ?? '',
        reply: data['detail']?.toString() ?? 'خطا در پردازش فرمان',
        audioUrl: data['audio_url']?.toString(),
        errorCode: data['error_code']?.toString(),
        detail: data['detail']?.toString(),
      );
    }

    final detail = data['detail']?.toString() ??
        'خطا در پردازش فرمان صوتی (HTTP ${response.statusCode})';
    throw Exception(detail);
  }

  Future<Uint8List> downloadAudioBytes(String audioUrl, String token) async {
    var baseUrl = _conn.activeBaseUrl;
    try {
      final res = await _downloadAudioBytesInternal(baseUrl, audioUrl, token);
      _conn.recordSuccess();
      return res;
    } catch (e) {
      final failoverUrl = await _conn.handleRequestFailure(baseUrl);
      if (failoverUrl != baseUrl) {
        debugPrint('[ApiService] تلاش مجدد دانلود صدا روی آدرس جایگزین: $failoverUrl');
        try {
          final res = await _downloadAudioBytesInternal(failoverUrl, audioUrl, token);
          _conn.recordSuccess();
          return res;
        } catch (e2) {
          final secondFailover = await _conn.handleRequestFailure(failoverUrl);
          if (secondFailover != failoverUrl && secondFailover != baseUrl) {
            debugPrint('[ApiService] تلاش سوم دانلود صدا روی آدرس لوکال: $secondFailover');
            final res = await _downloadAudioBytesInternal(secondFailover, audioUrl, token);
            _conn.recordSuccess();
            return res;
          }
          rethrow;
        }
      }
      rethrow;
    }
  }

  Future<Uint8List> _downloadAudioBytesInternal(String baseUrl, String audioUrl, String token) async {
    String fullUrl;
    if (audioUrl.startsWith('http://') || audioUrl.startsWith('https://')) {
      fullUrl = audioUrl;
    } else {
      final cleanAudio = audioUrl.startsWith('/') ? audioUrl : '/$audioUrl';
      fullUrl = '$baseUrl$cleanAudio';
    }

    final response = await http.get(
      Uri.parse(fullUrl),
      headers: {'Authorization': 'Bearer $token'},
    ).timeout(const Duration(seconds: 20));

    if (response.statusCode == 200) {
      return response.bodyBytes;
    }
    throw Exception('خطا در دریافت فایل صوتی (HTTP ${response.statusCode})');
  }

  Future<PendingEventResponse?> checkPendingEvent(String token) async {
    final baseUrl = _conn.activeBaseUrl;
    final uri = Uri.parse('$baseUrl/api/pending-event');

    try {
      final response = await http.get(
        uri,
        headers: {
          'Authorization': 'Bearer $token',
          'Cache-Control': 'no-store',
        },
      ).timeout(const Duration(seconds: 4));

      if (response.statusCode == 200) {
        final data = jsonDecode(utf8.decode(response.bodyBytes)) as Map<String, dynamic>;
        return PendingEventResponse.fromJson(data);
      }
      return null;
    } catch (_) {
      return null;
    }
  }
}
