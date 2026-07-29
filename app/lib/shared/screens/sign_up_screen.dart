import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../theme/app_theme.dart';
import '../services/auth_backend.dart';
import '../services/auth_service.dart';
import '../services/demo_auth_backend.dart';
import '../widgets/form_widgets.dart';
import 'sign_in_screen.dart' show looksLikeEmail;

/// `/auth/sign-up` — 註冊，同時宣告身分。
///
/// 長者與照護者共用同一份註冊流程：兩邊都是自己在自己的手機上開帳號，
/// 差別只在登入後 token 有沒有 elder_id claim。
///
/// 只要一次密碼，不做「再輸入一次確認」——長輩打字吃力，重打一次的錯誤率
/// 比打錯密碼本身還高；密碼規則直接寫在欄位下方，不藏在錯誤訊息裡。
///
/// 身分在這一頁問，不再另開一頁：可互動元素因此變成六個（信箱、密碼、兩張身分卡、
/// 註冊、去登入），比長者模式的上限 3 多。可以接受的理由有兩個——認證頁本來就是 §3 的
/// 刻意例外（[SignInScreen] 同樣超出，登入需要的欄位砍不掉）；而且在這裡順手問一句
/// 「你是誰」，可以整整省掉登入後那一頁只有兩個選項的畫面，對長輩是少一次迷路的機會。
/// 登入之後的每一頁仍守 <=3。
///
/// 沒有預設選項：預設任一邊，等於在使用者沒表態時默默替他指派身分，選錯的人會直接進到
/// 另一種模式而不知道發生了什麼。未選就送出一律擋下並說明。
class SignUpScreen extends StatefulWidget {
  const SignUpScreen({super.key});

  @override
  State<SignUpScreen> createState() => _SignUpScreenState();
}

class _SignUpScreenState extends State<SignUpScreen> {
  final _emailCtrl = TextEditingController();
  final _passwordCtrl = TextEditingController();

  /// 宣告的身分；null = 還沒選（刻意沒有預設值）。
  UserRole? _role;

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
    final role = _role;

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
    // 身分不給預設值，所以未選就得擋下來——猜錯的代價是整個 App 進錯模式。
    if (role == null) {
      setState(() => _error = '請先選擇你是長輩還是家人');
      return;
    }

    setState(() {
      _busy = true;
      _error = null;
    });

    try {
      final outcome = await AuthService.instance.backend
          .signUp(email: email, password: password);
      // 註冊成功才記身分：註冊失敗（例如信箱已被用）時寫進去，會在下一次別人用同一個
      // 信箱登入時被誤採用。此時還沒有 token，所以只能按 email 暫存，
      // 等第一次登入拿到 sub 才轉正（見 AuthService.declarePendingRole）。
      await AuthService.instance.declarePendingRole(email: email, role: role);
      if (!mounted) return;
      setState(() => _busy = false);

      if (outcome == SignUpOutcome.needsConfirmation) {
        // 長輩多一站：先建基本資料，再去驗證碼。放在註冊流程之內而不是第一次登入之後，
        // 是因為「開帳號」對使用者是一件事，中間被登入切成兩半只會讓人以為還沒設定完。
        // 照護者沒有要填的資料，直接進驗證碼頁。
        //
        // 兩邊都帶信箱：驗證碼頁要用它送驗證碼與重寄，/setup 要用它暫存長輩資料
        // （此時還沒登入，沒有帳號可掛，見 AppSession.savePendingSetup）。
        context.push(role == UserRole.elder ? '/setup' : '/auth/verify',
            extra: email);
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

              // 身分宣告。放在密碼與註冊之間，讓「填完資料 → 說明自己是誰 → 送出」
              // 是一條直線，不必回頭改上面的欄位。
              Text('請問你是？', style: text.headlineSmall),
              const SizedBox(height: AppSpacing.sm),
              BigChoiceCard(
                icon: Icons.elderly,
                label: '長輩',
                selected: _role == UserRole.elder,
                onTap: _busy ? null : () => setState(() => _role = UserRole.elder),
              ),
              const SizedBox(height: AppSpacing.md),
              BigChoiceCard(
                icon: Icons.favorite_border,
                label: '家人 / 照護者',
                selected: _role == UserRole.caregiver,
                onTap:
                    _busy ? null : () => setState(() => _role = UserRole.caregiver),
              ),
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
