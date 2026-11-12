import 'package:flutter/material.dart';
import '../services/api_service.dart';
import '../services/voice_service.dart';
import '../theme/trade_theme.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({Key? key}) : super(key: key);

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  final ApiService _api = ApiService();
  final VoiceService _voice = VoiceService();

  late TextEditingController _urlController;
  final TextEditingController _tokenController = TextEditingController();

  bool _isTesting = false;
  int? _latencyMs;
  bool _isLoggedIn = false;
  String _userName = "Checking...";

  @override
  void initState() {
    super.initState();
    _urlController = TextEditingController(text: _api.baseUrl);
    _checkStatus();
  }

  @override
  void dispose() {
    _urlController.dispose();
    _tokenController.dispose();
    super.dispose();
  }

  Future<void> _checkStatus() async {
    final status = await _api.getStatus();
    if (mounted) {
      setState(() {
        _isLoggedIn = status['logged_in'] ?? false;
        _userName = status['user_name'] ?? (_isLoggedIn ? "Kite User" : "Not Connected");
      });
    }
  }

  Future<void> _testConnection() async {
    setState(() {
      _isTesting = true;
      _latencyMs = null;
    });

    final target = _urlController.text.trim();
    final latency = await _api.testPing(target);

    if (mounted) {
      setState(() {
        _isTesting = false;
        _latencyMs = latency;
      });

      if (latency != null) {
        await _api.setBaseUrl(target);
        await _checkStatus();
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text("Connected successfully! Latency: ${latency}ms"),
            backgroundColor: TradeTheme.bullish,
          ),
        );
      } else {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text("Connection failed. Check URL or network."),
            backgroundColor: TradeTheme.bearish,
          ),
        );
      }
    }
  }

  void _selectPreset(String url) {
    _urlController.text = url;
    _testConnection();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text("Connection & Settings"),
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          // Connection Preset Cards
          _buildCard(
            title: "BACKEND SERVER CONNECTION",
            icon: Icons.cloud_sync,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(
                  "Select where your TradeAI engine is deployed:",
                  style: TextStyle(fontSize: 12, color: TradeTheme.textMuted),
                ),
                const SizedBox(height: 12),
                Row(
                  children: [
                    Expanded(
                      child: OutlinedButton(
                        onPressed: () => _selectPreset(ApiService.defaultRenderUrl),
                        style: OutlinedButton.styleFrom(
                          side: BorderSide(
                            color: _urlController.text.contains("onrender.com")
                                ? TradeTheme.primary
                                : TradeTheme.cardBorder,
                          ),
                          backgroundColor: _urlController.text.contains("onrender.com")
                              ? TradeTheme.primary.withOpacity(0.12)
                              : TradeTheme.surfaceElevated,
                          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
                          padding: const EdgeInsets.symmetric(vertical: 12),
                        ),
                        child: const Text("Render Cloud", style: TextStyle(fontSize: 12)),
                      ),
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: OutlinedButton(
                        onPressed: () => _selectPreset(ApiService.defaultLocalUrl),
                        style: OutlinedButton.styleFrom(
                          side: BorderSide(
                            color: _urlController.text.contains("192.168")
                                ? TradeTheme.primary
                                : TradeTheme.cardBorder,
                          ),
                          backgroundColor: _urlController.text.contains("192.168")
                              ? TradeTheme.primary.withOpacity(0.12)
                              : TradeTheme.surfaceElevated,
                          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
                          padding: const EdgeInsets.symmetric(vertical: 12),
                        ),
                        child: const Text("Local Wi-Fi", style: TextStyle(fontSize: 12)),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 14),
                TextField(
                  controller: _urlController,
                  decoration: InputDecoration(
                    labelText: "Server Base URL",
                    labelStyle: const TextStyle(color: TradeTheme.textMuted, fontSize: 13),
                    filled: true,
                    fillColor: TradeTheme.surfaceElevated,
                    border: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(10),
                      borderSide: const BorderSide(color: TradeTheme.cardBorder),
                    ),
                    suffixIcon: _latencyMs != null
                        ? Padding(
                            padding: const EdgeInsets.all(12),
                            child: Text(
                              "${_latencyMs}ms",
                              style: const TextStyle(
                                color: TradeTheme.bullish,
                                fontWeight: FontWeight.bold,
                                fontSize: 12,
                              ),
                            ),
                          )
                        : null,
                  ),
                ),
                const SizedBox(height: 12),
                SizedBox(
                  width: double.infinity,
                  child: ElevatedButton.icon(
                    onPressed: _isTesting ? null : _testConnection,
                    icon: _isTesting
                        ? const SizedBox(
                            width: 16,
                            height: 16,
                            child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                          )
                        : const Icon(Icons.network_check),
                    label: Text(_isTesting ? "Testing..." : "Test Connection & Save"),
                    style: ElevatedButton.styleFrom(
                      backgroundColor: TradeTheme.primaryVariant,
                      foregroundColor: Colors.white,
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
                      padding: const EdgeInsets.symmetric(vertical: 12),
                    ),
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 16),

          // Kite Connect Auth Status
          _buildCard(
            title: "ZERODHA KITE STATUS",
            icon: Icons.shield,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Row(
                      children: [
                        Icon(
                          _isLoggedIn ? Icons.check_circle : Icons.warning_amber,
                          color: _isLoggedIn ? TradeTheme.bullish : TradeTheme.accentOrange,
                          size: 18,
                        ),
                        const SizedBox(width: 8),
                        Text(
                          _isLoggedIn ? "Connected" : "Not Logged In",
                          style: TextStyle(
                            fontWeight: FontWeight.bold,
                            color: _isLoggedIn ? TradeTheme.bullish : TradeTheme.accentOrange,
                          ),
                        ),
                      ],
                    ),
                    Text(
                      _userName,
                      style: const TextStyle(fontWeight: FontWeight.w600, color: TradeTheme.textPrimary),
                    ),
                  ],
                ),
                const SizedBox(height: 12),
                const Text(
                  "Your Kite session is managed automatically through the server token.json cache.",
                  style: TextStyle(fontSize: 11, color: TradeTheme.textMuted),
                ),
              ],
            ),
          ),
          const SizedBox(height: 16),

          // Voice Alerts & Speech Config
          _buildCard(
            title: "VOICE SIGNALS & AUDIO",
            icon: Icons.volume_up,
            child: Column(
              children: [
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text("Live Voice Announcements", style: TextStyle(fontSize: 14)),
                  subtitle: const Text(
                    "Speaks scrip name & ATM option when a signal triggers",
                    style: TextStyle(fontSize: 11, color: TradeTheme.textMuted),
                  ),
                  value: _voice.isEnabled,
                  activeColor: TradeTheme.primary,
                  onChanged: (val) {
                    setState(() => _voice.isEnabled = val);
                  },
                ),
                const SizedBox(height: 8),
                Row(
                  children: [
                    Expanded(
                      child: OutlinedButton.icon(
                        onPressed: () {
                          _voice.speakText("Bullish signal on NIFTY! Buy 25200 Call at 180 rupees.", isBullish: true);
                        },
                        icon: const Icon(Icons.play_circle_outline, color: TradeTheme.bullish),
                        label: const Text("Test Buy Voice", style: TextStyle(fontSize: 12)),
                        style: OutlinedButton.styleFrom(
                          side: const BorderSide(color: TradeTheme.bullish),
                          foregroundColor: TradeTheme.bullish,
                        ),
                      ),
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: OutlinedButton.icon(
                        onPressed: () {
                          _voice.speakText("Bearish signal on BANKNIFTY! Sell 52000 Put at 240 rupees.", isBullish: false);
                        },
                        icon: const Icon(Icons.play_circle_outline, color: TradeTheme.bearish),
                        label: const Text("Test Sell Voice", style: TextStyle(fontSize: 12)),
                        style: OutlinedButton.styleFrom(
                          side: const BorderSide(color: TradeTheme.bearish),
                          foregroundColor: TradeTheme.bearish,
                        ),
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildCard({
    required String title,
    required IconData icon,
    required Widget child,
  }) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: TradeTheme.surface,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: TradeTheme.cardBorder),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(icon, size: 16, color: TradeTheme.primary),
              const SizedBox(width: 8),
              Text(
                title,
                style: const TextStyle(
                  fontSize: 11,
                  fontWeight: FontWeight.bold,
                  color: TradeTheme.primary,
                  letterSpacing: 0.8,
                ),
              ),
            ],
          ),
          const SizedBox(height: 12),
          child,
        ],
      ),
    );
  }
}
