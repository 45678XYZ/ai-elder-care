import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../theme/app_theme.dart';
import '../services/auth_backend.dart';
import '../services/auth_service.dart';
import '../widgets/form_widgets.dart';

/// `/auth/sign-in` — 登入。
///
/// 登入前不知道對面是長輩還是家人，所以整頁照**長者規格**做（字級 >=24sp、觸控 >=60dp）：
/// 大字對照護者不會不好用，反過來則會。
///
/// 可互動元素四個（信箱、密碼、登入、去註冊），比長者模式的上限多一個——
/// 登入本來就需要這四個，砍掉任何一個都會讓人無路可走。這是刻意的例外，
/// 登入之後的每一頁仍守 <=3。
class SignInScreen extends StatefulWidget {
  const SignInScreen({super.key});

  @override
  State<SignInScreen> createState() => _SignInScreenState();
}

class _SignInScreenState extends State<SignInScreen> {
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

    // 先擋明顯的空白與格式，不必等後端來回一趟才知道少填。
    if (email.isEmpty || password.isEmpty) {
      setState(() => _error = '請填信箱和密碼');
      return;
    }
    if (!looksLikeEmail(email)) {
      setState(() => _error = '信箱格式不太對，請再看一下');
      return;
    }

    setState(() {
      _busy = true;
      _error = null;
    });

    try {
      final identity =
          await AuthService.instance.signIn(email: email, password: password);
      if (!mounted) return;
      // 進哪個模式由 token 的 elder_id claim 決定，與後端 auth.py 同一套判準。
      context.go(
          identity.role == UserRole.elder ? '/elder/today' : '/care/summary');
    } on AuthException catch (e) {
      if (!mounted) return;
      // 帳號還沒驗證信箱時，該做的是把人帶去輸入驗證碼，不是丟一句錯誤讓他自己想辦法。
      if (e.code == AuthErrorCode.userNotConfirmed) {
        setState(() => _busy = false);
        context.push('/auth/verify', extra: email);
        return;
      }
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
              Text('登入', style: text.headlineLarge),
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
              const SizedBox(height: AppSpacing.xl),

              BigButton(label: '登入', busy: _busy, onPressed: _submit),

              if (_error != null) ...[
                const SizedBox(height: AppSpacing.lg),
                FeedbackBanner(message: _error!, isError: true),
              ],

              const SizedBox(height: AppSpacing.xl),
              Center(
                child: TextLink(
                  label: '還沒有帳號？註冊',
                  onTap: _busy ? null : () => context.push('/auth/sign-up'),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// 只做最基本的形狀檢查。真正有沒有這個信箱要靠寄驗證信，
/// 在這裡用嚴格的正規表達式只會擋掉合法的少見位址。
bool looksLikeEmail(String v) =>
    RegExp(r'^[^@\s]+@[^@\s]+\.[^@\s]+$').hasMatch(v);
