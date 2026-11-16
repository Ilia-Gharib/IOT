import 'package:flutter/material.dart';
import '../models/connection_mode.dart';
import '../services/connection_service.dart';

class ConnectionBadge extends StatelessWidget {
  final VoidCallback? onTap;

  const ConnectionBadge({super.key, this.onTap});

  @override
  Widget build(BuildContext context) {
    final conn = ConnectionService();

    return ValueListenableBuilder<ConnectionMode>(
      valueListenable: conn.modeNotifier,
      builder: (context, mode, _) {
        return InkWell(
          onTap: onTap ?? () => conn.checkConnection(),
          borderRadius: BorderRadius.circular(20),
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
            decoration: BoxDecoration(
              color: mode.color.withOpacity(0.12),
              borderRadius: BorderRadius.circular(20),
              border: Border.all(color: mode.color.withOpacity(0.4), width: 1),
            ),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Container(
                  width: 8,
                  height: 8,
                  decoration: BoxDecoration(
                    color: mode.color,
                    shape: BoxShape.circle,
                    boxShadow: [
                      BoxShadow(
                        color: mode.color.withOpacity(0.6),
                        blurRadius: 4,
                        spreadRadius: 1,
                      ),
                    ],
                  ),
                ),
                const SizedBox(width: 6),
                Text(
                  mode.persianLabel,
                  style: TextStyle(
                    color: mode.color,
                    fontSize: 11,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ],
            ),
          ),
        );
      },
    );
  }
}
