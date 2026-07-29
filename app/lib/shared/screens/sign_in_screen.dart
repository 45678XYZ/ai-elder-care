import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../theme/app_theme.dart';
import '../services/auth_backend.dart';
import '../services/auth_service.dart';
import '../services/session_store.dart';
import '../widgets/form_widgets.dart';

/// `/auth/sign-in` — 登入。
///
/// 登入前不知道對面是長輩還是家人，所以整頁照**長者規格**做（字級 >=24sp、觸控 >=60dp）：
/// 大字對照護者不會不好用，反過來則會。
///
/// 可互動元素四個（信箱、密碼、登入、去註冊），比長者模式的上限多一個——
/// 登入本來就需要這四個，砍掉任何一個都會讓人無路可走。這是刻意的例外，
/// 登入之後的每一頁仍守 <=3。
///
/// 錯誤的位置分兩層：指得到單一欄位的（信箱格式、密碼沒填）長在該欄位下面，兩格
/// 都有問題就兩句一起出現；指不到欄位的（「信箱或密碼錯誤」、連線失敗）留在頁尾的
/// [FeedbackBanner]。
///
/// 「信箱或密碼錯誤」不能拆成兩個欄位錯誤：後端（Cognito）對「查無此人」與「密碼錯」
/// 回同一種錯誤，本來就不知道是哪一個；硬拆會變成憑空猜測，還會洩漏某個信箱有沒有註冊過。
class SignInScreen extends StatefulWidget {
  const SignInScreen({super.key});

  @override
  State<SignInScreen> createState() => _SignInScreenState();
}

class _SignInScreenState extends State<SignInScreen> {
  final _emailCtrl = TextEditingController();
  final _passwordCtrl = TextEditingController();

  String? _error;

  /// 欄位層級的錯誤，長在出問題的那一格下面，不丟到頁尾 banner。
  ///
  /// 「信箱或密碼錯誤」不在此列——它刻意不說是哪一個錯（不洩漏信箱是否註冊過），
  /// 指不到欄位，所以留在頁尾。
  String? _emailError;
  String? _passwordError;
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

    // 先擋明顯的空白與格式，不必等後端來回一趟才知道少填。兩格一次全驗，
    // 兩邊都有問題就兩句一起出現，不要讓人送出兩次才看完。
    //
    // 密碼在這裡**只檢查有沒有填**，不驗格式：既有帳號的密碼未必符合現行規則，
    // 在登入頁擋格式會把合法使用者關在門外。格式是註冊時的事。
    final emailError = looksLikeEmail(email) ? null : '信箱格式不太對，請再看一下';
    final passwordError = password.isEmpty ? '請填密碼' : null;

    if (emailError != null || passwordError != null) {
      setState(() {
        _error = null;
        _emailError = emailError;
        _passwordError = passwordError;
      });
      return;
    }

    setState(() {
      _busy = true;
      _error = null;
      _emailError = null;
      _passwordError = null;
    });

    try {
      final identity =
          await AuthService.instance.signIn(email: email, password: password);
      // 換帳號後要重載長者情境：「已完成首次設定」等狀態是按帳號存的（見 AppSession），
      // 而 AppSession 是單例，這時候手上可能還是上一個帳號的值。少了這一步，
      // 下面的 redirect 會拿別人的旗標來決定這一位要不要先建資料。
      await AppSession.instance.loadForAccount(identity.userId);
      if (!mounted) return;
      // 落點不在這裡決定：登入成功只代表「可以進 App 了」，至於進哪個模式還要看
      // 有沒有宣告身分、長者有沒有建資料。統一丟給 router 的 redirect 判定（app_router）。
      context.go('/');
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
                onChanged: _emailError == null
                    ? null
                    : (_) => setState(() => _emailError = null),
              ),
              if (_emailError != null) ...[
                const SizedBox(height: AppSpacing.sm),
                FieldNote(_emailError!, isError: true),
              ],
              const SizedBox(height: AppSpacing.lg),

              Text('密碼', style: text.headlineSmall),
              const SizedBox(height: AppSpacing.sm),
              BigTextField(
                controller: _passwordCtrl,
                enabled: !_busy,
                obscureText: true,
                textInputAction: TextInputAction.done,
                onChanged: _passwordError == null
                    ? null
                    : (_) => setState(() => _passwordError = null),
                onSubmitted: (_) => _submit(),
              ),
              if (_passwordError != null) ...[
                const SizedBox(height: AppSpacing.sm),
                FieldNote(_passwordError!, isError: true),
              ],
              const SizedBox(height: AppSpacing.xl),

              BigButton(label: '登入', busy: _busy, onPressed: _submit),

              // 這裡只留指不到欄位的錯：「信箱或密碼錯誤」（刻意不說是哪一個）、
              // 連不上網路之類。
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
