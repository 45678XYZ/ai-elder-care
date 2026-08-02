import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:go_router/go_router.dart';

import '../../theme/app_theme.dart';
import '../services/auth_backend.dart';
import '../services/auth_service.dart';
import '../services/password_validator.dart' as pw;
import '../widgets/form_widgets.dart';
import 'sign_in_screen.dart' show looksLikeEmail;

/// `/auth/forgot-password` — 忘記密碼，兩階段。
///
/// 第一步：輸入信箱 -> 呼叫 Cognito `forgotPassword()` -> 寄驗證碼。
/// 第二步：輸入驗證碼 + 新密碼 -> 呼叫 `confirmNewPassword()` -> 成功回登入頁。
class ForgotPasswordScreen extends StatefulWidget {
  const ForgotPasswordScreen({super.key});

  @override
  State<ForgotPasswordScreen> createState() => _ForgotPasswordScreenState();
}

enum _Phase { enterEmail, enterCode }

class _ForgotPasswordScreenState extends State<ForgotPasswordScreen> {
  static const _codeLength = 6;

  final _emailCtrl = TextEditingController();
  final _codeCtrl = TextEditingController();
  final _passwordCtrl = TextEditingController();

  _Phase _phase = _Phase.enterEmail;
  String? _error;
  String? _notice;
  String? _emailError;
  String? _passwordError;
  bool _busy = false;

  @override
  void dispose() {
    _emailCtrl.dispose();
    _codeCtrl.dispose();
    _passwordCtrl.dispose();
    super.dispose();
  }

  String get _email => _emailCtrl.text.trim();

  Future<void> _sendCode() async {
    final emailError = looksLikeEmail(_email) ? null : '信箱格式錯誤';
    if (emailError != null) {
      setState(() {
        _error = null;
        _emailError = emailError;
      });
      return;
    }

    setState(() {
      _busy = true;
      _error = null;
      _emailError = null;
    });

    try {
      await AuthService.instance.backend.forgotPassword(email: _email);
      if (!mounted) return;
      setState(() {
        _busy = false;
        _phase = _Phase.enterCode;
        _notice = null;
      });
    } on AuthException catch (e) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = e.message;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = AuthException.of(AuthErrorCode.unknown).message;
      });
    }
  }

  Future<void> _confirmReset() async {
    final code = _codeCtrl.text.trim();
    final password = _passwordCtrl.text;

    String? passwordError;
    if (password.isEmpty) {
      passwordError = '請填新密碼';
    } else if (!pw.isPasswordValid(password)) {
      passwordError = '密碼格式錯誤';
    }

    if (code.length < _codeLength || passwordError != null) {
      setState(() {
        _error = code.length < _codeLength ? '請輸入信件裡的 $_codeLength 位數字' : null;
        _passwordError = passwordError;
      });
      return;
    }

    setState(() {
      _busy = true;
      _error = null;
      _passwordError = null;
    });

    try {
      await AuthService.instance.backend.confirmNewPassword(
        email: _email,
        code: code,
        newPassword: password,
      );
      if (!mounted) return;
      setState(() {
        _busy = false;
        _notice = '密碼已重設，請重新登入';
      });
      await Future<void>.delayed(const Duration(seconds: 2));
      if (!mounted) return;
      context.go('/auth/sign-in');
    } on AuthException catch (e) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = e.message;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = AuthException.of(AuthErrorCode.unknown).message;
      });
    }
  }

  Future<void> _resend() async {
    setState(() {
      _busy = true;
      _error = null;
      _notice = null;
    });
    try {
      await AuthService.instance.backend.forgotPassword(email: _email);
      if (!mounted) return;
      setState(() {
        _busy = false;
        _notice = '已經重新寄出，請看信箱';
      });
    } on AuthException catch (e) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = e.message;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = AuthException.of(AuthErrorCode.unknown).message;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Scaffold(
      body: SafeArea(
        child: SingleChildScrollView(
          padding: AppSpacing.pageBody,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              BigBackButton(onTap: () {
                if (_phase == _Phase.enterCode) {
                  setState(() {
                    _phase = _Phase.enterEmail;
                    _error = null;
                    _notice = null;
                    _passwordError = null;
                    _codeCtrl.clear();
                    _passwordCtrl.clear();
                  });
                } else {
                  context.pop();
                }
              }),
              const SizedBox(height: AppSpacing.xl),
              if (_phase == _Phase.enterEmail) ..._buildEmailPhase(text),
              if (_phase == _Phase.enterCode) ..._buildCodePhase(text),
            ],
          ),
        ),
      ),
    );
  }

  List<Widget> _buildEmailPhase(TextTheme text) => [
        Text('忘記密碼', style: text.headlineLarge),
        const SizedBox(height: AppSpacing.md),
        Text(
          '輸入你的信箱，我們會寄一組驗證碼給你',
          style: text.headlineSmall?.copyWith(color: AppColors.inkSecondary),
        ),
        const SizedBox(height: AppSpacing.xl),
        Text('信箱', style: text.headlineSmall),
        const SizedBox(height: AppSpacing.sm),
        BigTextField(
          controller: _emailCtrl,
          hint: 'name@example.com',
          enabled: !_busy,
          keyboardType: TextInputType.emailAddress,
          textInputAction: TextInputAction.done,
          onChanged:
              _emailError == null ? null : (_) => setState(() => _emailError = null),
          onSubmitted: (_) => _sendCode(),
        ),
        if (_emailError != null) ...[
          const SizedBox(height: AppSpacing.sm),
          FieldNote(_emailError!, isError: true),
        ],
        const SizedBox(height: AppSpacing.xl),
        BigButton(label: '寄送驗證碼', busy: _busy, onPressed: _sendCode),
        if (_error != null) ...[
          const SizedBox(height: AppSpacing.lg),
          FeedbackBanner(message: _error!, isError: true),
        ],
      ];

  List<Widget> _buildCodePhase(TextTheme text) => [
        Text('設定新密碼', style: text.headlineLarge),
        const SizedBox(height: AppSpacing.md),
        Text(
          '已寄驗證碼到\n$_email',
          style: text.headlineSmall?.copyWith(color: AppColors.inkSecondary),
        ),
        const SizedBox(height: AppSpacing.xl),
        Text('驗證碼', style: text.headlineSmall),
        const SizedBox(height: AppSpacing.sm),
        BigTextField(
          controller: _codeCtrl,
          hint: '000000',
          enabled: !_busy,
          keyboardType: TextInputType.number,
          textInputAction: TextInputAction.next,
          textAlign: TextAlign.center,
          letterSpacing: 12,
          inputFormatters: [
            FilteringTextInputFormatter.digitsOnly,
            LengthLimitingTextInputFormatter(_codeLength),
          ],
        ),
        const SizedBox(height: AppSpacing.lg),
        Text('新密碼', style: text.headlineSmall),
        const SizedBox(height: AppSpacing.sm),
        BigTextField(
          controller: _passwordCtrl,
          enabled: !_busy,
          obscureText: true,
          textInputAction: TextInputAction.done,
          onChanged: _passwordError == null
              ? null
              : (_) => setState(() => _passwordError = null),
          onSubmitted: (_) => _confirmReset(),
        ),
        const SizedBox(height: AppSpacing.sm),
        FieldNote('至少 8 個字，要有英文字母和數字', isError: _passwordError != null),
        if (_passwordError != null) ...[
          const SizedBox(height: AppSpacing.sm),
          FieldNote(_passwordError!, isError: true),
        ],
        const SizedBox(height: AppSpacing.xl),
        BigButton(label: '確認', busy: _busy, onPressed: _confirmReset),
        if (_error != null) ...[
          const SizedBox(height: AppSpacing.lg),
          FeedbackBanner(message: _error!, isError: true),
        ],
        if (_notice != null) ...[
          const SizedBox(height: AppSpacing.lg),
          FeedbackBanner(message: _notice!, isError: false),
        ],
        const SizedBox(height: AppSpacing.xl),
        Center(
          child: TextLink(
            label: '沒收到？重新寄一次',
            onTap: _busy ? null : _resend,
          ),
        ),
      ];
}
