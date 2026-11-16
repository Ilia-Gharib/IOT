import 'package:flutter/material.dart';
import '../services/connection_service.dart';
import '../services/storage_service.dart';
import '../models/auth_user.dart';
import '../models/connection_mode.dart';
import 'login_screen.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key});

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  final _localUrlController = TextEditingController();
  final _remoteUrlController = TextEditingController();
  final _conn = ConnectionService();

  AuthUser? _user;
  bool _isTesting = false;
  String? _testResult;

  bool _isTestingRemote = false;
  RemoteTestResult? _remoteTestResult;

  @override
  void initState() {
    super.initState();
    _loadSettings();
  }

  Future<void> _loadSettings() async {
    final local = await StorageService.getLocalUrl();
    final remote = await StorageService.getRemoteUrl();
    final u = await StorageService.getUser();

    if (mounted) {
      setState(() {
        _localUrlController.text = local;
        _remoteUrlController.text = remote;
        _user = u;
      });
    }
  }

  @override
  void dispose() {
    _localUrlController.dispose();
    _remoteUrlController.dispose();
    super.dispose();
  }

  Future<void> _saveSettings() async {
    final local = ConnectionService.sanitizeUrl(_localUrlController.text);
    final remote = ConnectionService.sanitizeUrl(_remoteUrlController.text);
    _localUrlController.text = local;
    _remoteUrlController.text = remote;
    await StorageService.setLocalUrl(local);
    await StorageService.setRemoteUrl(remote);
    await _testConnection();
  }

  Future<void> _testConnection() async {
    setState(() {
      _isTesting = true;
      _testResult = null;
    });

    final success = await _conn.checkConnection();

    if (!mounted) return;
    final mode = _conn.modeNotifier.value;
    String resultMessage;

    if (success) {
      if (mode == ConnectionMode.local) {
        resultMessage = '✅ اتصال برقرار شد (شبکه محلی / هات‌اسپات)';
      } else {
        resultMessage = '✅ اتصال برقرار شد (آنلاین از طریق کلودفلر)';
      }
    } else {
      final remote = _remoteUrlController.text.trim();
      if (remote.isNotEmpty) {
        final remoteRes = await _conn.testRemoteConnection(remote);
        if (remoteRes.success) {
          resultMessage = '✅ اتصال آنلاین کلودفلر برقرار است (سرور محلی در دسترس نیست).';
        } else {
          resultMessage = '❌ خطا: شبکه محلی و اتصال کلودفلر پاسخ ندادند (${remoteRes.message})';
        }
      } else {
        resultMessage = '❌ خطا: هیچ پاسخی از سرور محلی دریافت نشد و آدرس کلودفلر وارد نشده است.';
      }
    }

    if (!mounted) return;
    setState(() {
      _isTesting = false;
      _testResult = resultMessage;
    });
  }

  Future<void> _testRemoteCloudflare() async {
    final remoteUrl = ConnectionService.sanitizeUrl(_remoteUrlController.text);
    _remoteUrlController.text = remoteUrl;
    setState(() {
      _isTestingRemote = true;
      _remoteTestResult = null;
    });

    final result = await _conn.testRemoteConnection(remoteUrl);

    if (!mounted) return;
    setState(() {
      _isTestingRemote = false;
      _remoteTestResult = result;
    });
  }

  Future<void> _fetchFromDiscovery() async {
    setState(() {
      _isTestingRemote = true;
      _remoteTestResult = null;
    });

    final updated = await _conn.refreshRemoteUrlFromDiscovery(timeout: const Duration(seconds: 3));
    if (!mounted) return;

    if (updated != null && updated.isNotEmpty) {
      _remoteUrlController.text = updated;
      setState(() {
        _isTestingRemote = false;
        _remoteTestResult = RemoteTestResult(
          success: true,
          message: '✅ آدرس جدید از دیسکاوری دریافت و جایگزین شد:\n$updated',
          statusCode: 200,
        );
      });
    } else {
      setState(() {
        _isTestingRemote = false;
        _remoteTestResult = const RemoteTestResult(
          success: false,
          message: '❌ عدم دریافت پاسخ از هاست دیسکاوری (ava-gateway.php)',
        );
      });
    }
  }

  Future<void> _handleLogout() async {
    await StorageService.clearAuth();
    if (!mounted) return;
    Navigator.pushAndRemoveUntil(
      context,
      MaterialPageRoute(builder: (_) => const LoginScreen()),
      (route) => false,
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF0F172A),
      appBar: AppBar(
        backgroundColor: const Color(0xFF1E293B),
        elevation: 0,
        title: const Text(
          'تنظیمات AVA 8',
          style: TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.bold),
        ),
        centerTitle: true,
      ),
      body: Directionality(
        textDirection: TextDirection.rtl,
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(20),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              // User info card if logged in
              if (_user != null) ...[
                Container(
                  padding: const EdgeInsets.all(16),
                  decoration: BoxDecoration(
                    color: const Color(0xFF1E293B),
                    borderRadius: BorderRadius.circular(16),
                    border: Border.all(color: const Color(0xFF334155)),
                  ),
                  child: Row(
                    children: [
                      Container(
                        width: 46,
                        height: 46,
                        decoration: BoxDecoration(
                          shape: BoxShape.circle,
                          color: const Color(0xFF10B981).withOpacity(0.15),
                        ),
                        child: const Icon(Icons.person, color: Color(0xFF10B981)),
                      ),
                      const SizedBox(width: 14),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              _user!.username,
                              style: const TextStyle(
                                color: Colors.white,
                                fontSize: 15,
                                fontWeight: FontWeight.bold,
                              ),
                            ),
                            const SizedBox(height: 3),
                            Text(
                              'نقش: ${_user!.role} | طبقه پیش‌فرض: ${_user!.floor}',
                              style: const TextStyle(
                                color: Color(0xFF94A3B8),
                                fontSize: 12,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 20),
              ],

              // Network URLs Card
              Container(
                padding: const EdgeInsets.all(16),
                decoration: BoxDecoration(
                  color: const Color(0xFF1E293B),
                  borderRadius: BorderRadius.circular(16),
                  border: Border.all(color: const Color(0xFF334155)),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text(
                      'پیکربندی سرور گیت‌وی صوتی',
                      style: TextStyle(
                        color: Colors.white,
                        fontSize: 14,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                    const SizedBox(height: 6),
                    const Text(
                      'سیستم ابتدا هات‌اسپات محلی را تست می‌کند؛ در صورت عدم پاسخ به آدرس کلودفلر سوییچ خواهد کرد.',
                      style: TextStyle(color: Color(0xFF94A3B8), fontSize: 11),
                    ),
                    const SizedBox(height: 16),

                    // Local URL (Hotspot)
                    TextField(
                      controller: _localUrlController,
                      style: const TextStyle(color: Colors.white),
                      decoration: InputDecoration(
                        labelText: 'آدرس شبکه داخلی / هات‌اسپات (Local)',
                        helperText: 'پیش‌فرض: http://192.168.137.1:8000',
                        helperStyle: const TextStyle(color: Color(0xFF64748B), fontSize: 10),
                        labelStyle: const TextStyle(color: Color(0xFF94A3B8)),
                        prefixIcon: const Icon(Icons.wifi, color: Color(0xFF10B981)),
                        filled: true,
                        fillColor: const Color(0xFF0F172A),
                        border: OutlineInputBorder(borderRadius: BorderRadius.circular(12)),
                      ),
                    ),
                    const SizedBox(height: 16),

                    // Remote URL (Cloudflare)
                    TextField(
                      controller: _remoteUrlController,
                      style: const TextStyle(color: Colors.white),
                      onChanged: (val) {
                        final sanitized = ConnectionService.sanitizeUrl(val);
                        if (sanitized != val && (val.contains('(') || val.contains('[') || val.contains(' '))) {
                          _remoteUrlController.value = TextEditingValue(
                            text: sanitized,
                            selection: TextSelection.collapsed(offset: sanitized.length),
                          );
                        }
                      },
                      decoration: InputDecoration(
                        labelText: 'آدرس راه‌دور / آنلاین (Remote)',
                        helperText: 'پیش‌فرض: https://fossil-walker-leaf-authentic.trycloudflare.com',
                        helperStyle: const TextStyle(color: Color(0xFF64748B), fontSize: 10),
                        labelStyle: const TextStyle(color: Color(0xFF94A3B8)),
                        prefixIcon: const Icon(Icons.cloud_outlined, color: Color(0xFF3B82F6)),
                        filled: true,
                        fillColor: const Color(0xFF0F172A),
                        border: OutlineInputBorder(borderRadius: BorderRadius.circular(12)),
                      ),
                    ),
                    const SizedBox(height: 10),

                    // Dedicated Cloudflare Test & Discovery Buttons
                    Wrap(
                      spacing: 8,
                      runSpacing: 8,
                      children: [
                        OutlinedButton.icon(
                          onPressed: _isTestingRemote ? null : _testRemoteCloudflare,
                          icon: _isTestingRemote
                              ? const SizedBox(
                                  width: 14,
                                  height: 14,
                                  child: CircularProgressIndicator(
                                    strokeWidth: 2,
                                    color: Color(0xFF38BDF8),
                                  ),
                                )
                              : const Icon(Icons.cloud_sync_outlined, size: 16, color: Color(0xFF38BDF8)),
                          label: const Text(
                            'تست اتصال آنلاین',
                            style: TextStyle(
                              color: Color(0xFF38BDF8),
                              fontSize: 12,
                              fontWeight: FontWeight.bold,
                            ),
                          ),
                          style: OutlinedButton.styleFrom(
                            side: const BorderSide(color: Color(0xFF0284C7)),
                            backgroundColor: const Color(0xFF0284C7).withOpacity(0.12),
                            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
                          ),
                        ),
                        OutlinedButton.icon(
                          onPressed: _isTestingRemote ? null : _fetchFromDiscovery,
                          icon: const Icon(Icons.travel_explore, size: 16, color: Color(0xFF10B981)),
                          label: const Text(
                            'دریافت از دیسکاوری هاست',
                            style: TextStyle(
                              color: Color(0xFF10B981),
                              fontSize: 12,
                              fontWeight: FontWeight.bold,
                            ),
                          ),
                          style: OutlinedButton.styleFrom(
                            side: const BorderSide(color: Color(0xFF059669)),
                            backgroundColor: const Color(0xFF059669).withOpacity(0.12),
                            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
                          ),
                        ),
                      ],
                    ),

                    if (_remoteTestResult != null) ...[
                      const SizedBox(height: 10),
                      Container(
                        padding: const EdgeInsets.all(12),
                        decoration: BoxDecoration(
                          color: _remoteTestResult!.success
                              ? const Color(0xFF10B981).withOpacity(0.14)
                              : const Color(0xFFEF4444).withOpacity(0.14),
                          borderRadius: BorderRadius.circular(10),
                          border: Border.all(
                            color: _remoteTestResult!.success
                                ? const Color(0xFF10B981).withOpacity(0.4)
                                : const Color(0xFFEF4444).withOpacity(0.4),
                          ),
                        ),
                        child: Row(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Icon(
                              _remoteTestResult!.success ? Icons.check_circle : Icons.error_outline,
                              color: _remoteTestResult!.success
                                  ? const Color(0xFF10B981)
                                  : const Color(0xFFEF4444),
                              size: 18,
                            ),
                            const SizedBox(width: 8),
                            Expanded(
                              child: Text(
                                _remoteTestResult!.message,
                                style: TextStyle(
                                  color: _remoteTestResult!.success
                                      ? const Color(0xFF10B981)
                                      : const Color(0xFFEF4444),
                                  fontSize: 12,
                                  fontWeight: FontWeight.w500,
                                  height: 1.35,
                                ),
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],

                    const SizedBox(height: 20),

                    // Test result banner for Save & Test
                    if (_testResult != null) ...[
                      Container(
                        padding: const EdgeInsets.all(12),
                        decoration: BoxDecoration(
                          color: _testResult!.contains('برقرار')
                              ? const Color(0xFF10B981).withOpacity(0.12)
                              : const Color(0xFFEF4444).withOpacity(0.12),
                          borderRadius: BorderRadius.circular(10),
                          border: Border.all(
                            color: _testResult!.contains('برقرار')
                                ? const Color(0xFF10B981).withOpacity(0.4)
                                : const Color(0xFFEF4444).withOpacity(0.4),
                          ),
                        ),
                        child: Text(
                          _testResult!,
                          style: TextStyle(
                            color: _testResult!.contains('برقرار')
                                ? const Color(0xFF10B981)
                                : const Color(0xFFEF4444),
                            fontSize: 12,
                            fontWeight: FontWeight.w500,
                          ),
                        ),
                      ),
                      const SizedBox(height: 16),
                    ],

                    Row(
                      children: [
                        Expanded(
                          child: ElevatedButton.icon(
                            onPressed: _isTesting ? null : _saveSettings,
                            icon: _isTesting
                                ? const SizedBox(
                                    width: 16,
                                    height: 16,
                                    child: CircularProgressIndicator(
                                      strokeWidth: 2,
                                      color: Colors.white,
                                    ),
                                  )
                                : const Icon(Icons.check, size: 18),
                            label: const Text('ذخیره و تست اتصال'),
                            style: ElevatedButton.styleFrom(
                              backgroundColor: const Color(0xFF10B981),
                              foregroundColor: Colors.white,
                              padding: const EdgeInsets.symmetric(vertical: 12),
                              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                            ),
                          ),
                        ),
                        const SizedBox(width: 10),
                        OutlinedButton(
                          onPressed: () {
                            _localUrlController.text = StorageService.defaultLocalUrl;
                            _remoteUrlController.text = StorageService.defaultRemoteUrl;
                          },
                          style: OutlinedButton.styleFrom(
                            side: const BorderSide(color: Color(0xFF475569)),
                            padding: const EdgeInsets.symmetric(vertical: 12, horizontal: 12),
                            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                          ),
                          child: const Text('بازنشانی', style: TextStyle(color: Color(0xFF94A3B8))),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 24),

              // Logout Button
              if (_user != null) ...[
                OutlinedButton.icon(
                  onPressed: _handleLogout,
                  icon: const Icon(Icons.logout, color: Color(0xFFEF4444)),
                  label: const Text('خروج از حساب کاربری', style: TextStyle(color: Color(0xFFEF4444))),
                  style: OutlinedButton.styleFrom(
                    side: const BorderSide(color: Color(0xFFEF4444)),
                    padding: const EdgeInsets.symmetric(vertical: 12),
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                  ),
                ),
                const SizedBox(height: 24),
              ],

              // Version info footer
              const Center(
                child: Text(
                  'AVA 8 · نسخه 0.2.1+8',
                  style: TextStyle(
                    color: Color(0xFF64748B),
                    fontSize: 12,
                    fontWeight: FontWeight.w500,
                  ),
                ),
              ),
              const SizedBox(height: 12),
            ],
          ),
        ),
      ),
    );
  }
}
