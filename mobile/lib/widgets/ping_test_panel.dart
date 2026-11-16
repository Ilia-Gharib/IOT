import 'package:flutter/material.dart';
import '../services/connection_service.dart';

class PingTestPanel extends StatefulWidget {
  const PingTestPanel({super.key});

  @override
  State<PingTestPanel> createState() => _PingTestPanelState();
}

class _PingTestPanelState extends State<PingTestPanel> {
  final _conn = ConnectionService();

  bool _isTestingOffline = false;
  PingResult? _offlineResult;

  bool _isTestingOnline = false;
  PingResult? _onlineResult;

  Future<void> _testOffline() async {
    setState(() {
      _isTestingOffline = true;
      _offlineResult = null;
    });

    final res = await _conn.pingOffline();
    if (res.success) {
      await _conn.checkConnection();
    }

    if (!mounted) return;
    setState(() {
      _isTestingOffline = false;
      _offlineResult = res;
    });
  }

  Future<void> _testOnline() async {
    setState(() {
      _isTestingOnline = true;
      _onlineResult = null;
    });

    final res = await _conn.pingOnline();
    if (res.success) {
      await _conn.checkConnection();
    }

    if (!mounted) return;
    setState(() {
      _isTestingOnline = false;
      _onlineResult = res;
    });
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: const Color(0xFF1E293B),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: const Color(0xFF334155)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const Row(
            children: [
              Icon(Icons.network_ping_rounded, color: Color(0xFF38BDF8), size: 16),
              SizedBox(width: 8),
              Text(
                'تست زنده اتصال و پینگ (Health Ping)',
                style: TextStyle(
                  color: Color(0xFFE2E8F0),
                  fontSize: 12,
                  fontWeight: FontWeight.bold,
                ),
              ),
            ],
          ),
          const SizedBox(height: 10),
          Row(
            children: [
              // 1. Offline Ping Button
              Expanded(
                child: _buildPingButton(
                  title: 'تست دسترسی آفلاین (هات‌اسپات)',
                  subtext: '192.168.137.1',
                  icon: Icons.wifi,
                  activeColor: const Color(0xFF10B981),
                  isLoading: _isTestingOffline,
                  result: _offlineResult,
                  onTap: _isTestingOffline ? null : _testOffline,
                ),
              ),
              const SizedBox(width: 8),
              // 2. Online Ping Button
              Expanded(
                child: _buildPingButton(
                  title: 'تست دسترسی آنلاین (اینترنت)',
                  subtext: 'Cloudflare Tunnel',
                  icon: Icons.public,
                  activeColor: const Color(0xFF3B82F6),
                  isLoading: _isTestingOnline,
                  result: _onlineResult,
                  onTap: _isTestingOnline ? null : _testOnline,
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildPingButton({
    required String title,
    required String subtext,
    required IconData icon,
    required Color activeColor,
    required bool isLoading,
    required PingResult? result,
    required VoidCallback? onTap,
  }) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(10),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
        decoration: BoxDecoration(
          color: activeColor.withOpacity(0.08),
          borderRadius: BorderRadius.circular(10),
          border: Border.all(
            color: result == null
                ? activeColor.withOpacity(0.3)
                : (result.success ? const Color(0xFF10B981) : const Color(0xFFEF4444)),
            width: 1,
          ),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Row(
              children: [
                Icon(icon, size: 14, color: activeColor),
                const SizedBox(width: 6),
                Expanded(
                  child: Text(
                    title,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                      color: activeColor,
                      fontSize: 11,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 4),
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text(
                  subtext,
                  style: const TextStyle(
                    color: Color(0xFF64748B),
                    fontSize: 9,
                  ),
                ),
                if (isLoading)
                  SizedBox(
                    width: 12,
                    height: 12,
                    child: CircularProgressIndicator(
                      strokeWidth: 1.5,
                      color: activeColor,
                    ),
                  )
                else if (result != null)
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 2),
                    decoration: BoxDecoration(
                      color: result.success
                          ? const Color(0xFF10B981).withOpacity(0.2)
                          : const Color(0xFFEF4444).withOpacity(0.2),
                      borderRadius: BorderRadius.circular(6),
                    ),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Icon(
                          result.success ? Icons.check_circle : Icons.cancel,
                          size: 10,
                          color: result.success ? const Color(0xFF10B981) : const Color(0xFFEF4444),
                        ),
                        const SizedBox(width: 3),
                        Text(
                          result.success ? '${result.latencyMs}ms' : 'قطع',
                          style: TextStyle(
                            color: result.success ? const Color(0xFF10B981) : const Color(0xFFEF4444),
                            fontSize: 10,
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                      ],
                    ),
                  )
                else
                  Text(
                    'لمس جهت تست',
                    style: TextStyle(
                      color: activeColor.withOpacity(0.7),
                      fontSize: 9,
                    ),
                  ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
