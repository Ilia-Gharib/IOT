import 'package:flutter_test/flutter_test.dart';
import 'package:ava_mobile/services/wake_word_service.dart';

void main() {
  group('WakeWordService containsWakeWord tests', () {
    test('detects Persian wake words and homophones', () {
      expect(WakeWordService.containsWakeWord('هی آوا'), isTrue);
      expect(WakeWordService.containsWakeWord('هی اوا'), isTrue);
      expect(WakeWordService.containsWakeWord('هی‌اوا'), isTrue);
      expect(WakeWordService.containsWakeWord('هیاوا'), isTrue);
      expect(WakeWordService.containsWakeWord('ای آوا'), isTrue);
      expect(WakeWordService.containsWakeWord('ای اوا'), isTrue);
      expect(WakeWordService.containsWakeWord('های آوا'), isTrue);
      expect(WakeWordService.containsWakeWord('های اوا'), isTrue);
      expect(WakeWordService.containsWakeWord('هی هوا'), isTrue);
      expect(WakeWordService.containsWakeWord('هوا'), isTrue);
      expect(WakeWordService.containsWakeWord('اووا'), isTrue);
      expect(WakeWordService.containsWakeWord('آبا'), isTrue);
      expect(WakeWordService.containsWakeWord('آوا چراغ سالن رو روشن کن'), isTrue);
      expect(WakeWordService.containsWakeWord('سلام آوا خوبی'), isTrue);
      expect(WakeWordService.containsWakeWord('اوا دمای اتاق چنده'), isTrue);
    });

    test('detects English wake words', () {
      expect(WakeWordService.containsWakeWord('hey ava'), isTrue);
      expect(WakeWordService.containsWakeWord('heyava'), isTrue);
      expect(WakeWordService.containsWakeWord('Hey Ava turn on the lights'), isTrue);
      expect(WakeWordService.containsWakeWord('ava'), isTrue);
      expect(WakeWordService.containsWakeWord('Ava what time is it'), isTrue);
    });

    test('does not falsely trigger on unrelated non-wake words', () {
      expect(WakeWordService.containsWakeWord('دعوا نکنید'), isFalse);
      expect(WakeWordService.containsWakeWord('hello world'), isFalse);
      expect(WakeWordService.containsWakeWord(''), isFalse);
    });
  });
}
