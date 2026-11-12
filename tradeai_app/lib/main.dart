import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'screens/dashboard_screen.dart';
import 'screens/option_chain_screen.dart';
import 'screens/settings_screen.dart';
import 'screens/strategies_screen.dart';
import 'services/api_service.dart';
import 'services/voice_service.dart';
import 'theme/trade_theme.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  SystemChrome.setSystemUIOverlayStyle(
    const SystemUiOverlayStyle(
      statusBarColor: Colors.transparent,
      statusBarIconBrightness: Brightness.light,
      systemNavigationBarColor: TradeTheme.surface,
      systemNavigationBarIconBrightness: Brightness.light,
    ),
  );

  await ApiService().init();
  await VoiceService().init();

  runApp(const TradeAIApp());
}

class TradeAIApp extends StatelessWidget {
  const TradeAIApp({Key? key}) : super(key: key);

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'TradeAI',
      debugShowCheckedModeBanner: false,
      theme: TradeTheme.darkTheme,
      home: const MainNavigationShell(),
    );
  }
}

class MainNavigationShell extends StatefulWidget {
  const MainNavigationShell({Key? key}) : super(key: key);

  @override
  State<MainNavigationShell> createState() => _MainNavigationShellState();
}

class _MainNavigationShellState extends State<MainNavigationShell> {
  int _currentIndex = 0;

  @override
  Widget build(BuildContext context) {
    final List<Widget> screens = [
      DashboardScreen(onOpenSettings: () {
        setState(() => _currentIndex = 3);
      }),
      const StrategiesScreen(),
      const OptionChainScreen(),
      const SettingsScreen(),
    ];

    return Scaffold(
      body: IndexedStack(
        index: _currentIndex,
        children: screens,
      ),
      bottomNavigationBar: Container(
        decoration: const BoxDecoration(
          border: Border(
            top: BorderSide(color: TradeTheme.cardBorder, width: 0.8),
          ),
        ),
        child: BottomNavigationBar(
          currentIndex: _currentIndex,
          onTap: (index) => setState(() => _currentIndex = index),
          backgroundColor: TradeTheme.surface,
          selectedItemColor: TradeTheme.primary,
          unselectedItemColor: TradeTheme.textMuted,
          selectedFontSize: 11,
          unselectedFontSize: 11,
          type: BottomNavigationBarType.fixed,
          items: const [
            BottomNavigationBarItem(
              icon: Icon(Icons.show_chart),
              activeIcon: Icon(Icons.show_chart, color: TradeTheme.primary),
              label: "Live Signals",
            ),
            BottomNavigationBarItem(
              icon: Icon(Icons.grid_view_rounded),
              activeIcon: Icon(Icons.grid_view_rounded, color: TradeTheme.primary),
              label: "Strategies E&F",
            ),
            BottomNavigationBarItem(
              icon: Icon(Icons.table_chart_outlined),
              activeIcon: Icon(Icons.table_chart_outlined, color: TradeTheme.primary),
              label: "Option Chain",
            ),
            BottomNavigationBarItem(
              icon: Icon(Icons.tune),
              activeIcon: Icon(Icons.tune, color: TradeTheme.primary),
              label: "Settings",
            ),
          ],
        ),
      ),
    );
  }
}
