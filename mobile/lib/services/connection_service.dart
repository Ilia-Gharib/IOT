import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import '../models/connection_mode.dart';
import 'storage_service.dart';

class PingResult {
  final bool success;
  final int latencyMs;
  final String message;
  final int? statusCode;

  const PingResult({
    required this.success,
    required this.latencyMs,
    required this.message,
    this.statusCode,
  });
}

class RemoteTestResult {
  final bool success;
  final String message;
  final int? statusCode;

  const RemoteTestResult({
    required this.success,
    required this.message,
    this.statusCode,
  });
}

/// سرویس مدیریت اتصال چسبنده (Sticky Failover) با حذف پینگ‌های عصبی و تغییر وضعیت لحظه‌ای
class ConnectionService {
  static final ConnectionService _instance = ConnectionService._internal();
  factory ConnectionService() => _instance;
  ConnectionService._internal();

  /// آدرس‌های پیش‌فرض دائمی
  static const String defaultLocalUrl = StorageService.defaultLocalUrl;
  static const String defaultRemoteUrl = StorageService.defaultRemoteUrl;
  static const String defaultDiscoveryUrl = StorageService.defaultDiscoveryUrl;

  /// مهلت دریافت پاسخ برای پردازش فرمان‌های صوتی و درخواست‌های شبکه (۳۰ ثانیه)
  static const Duration receiveTimeout = Duration(seconds: 30);
  Duration get commandReceiveTimeout => receiveTimeout;

  final ValueNotifier<ConnectionMode> modeNotifier =
      ValueNotifier<ConnectionMode>(ConnectionMode.disconnected);

  String _activeBaseUrl = StorageService.defaultLocalUrl;
  String get activeBaseUrl => _activeBaseUrl;

  StreamSubscription<List<ConnectivityResult>>? _connectivitySub;
  bool _isChecking = false;
  int _consecutiveFailures = 0;

  /// شروع مانیتورینگ رویدادمحور بر اساس تغییر وضعیت واقعی شبکه اندروید
  void startMonitoring() {
    _connectivitySub?.cancel();
    _checkInitialNetwork();
    unawaited(refreshRemoteUrlFromDiscovery());

    _connectivitySub = Connectivity().onConnectivityChanged.listen((results) {
      _handleNetworkChange(results);
    });
  }

  void stopMonitoring() {
    _connectivitySub?.cancel();
    _connectivitySub = null;
  }

  Future<void> _checkInitialNetwork() async {
    try {
      final results = await Connectivity().checkConnectivity();
      await _handleNetworkChange(results);
    } catch (_) {
      await checkConnection();
    }
  }

  /// مدیریت هوشمند تغییر شبکه بدون ایجاد تاخیر و بدون چشمک زدن
  Future<void> _handleNetworkChange(List<ConnectivityResult> results) async {
    final hasWifi = results.contains(ConnectivityResult.wifi);
    final hasVpn = results.contains(ConnectivityResult.vpn);
    final hasEthernet = results.contains(ConnectivityResult.ethernet);

    if (hasWifi || hasVpn || hasEthernet) {
      // اگر به وای‌فای یا VPN (یا اترنت) متصل است، حتماً ابتدا تست سلامت لوکال (http://192.168.137.1:8000/health) را با مهلت ۱.۲ ثانیه تست می‌کنیم
      await _checkAndLockLocalOrRemote();
    } else {
      // فقط اگر وای‌فای/VPN خاموش است و صرفاً دیتای موبایل فعال است، مستقیم روی کلودفلر قفل شو
      await _lockToRemote();
    }
  }

  Future<void> _checkAndLockLocalOrRemote() async {
    if (_isChecking) return;
    _isChecking = true;

    try {
      final localUrl = await StorageService.getLocalUrl();
      final targetLocal = localUrl.isNotEmpty ? localUrl : defaultLocalUrl;

      // تست سریع هات‌اسپات محلی با مهلت ۱.۲ ثانیه
      final localSuccess = await _pingHealth(targetLocal, const Duration(milliseconds: 1200));
      if (localSuccess) {
        _activeBaseUrl = _cleanUrl(targetLocal);
        modeNotifier.value = ConnectionMode.local;
        _consecutiveFailures = 0;
        _isChecking = false;
        unawaited(syncGatewayInfo(targetLocal));
        debugPrint('[ConnectionService] قفل چسبنده روی حالت محلی (هات‌اسپات): $_activeBaseUrl');
        return;
      }

      // سرور محلی در دسترس نیست؛ قفل روی کلودفلر
      await _lockToRemote();
    } catch (_) {
      await _lockToRemote();
    } finally {
      _isChecking = false;
    }
  }

  Future<void> _lockToRemote() async {
    final remoteUrl = await StorageService.getRemoteUrl();
    final targetRemote = remoteUrl.isNotEmpty ? remoteUrl : defaultRemoteUrl;
    _activeBaseUrl = _cleanUrl(targetRemote);
    modeNotifier.value = ConnectionMode.remote;
    _consecutiveFailures = 0;
    debugPrint('[ConnectionService] قفل چسبنده روی حالت آنلاین (کلودفلر): $_activeBaseUrl');
  }

  /// استعلام سریع آدرس کلودفلر از هاست دیسکاوری (با مهلت ۲ ثانیه) و به‌روزرسانی ریموت
  Future<String?> refreshRemoteUrlFromDiscovery({
    Duration timeout = const Duration(seconds: 2),
  }) async {
    try {
      final updatedUrl = await StorageService.refreshRemoteUrlFromDiscovery(timeout: timeout);
      if (updatedUrl != null && updatedUrl.isNotEmpty) {
        final clean = _cleanUrl(updatedUrl);
        debugPrint('[ConnectionService] دریافت موفق آدرس از دیسکاوری هاست: $clean');
        if (modeNotifier.value == ConnectionMode.remote || _activeBaseUrl != defaultLocalUrl) {
          _activeBaseUrl = clean;
        }
        return clean;
      }
    } catch (e) {
      debugPrint('[ConnectionService] خطا در استعلام دیسکاوری: $e');
    }
    return null;
  }

  /// سوییچ کاملاً دوطرفه (Bidirectional Failover) در صورت خطای شبکه یا SocketException
  Future<String> handleRequestFailure(String failedUrl) async {
    _consecutiveFailures++;
    final localUrl = await StorageService.getLocalUrl();
    final remoteUrl = await StorageService.getRemoteUrl();
    final cleanFailed = _cleanUrl(failedUrl);
    final cleanLocal = _cleanUrl(localUrl.isNotEmpty ? localUrl : defaultLocalUrl);
    final cleanRemote = _cleanUrl(remoteUrl.isNotEmpty ? remoteUrl : defaultRemoteUrl);

    final isFailedLocal = cleanFailed == cleanLocal;

    // ۱. اگر در حالت محلی بود و درخواست واقعی خطا داد، همان لحظه به کلودفلر سوییچ کن
    if (isFailedLocal) {
      debugPrint('[ConnectionService] شکست درخواست محلی ($cleanFailed) -> سوییچ فوری به کلودفلر: $cleanRemote');
      _activeBaseUrl = cleanRemote;
      modeNotifier.value = ConnectionMode.remote;
      return _activeBaseUrl;
    }

    // ۲. اگر در حالت ریموت (کلودفلر) بود و با خطای شبکه یا SocketException مواجه شد:
    debugPrint('[ConnectionService] شکست درخواست ریموت ($cleanFailed) -> استعلام آخرین آدرس از دیسکاوری هاست...');
    try {
      final discoveredUrl = await refreshRemoteUrlFromDiscovery(timeout: const Duration(seconds: 2));
      if (discoveredUrl != null && discoveredUrl.isNotEmpty && discoveredUrl != cleanFailed) {
        final isAlive = await _pingHealth(discoveredUrl, const Duration(milliseconds: 1200));
        if (isAlive) {
          debugPrint('[ConnectionService] آدرس جدید از دیسکاوری تایید شد: $discoveredUrl');
          _activeBaseUrl = discoveredUrl;
          modeNotifier.value = ConnectionMode.remote;
          _consecutiveFailures = 0;
          return _activeBaseUrl;
        }
      }
    } catch (e) {
      debugPrint('[ConnectionService] خطا در استعلام دیسکاوری هاست: $e');
    }

    // اگر آدرس دیسکاوری در دسترس نبود یا پاسخی نداد، بلافاصله به آدرس لوکال سوییچ کن
    debugPrint('[ConnectionService] ریموت در دسترس نیست -> سوییچ فوری به سرور محلی: $cleanLocal');
    _activeBaseUrl = cleanLocal;
    modeNotifier.value = ConnectionMode.local;

    if (_consecutiveFailures >= 2) {
      modeNotifier.value = ConnectionMode.disconnected;
    }
    return _activeBaseUrl;
  }

  /// ثبت موفقیت درخواست واقعی جهت بازنشانی شمارنده خطاها
  void recordSuccess() {
    _consecutiveFailures = 0;
  }

  /// بررسی دستی اتصال در صورت نیاز
  Future<bool> checkConnection() async {
    try {
      final results = await Connectivity().checkConnectivity();
      await _handleNetworkChange(results);
      return modeNotifier.value != ConnectionMode.disconnected;
    } catch (_) {
      return false;
    }
  }

  /// همگام‌سازی خودکار و بی‌صدا جدیدترین آدرس کلودفلر از سرور محلی
  Future<void> syncGatewayInfo(String localUrl) async {
    try {
      final clean = _cleanUrl(localUrl);
      final uri = Uri.parse('$clean/api/gateway-info');
      final resp = await http.get(uri).timeout(const Duration(seconds: 3));
      if (resp.statusCode == 200) {
        final data = jsonDecode(utf8.decode(resp.bodyBytes)) as Map<String, dynamic>;
        final newRemoteUrl = data['remote_url']?.toString().trim();
        if (newRemoteUrl != null && (newRemoteUrl.startsWith('http://') || newRemoteUrl.startsWith('https://'))) {
          final savedRemote = await StorageService.getRemoteUrl();
          if (savedRemote != newRemoteUrl) {
            await StorageService.setRemoteUrl(newRemoteUrl);
            debugPrint('[ConnectionService] لینک کلودفلر خودکار همگام‌سازی شد: $newRemoteUrl');
          }
        }
      }
    } catch (_) {}
  }

  /// تست پینگ دستی با محاسبه زمان رفت و برگشت (Latency in ms)
  Future<PingResult> pingEndpoint(String baseUrl, {Duration timeout = const Duration(seconds: 4)}) async {
    final clean = _cleanUrl(baseUrl);
    if (clean.isEmpty) {
      return const PingResult(
        success: false,
        latencyMs: 0,
        message: 'آدرس وارد نشده است.',
      );
    }

    final stopwatch = Stopwatch()..start();
    try {
      final uri = Uri.parse('$clean/health');
      final resp = await http.get(uri).timeout(timeout);
      stopwatch.stop();

      if (resp.statusCode == 200) {
        return PingResult(
          success: true,
          latencyMs: stopwatch.elapsedMilliseconds,
          message: 'پاسخ موفق (${stopwatch.elapsedMilliseconds} ms)',
          statusCode: 200,
        );
      }

      final apiUri = Uri.parse('$clean/api/health');
      final apiResp = await http.get(apiUri).timeout(timeout);
      if (apiResp.statusCode == 200) {
        return PingResult(
          success: true,
          latencyMs: stopwatch.elapsedMilliseconds,
          message: 'پاسخ موفق (${stopwatch.elapsedMilliseconds} ms)',
          statusCode: 200,
        );
      }

      return PingResult(
        success: false,
        latencyMs: stopwatch.elapsedMilliseconds,
        message: 'خطای سرور: HTTP ${resp.statusCode}',
        statusCode: resp.statusCode,
      );
    } on TimeoutException {
      stopwatch.stop();
      return PingResult(
        success: false,
        latencyMs: stopwatch.elapsedMilliseconds,
        message: 'خطای مهلت زمانی (Timeout)',
      );
    } on SocketException catch (e) {
      stopwatch.stop();
      return PingResult(
        success: false,
        latencyMs: stopwatch.elapsedMilliseconds,
        message: 'خطای شبکه: ${e.message}',
      );
    } catch (e) {
      stopwatch.stop();
      return PingResult(
        success: false,
        latencyMs: stopwatch.elapsedMilliseconds,
        message: 'خطا در ارتباط: ${e.toString().replaceAll("Exception: ", "")}',
      );
    }
  }

  /// تست دسترسی آفلاین (هات‌اسپات) -> http://192.168.137.1:8000/health
  Future<PingResult> pingOffline() async {
    final localUrl = await StorageService.getLocalUrl();
    final target = localUrl.isNotEmpty ? localUrl : defaultLocalUrl;
    final res = await pingEndpoint(target, timeout: const Duration(seconds: 2));
    if (res.success) {
      unawaited(syncGatewayInfo(target));
    }
    return res;
  }

  /// تست دسترسی آنلاین (اینترنت) -> کلودفلر
  Future<PingResult> pingOnline() async {
    final remoteUrl = await StorageService.getRemoteUrl();
    final target = remoteUrl.isNotEmpty ? remoteUrl : defaultRemoteUrl;
    return pingEndpoint(target, timeout: const Duration(seconds: 4));
  }

  /// تست اختصاصی اتصال آنلاین
  Future<RemoteTestResult> testRemoteConnection(String remoteUrl) async {
    final trimmed = remoteUrl.trim();
    if (trimmed.isEmpty) {
      return const RemoteTestResult(
        success: false,
        message: 'لطفاً آدرس راه‌دور آنلاین را وارد کنید.',
      );
    }

    final ping = await pingEndpoint(trimmed, timeout: const Duration(seconds: 5));
    if (ping.success) {
      return RemoteTestResult(
        success: true,
        message: '✅ اتصال آنلاین برقرار و تایید شد (${ping.latencyMs} ms)',
        statusCode: ping.statusCode,
      );
    } else {
      return RemoteTestResult(
        success: false,
        message: '❌ عدم برقراری ارتباط آنلاین: ${ping.message}',
        statusCode: ping.statusCode,
      );
    }
  }

  Future<bool> _pingHealth(String baseUrl, Duration timeout) async {
    final clean = _cleanUrl(baseUrl);
    try {
      final uri = Uri.parse('$clean/health');
      final resp = await http.get(uri).timeout(timeout);
      if (resp.statusCode == 200) return true;

      final apiUri = Uri.parse('$clean/api/health');
      final apiResp = await http.get(apiUri).timeout(timeout);
      return apiResp.statusCode == 200;
    } catch (_) {
      return false;
    }
  }

  /// پاکسازی آدرس از پرانتز، براکت، آکولاد، گیومه، فاصله و کاراکترهای اضافی
  static String sanitizeUrl(String url) {
    var s = url.trim();
    s = s.replaceAll(RegExp(r'''[()\[\]{}<>"'`\s]'''), '');
    while (s.endsWith('/')) {
      s = s.substring(0, s.length - 1);
    }
    if (s.isEmpty) return '';
    if (!s.startsWith('http://') && !s.startsWith('https://')) {
      s = 'https://$s';
    }
    return s;
  }

  String _cleanUrl(String url) => sanitizeUrl(url);
}
