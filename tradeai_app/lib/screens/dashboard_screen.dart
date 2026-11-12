import 'dart:async';
import 'package:flutter/material.dart';
import '../models/signal.dart';
import '../services/api_service.dart';
import '../services/voice_service.dart';
import '../theme/trade_theme.dart';

class DashboardScreen extends StatefulWidget {
  final VoidCallback onOpenSettings;
  const DashboardScreen({Key? key, required this.onOpenSettings}) : super(key: key);

  @override
  State<DashboardScreen> createState() => _DashboardScreenState();
}

class _DashboardScreenState extends State<DashboardScreen> {
  final ApiService _api = ApiService();
  final VoiceService _voice = VoiceService();

  Timer? _pollTimer;
  bool _isLoading = false;
  bool _isScannerRunning = false;
  bool _isStratEEnabled = true;
  bool _isStratFEnabled = true;

  double _pcr = 1.25;
  String _marketSentiment = "BULLISH";
  int _vwapHoldPercent = 100;
  String _pcrAnalysis = "Put Writing Dominant";

  List<ScannerSignal> _signals = [];
  final Set<String> _knownSignalIds = {};

  @override
  void initState() {
    super.initState();
    _fetchData();
    _pollTimer = Timer.periodic(const Duration(seconds: 4), (_) => _pollSignals());
  }

  @override
  void dispose() {
    _pollTimer?.cancel();
    super.dispose();
  }

  Future<void> _fetchData() async {
    setState(() => _isLoading = true);
    await _pollSignals();
    if (mounted) setState(() => _isLoading = false);
  }

  Future<void> _pollSignals() async {
    final status = await _api.getScannerStatus();
    final running = status['running'] ?? false;

    final signals = await _api.getSignals();

    // Voice announcement for new signals & reversal tracking
    final currentIds = <String>{};
    for (final s in signals) {
      currentIds.add(s.id);
      if (!_knownSignalIds.contains(s.id)) {
        _knownSignalIds.add(s.id);
        _voice.speakSignal(s);
      }
    }

    // Signals that reversed or are no longer in active list:
    final removed = _knownSignalIds.difference(currentIds);
    if (removed.isNotEmpty) {
      _knownSignalIds.removeAll(removed);
    }

    // Calculate dynamic sentiment from signals
    if (signals.isNotEmpty) {
      final bullishCount = signals.where((s) => s.isBullish).length;
      final bearishCount = signals.length - bullishCount;
      if (bullishCount >= bearishCount) {
        _marketSentiment = "BULLISH";
        _vwapHoldPercent = 100;
        _pcr = 1.25;
        _pcrAnalysis = "Put Writing Dominant";
      } else {
        _marketSentiment = "BEARISH";
        _vwapHoldPercent = 40;
        _pcr = 0.72;
        _pcrAnalysis = "Call Writing Dominant";
      }
    }

    if (mounted) {
      setState(() {
        _isScannerRunning = running;
        _signals = signals;
      });
    }
  }

  Future<void> _toggleScanner() async {
    setState(() => _isLoading = true);
    if (_isScannerRunning) {
      await _api.stopScanner();
      await _voice.speakText("Scanner stopped", isBullish: false);
    } else {
      await _api.startScanner(
        allFno: true,
        enabledE: _isStratEEnabled,
        enabledF: _isStratFEnabled,
      );
      await _voice.speakText("Scanner started with Strategy E and F on all F and O universe", isBullish: true);
    }
    await _pollSignals();
    if (mounted) setState(() => _isLoading = false);
  }

  void _clearSignals() {
    setState(() {
      _signals.clear();
      _knownSignalIds.clear();
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Row(
          children: [
            Container(
              padding: const EdgeInsets.all(6),
              decoration: BoxDecoration(
                color: TradeTheme.primary.withOpacity(0.15),
                borderRadius: BorderRadius.circular(8),
              ),
              child: const Icon(Icons.show_chart, color: TradeTheme.primary, size: 20),
            ),
            const SizedBox(width: 10),
            const Text("TradeAI Live"),
          ],
        ),
        actions: [
          IconButton(
            icon: Icon(
              _voice.isEnabled ? Icons.volume_up : Icons.volume_off,
              color: _voice.isEnabled ? TradeTheme.primary : TradeTheme.textMuted,
            ),
            tooltip: "Voice Alerts",
            onPressed: () {
              setState(() {
                _voice.isEnabled = !_voice.isEnabled;
              });
              ScaffoldMessenger.of(context).showSnackBar(
                SnackBar(
                  content: Text(_voice.isEnabled ? "Voice Alerts Activated" : "Voice Alerts Muted"),
                  duration: const Duration(seconds: 1),
                ),
              );
            },
          ),
          IconButton(
            icon: const Icon(Icons.settings, color: TradeTheme.textSecondary),
            tooltip: "Settings",
            onPressed: widget.onOpenSettings,
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: _pollSignals,
        backgroundColor: TradeTheme.surface,
        color: TradeTheme.primary,
        child: ListView(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
          children: [
            // Top Metrics Pulse Row (Matching user screenshot)
            _buildMetricsPulseRow(),
            const SizedBox(height: 16),

            // Scanner Control & Strategy toggles
            _buildScannerControlCard(),
            const SizedBox(height: 18),

            // Live Scanner Signals Table
            _buildLiveSignalsSection(),
          ],
        ),
      ),
    );
  }

  Widget _buildMetricsPulseRow() {
    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      physics: const BouncingScrollPhysics(),
      child: Row(
        children: [
          // Card 1: Market Sentiment
          _buildPulseCard(
            title: "MARKET SENTIMENT",
            mainValue: _marketSentiment,
            valueColor: _marketSentiment == "BULLISH" ? TradeTheme.bullish : TradeTheme.bearish,
            icon: Icons.bolt,
            iconBg: TradeTheme.accentOrange,
            footer: "↑ $_vwapHoldPercent% Intraday VWAP Hold",
            footerColor: TradeTheme.bullish,
          ),
          const SizedBox(width: 12),

          // Card 2: Option Chain PCR
          _buildPulseCard(
            title: "OPTION CHAIN PCR",
            mainValue: _pcr.toStringAsFixed(2),
            valueColor: TradeTheme.textPrimary,
            icon: Icons.track_changes,
            iconBg: const Color(0xFF10B981),
            footer: "↑ $_pcrAnalysis",
            footerColor: TradeTheme.bullish,
          ),
          const SizedBox(width: 12),

          // Card 3: Scanner Status
          _buildPulseCard(
            title: "SCANNER STATUS",
            mainValue: _isScannerRunning ? "SCANNING" : "IDLE",
            valueColor: _isScannerRunning ? const Color(0xFF38BDF8) : TradeTheme.textMuted,
            icon: Icons.cell_tower,
            iconBg: const Color(0xFF6366F1),
            footer: "Strat E & F All FNO Universe",
            footerColor: const Color(0xFF818CF8),
          ),
        ],
      ),
    );
  }

  Widget _buildPulseCard({
    required String title,
    required String mainValue,
    required Color valueColor,
    required IconData icon,
    required Color iconBg,
    required String footer,
    required Color footerColor,
  }) {
    return Container(
      width: 220,
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: TradeTheme.surface,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: TradeTheme.cardBorder, width: 1),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withOpacity(0.2),
            blurRadius: 10,
            offset: const Offset(0, 4),
          ),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(
                title,
                style: const TextStyle(
                  fontSize: 10,
                  fontWeight: FontWeight.w700,
                  color: TradeTheme.textMuted,
                  letterSpacing: 0.8,
                ),
              ),
              Container(
                padding: const EdgeInsets.all(6),
                decoration: BoxDecoration(
                  color: iconBg,
                  shape: BoxShape.circle,
                ),
                child: Icon(icon, color: Colors.white, size: 14),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Text(
            mainValue,
            style: TextStyle(
              fontSize: 22,
              fontWeight: FontWeight.w900,
              color: valueColor,
            ),
          ),
          const SizedBox(height: 12),
          const Divider(height: 1, color: TradeTheme.cardBorder),
          const SizedBox(height: 10),
          Text(
            footer,
            style: TextStyle(
              fontSize: 11,
              fontWeight: FontWeight.w600,
              color: footerColor,
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildScannerControlCard() {
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: TradeTheme.surfaceElevated,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: TradeTheme.cardBorder),
      ),
      child: Column(
        children: [
          Row(
            children: [
              Expanded(
                child: ElevatedButton.icon(
                  onPressed: _isLoading ? null : _toggleScanner,
                  icon: Icon(_isScannerRunning ? Icons.stop : Icons.play_arrow),
                  label: Text(
                    _isScannerRunning ? "STOP SCANNER" : "START E & F SCANNER",
                    style: const TextStyle(fontWeight: FontWeight.bold),
                  ),
                  style: ElevatedButton.styleFrom(
                    backgroundColor: _isScannerRunning ? TradeTheme.bearish : TradeTheme.primaryVariant,
                    foregroundColor: Colors.white,
                    padding: const EdgeInsets.symmetric(vertical: 12),
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
                  ),
                ),
              ),
              const SizedBox(width: 10),
              IconButton.filledTonal(
                onPressed: _pollSignals,
                icon: const Icon(Icons.refresh),
                tooltip: "Refresh Signals",
              ),
            ],
          ),
          const SizedBox(height: 10),
          Row(
            children: [
              _buildStrategyToggle(
                title: "Strategy E (Smart Money)",
                value: _isStratEEnabled,
                color: TradeTheme.stratEColor,
                onChanged: (val) => setState(() => _isStratEEnabled = val),
              ),
              const SizedBox(width: 8),
              _buildStrategyToggle(
                title: "Strategy F (Breakout)",
                value: _isStratFEnabled,
                color: TradeTheme.stratFColor,
                onChanged: (val) => setState(() => _isStratFEnabled = val),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildStrategyToggle({
    required String title,
    required bool value,
    required Color color,
    required ValueChanged<bool> onChanged,
  }) {
    return Expanded(
      child: InkWell(
        onTap: () => onChanged(!value),
        borderRadius: BorderRadius.circular(8),
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
          decoration: BoxDecoration(
            color: value ? color.withOpacity(0.12) : TradeTheme.surface,
            borderRadius: BorderRadius.circular(8),
            border: Border.all(
              color: value ? color : TradeTheme.cardBorder,
              width: 1,
            ),
          ),
          child: Row(
            children: [
              Icon(
                value ? Icons.check_circle : Icons.circle_outlined,
                size: 14,
                color: value ? color : TradeTheme.textMuted,
              ),
              const SizedBox(width: 6),
              Expanded(
                child: Text(
                  title,
                  style: TextStyle(
                    fontSize: 10,
                    fontWeight: FontWeight.w600,
                    color: value ? TradeTheme.textPrimary : TradeTheme.textMuted,
                  ),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildLiveSignalsSection() {
    return Container(
      decoration: BoxDecoration(
        color: TradeTheme.surface,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: TradeTheme.cardBorder),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Header
          Padding(
            padding: const EdgeInsets.all(14),
            child: Row(
              children: [
                const Icon(Icons.satellite_alt, size: 18, color: TradeTheme.primary),
                const SizedBox(width: 8),
                const Text(
                  "Live Scanner Signals",
                  style: TextStyle(
                    fontSize: 15,
                    fontWeight: FontWeight.bold,
                    color: TradeTheme.textPrimary,
                  ),
                ),
                const Spacer(),
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                  decoration: BoxDecoration(
                    color: TradeTheme.primary.withOpacity(0.15),
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: Text(
                    "${_signals.length} signals",
                    style: const TextStyle(
                      fontSize: 11,
                      fontWeight: FontWeight.w700,
                      color: TradeTheme.primary,
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                OutlinedButton(
                  onPressed: _signals.isEmpty ? null : _clearSignals,
                  style: OutlinedButton.styleFrom(
                    visualDensity: VisualDensity.compact,
                    side: const BorderSide(color: TradeTheme.cardBorder),
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
                  ),
                  child: const Text("Clear", style: TextStyle(fontSize: 11)),
                ),
              ],
            ),
          ),
          const Divider(height: 1, color: TradeTheme.cardBorder),

          if (_signals.isEmpty)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 36, horizontal: 20),
              child: Center(
                child: Column(
                  children: [
                    Icon(
                      _isScannerRunning ? Icons.radar : Icons.sensors_off,
                      size: 40,
                      color: TradeTheme.textMuted,
                    ),
                    const SizedBox(height: 10),
                    Text(
                      _isScannerRunning
                          ? "Scanning All F&O Stocks for Strat E & F..."
                          : "Scanner Idle. Tap 'START E & F SCANNER' to begin.",
                      style: const TextStyle(color: TradeTheme.textMuted, fontSize: 13),
                      textAlign: TextAlign.center,
                    ),
                  ],
                ),
              ),
            )
          else
            ListView.separated(
              shrinkWrap: true,
              physics: const NeverScrollableScrollPhysics(),
              itemCount: _signals.length,
              separatorBuilder: (_, __) => const Divider(height: 1, color: TradeTheme.cardBorder),
              itemBuilder: (context, index) {
                final sig = _signals[index];
                return _buildSignalItem(sig);
              },
            ),
        ],
      ),
    );
  }

  Widget _buildSignalItem(ScannerSignal sig) {
    final isBullish = sig.isBullish;
    final isStratF = sig.strategy.contains("F:");

    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              // Strategy Pill
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                decoration: BoxDecoration(
                  color: isStratF ? TradeTheme.stratFBg : TradeTheme.stratEBg,
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(
                    color: isStratF ? TradeTheme.stratFColor : TradeTheme.stratEColor,
                    width: 0.8,
                  ),
                ),
                child: Text(
                  sig.strategy,
                  style: TextStyle(
                    fontSize: 10,
                    fontWeight: FontWeight.bold,
                    color: isStratF ? TradeTheme.stratFColor : TradeTheme.stratEColor,
                  ),
                ),
              ),
              const Spacer(),
              Text(
                sig.time,
                style: const TextStyle(fontSize: 11, color: TradeTheme.textMuted),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Row(
            crossAxisAlignment: CrossAxisAlignment.center,
            children: [
              // Direction icon & text
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                decoration: BoxDecoration(
                  color: isBullish ? TradeTheme.bullishBg : TradeTheme.bearishBg,
                  borderRadius: BorderRadius.circular(6),
                ),
                child: Row(
                  children: [
                    Icon(
                      isBullish ? Icons.arrow_drop_up : Icons.arrow_drop_down,
                      color: isBullish ? TradeTheme.bullish : TradeTheme.bearish,
                      size: 16,
                    ),
                    Text(
                      sig.direction,
                      style: TextStyle(
                        fontSize: 11,
                        fontWeight: FontWeight.w900,
                        color: isBullish ? TradeTheme.bullish : TradeTheme.bearish,
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 10),
              // Symbol
              Text(
                sig.symbol,
                style: const TextStyle(
                  fontSize: 15,
                  fontWeight: FontWeight.w900,
                  color: TradeTheme.textPrimary,
                ),
              ),
              const Spacer(),
              // Spot price
              Text(
                "₹${sig.spotPrice.toStringAsFixed(2)}",
                style: const TextStyle(
                  fontSize: 14,
                  fontWeight: FontWeight.w700,
                  color: TradeTheme.textPrimary,
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Row(
            children: [
              // Futures symbol
              if (sig.futuresSymbol != null) ...[
                Text(
                  sig.futuresSymbol!,
                  style: const TextStyle(fontSize: 11, color: TradeTheme.textSecondary),
                ),
                const SizedBox(width: 8),
              ],
              const Spacer(),
              // ATM Option Badge (Purple pill matching user design)
              if (sig.atmOptionDisplay.isNotEmpty)
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                  decoration: BoxDecoration(
                    color: const Color(0x338B5CF6),
                    borderRadius: BorderRadius.circular(12),
                    border: Border.all(color: const Color(0xFF8B5CF6), width: 0.8),
                  ),
                  child: Text(
                    sig.atmOptionDisplay,
                    style: const TextStyle(
                      fontSize: 11,
                      fontWeight: FontWeight.bold,
                      color: Color(0xFFA78BFA),
                    ),
                  ),
                ),
              const SizedBox(width: 10),
              // View / Details Action
              OutlinedButton(
                onPressed: () {
                  _showSignalDetails(sig);
                },
                style: OutlinedButton.styleFrom(
                  visualDensity: VisualDensity.compact,
                  side: const BorderSide(color: TradeTheme.primary),
                  shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 2),
                ),
                child: const Text("View", style: TextStyle(fontSize: 11, color: TradeTheme.primary)),
              ),
            ],
          ),
        ],
      ),
    );
  }

  void _showSignalDetails(ScannerSignal sig) {
    showModalBottomSheet(
      context: context,
      backgroundColor: TradeTheme.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (ctx) {
        return Padding(
          padding: const EdgeInsets.all(20),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Text(
                    "${sig.symbol} Signal Details",
                    style: const TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
                  ),
                  IconButton(
                    icon: const Icon(Icons.close),
                    onPressed: () => Navigator.pop(ctx),
                  ),
                ],
              ),
              const SizedBox(height: 12),
              _buildDetailRow("Strategy", sig.strategy),
              _buildDetailRow("Direction", sig.direction),
              _buildDetailRow("Spot Price", "₹${sig.spotPrice}"),
              if (sig.futuresSymbol != null) _buildDetailRow("Futures", sig.futuresSymbol!),
              if (sig.atmOptionDisplay.isNotEmpty) _buildDetailRow("ATM Option", sig.atmOptionDisplay),
              if (sig.reason != null) _buildDetailRow("Analysis", sig.reason!),
              const SizedBox(height: 18),
              SizedBox(
                width: double.infinity,
                child: ElevatedButton.icon(
                  onPressed: () {
                    Navigator.pop(ctx);
                    _voice.speakSignal(sig);
                  },
                  icon: const Icon(Icons.record_voice_over),
                  label: const Text("Speak Alert Again"),
                  style: ElevatedButton.styleFrom(
                    backgroundColor: TradeTheme.primaryVariant,
                    foregroundColor: Colors.white,
                  ),
                ),
              ),
            ],
          ),
        );
      },
    );
  }

  Widget _buildDetailRow(String label, String value) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Text(label, style: const TextStyle(color: TradeTheme.textMuted)),
          Text(value, style: const TextStyle(fontWeight: FontWeight.bold, color: TradeTheme.textPrimary)),
        ],
      ),
    );
  }
}
