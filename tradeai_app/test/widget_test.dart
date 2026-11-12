import 'package:flutter_test/flutter_test.dart';
import 'package:tradeai_app/main.dart';

void main() {
  testWidgets('TradeAI smoke test', (WidgetTester tester) async {
    await tester.pumpWidget(const TradeAIApp());
    expect(find.text('Live Signals'), findsOneWidget);
  });
}
