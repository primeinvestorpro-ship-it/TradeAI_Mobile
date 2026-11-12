import 'package:flutter_tts/flutter_tts.dart';
import '../models/signal.dart';

class VoiceService {
  static final VoiceService _instance = VoiceService._internal();
  factory VoiceService() => _instance;
  VoiceService._internal();

  final FlutterTts _tts = FlutterTts();
  bool _isInitialized = false;
  bool isEnabled = true;
  double volume = 1.0;
  double speechRate = 0.5;

  Future<void> init() async {
    if (_isInitialized) return;
    try {
      await _tts.setLanguage("en-IN");
      await _tts.setSpeechRate(speechRate);
      await _tts.setVolume(volume);
      await _tts.setPitch(1.0);
      _isInitialized = true;
    } catch (_) {
      try {
        await _tts.setLanguage("en-US");
        _isInitialized = true;
      } catch (_) {}
    }
  }

  Future<void> speakSignal(ScannerSignal signal) async {
    if (!isEnabled) return;
    await init();

    final isBullish = signal.isBullish;
    final dirText = isBullish ? "Buy Call" : "Sell Put";
    final symbolClean = signal.symbol.replaceAll('_', ' ');

    String optText = "";
    if (signal.atmOption != null) {
      final strike = signal.atmOption!['strike'] ?? '';
      final optType = signal.atmOption!['option_type'] ?? '';
      final ltp = signal.atmOption!['ltp'];
      if (strike != '') {
        optText = " $strike $optType";
      }
      if (ltp != null) {
        optText += " at ${(ltp as num).toStringAsFixed(1)} rupees";
      }
    }

    final speech = "$dirText signal on $symbolClean.$optText";

    try {
      // Use different pitch for Buy vs Sell
      if (isBullish) {
        await _tts.setPitch(1.2); // Higher, energetic pitch for Bullish
      } else {
        await _tts.setPitch(0.85); // Deeper, serious pitch for Bearish
      }
      await _tts.stop();
      await _tts.speak(speech);
    } catch (_) {}
  }

  Future<void> speakText(String text, {bool isBullish = true}) async {
    if (!isEnabled) return;
    await init();
    try {
      await _tts.setPitch(isBullish ? 1.2 : 0.85);
      await _tts.stop();
      await _tts.speak(text);
    } catch (_) {}
  }

  Future<void> stop() async {
    try {
      await _tts.stop();
    } catch (_) {}
  }
}
