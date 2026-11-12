import 'package:flutter/material.dart';

class TradeTheme {
  // Brand colors
  static const Color background = Color(0xFF0A0E17);
  static const Color surface = Color(0xFF131B2E);
  static const Color surfaceElevated = Color(0xFF1E293B);
  static const Color cardBorder = Color(0xFF2A374E);

  // Bullish / Bearish
  static const Color bullish = Color(0xFF10B981);
  static const Color bullishBg = Color(0x1A10B981);
  static const Color bearish = Color(0xFFEF4444);
  static const Color bearishBg = Color(0x1AEF4444);

  // Accents
  static const Color primary = Color(0xFF38BDF8);
  static const Color primaryVariant = Color(0xFF0284C7);
  static const Color accentPurple = Color(0xFF8B5CF6);
  static const Color accentOrange = Color(0xFFF97316);

  // Strategy badge colors
  static const Color stratFColor = Color(0xFF06B6D4); // Cyan
  static const Color stratFBg = Color(0x2206B6D4);
  static const Color stratEColor = Color(0xFFF43F5E); // Coral / Rose
  static const Color stratEBg = Color(0x22F43F5E);

  // Typography colors
  static const Color textPrimary = Color(0xFFF8FAFC);
  static const Color textSecondary = Color(0xFF94A3B8);
  static const Color textMuted = Color(0xFF64748B);

  static ThemeData get darkTheme {
    return ThemeData(
      brightness: Brightness.dark,
      scaffoldBackgroundColor: background,
      primaryColor: primary,
      cardColor: surface,
      colorScheme: const ColorScheme.dark(
        primary: primary,
        secondary: accentPurple,
        surface: surface,
        error: bearish,
      ),
      appBarTheme: const AppBarTheme(
        backgroundColor: surface,
        elevation: 0,
        centerTitle: false,
        titleTextStyle: TextStyle(
          color: textPrimary,
          fontSize: 18,
          fontWeight: FontWeight.bold,
          letterSpacing: 0.5,
        ),
        iconTheme: IconThemeData(color: textPrimary),
      ),
      bottomNavigationBarTheme: const BottomNavigationBarThemeData(
        backgroundColor: surface,
        selectedItemColor: primary,
        unselectedItemColor: textMuted,
        type: BottomNavigationBarType.fixed,
        elevation: 8,
      ),
      fontFamily: 'Segoe UI',
    );
  }
}
