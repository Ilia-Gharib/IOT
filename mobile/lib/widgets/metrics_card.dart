import 'package:flutter/material.dart';
import '../models/process_audio_response.dart';

class MetricsCard extends StatelessWidget {
  final ProcessAudioResponse? response;

  const MetricsCard({super.key, this.response});

  @override
  Widget build(BuildContext context) {
    if (response == null) {
      return const SizedBox.shrink();
    }

    final resp = response!;
    final isChat = resp.isChat;

    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: const Color(0xFF1E293B),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: const Color(0xFF334155)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(
                isChat ? 'اطلاعات هوش مصنوعی' : 'جزئیات کنترل اینترنت اشیا',
                style: const TextStyle(
                  color: Color(0xFF94A3B8),
                  fontSize: 12,
                  fontWeight: FontWeight.w600,
                ),
              ),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                decoration: BoxDecoration(
                  color: isChat
                      ? const Color(0xFF06B6D4).withOpacity(0.15)
                      : const Color(0xFF10B981).withOpacity(0.15),
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Text(
                  isChat ? 'گفت‌وگو' : 'اینترنت اشیا',
                  style: TextStyle(
                    color: isChat
                        ? const Color(0xFF06B6D4)
                        : const Color(0xFF10B981),
                    fontSize: 11,
                    fontWeight: FontWeight.bold,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              Expanded(
                child: _buildMetricItem('محدوده / طبقه', resp.floorDisplay),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: _buildMetricItem('سرویس', resp.serviceDisplay),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Row(
            children: [
              Expanded(
                child: _buildMetricItem('تجهیز هدف', resp.entityDisplay),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: _buildMetricItem('وضعیت اقدام', resp.haStatusDisplay,
                    highlight: !isChat),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildMetricItem(String title, String value, {bool highlight = false}) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
      decoration: BoxDecoration(
        color: const Color(0xFF0F172A),
        borderRadius: BorderRadius.circular(10),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            title,
            style: const TextStyle(
              color: Color(0xFF64748B),
              fontSize: 10,
            ),
          ),
          const SizedBox(height: 2),
          Text(
            value,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(
              color: highlight ? const Color(0xFF10B981) : Colors.white,
              fontSize: 12,
              fontWeight: FontWeight.w600,
            ),
          ),
        ],
      ),
    );
  }
}
