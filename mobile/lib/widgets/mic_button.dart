import 'package:flutter/material.dart';

enum MicState {
  idle,
  recording,
  processing,
  playing,
}

class MicButton extends StatefulWidget {
  final MicState state;
  final VoidCallback onTap;

  const MicButton({
    super.key,
    required this.state,
    required this.onTap,
  });

  @override
  State<MicButton> createState() => _MicButtonState();
}

class _MicButtonState extends State<MicButton>
    with SingleTickerProviderStateMixin {
  late AnimationController _animController;
  late Animation<double> _scaleAnimation;

  @override
  void initState() {
    super.initState();
    _animController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1000),
    );
    _scaleAnimation = Tween<double>(begin: 1.0, end: 1.12).animate(
      CurvedAnimation(parent: _animController, curve: Curves.easeInOut),
    );
  }

  @override
  void didUpdateWidget(MicButton oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.state == MicState.recording ||
        widget.state == MicState.processing ||
        widget.state == MicState.playing) {
      if (!_animController.isAnimating) {
        _animController.repeat(reverse: true);
      }
    } else {
      _animController.stop();
      _animController.reset();
    }
  }

  @override
  void dispose() {
    _animController.dispose();
    super.dispose();
  }

  Color _getPrimaryColor() {
    switch (widget.state) {
      case MicState.recording:
        return const Color(0xFFEF4444); // Red
      case MicState.processing:
        return const Color(0xFFF59E0B); // Amber
      case MicState.playing:
        return const Color(0xFF06B6D4); // Cyan
      case MicState.idle:
        return const Color(0xFF10B981); // Emerald
    }
  }

  IconData _getIcon() {
    switch (widget.state) {
      case MicState.recording:
        return Icons.stop_rounded;
      case MicState.processing:
        return Icons.auto_awesome;
      case MicState.playing:
        return Icons.volume_up_rounded;
      case MicState.idle:
        return Icons.mic_rounded;
    }
  }

  @override
  Widget build(BuildContext context) {
    final color = _getPrimaryColor();

    return AnimatedBuilder(
      animation: _scaleAnimation,
      builder: (context, child) {
        final scale = (widget.state != MicState.idle)
            ? _scaleAnimation.value
            : 1.0;

        return Transform.scale(
          scale: scale,
          child: GestureDetector(
            onTap: widget.onTap,
            child: Container(
              width: 110,
              height: 110,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                gradient: RadialGradient(
                  colors: [
                    color.withOpacity(0.9),
                    color.withOpacity(0.4),
                  ],
                ),
                boxShadow: [
                  BoxShadow(
                    color: color.withOpacity(0.4),
                    blurRadius: 24,
                    spreadRadius: 4,
                  ),
                ],
                border: Border.all(
                  color: color.withOpacity(0.8),
                  width: 3,
                ),
              ),
              child: Center(
                child: Icon(
                  _getIcon(),
                  size: 46,
                  color: Colors.white,
                ),
              ),
            ),
          ),
        );
      },
    );
  }
}
