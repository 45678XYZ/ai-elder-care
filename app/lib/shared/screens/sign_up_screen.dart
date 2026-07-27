import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../theme/app_theme.dart';
import '../services/auth_backend.dart';
import '../services/auth_service.dart';
import '../services/demo_auth_backend.dart';
import '../widgets/form_widgets.dart';
import 'sign_in_screen.dart' show looksLikeEmail;

/// `/auth/sign-up` — 註冊。
///
/// 長者與照護者共用同一份註冊流程：兩邊都是自己在自己的手機上開帳號，
/// 差別只在登入後 token 有沒有 elder_id claim。
///
/// 只要一次密碼，不做「再輸入一次確認」——長輩打字吃力，重打一次的錯誤率
/// 比打錯密碼本身還高；密碼規則直接寫在欄位下方，不藏在錯誤訊息裡。
class SignUpScreen extends StatefulWidget {
  const SignUpScreen({super.key});

  @override
  State<SignUpScreen> createState() => _SignUpScreenState();
}

class _SignUpScreenState extends State<SignUpScreen> {
  final _emailCtrl = TextEditingController();
  final _passwordCtrl = TextEditingController();

  String? _error;
  bool _busy = false;

  @override
  void dispose() {
    _emailCtrl.dispose();
    _passwordCtrl.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final email = _emailCtrl.text.trim();
    final password = _passwordCtrl.text;

    if (email.isEmpty || password.isEmpty) {
      setState(() => _error = '請填信箱和密碼');
      return;
    }
    if (!looksLikeEmail(email)) {
      setState(() => _error = '信箱格式不太對，請再看一下');
      return;
    }
    // 密碼規則在送出前就檢查：規則寫在畫面上，就不該讓人送出後才被打回票。
    if (!DemoAuthBackend.isPasswordValid(password)) {
      setState(() =>
          _error = AuthException.of(AuthErrorCode.invalidPassword).message);
      return;
    }

    setState(() {
      _busy = true;
      _error = null;
    });

    try {
      final outcome = await AuthService.instance.backend
          .signUp(email: email, password: password);
      if (!mounted) return;
      setState(() => _busy = false);

      if (outcome == SignUpOutcome.needsConfirmation) {
        // 帶著信箱進驗證碼頁——那一頁要用它來送驗證碼與重寄。
        context.push('/auth/verify', extra: email);
      } else {
        context.go('/auth/sign-in');
      }
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
              const AppLogoPill(),
              const SizedBox(height: AppSpacing.xl),
              Text('註冊', style: text.headlineLarge),
              const SizedBox(height: AppSpacing.xl),

              Text('信箱', style: text.headlineSmall),
              const SizedBox(height: AppSpacing.sm),
              BigTextField(
                controller: _emailCtrl,
                hint: 'name@example.com',
                enabled: !_busy,
                keyboardType: TextInputType.emailAddress,
                textInputAction: TextInputAction.next,
              ),
              const SizedBox(height: AppSpacing.sm),
              Text('等一下會寄驗證碼到這個信箱',
                  style: text.bodyLarge?.copyWith(color: AppColors.inkSecondary)),
              const SizedBox(height: AppSpacing.lg),

              Text('密碼', style: text.headlineSmall),
              const SizedBox(height: AppSpacing.sm),
              BigTextField(
                controller: _passwordCtrl,
                enabled: !_busy,
                obscureText: true,
                textInputAction: TextInputAction.done,
                onSubmitted: (_) => _submit(),
              ),
              const SizedBox(height: AppSpacing.sm),
              Text('至少 8 個字，要有英文字母和數字',
                  style: text.bodyLarge?.copyWith(color: AppColors.inkSecondary)),
              const SizedBox(height: AppSpacing.xl),

              BigButton(label: '註冊', busy: _busy, onPressed: _submit),

              if (_error != null) ...[
                const SizedBox(height: AppSpacing.lg),
                FeedbackBanner(message: _error!, isError: true),
              ],

              const SizedBox(height: AppSpacing.xl),
              Center(
                child: TextLink(
                  label: '已經有帳號了？登入',
                  onTap: _busy ? null : () => context.go('/auth/sign-in'),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
