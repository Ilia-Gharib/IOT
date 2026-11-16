import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'models/auth_user.dart';
import 'screens/login_screen.dart';
import 'screens/main_screen.dart';
import 'services/connection_service.dart';
import 'services/storage_service.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();

  // آغاز پایش وضعیت اتصال به سرور محلی و راه‌دور و به‌روزرسانی اولیه آدرس کلودفلر از هاست
  ConnectionService().startMonitoring();
  unawaited(ConnectionService().refreshRemoteUrlFromDiscovery());

  final AuthUser? savedUser = await StorageService.getUser();
  final String? savedToken = await StorageService.getToken();

  final bool isLoggedIn = savedUser != null && savedToken != null && savedToken.isNotEmpty;

  runApp(AvaApp(initialUser: isLoggedIn ? savedUser : null));
}

class AvaApp extends StatelessWidget {
  final AuthUser? initialUser;

  const AvaApp({super.key, this.initialUser});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'دستیار صوتی آوا',
      debugShowCheckedModeBanner: false,
      locale: const Locale('fa', 'IR'),
      supportedLocales: const [
        Locale('fa', 'IR'),
        Locale('en', 'US'),
      ],
      localizationsDelegates: const [
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      theme: ThemeData(
        useMaterial3: true,
        brightness: Brightness.dark,
        scaffoldBackgroundColor: const Color(0xFF0F172A),
        colorScheme: const ColorScheme.dark(
          primary: Color(0xFF10B981),
          secondary: Color(0xFF06B6D4),
          surface: Color(0xFF1E293B),
          error: Color(0xFFEF4444),
        ),
        appBarTheme: const AppBarTheme(
          backgroundColor: Color(0xFF1E293B),
          elevation: 0,
          centerTitle: false,
          iconTheme: IconThemeData(color: Colors.white),
        ),
      ),
      home: initialUser != null
          ? MainScreen(user: initialUser!)
          : const LoginScreen(),
    );
  }
}
