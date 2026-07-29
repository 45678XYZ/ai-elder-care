import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import 'caregiver/caregiver_shell.dart';
import 'caregiver/screens/elders_screen.dart';
import 'caregiver/screens/stats_screen.dart';
import 'caregiver/screens/summaries_screen.dart';
import 'caregiver/screens/timeline_screen.dart';
import 'caregiver/screens/setup_screen.dart';
import 'elder/elder_shell.dart';
import 'elder/screens/chat_screen.dart';
import 'elder/screens/link_caregiver_screen.dart';
import 'elder/screens/today_screen.dart';
import 'shared/screens/role_select_screen.dart';
import 'shared/screens/sign_in_screen.dart';
import 'shared/screens/sign_up_screen.dart';
import 'shared/screens/verify_email_screen.dart';
import 'shared/services/auth_service.dart';
import 'shared/services/session_store.dart';

/// App 導覽。畫面編號與路徑對照見 HANDOFF.md 畫面清單。
/// 兩模式各自一個 StatefulShellRoute，切 tab 保留畫面狀態與捲動位置（§6）。
final _elderNavKey = GlobalKey<NavigatorState>();
final _careNavKey = GlobalKey<NavigatorState>();

/// 全域守門：每次導覽都重算「以現在的登入狀態，這個位置合不合法」。
///
/// 集中在這裡而不是散在各畫面的 `context.go`，是因為落點的條件有三層（有沒有登入、
/// 有沒有身分、長者有沒有建資料），任何一個畫面自己判斷都會漏掉某個組合；
/// 直接開網址或重整（web）更是完全繞過畫面裡的判斷。
///
/// 回 null 表示「留在原地」。
String? _redirect(BuildContext context, GoRouterState state) {
  final auth = AuthService.instance;
  final location = state.matchedLocation;
  final isAuthRoute = location.startsWith('/auth/');

  // 未登入：只准待在認證流程裡（登入／註冊／驗證碼三頁可自由來回），外加一個例外——
  // 註冊流程中的 /setup。長者的基本資料是在**註冊之內**填的（註冊 → /setup → 驗證碼
  // → 登入），那個時間點本來就還沒登入，守衛若一律踢回登入頁，這條流程根本走不到。
  //
  // 判斷依據是 extra 帶著非空的 email：那是註冊頁 push 進來時才有的東西，也是這份資料
  // 唯一能寄放的位置（見 AppSession.savePendingSetup 的時序說明）。沒有 extra——直接開
  // 網址、或 web 上重整——就照舊踢回登入頁。這與 /auth/verify 的處理一致：那一頁沒有
  // extra 時也是退回註冊頁重走，因為少了 email 就沒有辦法完成這一步。
  if (!auth.isSignedIn) {
    final extra = state.extra;
    final isSignUpSetup =
        location == '/setup' && extra is String && extra.isNotEmpty;
    return (isAuthRoute || isSignUpSetup) ? null : '/auth/sign-in';
  }

  // 已登入的人不該再看到認證頁或身分宣告頁，這三個位置一律要被踢走。
  final isPreAppRoute = isAuthRoute || location == '/' || location == '/setup';

  final role = auth.effectiveRole;

  // 還沒宣告身分：token 沒有 elder_id 也沒選過，只能先去選。
  //
  // 這是**退路**，不是正常流程的一站——身分在註冊頁就問完了（SignUpScreen）。會落到這裡
  // 的是「本機沒有身分記錄」的情況：在別台裝置註冊、或清掉 App 資料後再登入（照護者身分
  // 目前只存在本機，見 AuthService 檔尾 TODO(backend)）。所以這一段不能拿掉。
  if (role == null) {
    return location == '/' ? null : '/';
  }

  if (role == UserRole.caregiver) {
    return isPreAppRoute ? '/care/summary' : null;
  }

  // 長者但還沒建個人資料：先把資料補完，其餘畫面沒有稱呼與行程可用。
  // 這個旗標是**這個帳號**的（見 AppSession.loadForAccount），不是這台裝置的——
  // 否則照護者在同一台用過 /setup 之後，長者登入就會被當成已設定而跳過。
  if (!AppSession.instance.setupDone) {
    return location == '/setup' ? null : '/setup';
  }
  return isPreAppRoute ? '/elder/today' : null;
}

GoRouter buildRouter({String initialLocation = '/auth/sign-in'}) => GoRouter(
      initialLocation: initialLocation,
      redirect: _redirect,
      routes: [
        // 認證。長者與照護者共用同一組畫面，登入後才由 token 的 elder_id claim 分流。
        GoRoute(path: '/auth/sign-in', builder: (_, __) => const SignInScreen()),
        GoRoute(path: '/auth/sign-up', builder: (_, __) => const SignUpScreen()),
        GoRoute(
          path: '/auth/verify',
          // 信箱由上一頁用 extra 帶進來。直接開網址（例如重整）時沒有 extra，
          // 這時退回註冊頁重走一次，比帶著空信箱送出驗證碼要好。
          builder: (context, state) {
            final email = state.extra;
            if (email is! String || email.isEmpty) return const SignUpScreen();
            return VerifyEmailScreen(email: email);
          },
        ),

        // S1 首次設定。兩種情境共用同一個畫面，差別只在有沒有 extra（信箱）：
        // - 註冊流程（未登入）：註冊頁 push 進來並帶 email，資料先按 email 暫存
        // - 已登入但這個帳號沒有資料（換裝置的退路）：沒有 email，直接寫進帳號
        GoRoute(
          path: '/setup',
          builder: (_, state) {
            final email = state.extra;
            return SetupScreen(
                email: email is String && email.isNotEmpty ? email : null);
          },
        ),

        // S2 角色選擇。正常流程走不到這裡（身分在註冊頁就宣告了），只在本機沒有身分
        // 記錄時當退路用，見 RoleSelectScreen。
        GoRoute(path: '/', builder: (_, __) => const RoleSelectScreen()),

        // 連結家人。刻意放在 shell 之外——它是蓋在長者 tab 上的一次性流程，
        // 不該變成第三個常駐 tab（長者模式一頁最多 3 個可互動元素）。
        // 從今天頁 push 進來，返回時回到原本的 tab 與捲動位置。
        GoRoute(
            path: '/elder/link', builder: (_, __) => const LinkCaregiverScreen()),

        // S3+S4 長者模式：底部 2 tab
        StatefulShellRoute.indexedStack(
          builder: (_, __, shell) => ElderShell(navigationShell: shell),
          branches: [
            StatefulShellBranch(
              navigatorKey: _elderNavKey,
              routes: [
                GoRoute(
                    path: '/elder/chat',
                    builder: (_, __) => const ChatScreen()),
              ],
            ),
            StatefulShellBranch(
              routes: [
                GoRoute(
                    path: '/elder/today',
                    builder: (_, __) => const TodayScreen()),
              ],
            ),
          ],
        ),

        // S5–S8 照護者模式：底部 4 tab
        StatefulShellRoute.indexedStack(
          builder: (_, __, shell) => CaregiverShell(navigationShell: shell),
          branches: [
            StatefulShellBranch(
              navigatorKey: _careNavKey,
              routes: [
                GoRoute(
                    path: '/care/summary',
                    builder: (_, __) => const SummariesScreen()),
              ],
            ),
            StatefulShellBranch(
              routes: [
                GoRoute(
                    path: '/care/timeline',
                    builder: (_, __) => const TimelineScreen()),
              ],
            ),
            StatefulShellBranch(
              routes: [
                GoRoute(
                    path: '/care/stats',
                    builder: (_, __) => const StatsScreen()),
              ],
            ),
            StatefulShellBranch(
              routes: [
                GoRoute(
                    path: '/care/manage',
                    builder: (_, __) => const EldersScreen()),
              ],
            ),
          ],
        ),
      ],
    );
