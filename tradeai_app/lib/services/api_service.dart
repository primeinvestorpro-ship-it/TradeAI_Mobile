import 'dart:async';
import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import '../models/signal.dart';

class ApiService {
  static final ApiService _instance = ApiService._internal();
  factory ApiService() => _instance;
  ApiService._internal();

  static const String defaultRenderUrl = "https://tradeai-mobile.onrender.com";
  static const String defaultLocalUrl = "http://192.168.1.43:5050";

  String _baseUrl = defaultRenderUrl;
  String get baseUrl => _baseUrl;

  Future<void> init() async {
    final prefs = await SharedPreferences.getInstance();
    _baseUrl = prefs.getString('server_url') ?? defaultRenderUrl;
  }

  Future<void> setBaseUrl(String url) async {
    var clean = url.trim();
    if (clean.endsWith('/')) {
      clean = clean.substring(0, clean.length - 1);
    }
    _baseUrl = clean;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('server_url', clean);
  }

  Uri _uri(String path, [Map<String, String>? queryParams]) {
    final urlStr = '$_baseUrl$path';
    return Uri.parse(urlStr).replace(queryParameters: queryParams);
  }

  Future<int?> testPing(String targetUrl) async {
    final stopwatch = Stopwatch()..start();
    try {
      var clean = targetUrl.trim();
      if (clean.endsWith('/')) {
        clean = clean.substring(0, clean.length - 1);
      }
      final uri = Uri.parse('$clean/api/status');
      final resp = await http.get(uri).timeout(const Duration(seconds: 4));
      stopwatch.stop();
      if (resp.statusCode == 200) {
        return stopwatch.elapsedMilliseconds;
      }
      return null;
    } catch (_) {
      return null;
    }
  }

  Future<Map<String, dynamic>> getStatus() async {
    try {
      final resp = await http.get(_uri('/api/status')).timeout(const Duration(seconds: 5));
      if (resp.statusCode == 200) {
        return jsonDecode(resp.body) as Map<String, dynamic>;
      }
    } catch (_) {}
    return {'logged_in': false, 'running': false};
  }

  Future<Map<String, dynamic>> getScannerStatus() async {
    try {
      final resp = await http.get(_uri('/api/scanner/status')).timeout(const Duration(seconds: 5));
      if (resp.statusCode == 200) {
        return jsonDecode(resp.body) as Map<String, dynamic>;
      }
    } catch (_) {}
    return {'running': false, 'signal_count': 0};
  }

  Future<List<ScannerSignal>> getSignals() async {
    try {
      final resp = await http.get(_uri('/api/scanner/signals')).timeout(const Duration(seconds: 6));
      if (resp.statusCode == 200) {
        final data = jsonDecode(resp.body) as Map<String, dynamic>;
        final list = (data['signals'] as List<dynamic>?) ?? [];
        return list.map((item) => ScannerSignal.fromJson(item as Map<String, dynamic>)).toList();
      }
    } catch (_) {}
    return [];
  }

  Future<bool> startScanner({
    bool allFno = true,
    bool enabledE = true,
    bool enabledF = true,
  }) async {
    try {
      final resp = await http.post(
        _uri('/api/scanner/start'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({
          'all_fno': allFno,
          'enabled_e': enabledE,
          'enabled_f': enabledF,
        }),
      ).timeout(const Duration(seconds: 7));
      return resp.statusCode == 200;
    } catch (_) {
      return false;
    }
  }

  Future<bool> stopScanner() async {
    try {
      final resp = await http.post(_uri('/api/scanner/stop')).timeout(const Duration(seconds: 5));
      return resp.statusCode == 200;
    } catch (_) {
      return false;
    }
  }

  Future<Map<String, dynamic>> getSmartMoney() async {
    try {
      final resp = await http.get(_uri('/api/scanner/smart_money')).timeout(const Duration(seconds: 6));
      if (resp.statusCode == 200) {
        final data = jsonDecode(resp.body);
        return (data['smart_money'] as Map<String, dynamic>?) ?? {};
      }
    } catch (_) {}
    return {};
  }

  Future<Map<String, dynamic>> getOptionChain(String ticker) async {
    try {
      final resp = await http.get(_uri('/api/option_chain', {'ticker': ticker})).timeout(const Duration(seconds: 6));
      if (resp.statusCode == 200) {
        final data = jsonDecode(resp.body);
        return (data['option_chain'] as Map<String, dynamic>?) ?? {};
      }
    } catch (_) {}
    return {};
  }

  Future<Map<String, dynamic>> getLastAnalysis() async {
    try {
      final resp = await http.get(_uri('/api/last_analysis')).timeout(const Duration(seconds: 5));
      if (resp.statusCode == 200) {
        return jsonDecode(resp.body) as Map<String, dynamic>;
      }
    } catch (_) {}
    return {};
  }
}
