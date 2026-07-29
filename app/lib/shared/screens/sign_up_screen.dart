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
/// 比打錯密碼本身還高。密碼規則從進頁面就寫在欄位下方、任何狀態都不收起來；
/// 格式不符時在規則上方多一行錯誤，而不是把規則換掉（規則就是修正方法）。
/// 密碼欄位另給一顆顯示／隱藏鈕，看得到自己打了什麼比藏起來重要。
///
/// 身分在這一頁問，不再另開一頁：可互動元素因此變成六個（信箱、密碼、兩張身分卡、
/// 註冊、去登入），比長者模式的上限 3 多。可以接受的理由有兩個——認證頁本來就是 §3 的
/// 刻意例外（[SignInScreen] 同樣超出，登入需要的欄位砍不掉）；而且在這裡順手問一句
/// 「你是誰」，可以整整省掉登入後那一頁只有兩個選項的畫面，對長輩是少一次迷路的機會。
/// 登入之後的每一頁仍守 <=3。
///
/// 沒有預設選項：預設任一邊，等於在使用者沒表態時默默替他指派身分，選錯的人會直接進到
/// 另一種模式而不知道發生了什麼。未選就送出一律擋下並說明。
///
/// 三格（信箱、密碼、身分）一次全驗，每一種錯都長在自己那一格下面。按鈕底下的
/// [FeedbackBanner] 只剩指不到欄位的錯（連不上網路之類）。
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

  /// 欄位層級的錯誤。跟 [_error] 分開放：它們長在出問題的那個欄位下面，
  /// 不跑到頁尾的 banner 去讓人自己回頭找是哪一格有問題。
  ///
  /// 留在 [_error] 的只有跨欄位或整頁層級的事：兩格都沒填、身分沒選、後端回的錯。
  String? _emailError;
  String? _passwordError;
  String? _roleError;
  bool _busy = false;

  /// 送出前把欄位層級的錯誤清乾淨，一次只呈現目前這一輪的問題。
  void _clearFieldErrors() {
    _emailError = null;
    _passwordError = null;
    _roleError = null;
  }

  /// 選身分。選了就把「請選擇身分」收掉，不必等下一次送出。
  void _pickRole(UserRole role) => setState(() {
        _role = role;
        _roleError = null;
      });

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

    // 三格一次全驗，不是遇到第一個錯就 return。逐項回報的話，三件事都有問題的人
    // 得送出三次、被打回票三次才看得完，而且每次只知道一件事。
    //
    // 沒填也走同一條規則，不另外給「請填…」：空的信箱本來就不符合信箱格式，
    // 空的密碼也不符合密碼規則，講同一句話就好，而且訊息就長在該負責的那一格下面。
    final emailError = looksLikeEmail(email) ? null : '信箱格式不太對，請再看一下';
    // 密碼規則在送出前就檢查：規則寫在畫面上，就不該讓人送出後才被打回票。
    // 訊息刻意不重述規則（那一行本來就一直在下面），只講「這裡有問題」。
    final passwordError =
        DemoAuthBackend.isPasswordValid(password) ? null : '密碼格式錯誤';

    // 身分不給預設值，所以未選就得擋下來——猜錯的代價是整個 App 進錯模式。
    // `role == null` 寫在條件裡（而不是先算成訊息）才能讓下面的 role 被推導成非 null。
    if (emailError != null || passwordError != null || role == null) {
      setState(() {
        _error = null;
        _emailError = emailError;
        _passwordError = passwordError;
        _roleError = role == null ? '請選擇身分' : null;
      });
      return;
    }

    setState(() {
      _busy = true;
      _error = null;
      _clearFieldErrors();
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
                onChanged: _emailError == null
                    ? null
                    : (_) => setState(() => _emailError = null),
              ),
              const SizedBox(height: AppSpacing.sm),
              if (_emailError != null) ...[
                FieldNote(_emailError!, isError: true),
                const SizedBox(height: AppSpacing.sm),
              ],
              const FieldNote('等一下會寄驗證碼到這個信箱'),
              const SizedBox(height: AppSpacing.lg),

              Text('密碼', style: text.headlineSmall),
              const SizedBox(height: AppSpacing.sm),
              BigTextField(
                controller: _passwordCtrl,
                enabled: !_busy,
                obscureText: true,
                showObscureToggle: true,
                textInputAction: TextInputAction.done,
                // 開始修就把錯誤收掉，不要一邊改一邊被舊訊息盯著
                onChanged: _passwordError == null
                    ? null
                    : (_) => setState(() => _passwordError = null),
                onSubmitted: (_) => _submit(),
              ),
              const SizedBox(height: AppSpacing.sm),
              // 錯誤是**多**一行，不取代規則說明：規則本身就是修正方法，
              // 被錯誤蓋掉的話人反而不知道要改成什麼。
              if (_passwordError != null) ...[
                FieldNote(_passwordError!, isError: true),
                const SizedBox(height: AppSpacing.sm),
              ],
              const FieldNote('至少 8 個字，要有英文字母和數字'),
              const SizedBox(height: AppSpacing.xl),

              // 身分宣告。放在密碼與註冊之間，讓「填完資料 → 說明自己是誰 → 送出」
              // 是一條直線，不必回頭改上面的欄位。
              Text('請問你是？', style: text.headlineSmall),
              const SizedBox(height: AppSpacing.sm),
              BigChoiceCard(
                icon: Icons.elderly,
                label: '長輩',
                selected: _role == UserRole.elder,
                onTap: _busy ? null : () => _pickRole(UserRole.elder),
              ),
              const SizedBox(height: AppSpacing.md),
              BigChoiceCard(
                icon: Icons.favorite_border,
                label: '家人 / 照護者',
                selected: _role == UserRole.caregiver,
                onTap: _busy ? null : () => _pickRole(UserRole.caregiver),
              ),
              if (_roleError != null) ...[
                const SizedBox(height: AppSpacing.sm),
                FieldNote(_roleError!, isError: true),
              ],
              const SizedBox(height: AppSpacing.xl),

              BigButton(label: '註冊', busy: _busy, onPressed: _submit),

              // 這裡只留指不到欄位的錯（連不上網路之類）。填錯什麼一律長在該欄位下面，
              // 不然人得從按鈕底下的一句話自己回頭找是哪一格。
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
