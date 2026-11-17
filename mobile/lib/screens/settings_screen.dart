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

  String _connectionMode = StorageService.modeAuto;
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
    final mode = await StorageService.getConnectionMode();
    final u = await StorageService.getUser();

    if (mounted) {
      setState(() {
        _localUrlController.text = local;
        _remoteUrlController.text = remote;
        _connectionMode = mode;
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

  Future<void> _setConnectionMode(String newMode) async {
    if (_connectionMode == newMode) return;
    setState(() {
      _connectionMode = newMode;
    });
    await _conn.applyConnectionMode(newMode);

    if (!mounted) return;

    String label;
    switch (newMode) {
      case StorageService.modeOffline:
        label = 'فقط آفلاین (هات‌اسپات)';
        break;
      case StorageService.modeOnline:
        label = 'فقط آنلاین (کلودفلر)';
        break;
      case StorageService.modeAuto:
      default:
        label = 'خودکار (Auto)';
        break;
    }

    ScaffoldMessenger.of(context).hideCurrentSnackBar();
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Directionality(
          textDirection: TextDirection.rtl,
          child: Text(
            'حالت اتصال شبکه روی «$label» تنظیم شد.',
            style: const TextStyle(color: Colors.white, fontSize: 13),
          ),
        ),
        backgroundColor: const Color(0xFF1E293B),
        behavior: SnackBarBehavior.floating,
        duration: const Duration(seconds: 2),
      ),
    );
  }

  String _getModeDescription() {
    switch (_connectionMode) {
      case StorageService.modeOffline:
        return 'حالت فقط آفلاین (Offline): کل سیستم منحصراً روی آدرس هات‌اسپات محلی (192.168.137.1) قفل است و هیچ درخواستی به اینترنت یا کلودفلر ارسال نمی‌شود.';
      case StorageService.modeOnline:
        return 'حالت فقط آنلاین (Online): کل سیستم منحصراً روی آدرس کلودفلر قفل است و آدرس هات‌اسپات محلی بررسی یا تست نخواهد شد.';
      case StorageService.modeAuto:
      default:
        return 'حالت خودکار (Auto): سیستم ابتدا هات‌اسپات محلی (192.168.137.1) را تست می‌کند؛ در صورت عدم پاسخ به آدرس کلودفلر سوییچ خواهد کرد.';
    }
  }

  Widget _buildModeSelector() {
    IconData icon;
    String label;
    Color color;

    switch (_connectionMode) {
      case StorageService.modeOffline:
        icon = Icons.wifi;
        label = 'آفلاین';
        color = const Color(0xFFF59E0B); // Amber
        break;
      case StorageService.modeOnline:
        icon = Icons.cloud;
        label = 'آنلاین';
        color = const Color(0xFF38BDF8); // Sky Blue
        break;
      case StorageService.modeAuto:
      default:
        icon = Icons.sync_alt;
        label = 'اتو';
        color = const Color(0xFF10B981); // Emerald
        break;
    }

    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
      child: PopupMenuButton<String>(
        tooltip: 'انتخاب حالت اتصال شبکه',
        initialValue: _connectionMode,
        onSelected: _setConnectionMode,
        color: const Color(0xFF1E293B),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(14),
          side: const BorderSide(color: Color(0xFF334155)),
        ),
        elevation: 8,
        itemBuilder: (context) => [
          PopupMenuItem<String>(
            value: StorageService.modeAuto,
            child: Directionality(
              textDirection: TextDirection.rtl,
              child: Row(
                children: [
                  const Icon(Icons.sync_alt, color: Color(0xFF10B981), size: 18),
                  const SizedBox(width: 10),
                  const Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text('حالت خودکار (Auto)', style: TextStyle(color: Colors.white, fontSize: 13, fontWeight: FontWeight.bold)),
                        Text('سوییچ هوشمند بین هات‌اسپات و کلودفلر', style: TextStyle(color: Color(0xFF94A3B8), fontSize: 10)),
                      ],
                    ),
                  ),
                  if (_connectionMode == StorageService.modeAuto)
                    const Icon(Icons.check, color: Color(0xFF10B981), size: 18),
                ],
              ),
            ),
          ),
          const PopupMenuDivider(height: 1),
          PopupMenuItem<String>(
            value: StorageService.modeOffline,
            child: Directionality(
              textDirection: TextDirection.rtl,
              child: Row(
                children: [
                  const Icon(Icons.wifi, color: Color(0xFFF59E0B), size: 18),
                  const SizedBox(width: 10),
                  const Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text('فقط آفلاین (Offline)', style: TextStyle(color: Colors.white, fontSize: 13, fontWeight: FontWeight.bold)),
                        Text('منحصراً روی هات‌اسپات 192.168.137.1', style: TextStyle(color: Color(0xFF94A3B8), fontSize: 10)),
                      ],
                    ),
                  ),
                  if (_connectionMode == StorageService.modeOffline)
                    const Icon(Icons.check, color: Color(0xFFF59E0B), size: 18),
                ],
              ),
            ),
          ),
          const PopupMenuDivider(height: 1),
          PopupMenuItem<String>(
            value: StorageService.modeOnline,
            child: Directionality(
              textDirection: TextDirection.rtl,
              child: Row(
                children: [
                  const Icon(Icons.cloud, color: Color(0xFF38BDF8), size: 18),
                  const SizedBox(width: 10),
                  const Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text('فقط آنلاین (Online)', style: TextStyle(color: Colors.white, fontSize: 13, fontWeight: FontWeight.bold)),
                        Text('منحصراً روی آدرس ریموت کلودفلر', style: TextStyle(color: Color(0xFF94A3B8), fontSize: 10)),
                      ],
                    ),
                  ),
                  if (_connectionMode == StorageService.modeOnline)
                    const Icon(Icons.check, color: Color(0xFF38BDF8), size: 18),
                ],
              ),
            ),
          ),
        ],
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
          decoration: BoxDecoration(
            color: color.withOpacity(0.14),
            borderRadius: BorderRadius.circular(20),
            border: Border.all(color: color.withOpacity(0.5), width: 1.2),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(icon, color: color, size: 15),
              const SizedBox(width: 5),
              Text(
                label,
                style: TextStyle(
                  color: color,
                  fontSize: 12,
                  fontWeight: FontWeight.bold,
                ),
              ),
              const SizedBox(width: 3),
              Icon(Icons.arrow_drop_down, color: color, size: 16),
            ],
          ),
        ),
      ),
    );
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
          'تنظیمات AVA 9',
          style: TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.bold),
        ),
        centerTitle: true,
        actions: [
          _buildModeSelector(),
        ],
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
                    Text(
                      _getModeDescription(),
                      style: const TextStyle(color: Color(0xFF94A3B8), fontSize: 11, height: 1.4),
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
                  'AVA 9 · نسخه 0.3.0+9',
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
