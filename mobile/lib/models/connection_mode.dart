import 'package:flutter/material.dart';

enum ConnectionMode {
  local,
  remote,
  disconnected,
}

extension ConnectionModeExtension on ConnectionMode {
  String get persianLabel {
    switch (this) {
      case ConnectionMode.local:
        return 'متصل - هات‌اسپات / شبکه داخلی';
      case ConnectionMode.remote:
        return 'متصل - راه‌دور (کلودفلر)';
      case ConnectionMode.disconnected:
        return 'قطع ارتباط با سرور';
    }
  }

  Color get color {
    switch (this) {
      case ConnectionMode.local:
        return const Color(0xFF22C55E); // Green
      case ConnectionMode.remote:
        return const Color(0xFF3B82F6); // Blue
      case ConnectionMode.disconnected:
        return const Color(0xFFEF4444); // Red
    }
  }

  IconData get icon {
    switch (this) {
      case ConnectionMode.local:
        return Icons.wifi;
      case ConnectionMode.remote:
        return Icons.cloud_done;
      case ConnectionMode.disconnected:
        return Icons.wifi_off;
    }
  }
}
