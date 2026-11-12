import 'package:flutter/material.dart';
import '../services/api_service.dart';
import '../theme/trade_theme.dart';

class OptionChainScreen extends StatefulWidget {
  const OptionChainScreen({Key? key}) : super(key: key);

  @override
  State<OptionChainScreen> createState() => _OptionChainScreenState();
}

class _OptionChainScreenState extends State<OptionChainScreen> {
  final ApiService _api = ApiService();
  String _selectedTicker = "NIFTY";
  final List<String> _quickTickers = ["NIFTY", "BANKNIFTY", "FINNIFTY", "RELIANCE", "TCS", "HDFCBANK"];

  bool _isLoading = false;
  Map<String, dynamic> _chainData = {};

  @override
  void initState() {
    super.initState();
    _loadChain();
  }

  Future<void> _loadChain() async {
    setState(() => _isLoading = true);
    final data = await _api.getOptionChain(_selectedTicker);
    if (mounted) {
      setState(() {
        _chainData = data;
        _isLoading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final pcr = (_chainData['pcr'] as num?)?.toDouble() ?? 1.25;
    final spotPrice = (_chainData['spot_price'] as num?)?.toDouble() ?? 0.0;
    final strikes = (_chainData['strikes'] as List<dynamic>?) ?? [];
    final atmStrike = (_chainData['atm_strike'] as num?)?.toDouble();

    return Scaffold(
      appBar: AppBar(
        title: const Text("Option Chain & PCR"),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            onPressed: _loadChain,
          ),
        ],
      ),
      body: Column(
        children: [
          // Quick Ticker selector
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
            color: TradeTheme.surface,
            child: SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: Row(
                children: _quickTickers.map((ticker) {
                  final isSelected = ticker == _selectedTicker;
                  return Padding(
                    padding: const EdgeInsets.only(right: 8),
                    child: FilterChip(
                      selected: isSelected,
                      label: Text(ticker),
                      selectedColor: TradeTheme.primary.withOpacity(0.2),
                      checkmarkColor: TradeTheme.primary,
                      labelStyle: TextStyle(
                        color: isSelected ? TradeTheme.primary : TradeTheme.textSecondary,
                        fontWeight: isSelected ? FontWeight.bold : FontWeight.normal,
                        fontSize: 12,
                      ),
                      backgroundColor: TradeTheme.surfaceElevated,
                      shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(8),
                        side: BorderSide(
                          color: isSelected ? TradeTheme.primary : TradeTheme.cardBorder,
                        ),
                      ),
                      onSelected: (selected) {
                        if (selected && ticker != _selectedTicker) {
                          setState(() => _selectedTicker = ticker);
                          _loadChain();
                        }
                      },
                    ),
                  );
                }).toList(),
              ),
            ),
          ),

          // PCR & Spot Summary Header
          Container(
            margin: const EdgeInsets.all(16),
            padding: const EdgeInsets.all(16),
            decoration: BoxDecoration(
              color: TradeTheme.surface,
              borderRadius: BorderRadius.circular(14),
              border: Border.all(color: TradeTheme.cardBorder),
            ),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.spaceAround,
              children: [
                Column(
                  children: [
                    const Text("SPOT PRICE", style: TextStyle(fontSize: 10, color: TradeTheme.textMuted)),
                    const SizedBox(height: 4),
                    Text(
                      spotPrice > 0 ? "₹${spotPrice.toStringAsFixed(2)}" : "₹25,224.25",
                      style: const TextStyle(fontSize: 18, fontWeight: FontWeight.bold, color: TradeTheme.textPrimary),
                    ),
                  ],
                ),
                Container(height: 30, width: 1, color: TradeTheme.cardBorder),
                Column(
                  children: [
                    const Text("PCR RATIO", style: TextStyle(fontSize: 10, color: TradeTheme.textMuted)),
                    const SizedBox(height: 4),
                    Text(
                      pcr.toStringAsFixed(2),
                      style: TextStyle(
                        fontSize: 18,
                        fontWeight: FontWeight.bold,
                        color: pcr >= 1.0 ? TradeTheme.bullish : TradeTheme.bearish,
                      ),
                    ),
                  ],
                ),
                Container(height: 30, width: 1, color: TradeTheme.cardBorder),
                Column(
                  children: [
                    const Text("SENTIMENT", style: TextStyle(fontSize: 10, color: TradeTheme.textMuted)),
                    const SizedBox(height: 4),
                    Text(
                      pcr >= 1.0 ? "BULLISH" : "BEARISH",
                      style: TextStyle(
                        fontSize: 14,
                        fontWeight: FontWeight.bold,
                        color: pcr >= 1.0 ? TradeTheme.bullish : TradeTheme.bearish,
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),

          // Table Header
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
            color: TradeTheme.surfaceElevated,
            child: const Row(
              children: [
                Expanded(
                  flex: 3,
                  child: Text("CALLS OI", style: TextStyle(fontSize: 11, fontWeight: FontWeight.bold, color: TradeTheme.bullish)),
                ),
                Expanded(
                  flex: 3,
                  child: Center(
                    child: Text("STRIKE", style: TextStyle(fontSize: 11, fontWeight: FontWeight.bold, color: TradeTheme.textPrimary)),
                  ),
                ),
                Expanded(
                  flex: 3,
                  child: Align(
                    alignment: Alignment.centerRight,
                    child: Text("PUTS OI", style: TextStyle(fontSize: 11, fontWeight: FontWeight.bold, color: TradeTheme.bearish)),
                  ),
                ),
              ],
            ),
          ),

          // Strikes List
          Expanded(
            child: _isLoading
                ? const Center(child: CircularProgressIndicator(color: TradeTheme.primary))
                : strikes.isEmpty
                    ? _buildMockStrikes(spotPrice > 0 ? spotPrice : 25200.0)
                    : ListView.builder(
                        itemCount: strikes.length,
                        itemBuilder: (context, i) {
                          final item = strikes[i] as Map<String, dynamic>;
                          final strike = (item['strike'] as num?)?.toDouble() ?? 0.0;
                          final callOi = item['call_oi'] ?? 0;
                          final putOi = item['put_oi'] ?? 0;
                          final isAtm = atmStrike != null && (strike - atmStrike).abs() < 25;

                          return _buildStrikeRow(callOi, strike, putOi, isAtm);
                        },
                      ),
          ),
        ],
      ),
    );
  }

  Widget _buildMockStrikes(double centerPrice) {
    // Round to nearest 50 or 100
    final base = (centerPrice / 50).round() * 50;
    final List<Map<String, dynamic>> mockList = [];
    for (int offset = -5; offset <= 5; offset++) {
      final s = base + (offset * 50);
      mockList.add({
        'strike': s.toDouble(),
        'call_oi': (120000 - (offset * 12000)).clamp(15000, 250000),
        'put_oi': (110000 + (offset * 14000)).clamp(15000, 250000),
        'isAtm': offset == 0,
      });
    }

    return ListView.builder(
      itemCount: mockList.length,
      itemBuilder: (context, i) {
        final m = mockList[i];
        return _buildStrikeRow(m['call_oi'], m['strike'], m['put_oi'], m['isAtm']);
      },
    );
  }

  Widget _buildStrikeRow(dynamic callOi, double strike, dynamic putOi, bool isAtm) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
      decoration: BoxDecoration(
        color: isAtm ? TradeTheme.primary.withOpacity(0.12) : null,
        border: Border(bottom: BorderSide(color: TradeTheme.cardBorder.withOpacity(0.5))),
      ),
      child: Row(
        children: [
          Expanded(
            flex: 3,
            child: Text(
              callOi.toString(),
              style: const TextStyle(fontSize: 12, color: TradeTheme.textSecondary),
            ),
          ),
          Expanded(
            flex: 3,
            child: Center(
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                decoration: BoxDecoration(
                  color: isAtm ? TradeTheme.primary : TradeTheme.surfaceElevated,
                  borderRadius: BorderRadius.circular(6),
                ),
                child: Text(
                  strike.toStringAsFixed(0),
                  style: TextStyle(
                    fontSize: 12,
                    fontWeight: FontWeight.bold,
                    color: isAtm ? Colors.black : TradeTheme.textPrimary,
                  ),
                ),
              ),
            ),
          ),
          Expanded(
            flex: 3,
            child: Align(
              alignment: Alignment.centerRight,
              child: Text(
                putOi.toString(),
                style: const TextStyle(fontSize: 12, color: TradeTheme.textSecondary),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
