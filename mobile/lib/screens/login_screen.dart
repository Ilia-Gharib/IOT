import 'package:flutter/material.dart';
import '../models/auth_user.dart';
import '../services/api_service.dart';
import '../services/storage_service.dart';
import '../widgets/connection_badge.dart';
import 'main_screen.dart';
import 'settings_screen.dart';

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  // Login Controllers
  final _usernameController = TextEditingController();
  final _passwordController = TextEditingController();

  // Registration Controllers
  final _fullNameController = TextEditingController();
  final _phoneController = TextEditingController();
  final _registerEmailController = TextEditingController();
  final _registerPasswordController = TextEditingController();
  final _floorCodeController = TextEditingController();

  final _api = ApiService();

  bool _isRegisterMode = false;
  bool _isLoading = false;
  bool _obscurePassword = true;
  bool _obscureRegisterPassword = true;
  String? _errorMessage;

  @override
  void initState() {
    super.initState();
    _loadInitialData();
  }

  Future<void> _loadInitialData() async {
    final lastUser = await StorageService.getLastUsername();
    if (mounted) {
      _usernameController.text = lastUser;
    }
  }

  @override
  void dispose() {
    _usernameController.dispose();
    _passwordController.dispose();
    _fullNameController.dispose();
    _phoneController.dispose();
    _registerEmailController.dispose();
    _registerPasswordController.dispose();
    _floorCodeController.dispose();
    super.dispose();
  }

  Future<void> _handleLogin({String? overrideUser, String? overridePass}) async {
    final u = overrideUser ?? _usernameController.text.trim();
    final p = overridePass ?? _passwordController.text;

    if (u.isEmpty || p.isEmpty) {
      setState(() {
        _errorMessage = 'لطفاً نام کاربری و رمز عبور را وارد کنید.';
      });
      return;
    }

    setState(() {
      _isLoading = true;
      _errorMessage = null;
    });

    try {
      final AuthUser user = await _api.login(u, p);
      await StorageService.saveAuth(user);

      if (!mounted) return;
      Navigator.pushReplacement(
        context,
        MaterialPageRoute(builder: (_) => MainScreen(user: user)),
      );
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _errorMessage = e.toString().replaceFirst('Exception: ', '');
      });
    } finally {
      if (mounted) {
        setState(() {
          _isLoading = false;
        });
      }
    }
  }

  Future<void> _handleRegister() async {
    final fullName = _fullNameController.text.trim();
    final phone = _phoneController.text.trim();
    final email = _registerEmailController.text.trim();
    final password = _registerPasswordController.text;
    final floorCode = _floorCodeController.text.trim();

    if (fullName.isEmpty) {
      setState(() => _errorMessage = 'لطفاً نام و نام خانوادگی را وارد کنید.');
      return;
    }
    if (fullName.length < 3) {
      setState(() => _errorMessage = 'نام و نام خانوادگی باید حداقل ۳ کاراکتر باشد.');
      return;
    }
    if (phone.isEmpty) {
      setState(() => _errorMessage = 'لطفاً شماره موبایل را وارد کنید.');
      return;
    }
    if (phone.length < 10) {
      setState(() => _errorMessage = 'شماره موبایل وارد شده معتبر نیست (حداقل ۱۰ رقم).');
      return;
    }
    if (email.isEmpty) {
      setState(() => _errorMessage = 'لطفاً آدرس ایمیل را وارد کنید.');
      return;
    }
    if (!email.contains('@') || !email.contains('.')) {
      setState(() => _errorMessage = 'فرمت آدرس ایمیل نامعتبر است.');
      return;
    }
    if (password.isEmpty) {
      setState(() => _errorMessage = 'لطفاً رمز عبور را وارد کنید.');
      return;
    }
    if (password.length < 6) {
      setState(() => _errorMessage = 'رمز عبور باید حداقل ۶ کاراکتر باشد.');
      return;
    }
    if (floorCode.isEmpty) {
      setState(() => _errorMessage = 'لطفاً کد اختصاصی طبقه/واحد را وارد کنید.');
      return;
    }

    setState(() {
      _isLoading = true;
      _errorMessage = null;
    });

    try {
      final AuthUser user = await _api.registerResident(
        fullName: fullName,
        phone: phone,
        email: email,
        password: password,
        floorCode: floorCode,
      );

      if (!mounted) return;
      Navigator.pushReplacement(
        context,
        MaterialPageRoute(builder: (_) => MainScreen(user: user)),
      );
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _errorMessage = e.toString().replaceFirst('Exception: ', '');
      });
    } finally {
      if (mounted) {
        setState(() {
          _isLoading = false;
        });
      }
    }
  }

  void _quickLoginAsIlia() {
    _usernameController.text = 'ilia';
    _passwordController.text = '123456';
    _handleLogin(overrideUser: 'ilia', overridePass: '123456');
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF0F172A),
      body: SafeArea(
        child: Directionality(
          textDirection: TextDirection.rtl,
          child: Center(
            child: SingleChildScrollView(
              padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 20),
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      const ConnectionBadge(),
                      IconButton(
                        icon: const Icon(Icons.settings_outlined, color: Color(0xFF94A3B8)),
                        tooltip: 'تنظیمات سرور',
                        onPressed: () {
                          Navigator.push(
                            context,
                            MaterialPageRoute(builder: (_) => const SettingsScreen()),
                          );
                        },
                      ),
                    ],
                  ),
                  const SizedBox(height: 24),
                  // Logo / Avatar
                  Center(
                    child: Container(
                      width: 80,
                      height: 80,
                      decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        gradient: const LinearGradient(
                          colors: [Color(0xFF10B981), Color(0xFF065F46)],
                          begin: Alignment.topLeft,
                          end: Alignment.bottomRight,
                        ),
                        boxShadow: [
                          BoxShadow(
                            color: const Color(0xFF10B981).withOpacity(0.3),
                            blurRadius: 20,
                            spreadRadius: 3,
                          ),
                        ],
                      ),
                      child: const Center(
                        child: Icon(Icons.graphic_eq_rounded, size: 44, color: Colors.white),
                      ),
                    ),
                  ),
                  const SizedBox(height: 16),
                  const Text(
                    'AVA 9',
                    textAlign: TextAlign.center,
                    style: TextStyle(
                      color: Colors.white,
                      fontSize: 26,
                      fontWeight: FontWeight.bold,
                      letterSpacing: 0.5,
                    ),
                  ),
                  const SizedBox(height: 4),
                  const Text(
                    'دستیار صوتی هوشمند آوا (نسخه ۹)',
                    textAlign: TextAlign.center,
                    style: TextStyle(
                      color: Color(0xFF94A3B8),
                      fontSize: 13,
                    ),
                  ),
                  const SizedBox(height: 28),

                  // Mode Selector Tabs (ورود به سامانه / ثبت‌نام ساکن جدید)
                  Container(
                    height: 48,
                    padding: const EdgeInsets.all(4),
                    decoration: BoxDecoration(
                      color: const Color(0xFF1E293B),
                      borderRadius: BorderRadius.circular(16),
                      border: Border.all(color: const Color(0xFF334155)),
                    ),
                    child: Row(
                      children: [
                        Expanded(
                          child: InkWell(
                            onTap: _isLoading
                                ? null
                                : () {
                                    if (_isRegisterMode) {
                                      setState(() {
                                        _isRegisterMode = false;
                                        _errorMessage = null;
                                      });
                                    }
                                  },
                            borderRadius: BorderRadius.circular(12),
                            child: AnimatedContainer(
                              duration: const Duration(milliseconds: 200),
                              padding: const EdgeInsets.symmetric(vertical: 8),
                              decoration: BoxDecoration(
                                color: !_isRegisterMode ? const Color(0xFF10B981) : Colors.transparent,
                                borderRadius: BorderRadius.circular(12),
                                boxShadow: !_isRegisterMode
                                    ? [
                                        BoxShadow(
                                          color: const Color(0xFF10B981).withOpacity(0.3),
                                          blurRadius: 8,
                                          offset: const Offset(0, 2),
                                        ),
                                      ]
                                    : null,
                              ),
                              alignment: Alignment.center,
                              child: Row(
                                mainAxisAlignment: MainAxisAlignment.center,
                                children: [
                                  Icon(
                                    Icons.login_rounded,
                                    size: 18,
                                    color: !_isRegisterMode ? Colors.white : const Color(0xFF94A3B8),
                                  ),
                                  const SizedBox(width: 6),
                                  Text(
                                    'ورود به سامانه',
                                    style: TextStyle(
                                      color: !_isRegisterMode ? Colors.white : const Color(0xFF94A3B8),
                                      fontSize: 13,
                                      fontWeight: !_isRegisterMode ? FontWeight.bold : FontWeight.w500,
                                    ),
                                  ),
                                ],
                              ),
                            ),
                          ),
                        ),
                        Expanded(
                          child: InkWell(
                            onTap: _isLoading
                                ? null
                                : () {
                                    if (!_isRegisterMode) {
                                      setState(() {
                                        _isRegisterMode = true;
                                        _errorMessage = null;
                                      });
                                    }
                                  },
                            borderRadius: BorderRadius.circular(12),
                            child: AnimatedContainer(
                              duration: const Duration(milliseconds: 200),
                              padding: const EdgeInsets.symmetric(vertical: 8),
                              decoration: BoxDecoration(
                                color: _isRegisterMode ? const Color(0xFF10B981) : Colors.transparent,
                                borderRadius: BorderRadius.circular(12),
                                boxShadow: _isRegisterMode
                                    ? [
                                        BoxShadow(
                                          color: const Color(0xFF10B981).withOpacity(0.3),
                                          blurRadius: 8,
                                          offset: const Offset(0, 2),
                                        ),
                                      ]
                                    : null,
                              ),
                              alignment: Alignment.center,
                              child: Row(
                                mainAxisAlignment: MainAxisAlignment.center,
                                children: [
                                  Icon(
                                    Icons.person_add_alt_1_rounded,
                                    size: 18,
                                    color: _isRegisterMode ? Colors.white : const Color(0xFF94A3B8),
                                  ),
                                  const SizedBox(width: 6),
                                  Text(
                                    'ثبت‌نام ساکن جدید',
                                    style: TextStyle(
                                      color: _isRegisterMode ? Colors.white : const Color(0xFF94A3B8),
                                      fontSize: 13,
                                      fontWeight: _isRegisterMode ? FontWeight.bold : FontWeight.w500,
                                    ),
                                  ),
                                ],
                              ),
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 20),

                  // Error Banner
                  if (_errorMessage != null) ...[
                    Container(
                      padding: const EdgeInsets.all(12),
                      decoration: BoxDecoration(
                        color: const Color(0xFFEF4444).withOpacity(0.12),
                        borderRadius: BorderRadius.circular(12),
                        border: Border.all(color: const Color(0xFFEF4444).withOpacity(0.4)),
                      ),
                      child: Row(
                        children: [
                          const Icon(Icons.error_outline, color: Color(0xFFEF4444), size: 20),
                          const SizedBox(width: 8),
                          Expanded(
                            child: Text(
                              _errorMessage!,
                              style: const TextStyle(color: Color(0xFFEF4444), fontSize: 12),
                            ),
                          ),
                        ],
                      ),
                    ),
                    const SizedBox(height: 16),
                  ],

                  // Conditional Forms
                  if (!_isRegisterMode) ...[
                    // LOGIN FORM
                    TextField(
                      controller: _usernameController,
                      style: const TextStyle(color: Colors.white),
                      textInputAction: TextInputAction.next,
                      decoration: InputDecoration(
                        labelText: 'نام کاربری یا ایمیل',
                        labelStyle: const TextStyle(color: Color(0xFF94A3B8)),
                        prefixIcon: const Icon(Icons.person_outline, color: Color(0xFF64748B)),
                        filled: true,
                        fillColor: const Color(0xFF1E293B),
                        border: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(14),
                          borderSide: const BorderSide(color: Color(0xFF334155)),
                        ),
                        enabledBorder: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(14),
                          borderSide: const BorderSide(color: Color(0xFF334155)),
                        ),
                        focusedBorder: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(14),
                          borderSide: const BorderSide(color: Color(0xFF10B981), width: 1.5),
                        ),
                      ),
                    ),
                    const SizedBox(height: 16),
                    TextField(
                      controller: _passwordController,
                      obscureText: _obscurePassword,
                      textInputAction: TextInputAction.done,
                      onSubmitted: (_) => _handleLogin(),
                      style: const TextStyle(color: Colors.white),
                      decoration: InputDecoration(
                        labelText: 'رمز عبور',
                        labelStyle: const TextStyle(color: Color(0xFF94A3B8)),
                        prefixIcon: const Icon(Icons.lock_outline, color: Color(0xFF64748B)),
                        suffixIcon: IconButton(
                          icon: Icon(
                            _obscurePassword ? Icons.visibility_outlined : Icons.visibility_off_outlined,
                            color: const Color(0xFF64748B),
                          ),
                          onPressed: () {
                            setState(() {
                              _obscurePassword = !_obscurePassword;
                            });
                          },
                        ),
                        filled: true,
                        fillColor: const Color(0xFF1E293B),
                        border: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(14),
                          borderSide: const BorderSide(color: Color(0xFF334155)),
                        ),
                        enabledBorder: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(14),
                          borderSide: const BorderSide(color: Color(0xFF334155)),
                        ),
                        focusedBorder: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(14),
                          borderSide: const BorderSide(color: Color(0xFF10B981), width: 1.5),
                        ),
                      ),
                    ),
                    const SizedBox(height: 24),

                    // Login Button
                    ElevatedButton(
                      onPressed: _isLoading ? null : () => _handleLogin(),
                      style: ElevatedButton.styleFrom(
                        backgroundColor: const Color(0xFF10B981),
                        foregroundColor: Colors.white,
                        padding: const EdgeInsets.symmetric(vertical: 14),
                        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
                        elevation: 2,
                      ),
                      child: _isLoading
                          ? const SizedBox(
                              width: 20,
                              height: 20,
                              child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                            )
                          : const Text(
                              'ورود به سامانه',
                              style: TextStyle(fontSize: 15, fontWeight: FontWeight.bold),
                            ),
                    ),
                    const SizedBox(height: 12),

                    // Quick test user login button
                    OutlinedButton.icon(
                      onPressed: _isLoading ? null : _quickLoginAsIlia,
                      icon: const Icon(Icons.flash_on_rounded, size: 18, color: Color(0xFF06B6D4)),
                      label: const Text(
                        'ورود سریع به عنوان ilia (کاربر تست)',
                        style: TextStyle(fontSize: 13, color: Color(0xFF06B6D4)),
                      ),
                      style: OutlinedButton.styleFrom(
                        padding: const EdgeInsets.symmetric(vertical: 12),
                        side: const BorderSide(color: Color(0xFF06B6D4), width: 1),
                        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
                      ),
                    ),
                  ] else ...[
                    // REGISTRATION FORM
                    TextField(
                      controller: _fullNameController,
                      style: const TextStyle(color: Colors.white),
                      textInputAction: TextInputAction.next,
                      decoration: InputDecoration(
                        labelText: 'نام و نام خانوادگی',
                        labelStyle: const TextStyle(color: Color(0xFF94A3B8)),
                        hintText: 'مثلاً علی رضایی',
                        hintStyle: const TextStyle(color: Color(0xFF475569), fontSize: 13),
                        prefixIcon: const Icon(Icons.badge_outlined, color: Color(0xFF64748B)),
                        filled: true,
                        fillColor: const Color(0xFF1E293B),
                        border: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(14),
                          borderSide: const BorderSide(color: Color(0xFF334155)),
                        ),
                        enabledBorder: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(14),
                          borderSide: const BorderSide(color: Color(0xFF334155)),
                        ),
                        focusedBorder: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(14),
                          borderSide: const BorderSide(color: Color(0xFF10B981), width: 1.5),
                        ),
                      ),
                    ),
                    const SizedBox(height: 14),

                    TextField(
                      controller: _phoneController,
                      keyboardType: TextInputType.phone,
                      textInputAction: TextInputAction.next,
                      style: const TextStyle(color: Colors.white),
                      decoration: InputDecoration(
                        labelText: 'شماره موبایل',
                        labelStyle: const TextStyle(color: Color(0xFF94A3B8)),
                        hintText: '۰۹۱۲۳۴۵۶۷۸۹',
                        hintStyle: const TextStyle(color: Color(0xFF475569), fontSize: 13),
                        prefixIcon: const Icon(Icons.phone_android_outlined, color: Color(0xFF64748B)),
                        filled: true,
                        fillColor: const Color(0xFF1E293B),
                        border: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(14),
                          borderSide: const BorderSide(color: Color(0xFF334155)),
                        ),
                        enabledBorder: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(14),
                          borderSide: const BorderSide(color: Color(0xFF334155)),
                        ),
                        focusedBorder: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(14),
                          borderSide: const BorderSide(color: Color(0xFF10B981), width: 1.5),
                        ),
                      ),
                    ),
                    const SizedBox(height: 14),

                    TextField(
                      controller: _registerEmailController,
                      keyboardType: TextInputType.emailAddress,
                      textInputAction: TextInputAction.next,
                      style: const TextStyle(color: Colors.white),
                      decoration: InputDecoration(
                        labelText: 'ایمیل',
                        labelStyle: const TextStyle(color: Color(0xFF94A3B8)),
                        hintText: 'resident@building.local',
                        hintStyle: const TextStyle(color: Color(0xFF475569), fontSize: 13),
                        prefixIcon: const Icon(Icons.email_outlined, color: Color(0xFF64748B)),
                        filled: true,
                        fillColor: const Color(0xFF1E293B),
                        border: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(14),
                          borderSide: const BorderSide(color: Color(0xFF334155)),
                        ),
                        enabledBorder: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(14),
                          borderSide: const BorderSide(color: Color(0xFF334155)),
                        ),
                        focusedBorder: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(14),
                          borderSide: const BorderSide(color: Color(0xFF10B981), width: 1.5),
                        ),
                      ),
                    ),
                    const SizedBox(height: 14),

                    TextField(
                      controller: _registerPasswordController,
                      obscureText: _obscureRegisterPassword,
                      textInputAction: TextInputAction.next,
                      style: const TextStyle(color: Colors.white),
                      decoration: InputDecoration(
                        labelText: 'رمز عبور',
                        labelStyle: const TextStyle(color: Color(0xFF94A3B8)),
                        hintText: 'حداقل ۶ کاراکتر',
                        hintStyle: const TextStyle(color: Color(0xFF475569), fontSize: 13),
                        prefixIcon: const Icon(Icons.lock_outline, color: Color(0xFF64748B)),
                        suffixIcon: IconButton(
                          icon: Icon(
                            _obscureRegisterPassword
                                ? Icons.visibility_outlined
                                : Icons.visibility_off_outlined,
                            color: const Color(0xFF64748B),
                          ),
                          onPressed: () {
                            setState(() {
                              _obscureRegisterPassword = !_obscureRegisterPassword;
                            });
                          },
                        ),
                        filled: true,
                        fillColor: const Color(0xFF1E293B),
                        border: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(14),
                          borderSide: const BorderSide(color: Color(0xFF334155)),
                        ),
                        enabledBorder: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(14),
                          borderSide: const BorderSide(color: Color(0xFF334155)),
                        ),
                        focusedBorder: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(14),
                          borderSide: const BorderSide(color: Color(0xFF10B981), width: 1.5),
                        ),
                      ),
                    ),
                    const SizedBox(height: 14),

                    TextField(
                      controller: _floorCodeController,
                      textInputAction: TextInputAction.done,
                      onSubmitted: (_) => _handleRegister(),
                      style: const TextStyle(color: Colors.white, letterSpacing: 0.8),
                      decoration: InputDecoration(
                        labelText: 'کد اختصاصی طبقه',
                        labelStyle: const TextStyle(color: Color(0xFF94A3B8)),
                        hintText: 'مثلاً FL1-402 یا کد اختصاصی واحد',
                        hintStyle: const TextStyle(color: Color(0xFF475569), fontSize: 13),
                        helperText: 'کد ۴ رقمی/حرفی اختصاصی واحد خود را وارد کنید',
                        helperStyle: const TextStyle(color: Color(0xFF38BDF8), fontSize: 11),
                        prefixIcon: const Icon(Icons.vpn_key_outlined, color: Color(0xFF64748B)),
                        filled: true,
                        fillColor: const Color(0xFF1E293B),
                        border: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(14),
                          borderSide: const BorderSide(color: Color(0xFF334155)),
                        ),
                        enabledBorder: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(14),
                          borderSide: const BorderSide(color: Color(0xFF334155)),
                        ),
                        focusedBorder: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(14),
                          borderSide: const BorderSide(color: Color(0xFF10B981), width: 1.5),
                        ),
                      ),
                    ),
                    const SizedBox(height: 24),

                    // Register Button
                    ElevatedButton(
                      onPressed: _isLoading ? null : () => _handleRegister(),
                      style: ElevatedButton.styleFrom(
                        backgroundColor: const Color(0xFF10B981),
                        foregroundColor: Colors.white,
                        padding: const EdgeInsets.symmetric(vertical: 14),
                        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
                        elevation: 2,
                      ),
                      child: _isLoading
                          ? const SizedBox(
                              width: 20,
                              height: 20,
                              child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                            )
                          : const Text(
                              'ثبت‌نام و ورود به سامانه',
                              style: TextStyle(fontSize: 15, fontWeight: FontWeight.bold),
                            ),
                    ),
                  ],
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
