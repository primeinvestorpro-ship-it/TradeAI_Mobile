import 'package:flutter/material.dart';
import '../services/api_service.dart';
import '../theme/trade_theme.dart';

class StrategiesScreen extends StatefulWidget {
  const StrategiesScreen({Key? key}) : super(key: key);

  @override
  State<StrategiesScreen> createState() => _StrategiesScreenState();
}

class _StrategiesScreenState extends State<StrategiesScreen> with SingleTickerProviderStateMixin {
  final ApiService _api = ApiService();
  late TabController _tabController;
  bool _isLoading = false;
  Map<String, dynamic> _smartMoneyData = {};

  @override
  void initState() {
    super.initState();
    _tabController = TabController(length: 4, vsync: this);
    _loadData();
  }

  @override
  void dispose() {
    _tabController.dispose();
    super.dispose();
  }

  Future<void> _loadData() async {
    setState(() => _isLoading = true);
    final data = await _api.getSmartMoney();
    if (mounted) {
      setState(() {
        _smartMoneyData = data;
        _isLoading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text("Strategies E & F Matrix"),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            onPressed: _loadData,
          ),
        ],
        bottom: TabBar(
          controller: _tabController,
          indicatorColor: TradeTheme.primary,
          labelColor: TradeTheme.primary,
          unselectedLabelColor: TradeTheme.textMuted,
          isScrollable: true,
          tabs: const [
            Tab(text: "Bottom Accumulation"),
            Tab(text: "Top Distribution"),
            Tab(text: "Confirmed Signals"),
            Tab(text: "Smart Watchlist"),
          ],
        ),
      ),
      body: _isLoading
          ? const Center(child: CircularProgressIndicator(color: TradeTheme.primary))
          : TabBarView(
              controller: _tabController,
              children: [
                _buildSectionList(_smartMoneyData['bottom_accumulation'] as List<dynamic>? ?? [], isBullish: true),
                _buildSectionList(_smartMoneyData['top_distribution'] as List<dynamic>? ?? [], isBullish: false),
                _buildConfirmedSection(),
                _buildSectionList(_smartMoneyData['watchlist'] as List<dynamic>? ?? [], isBullish: true),
              ],
            ),
    );
  }

  Widget _buildConfirmedSection() {
    final buys = _smartMoneyData['buy_confirmed'] as List<dynamic>? ?? [];
    final sells = _smartMoneyData['sell_confirmed'] as List<dynamic>? ?? [];

    if (buys.isEmpty && sells.isEmpty) {
      return _buildEmptyState("No Confirmed Entries yet. Scanning F&O universe...");
    }

    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        if (buys.isNotEmpty) ...[
          const Text(
            "▲ CONFIRMED BUY / LONG SIGNALS",
            style: TextStyle(fontSize: 12, fontWeight: FontWeight.bold, color: TradeTheme.bullish),
          ),
          const SizedBox(height: 8),
          ...buys.map((item) => _buildCard(item, isBullish: true)),
          const SizedBox(height: 16),
        ],
        if (sells.isNotEmpty) ...[
          const Text(
            "▼ CONFIRMED SELL / SHORT SIGNALS",
            style: TextStyle(fontSize: 12, fontWeight: FontWeight.bold, color: TradeTheme.bearish),
          ),
          const SizedBox(height: 8),
          ...sells.map((item) => _buildCard(item, isBullish: false)),
        ],
      ],
    );
  }

  Widget _buildSectionList(List<dynamic> list, {required bool isBullish}) {
    if (list.isEmpty) {
      return _buildEmptyState("No active candidates in this phase.");
    }
    return ListView.builder(
      padding: const EdgeInsets.all(16),
      itemCount: list.length,
      itemBuilder: (context, i) {
        return _buildCard(list[i], isBullish: isBullish);
      },
    );
  }

  Widget _buildCard(dynamic data, {required bool isBullish}) {
    final sym = data['symbol'] ?? data['ticker'] ?? 'F&O SYMBOL';
    final price = data['spot_price'] ?? data['ltp'] ?? 0;
    final rsi = data['rsi'] != null ? "RSI: ${(data['rsi'] as num).toStringAsFixed(1)}" : "";
    final vwapDiff = data['vwap_diff'] != null ? "VWAP: ${(data['vwap_diff'] as num).toStringAsFixed(2)}%" : "";
    final reason = data['reason'] ?? data['notes'] ?? '';

    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: TradeTheme.surface,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(
          color: isBullish ? TradeTheme.bullish.withOpacity(0.3) : TradeTheme.bearish.withOpacity(0.3),
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(
                sym,
                style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold, color: TradeTheme.textPrimary),
              ),
              Text(
                "₹$price",
                style: const TextStyle(fontSize: 14, fontWeight: FontWeight.bold, color: TradeTheme.textPrimary),
              ),
            ],
          ),
          const SizedBox(height: 6),
          Row(
            children: [
              if (vwapDiff.isNotEmpty) ...[
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                  decoration: BoxDecoration(
                    color: Colors.white.withOpacity(0.06),
                    borderRadius: BorderRadius.circular(4),
                  ),
                  child: Text(vwapDiff, style: const TextStyle(fontSize: 11, color: TradeTheme.primary)),
                ),
                const SizedBox(width: 8),
              ],
              if (rsi.isNotEmpty)
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                  decoration: BoxDecoration(
                    color: Colors.white.withOpacity(0.06),
                    borderRadius: BorderRadius.circular(4),
                  ),
                  child: Text(rsi, style: const TextStyle(fontSize: 11, color: TradeTheme.textSecondary)),
                ),
            ],
          ),
          if (reason.isNotEmpty) ...[
            const SizedBox(height: 8),
            Text(
              reason,
              style: const TextStyle(fontSize: 12, color: TradeTheme.textMuted),
            ),
          ],
        ],
      ),
    );
  }

  Widget _buildEmptyState(String msg) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            const Icon(Icons.analytics_outlined, size: 48, color: TradeTheme.textMuted),
            const SizedBox(height: 12),
            Text(
              msg,
              style: const TextStyle(color: TradeTheme.textMuted, fontSize: 13),
              textAlign: TextAlign.center,
            ),
          ],
        ),
      ),
    );
  }
}
