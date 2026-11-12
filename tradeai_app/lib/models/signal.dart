class ScannerSignal {
  final String id;
  final String symbol;
  final String strategy;
  final String direction; // 'BULLISH' or 'BEARISH'
  final double spotPrice;
  final String time;
  final String? futuresSymbol;
  final Map<String, dynamic>? atmOption;
  final double? pcr;
  final double? vwapHold;
  final String? reason;

  ScannerSignal({
    required this.id,
    required this.symbol,
    required this.strategy,
    required this.direction,
    required this.spotPrice,
    required this.time,
    this.futuresSymbol,
    this.atmOption,
    this.pcr,
    this.vwapHold,
    this.reason,
  });

  bool get isBullish => direction.toUpperCase() == 'BULLISH';

  String get atmOptionDisplay {
    if (atmOption == null) return '';
    final optType = atmOption!['option_type'] ?? (isBullish ? 'CE' : 'PE');
    final strike = atmOption!['strike'] ?? '';
    final ltp = atmOption!['ltp'];
    final ltpStr = ltp != null ? ' @ ₹${(ltp as num).toStringAsFixed(2)}' : '';
    return '$optType $strike$ltpStr';
  }

  factory ScannerSignal.fromJson(Map<String, dynamic> json) {
    final sym = json['symbol'] ?? json['ticker'] ?? 'UNKNOWN';
    final dir = json['direction'] ?? (json['strategy']?.toString().contains('Bullish') ?? true ? 'BULLISH' : 'BEARISH');
    final strat = json['strategy'] ?? 'Strategy Signal';
    final spot = (json['spot_price'] ?? json['ltp'] ?? 0.0) is num
        ? (json['spot_price'] ?? json['ltp'] ?? 0.0).toDouble()
        : double.tryParse(json['spot_price']?.toString() ?? '0') ?? 0.0;
    final timeStr = json['time'] ?? json['timestamp'] ?? '';

    return ScannerSignal(
      id: '${sym}_$dir',
      symbol: sym,
      strategy: strat,
      direction: dir,
      spotPrice: spot,
      time: timeStr,
      futuresSymbol: json['futures_symbol'],
      atmOption: json['atm_option'] is Map<String, dynamic> ? json['atm_option'] : null,
      pcr: (json['pcr'] is num) ? (json['pcr'] as num).toDouble() : null,
      vwapHold: (json['vwap_hold'] is num) ? (json['vwap_hold'] as num).toDouble() : null,
      reason: json['reason'],
    );
  }
}
