import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:go_router/go_router.dart';

import '../../theme/app_theme.dart';
import '../services/auth_backend.dart';
import '../services/auth_service.dart';
import '../widgets/form_widgets.dart';

/// `/auth/verify` — 信箱驗證碼。
///
/// 從註冊頁（剛送出）或登入頁（帳號還沒驗證）進來，兩邊都用 `extra` 帶信箱進來。
///
/// 驗證碼欄位限定六位數字、置中、拉開字距——長輩對著信件一個字一個字打，
/// 看得清楚比排版好看重要。
class VerifyEmailScreen extends StatefulWidget {
  const VerifyEmailScreen({super.key, required this.email});

  final String email;

  @override
  State<VerifyEmailScreen> createState() => _VerifyEmailScreenState();
}

class _VerifyEmailScreenState extends State<VerifyEmailScreen> {
  static const _codeLength = 6;

  final _codeCtrl = TextEditingController();

  String? _error;
  String? _notice;
  bool _busy = false;

  @override
  void dispose() {
    _codeCtrl.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final code = _codeCtrl.text.trim();
    if (code.length < _codeLength) {
      setState(() {
        _notice = null;
        _error = '請輸入信件裡的 $_codeLength 位數字';
      });
      return;
    }

    setState(() {
      _busy = true;
      _error = null;
      _notice = null;
    });

    try {
      await AuthService.instance.backend
          .confirmSignUp(email: widget.email, code: code);
      if (!mounted) return;
      // 驗證完不自動登入：Cognito 的 ConfirmSignUp 不會發 token，
      // 這裡也照著走，讓「驗證」與「登入」各自是一件明確完成的事。
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
      await AuthService.instance.backend.resendCode(email: widget.email);
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
              BigBackButton(onTap: () => context.pop()),
              const SizedBox(height: AppSpacing.xl),

              Text('收信箱拿驗證碼', style: text.headlineLarge),
              const SizedBox(height: AppSpacing.md),
              Text(
                '我們寄了一組 $_codeLength 位數字到\n${widget.email}',
                style:
                    text.headlineSmall?.copyWith(color: AppColors.inkSecondary),
              ),
              const SizedBox(height: AppSpacing.xl),

              BigTextField(
                controller: _codeCtrl,
                hint: '000000',
                enabled: !_busy,
                keyboardType: TextInputType.number,
                textInputAction: TextInputAction.done,
                textAlign: TextAlign.center,
                letterSpacing: 12,
                inputFormatters: [
                  FilteringTextInputFormatter.digitsOnly,
                  LengthLimitingTextInputFormatter(_codeLength),
                ],
                onSubmitted: (_) => _submit(),
              ),
              const SizedBox(height: AppSpacing.xl),

              BigButton(label: '確認', busy: _busy, onPressed: _submit),

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
            ],
          ),
        ),
      ),
    );
  }
}
