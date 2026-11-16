import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import '../models/auth_user.dart';

class StorageService {
  static const String _keyLocalUrl = 'local_gateway_url';
  static const String _keyRemoteUrl = 'remote_gateway_url';
  static const String _keyDiscoveryUrl = 'discovery_gateway_url';
  static const String _keyToken = 'auth_token';
  static const String _keyUserJson = 'auth_user_json';
  static const String _keyLastUsername = 'last_username';
  static const String _keyHandsFree = 'hands_free_enabled';

  static const String defaultLocalUrl = 'http://192.168.137.1:8000';
  static const String defaultRemoteUrl = 'https://fossil-walker-leaf-authentic.trycloudflare.com';
  static const String defaultDiscoveryUrl = 'https://rhomeoffice.ir/ava-gateway.php';

  static Future<String> getLocalUrl() async {
    final prefs = await SharedPreferences.getInstance();
    final url = prefs.getString(_keyLocalUrl);
    if (url == null || url.trim().isEmpty) {
      return defaultLocalUrl;
    }
    return url.trim();
  }

  static Future<void> setLocalUrl(String url) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_keyLocalUrl, url.trim());
  }

  static Future<String> getRemoteUrl() async {
    final prefs = await SharedPreferences.getInstance();
    final url = prefs.getString(_keyRemoteUrl);
    if (url == null || url.trim().isEmpty) {
      return defaultRemoteUrl;
    }
    return url.trim();
  }

  static Future<void> setRemoteUrl(String url) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_keyRemoteUrl, url.trim());
  }

  static Future<String> getDiscoveryUrl() async {
    final prefs = await SharedPreferences.getInstance();
    final url = prefs.getString(_keyDiscoveryUrl);
    if (url == null || url.trim().isEmpty) {
      return defaultDiscoveryUrl;
    }
    return url.trim();
  }

  static Future<void> setDiscoveryUrl(String url) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_keyDiscoveryUrl, url.trim());
  }

  /// دریافت خودکار و سریع آدرس کلودفلر از اندپوینت دیسکاوری هاست (با مهلت ۲ ثانیه) و ذخیره آن
  static Future<String?> refreshRemoteUrlFromDiscovery({
    Duration timeout = const Duration(seconds: 2),
  }) async {
    try {
      final discoveryUrl = await getDiscoveryUrl();
      final uri = Uri.parse(discoveryUrl);
      final response = await http.get(uri).timeout(timeout);
      if (response.statusCode == 200) {
        final data = jsonDecode(utf8.decode(response.bodyBytes)) as Map<String, dynamic>;
        final newRemoteUrl = data['url']?.toString().trim();
        if (newRemoteUrl != null &&
            (newRemoteUrl.startsWith('http://') || newRemoteUrl.startsWith('https://'))) {
          var clean = newRemoteUrl.replaceAll(RegExp(r'''[()\[\]{}<>"'`\s]'''), '');
          while (clean.endsWith('/')) {
            clean = clean.substring(0, clean.length - 1);
          }
          if (clean.isNotEmpty) {
            await setRemoteUrl(clean);
            return clean;
          }
        }
      }
    } catch (_) {}
    return null;
  }

  static Future<String?> getToken() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getString(_keyToken);
  }

  static Future<void> saveAuth(AuthUser user) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_keyToken, user.token);
    await prefs.setString(_keyUserJson, jsonEncode(user.toJson()));
    await prefs.setString(_keyLastUsername, user.username);
  }

  static Future<AuthUser?> getUser() async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString(_keyUserJson);
    if (raw == null || raw.isEmpty) return null;
    try {
      final map = jsonDecode(raw) as Map<String, dynamic>;
      return AuthUser.fromJson(map);
    } catch (_) {
      return null;
    }
  }

  static Future<String> getLastUsername() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getString(_keyLastUsername) ?? 'ilia';
  }

  static Future<bool> getHandsFree() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getBool(_keyHandsFree) ?? false;
  }

  static Future<void> setHandsFree(bool value) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool(_keyHandsFree, value);
  }

  static Future<void> clearAuth() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_keyToken);
    await prefs.remove(_keyUserJson);
  }
}
