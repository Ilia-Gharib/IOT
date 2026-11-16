import 'dart:io';
import 'package:path_provider/path_provider.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:record/record.dart';

class AudioRecorderService {
  final AudioRecorder _recorder = AudioRecorder();
  String? _currentFilePath;

  Future<bool> checkAndRequestPermission() async {
    final status = await Permission.microphone.status;
    if (status.isGranted) return true;
    final result = await Permission.microphone.request();
    return result.isGranted;
  }

  Future<bool> isRecording() async {
    return await _recorder.isRecording();
  }

  Future<String> startRecording() async {
    final hasPerm = await checkAndRequestPermission();
    if (!hasPerm) {
      throw Exception('مجوز استفاده از میکروفون صادر نشده است.');
    }

    final tempDir = await getTemporaryDirectory();
    final fileName = 'rec_${DateTime.now().millisecondsSinceEpoch}.m4a';
    final filePath = '${tempDir.path}/$fileName';
    _currentFilePath = filePath;

    await _recorder.start(
      const RecordConfig(
        encoder: AudioEncoder.aacLc,
        bitRate: 128000,
        sampleRate: 44100,
      ),
      path: filePath,
    );

    return filePath;
  }

  Future<File?> stopRecording() async {
    final path = await _recorder.stop();
    final actualPath = path ?? _currentFilePath;
    if (actualPath != null && File(actualPath).existsSync()) {
      return File(actualPath);
    }
    return null;
  }

  Future<void> dispose() async {
    await _recorder.dispose();
  }
}
